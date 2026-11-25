"""MR 巡检 (team/patrol.py, team/sources.py, team/jenkins.py), offline: brain-kit's GitLab client on an in-memory
GitLab and Jenkins, the session surface in memory, one clock for both. What it finds (a failed pipeline, a check
Jenkins reported with its log read where the runner reaches Jenkins, an open review thread, a conflict), what it
dispatches and when again (a fingerprint once; a new comment, pipeline, Jenkins build or head again), what it skips
and why, two sessions at a time (测试环境报错's fixes counted) with the MRs open before it started first, the two
failed attempts that block an MR with one comment, the claim an MR handed over keeps, a workspace cloned once and reset
to origin (real git), the reply helper's requests, and the session runner's word on a patrol turn.
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from appkit import BRAINKIT, ROOT, FakeGateway, GatewayError, commit_file, git_repo, mod, sh
from brainfix import needs_brainkit
from patrolfix import (BASE, JENKINS_BASE, JENKINS_ROOT_URL, SHA, Clock, FakeJenkins, FakeSessions, FakeWorkspaces,
                       PatrolGitLab, NOTE, note, pipeline, pipeline_lines)

pytestmark = needs_brainkit
P, PID = "grp/repo", 42
REPLY = ROOT / "backend" / "core" / "team" / "patrol_reply.py"


@pytest.fixture
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


def kit(**over):
    """kit.json as brain-kit reads it (its DEFAULTS under the project's own), patrol switched on unless *over* says."""
    cfg = mod("bk").m("config")
    return cfg.merged(cfg.DEFAULTS, dict({"work_sources": ["mr-handoff", "mr-patrol"]}, **over))


class World:
    def __init__(self, tmp_path, kits=None, claims=None):
        self.tmp, self.clock = tmp_path, Clock()
        self.gl = PatrolGitLab(self.clock)
        self.gl.jenkins = self.jk = FakeJenkins(clock=self.clock)
        self.jenkins = mod("team.jenkins").Jenkins.of(None)
        self.kits = kits or {P: kit()}
        for n, path in enumerate(self.kits):
            self.gl.project(path, PID + n)
        self.sessions, self.ws = FakeSessions(), FakeWorkspaces()
        self.ledger, self.claims = str(tmp_path / "patrol.sqlite"), claims or (lambda: [])
        self.said: list[str] = []
        self.staging = [0]                  # 测试环境报错's fix sessions at work
        self.patrol = self.start(list(self.kits))

    def start(self, projects):
        """Patrol as team mode starts it on bridge.json's *projects*, on the ledger and sessions there are."""
        patrol = mod("team.patrol")
        return patrol.Patrol(
            gl=mod("bk").m("rest").GitLab(BASE, "bridge-tok", self.gl), sessions=self.sessions,
            kit=lambda p: self.kits[p], projects=projects, ledger_path=self.ledger, workspaces=self.ws,
            claims=self.claims, jenkins=self.jenkins, shared=lambda: self.staging[0], now=self.clock,
            out=self.said.append,
            helper=patrol.Helper("/usr/bin/python3", "/app/backend/core/team/patrol_reply.py", "/setup/bridge.json",
                                 "/kit"))

    def with_jenkins(self):
        """Patrol started again as on a bridge.json whose "jenkins" names JENKINS_BASE and its read-only user, the token
        in a file only its owner reads."""
        token = self.tmp / "jenkins-token"
        token.write_text(self.jk.token + "\n")
        token.chmod(0o600)
        block = {"base": JENKINS_BASE, "user": self.jk.user, "token_file": str(token)}
        self.jenkins = mod("team.jenkins").Jenkins.of(block, self.gl)
        self.patrol.close()
        self.patrol = self.start(list(self.kits))

    def round(self):
        return self.patrol.round()

    def later(self, s=60):
        self.clock.t += s

    def item(self, iid, project=P):
        return next(i for i in self.patrol.view()["items"] if i["iid"] == iid and i["project"] == project)

    def slots(self):
        return [s for s, _, _ in self.sessions.sent]


def conflicted(**over):
    return dict({"has_conflicts": True, "detailed_merge_status": "conflict"}, **over)


def test_each_problem_kind_is_found_and_the_session_is_told_each_with_its_ids(bk, tmp_path):
    w = World(tmp_path)
    gl = w.gl
    log = b"".join(b"\x1b[0Ksection_start:1:step\r\x1b[0Kline %d \x1b[32mok\x1b[0m\r\n" % i for i in range(1, 101))
    w.clock.t -= 600
    gl.mr(P, 2)
    pid = gl.fail(P, 2, {"test": log + b"\x1b[0Ksection_end:1:step\r\x1b[0K\nsection_end:2:upload\r\n",
                          "lint": b"style\n"}, allowed=("lint",))
    w.later()
    gl.mr(P, 3)
    review = gl.thread(P, 3, "carol", "百分比要限制在 0-100\n超出就报错", "src/discount.py", 12)
    gl.thread(P, 3, "carol", "这个早改好了", resolved=True)
    gl.thread(P, 3, "dave", "整体可以", resolvable=False)                  # a plain comment from before patrol started
    gl.system(P, 3, "added 1 commit")
    answered = gl.thread(P, 3, "erin", "变量名改一下")
    gl.reply(P, 3, answered, "brain-bridge", "改了：x 改成 rate")             # patrol's own reply answers it
    w.later()
    gl.mr(P, 1, **conflicted())
    w.clock.t += 600
    assert w.round() == 3
    problems = {i["iid"]: i["problems"] for i in w.patrol.view()["items"]}
    assert problems == {1: [{"kind": "conflict", "text": "和 main 有冲突"}],
                        2: [{"kind": "ci_failed", "text": "流水线 #%d 没过：test" % pid}],
                        3: [{"kind": "review", "text": "@carol 的评审意见（src/discount.py:12）"}]}
    # two at a time, the earliest changed first: the conflict waits
    assert [(i, w.item(i)["state"]) for i in (1, 2, 3)] == [(1, "queued"), (2, "working"), (3, "working")]
    assert w.sessions.opened == [("patrol-42-2", "brain-patrol", "claude-opus-5.5", "grp/repo!2"),
                                 ("patrol-42-3", "brain-patrol", "claude-opus-5.5", "grp/repo!3")]
    ci = w.sessions.messages("patrol-42-2")[0]
    assert "流水线 #%d 没过" % pid in ci and "   - test：" in ci and "lint" not in ci
    assert "> line 21 ok" in ci and "> line 100 ok" in ci and "> line 20 ok" not in ci
    assert "\x1b" not in ci and "section_" not in ci
    assert "工作区：/Users/alice/workplace/brain-team/patrol/42-2" in ci and w.sessions.projects["patrol-42-2"].endswith("/42-2")
    note = review["notes"][0]
    rv = w.sessions.messages("patrol-42-3")[0]
    assert "讨论 %s，评论 #%d，@carol，在 src/discount.py:12" % (review["id"], note["id"]) in rv
    assert "   > 百分比要限制在 0-100\n   > 超出就报错" in rv
    helper = ("/usr/bin/python3 -I /app/backend/core/team/patrol_reply.py --config /setup/bridge.json --brainkit /kit "
              "--project grp/repo --mr 3")
    assert helper + " --discussion <讨论号> '回复的话'" in rv and helper + " --note '总结的话'" in rv
    assert "变量名改一下" not in rv and "整体可以" not in rv and "这个早改好了" not in rv
    assert "git push origin HEAD:feature（不加 --force）" in rv
    # both turns end: the review is answered, the pipeline worked once; the conflict goes out
    w.sessions.end()
    w.later()
    w.round()
    assert [(i, w.item(i)["state"]) for i in (1, 2, 3)] == [(1, "working"), (2, "done"), (3, "ok")]
    cf = w.sessions.messages("patrol-42-1")[0]
    assert "和目标分支 main 有冲突：git fetch origin，再 git merge origin/main，解决冲突后提交。不要 rebase，不要强推。" in cf


def test_a_fingerprint_is_dispatched_once_and_a_new_comment_or_head_dispatches_again(bk, tmp_path):
    """A turn that ended at a head was given its failed pipeline: a pipeline run again on that head and failing is no
    new work there, a person's comment is, and so is a new head's failed pipeline."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    first = gl.fail(P, 7)
    w.round()
    w.sessions.end()                    # the session could not fix it: nothing pushed, nothing said
    for _ in range(3):
        w.later()
        w.round()
    assert len(w.sessions.sent) == 1 and w.item(7)["state"] == "done"
    d = gl.thread(P, 7, "carol", "加个测试")
    w.later()
    w.round()                           # a new comment
    second = w.sessions.messages()[-1]
    assert len(w.sessions.sent) == 2 and "评论 #%d" % d["notes"][0]["id"] in second and "#%d 没过" % first in second
    w.sessions.end()
    retry = gl.fail(P, 7)               # a pipeline run again on the same head fails: GitLab leaves the MR as it was
    w.later()
    w.round()
    w.later(mod("team.patrol").RECHECK_S)
    w.round()                           # ... so it is seen when the MR is read again, and is no new work there
    assert len(w.sessions.sent) == 2 and w.item(7)["state"] == "done"
    assert w.item(7)["problems"] == [{"kind": "ci_failed", "text": "流水线 #%d 没过：test" % retry}]
    gl.push(P, 7, SHA[2])               # a new head; its pipeline runs, then fails
    w.later()
    w.round()
    gl.pipeline(P, 7, "running")
    w.later()
    w.round()
    assert len(w.sessions.sent) == 2 and w.item(7)["state"] == "ok"
    fresh = gl.fail(P, 7)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 3 and "#%d 没过" % fresh in w.sessions.messages()[-1]
    assert SHA[2][:12] in w.sessions.messages()[-1] and "加个测试" not in w.sessions.messages()[-1]
    # one session for the MR all along, every message its own sendId
    assert [o[0] for o in w.sessions.opened] == ["patrol-42-7"] and set(w.slots()) == {"patrol-42-7"}
    assert len({sid for _, _, sid in w.sessions.sent}) == 3


def test_a_turn_that_pushed_nothing_is_not_sent_the_same_head_again(bk, tmp_path):
    """As orders!4 went on the test server: main moved, so the MR conflicts with it, and Jenkins' build had failed. The
    session merges main in and pushes; at that new head GitLab still says it conflicts, and Jenkins' build fails the
    same way. That turn ends with nothing to push. Then GitLab sees the conflict is gone, and Jenkins builds the same
    head again and fails as before: neither is new work at that head, so the session is not told it again. A person's
    comment at that head goes out, and so does a conflict the turns there were not given."""
    patrol = mod("team.patrol")
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    gl.mr(P, 4, **conflicted())
    gl.check(P, 4, "unit-tests", "failed", jk.build("/job/orders/29", b"FAILED test_known_failure\n"))
    w.round()
    gl.push(P, 4, SHA[2])               # the merge of main
    gl.check(P, 4, "unit-tests", "failed", jk.build("/job/orders/74", b"FAILED test_known_failure\n"))
    w.sessions.end()
    w.later()
    w.round()
    assert len(w.sessions.sent) == 2 and "和目标分支 main 有冲突" in w.sessions.messages()[-1]
    w.sessions.end()                    # 「本次没有新提交」
    gl.mrs[(P, 4)].update(has_conflicts=False, detailed_merge_status="mergeable")
    gl.touch(P, 4)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 2 and w.item(4)["state"] == "done"
    gl.check(P, 4, "unit-tests", "failed", jk.build("/job/orders/75", b"FAILED test_known_failure\n"))
    w.later(patrol.RECHECK_S)
    w.round()
    assert len(w.sessions.sent) == 2 and w.item(4)["state"] == "done"
    assert jk.reads()[-1] == "/job/orders/75/logText/progressiveText?start=0"
    gl.thread(P, 4, "alice", "test_known_failure 是故意的，先别管它", resolvable=False)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 3 and "故意的" in w.sessions.messages()[-1]
    w.sessions.end()
    gl.mrs[(P, 4)].update(**conflicted())
    gl.touch(P, 4)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 4 and "和目标分支 main 有冲突" in w.sessions.messages()[-1]


def test_a_check_jenkins_reported_goes_out_with_the_tail_of_its_console_read_where_the_runner_reaches_jenkins(
        bk, tmp_path):
    """The MR head's pipeline is external (Jenkins' GitLab plugin posted its statuses): the failed check goes out with
    its link and the tail of its console, both at bridge.json's jenkins.base, not at the address Jenkins put in the
    link; one that passed does not. A GitLab pipeline Jenkins posted a check into has both, GitLab's job once."""
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    console = b"".join(b"\x1b[31m[INFO] step %d\x1b[0m\r\n" % i for i in range(1, 101))
    gl.mr(P, 7)
    gl.check(P, 7, "lint", "success", jk.build("/job/orders/1", b"fine\n"))
    gl.check(P, 7, "unit-tests", "failed", jk.build("/job/orders/2", console))
    gl.mr(P, 8)
    native = gl.fail(P, 8, {"test": b"FAILED test_discount\n"})
    gl.check(P, 8, "it", "failed", jk.build("/job/shop/job/orders-it/12", b"BUILD FAILURE\n"))
    w.round()
    external = gl.mrs[(P, 7)]["_pipeline"]
    assert external["source"] == "external" and gl.jobs[external["id"]] == []
    assert [w.item(i)["problems"] for i in (7, 8)] == [
        [{"kind": "ci_failed", "text": "流水线 #%d 没过：unit-tests" % external["id"]}],
        [{"kind": "ci_failed", "text": "流水线 #%d 没过：test、it" % native}]]
    msg = w.sessions.messages("patrol-42-7")[0]
    assert "流水线 #%d 没过" % external["id"] in msg
    assert "   - unit-tests（Jenkins 构建 %s/job/orders/2/）：\n" % JENKINS_BASE in msg
    assert "     > [INFO] step 21\n" in msg and "     > [INFO] step 100\n" in msg and "[INFO] step 20\n" not in msg
    assert "\x1b" not in msg and "\r" not in msg and "lint" not in msg and JENKINS_ROOT_URL not in msg
    both = w.sessions.messages("patrol-42-8")[0]
    assert "   - test：\n     > FAILED test_discount\n" in both
    assert "   - it（Jenkins 构建 %s/job/shop/job/orders-it/12/）：\n     > BUILD FAILURE\n" % JENKINS_BASE in both
    assert jk.calls == [("GET", JENKINS_BASE + "/job/orders/2/logText/progressiveText?start=0", "brain-reader"),
                        ("GET", JENKINS_BASE + "/job/shop/job/orders-it/12/logText/progressiveText?start=0",
                         "brain-reader")]


NO_LOG = {
    "no jenkins in bridge.json": ("Jenkins 构建 %s/job/orders/2/display/redirect" % JENKINS_ROOT_URL,
                                  "运行机没配 Jenkins（bridge.json 里没有 jenkins）", 0),
    "jenkins refuses (no Overall/Read)": ("Jenkins 构建 %s/job/orders/2/" % JENKINS_BASE,
                                          "Jenkins 不让 brain-reader 读这个构建（HTTP 403）：只读用户要有 Overall/Read 和 Job/Read", 1),
    "jenkins hides the job (no Job/Read)": ("Jenkins 构建 %s/job/orders/2/" % JENKINS_BASE,
                                            "Jenkins 上找不到这个构建（HTTP 404）：只读用户没有它的 Job/Read，或者构建删掉了，"
                                            "或者 bridge.json 的 jenkins.base 不对", 1),
    "a link to no jenkins build": ("外部 CI 报给 GitLab 的，https://ci.example.com/builds/77",
                                   "这个链接不是 Jenkins 构建的地址", 0),
}


@pytest.mark.parametrize("case", list(NO_LOG))
def test_a_check_whose_log_is_not_read_goes_out_with_its_link_and_one_line_why(bk, tmp_path, case):
    """It goes out all the same, the app's log says why once, and a refusal is not asked about again on the next read
    (jenkins.RETRY_S)."""
    w = World(tmp_path)
    gl, jk = w.gl, w.jk
    if case != "no jenkins in bridge.json":
        w.with_jenkins()
    jk.overall_read = "Overall/Read" not in case
    jk.job_read = "Job/Read" not in case
    build = jk.build("/job/orders/2", b"FAILED test_discount\n")
    gl.mr(P, 7)
    gl.check(P, 7, "unit-tests", "failed", "https://ci.example.com/builds/77" if "no jenkins build" in case else build)
    w.round()
    w.later()
    w.round()
    where, why, asked = NO_LOG[case]
    msg, = w.sessions.messages("patrol-42-7")
    pid = gl.mrs[(P, 7)]["_pipeline"]["id"]
    assert w.item(7)["problems"] == [{"kind": "ci_failed", "text": "流水线 #%d 没过：unit-tests" % pid}]
    assert "   - unit-tests（%s）：\n     没读到日志：%s\n" % (where, why) in msg and "FAILED test_discount" not in msg
    assert [s for s in w.said if "no log" in s] == ["grp/repo!7: unit-tests: no log: " + why]
    assert jk.reads() == ["/job/orders/2/logText/progressiveText?start=0"] * asked


@pytest.mark.parametrize("own", [False, True])
def test_a_new_jenkins_build_on_the_same_head_lifts_a_block_and_each_build_s_log_is_read_once(bk, tmp_path, own):
    """Jenkins builds again on the same head: GitLab posts its status into the same pipeline, failed before and after;
    the new build's status is what moves the fingerprint, also when it ran and failed between two reads. Two failed
    attempts blocked the MR on the first build: the new one lifts the block and goes out with its own log. *own*: the
    pipeline is GitLab's own, a job of it failed as well, and Jenkins posts into it."""
    patrol = mod("team.patrol")
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    gl.mr(P, 7)
    if own:
        gl.fail(P, 7, {"test": b"FAILED test_gitlab\n"})
    gl.check(P, 7, "unit-tests", "failed", jk.build("/job/orders/2", b"FAILED test_one\n"))
    w.round()
    w.sessions.end("failed")            # KiroCrew's error, twice: the MR is blocked on the first build
    w.later()
    w.round()
    w.sessions.end("failed")
    for _ in range(3):
        w.later()
        w.round()
    w.later(patrol.RECHECK_S)
    w.round()
    assert len(w.sessions.sent) == 2 and w.item(7)["state"] == "blocked"
    pid = gl.mrs[(P, 7)]["_pipeline"]["id"]
    third = jk.build("/job/orders/3", b"FAILED test_two\n")
    gl.check(P, 7, "unit-tests", "running", third)
    gl.check(P, 7, "unit-tests", "failed", third)
    w.later(patrol.RECHECK_S)
    w.round()
    assert gl.mrs[(P, 7)]["_pipeline"]["id"] == pid and len(w.sessions.sent) == 3
    again = w.sessions.messages()[-1]
    assert "Jenkins 构建 %s/job/orders/3/）：\n     > FAILED test_two\n" % JENKINS_BASE in again and "test_one" not in again
    assert ("> FAILED test_gitlab" in again) is own and gl.asked(r"/jobs/\d+/trace$") == (1 if own else 0)
    assert jk.reads() == ["/job/orders/%d/logText/progressiveText?start=0" % n for n in (2, 3)]


def test_a_build_s_log_is_read_once_and_only_its_end_however_often_its_mr_is_read(bk, tmp_path):
    """A long console: Jenkins says how long it is, and only its end is asked for, once, while its MR is read every
    round as it is worked, and again when Jenkins posts that build's status anew (a new status, at a head the turn that
    ended was given that failure at: nothing goes out again)."""
    patrol, jenkins = mod("team.patrol"), mod("team.jenkins")
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    console = b"".join(b"[INFO] step %d of the build\n" % i for i in range(1, 20001))
    url = jk.build("/job/orders/2", console)
    gl.mr(P, 7)
    gl.check(P, 7, "unit-tests", "failed", url)
    for _ in range(6):
        w.round()
        w.later()
    w.sessions.end()
    gl.check(P, 7, "unit-tests", "failed", url)
    w.later(patrol.RECHECK_S)
    w.round()
    assert len(jk.calls) == 2 and len(w.sessions.sent) == 1
    head, end = jk.reads()
    assert head == "/job/orders/2/logText/progressiveText?start=0"
    assert end == "/job/orders/2/logText/progressiveText?start=%d" % (len(console) - jenkins.TAIL_BYTES)
    for msg in w.sessions.messages():
        assert "     > [INFO] step 19921 of the build\n" in msg and msg.endswith("然后停下。")
        assert "     > [INFO] step 20000 of the build\n" in msg and "step 19920 of" not in msg


def quoted(msg, head):
    """The log lines the message quotes under *head*."""
    lines = msg.split("\n")
    out = []
    for line in lines[lines.index(head) + 1:]:
        if not line.startswith("     "):
            break
        out.append(line)
    return out


def test_a_pipeline_build_s_log_goes_out_as_its_console_text_reads(bk, tmp_path):
    """progressiveText keeps the console notes consoleText leaves out (one before each [Pipeline] line, two on the
    upstream line) and sends every line end as CRLF: the session gets the last lines as consoleText has them. Build 15
    of the test server, its log 136 KB: the end of its tail (the closing [Pipeline] lines, the exit code, the result)
    went out as notes, then nothing."""
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    short, short_console = pipeline([b"FAIL: test_small_amount (tests.test_parse.ParseYuanTest.test_small_amount)",
                                     b"AssertionError: 28 != 29", b"FAILED (failures=1)"])
    debug = [b"debug roundtrip %d %d.%02d %d" % (i, i // 100, i % 100, i) for i in range(4000)]
    long, long_console = pipeline([b"FAILED (failures=1)"] + debug)
    assert 130_000 < len(long) < 140_000 and long.count(b"\x1b[8mha:////4") == 22
    gl.mr(P, 7)
    gl.check(P, 7, "unit-tests", "failed", jk.build("/job/orders/14", short))
    gl.mr(P, 8)
    gl.check(P, 8, "unit-tests", "failed", jk.build("/job/orders/15", long))
    w.round()
    for iid, n, console in ((7, 14, short_console), (8, 15, long_console)):
        msg = w.sessions.messages("patrol-42-%d" % iid)[0]
        head = "   - unit-tests（Jenkins 构建 %s/job/orders/%d/）：" % (JENKINS_BASE, n)
        assert quoted(msg, head) == ["     > " + line for line in console.decode().split("\n")[:-1][-80:]]
    assert '     > Started by upstream project "orders-ci" build number 14\n' in w.sessions.messages("patrol-42-7")[0]
    end = w.sessions.messages("patrol-42-8")[0]
    assert "     > [Pipeline] // gitlabCommitStatus\n" in end
    assert "     > ERROR: script returned exit code 1\n     > Finished: FAILURE\n" in end


def reader(tmp_path, jk):
    """bridge.json's jenkins: the read-only user at JENKINS_BASE, its token in a file only its owner reads."""
    token = tmp_path / "jenkins-token"
    token.write_text(jk.token + "\n")
    token.chmod(0o600)
    return mod("team.jenkins").Jenkins.of({"base": JENKINS_BASE, "user": jk.user, "token_file": str(token)}, jk)


def window_in_a_note():
    """A log of two sh steps whose last 64 KiB (TAIL_BYTES) start 100 bytes into the note of the second one's
    [Pipeline] sh line: (the log, the consoleText of the lines after it)."""
    tail = mod("team.jenkins").TAIL_BYTES

    def rows(pad):
        return pipeline_lines([b"build line %04d " % i + b"x" * 980 for i in range(30)] + [NOTE + b"[Pipeline] sh"]
                              + [b"test line %04d " % i + b"x" * 980 for i in range(60)] + [b"y" * pad])

    first = rows(0)
    cut = max(i for i, (_, plain) in enumerate(first) if plain == b"[Pipeline] sh\n")
    pad = tail - len(first[cut][0]) + 100 - sum(len(k) for k, _ in first[cut + 1:])
    got = rows(pad)
    raw = b"".join(k for k, _ in got)
    assert len(raw) - tail == sum(len(k) for k, _ in got[:cut]) + 100 and got[cut][0].index(b"\x1b[0m") > 100
    return raw, b"".join(p for _, p in got[cut + 1:])


def still_writing_a_note():
    """A log Jenkins is still writing, 150 bytes into a note at its end: (the log, the consoleText of its whole
    lines)."""
    rows = pipeline_lines([b"FAILED (failures=1)"])[:-3]
    return b"".join(k for k, _ in rows) + note(99)[:150], b"".join(p for _, p in rows)


def just_under_the_window():
    """A plain log just under 64 KiB whose 2200 line ends take what progressiveText sends over it: (the log, the
    log)."""
    raw = b"".join(b"[INFO] step %04d of it......\n" % i for i in range(2200))
    assert len(raw) < mod("team.jenkins").TAIL_BYTES < len(raw) + 2200
    return raw, raw


@pytest.mark.parametrize("case", [window_in_a_note, still_writing_a_note, just_under_the_window])
def test_a_build_s_tail_reads_as_its_console_text_from_the_start_of_a_line_to_the_end_of_one(bk, tmp_path, case):
    """What Jenkins.tail gives is consoleText's end: no console note, LF line ends, and a line the window cuts (a note
    in it too) left out at either end, whatever more bytes than the log progressiveText sends; and whether it is the
    log's end: not while Jenkins says more is coming (X-More-Data), as it does for a log it still writes."""
    jk = FakeJenkins()
    raw, console = case()
    jk.build("/job/orders/2", raw)
    if case is still_writing_a_note:
        jk.running.add("/job/orders/2")
    assert reader(tmp_path, jk).tail("/job/orders/2", 10.0) == (console, case is not still_writing_a_note)


def test_a_build_still_running_when_its_check_failed_is_read_again_once_it_ends(bk, tmp_path):
    """Jenkins' GitLab plugin posts the check failed before the build ends: what the round reads then is not its log
    (X-More-Data). The MR waits, saying why, its build read again the next round; once it ended the session gets the
    log's end, the exit code and the result in it, and the build is not read again."""
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    rows = pipeline_lines([b"FAILED (failures=1)"])
    url = jk.build("/job/orders/15", b"".join(k for k, _ in rows[:-6]))   # its blocks not all closed yet
    jk.running.add("/job/orders/15")
    gl.mr(P, 7)
    gl.check(P, 7, "unit-tests", "failed", url)
    w.round()
    assert w.sessions.sent == [] and w.item(7)["state"] == "waiting"
    assert w.item(7)["note"] == "Jenkins 的构建还没跑完：跑完再派（最多等 10 分钟）"
    jk.build("/job/orders/15", b"".join(k for k, _ in rows))
    jk.running.discard("/job/orders/15")
    w.later()
    w.round()
    msg, = w.sessions.messages("patrol-42-7")
    assert ("     > [Pipeline] End of Pipeline\n     > ERROR: script returned exit code 1\n"
            "     > Finished: FAILURE\n") in msg
    assert "没跑完" not in msg
    for _ in range(3):
        w.later()
        w.round()
    assert jk.reads() == ["/job/orders/15/logText/progressiveText?start=0"] * 2


def test_a_build_that_does_not_end_goes_out_after_ten_minutes_with_what_was_read_and_says_so(bk, tmp_path):
    """A build Jenkins never ends does not hold its MR for good: read every round, ten minutes from the first read that
    found it running, then the last read goes out, the session told the build had not ended. It is not read again."""
    wait = 10 * 60
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    rows = pipeline_lines([b"waiting for orders-db to accept connections"] * 3)
    url = jk.build("/job/orders/15", b"".join(k for k, _ in rows[:-11]))
    jk.running.add("/job/orders/15")
    gl.mr(P, 7)
    gl.check(P, 7, "unit-tests", "failed", url)
    start = w.clock.t
    while w.clock.t - start < wait:
        w.round()
        assert w.sessions.sent == [] and w.item(7)["state"] == "waiting"
        w.later()
    assert len(jk.calls) == wait // 60
    w.round()
    msg, = w.sessions.messages("patrol-42-7")
    block = quoted(msg, "   - unit-tests（Jenkins 构建 %s/job/orders/15/）：" % JENKINS_BASE)
    assert block == ["     （构建等了 10 分钟还没跑完，下面是那时读到的）"] + [
        "     > " + plain.decode().rstrip("\n") for _, plain in rows[:-11]]
    assert block[-1] == "     > waiting for orders-db to accept connections"
    for _ in range(3):
        w.later()
        w.round()
    assert len(jk.calls) == wait // 60 + 1


def test_a_jenkins_the_runner_cannot_reach_costs_one_try_a_round_and_three_in_all_ten_minutes_apart(bk, tmp_path):
    """Every request to it times out: the round's first one says so for every build of the round, its MR goes out at
    once with why, and a build is asked for again RETRY_S later, MAX_TRIES times in all."""
    jenkins = mod("team.jenkins")
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    jk.down = True
    gl.mr(P, 7)
    for n in (1, 2, 3):
        gl.check(P, 7, "check-%d" % n, "failed", jk.build("/job/orders/%d" % n, b"FAILED\n"))
    start = w.clock.t
    w.round()
    assert len(jk.calls) == 1 and w.clock.t - start == jenkins.TIMEOUT_S
    assert w.sessions.messages("patrol-42-7")[0].count("     没读到日志：连不上 Jenkins：") == 3
    calls = []
    for _ in range(25):                 # worked, so read every round, for 25 minutes
        w.later()
        w.round()
        calls.append(len(jk.calls))
    assert calls == [1] * 9 + [2] * 10 + [3] * 6
    w.later(jenkins.RETRY_S)
    w.round()
    assert len(jk.calls) == 3
    assert all(c.why.endswith("（试了 3 次都没读到，不再读）") for c in w.patrol.led.get(P, 7).read.checks)


def test_jenkins_reads_stop_when_the_round_s_time_for_them_is_up_and_their_mr_waits_for_the_next_round(bk, tmp_path):
    """A slow Jenkins (6 s a request): a round's reads take ROUND_S at most, so the next round's hand-over steps wait no
    longer; the builds left are read the next round, and their MR goes out once it has every log."""
    jenkins = mod("team.jenkins")
    w = World(tmp_path)
    w.with_jenkins()
    gl, jk = w.gl, w.jk
    jk.wait = 6.0
    gl.mr(P, 7)
    for n in range(1, 6):
        gl.check(P, 7, "check-%d" % n, "failed", jk.build("/job/orders/%d" % n, b"FAILED test_%d\n" % n))
    start = w.clock.t
    w.round()
    assert len(jk.calls) == 4
    assert w.clock.t - start == jenkins.ROUND_S
    assert w.sessions.sent == [] and w.item(7)["state"] == "waiting"
    assert w.item(7)["note"] == "Jenkins 的日志这一轮没读完：下一轮读完再派"
    w.later()
    w.round()
    assert len(jk.calls) == 6 and len(w.sessions.sent) == 1
    assert all("     > FAILED test_%d\n" % n in w.sessions.messages()[0] for n in range(1, 6))
    assert not [s for s in w.said if "no log" in s]


def test_a_failed_trigger_job_is_the_pipeline_s_own_not_a_check_external_ci_reported(bk, tmp_path):
    """GitLab lists a pipeline's trigger jobs (bridges) among its commit statuses, not among its jobs: one that failed is
    not named as a check external CI reported, with a Jenkins reason, nor asked of Jenkins, nor in the fingerprint."""
    w = World(tmp_path)
    w.with_jenkins()
    gl = w.gl
    gl.mr(P, 7)
    pid = gl.fail(P, 7, {"test": b"FAILED test_discount\n"})
    gl.trigger(P, pid, "deploy-review", reason="downstream_pipeline_creation_failed")
    w.round()
    msg = w.sessions.messages("patrol-42-7")[0]
    assert "   - test：\n     > FAILED test_discount\n" in msg
    assert "deploy-review（" not in msg and "外部 CI 报给 GitLab 的" not in msg and "不是 Jenkins 构建" not in msg
    assert w.jk.calls == [] and w.patrol.led.get(P, 7).status_ids == ()


def test_a_merged_results_pipeline_is_the_mr_s_own_and_one_of_an_older_head_is_not(bk, tmp_path):
    """GitLab runs an MR's pipeline on the merge of its head into the target branch (merged results): its sha is that
    merge commit's, not the head's. It is the MR's head pipeline while a parent of that commit is the head; one an older
    head left behind (an older GitLab keeps it as head_pipeline after a push) is not."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    pid = gl.merged_result(P, 7, {"test": b"FAILED test_discount\n"})
    w.round()
    assert w.item(7)["problems"] == [{"kind": "ci_failed", "text": "流水线 #%d 没过：test" % pid}]
    assert "   - test：\n     > FAILED test_discount\n" in w.sessions.messages("patrol-42-7")[0]
    w.sessions.end()
    gl.push(P, 7, SHA[2], by="alice@example.com")
    gl.mrs[(P, 7)]["_pipeline"] = gl.pipes[pid]
    w.later()
    w.round()
    assert (w.item(7)["state"], w.item(7)["problems"]) == ("ok", []) and len(w.sessions.sent) == 1


def test_a_child_pipeline_s_failed_jobs_go_out_through_the_trigger_job_that_started_it(bk, tmp_path):
    """A pipeline fails because one its trigger job started failed (strategy: depend), with no failed job of its own:
    the session gets that pipeline's failed jobs and their logs, each named by the trigger job, a child's child too;
    a trigger job whose pipeline GitLab never made says why. None of them is a check external CI reported."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    pid = gl.pipeline(P, 7, "success")
    inner = gl.child(P, SHA[1], {"e2e": b"FAILED test_checkout\n"})
    kid = gl.child(P, SHA[1], {"unit": b"FAILED test_child\n"})
    gl.trigger(P, kid, "browser", child=inner)
    gl.trigger(P, pid, "frontend", child=kid)
    gl.trigger(P, pid, "deploy-review", reason="downstream_pipeline_creation_failed")
    w.round()
    assert w.item(7)["problems"] == [{"kind": "ci_failed", "text": "流水线 #%d 没过：frontend › unit、frontend › browser › "
                                                                     "e2e、deploy-review" % pid}]
    msg = w.sessions.messages("patrol-42-7")[0]
    assert "   - frontend › unit：\n     > FAILED test_child\n" in msg
    assert "   - frontend › browser › e2e：\n     > FAILED test_checkout\n" in msg
    assert "   - deploy-review：\n     > （它要触发的流水线没建成：downstream_pipeline_creation_failed）\n" in msg
    assert "外部 CI" not in msg and w.patrol.led.get(P, 7).status_ids == ()


def test_a_ledger_from_before_failed_checks_counted_keeps_the_mrs_it_worked_worked(bk, tmp_path):
    """A row as a runner without them wrote it (no status_ids, its fingerprints of the head, the pipeline, the conflict
    and the notes alone, the one its turn ended on kept as done_fp, as before 0.5.5) reads as before: the failed
    pipeline its turn ended on is not sent again."""
    w = World(tmp_path)
    w.gl.mr(P, 7)
    pid = w.gl.fail(P, 7)
    w.round()
    w.sessions.end()
    w.later()
    w.round()
    then = hashlib.sha256(json.dumps([SHA[1], pid, False, []]).encode()).hexdigest()[:20]
    db = w.patrol.led.db
    doc = json.loads(db.execute("SELECT doc FROM mrs WHERE project=? AND iid=7", (P,)).fetchone()[0])
    doc.pop("status_ids")
    doc["read"].pop("checks")
    for key in ("done_head", "done"):           # a row from before 0.5.5 says what its turn ended on as done_fp
        doc.pop(key, None)
    doc.update(fingerprint=then, done_fp=then)
    db.execute("UPDATE mrs SET doc=? WHERE project=? AND iid=7", (json.dumps(doc), P))
    w.later(mod("team.patrol").RECHECK_S)
    w.round()
    assert len(w.sessions.sent) == 1 and w.item(7)["state"] == "done"


def test_a_person_s_plain_comment_since_patrol_started_is_worked_and_answered_on_the_mr_quoting_it(bk, tmp_path):
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 5)
    gl.thread(P, 5, "dave", "顺便把 README 也改一下", resolvable=False)       # written before patrol started
    w.later()
    w.round()
    assert w.item(5)["state"] == "ok" and w.slots() == []
    w.later()
    note = gl.thread(P, 5, "erin", "折扣上限改成 50\n别忘了测试", resolvable=False)["notes"][0]
    gl.thread(P, 5, "project_42_bot_1a2b3c", "Code Quality: 2 new issues", resolvable=False)
    gl.thread(P, 5, "brain-bridge", "MR 巡检：上一轮的总结", resolvable=False)           # the runner's own
    gl.system(P, 5, "added 1 commit")
    w.later()
    w.round()
    assert w.item(5)["problems"] == [{"kind": "comment", "text": "@erin 的评论"}]
    msg = w.sessions.messages("patrol-42-5")[0]
    assert "MR 上的评论（不在讨论里）：评论 #%d，@erin：\n   > 折扣上限改成 50\n   > 别忘了测试" % note["id"] in msg
    assert "README" not in msg and "Code Quality" not in msg and "上一轮的总结" not in msg
    helper = ("/usr/bin/python3 -I /app/backend/core/team/patrol_reply.py --config /setup/bridge.json --brainkit /kit "
              "--project grp/repo --mr 5")
    assert "先引用它的原话（每行前面加 > ）" in msg and helper + " --note '回复的话'" in msg and "--discussion" not in msg
    w.sessions.end()
    w.later()
    w.round()
    assert w.item(5)["state"] == "ok" and len(w.sessions.sent) == 1          # answered by the turn that ended
    w.kits[P] = kit(work_sources=["mr-handoff"])                             # patrol off for a while ...
    w.later()
    w.round()
    gl.thread(P, 5, "erin", "巡检关着的时候写的", resolvable=False)
    w.kits[P] = kit()                                                        # ... and on again: it starts from now
    w.later()
    w.round()
    assert w.item(5)["state"] == "ok" and len(w.sessions.sent) == 1


def test_an_mr_from_or_into_the_state_relays_branch_is_never_patrolled(bk, tmp_path):
    """brain/status holds only the team server's state (状态中转): an MR from it, or into it, is left alone and said."""
    w = World(tmp_path)
    w.gl.mr(P, 10, source="brain/status", **conflicted())
    w.gl.mr(P, 11, target_branch="brain/status", **conflicted())
    w.round()
    assert {i["iid"]: (i["state"], i["skip_code"]) for i in w.patrol.view()["items"]} == {
        10: ("skipped", "status"), 11: ("skipped", "status")}
    assert w.item(10)["skip"] == "团队状态的分支 brain/status 的 MR：它只放团队服务器写的状态，不巡检，也别合并"
    assert w.slots() == []


def test_mrs_the_hand_over_owns_opted_out_from_a_fork_or_with_only_bots_comments_are_skipped(bk, tmp_path):
    w = World(tmp_path)
    gl = w.gl
    for iid, labels in ((1, ["ai-handoff"]), (2, ["ai-running", "bug"]), (3, ["ai-handoff-rejected"]), (4, ["ai-skip"])):
        gl.mr(P, iid, labels=labels, **conflicted())
    gl.mr(P, 5, source_project_id=99, **conflicted())
    gl.mr(P, 6)
    gl.thread(P, 6, "project_42_bot_1a2b3c", "Code Quality: 2 new issues")       # a project token's bot
    gl.mr(P, 7)
    gl.thread(P, 7, "brain-bot", "运行机的提交")                                    # kit.json bot_users
    gl.mr(P, 8, **conflicted())
    w.round()
    codes = {i["iid"]: i["skip_code"] for i in w.patrol.view()["items"]}
    assert codes == {1: "handoff", 2: "handoff", 3: "handoff", 4: "opt_out", 5: "fork", 6: "bots_only", 7: "bots_only",
                     8: ""}
    assert w.slots() == ["patrol-42-8"]
    assert w.item(2)["skip"] == "交接给运行机的 MR（标签 ai-running）：交接那边管它"
    assert w.item(5)["skip"] == "从 fork 来的 MR：运行机推不了它的分支"
    assert w.item(6)["skip"] == "只有机器人写的评论，没有要修的" and w.item(6)["state"] == "skipped"
    # a label the project names its own way counts as that label
    w.kits[P] = kit(handoff_label="hand-me")
    gl.mr(P, 9, labels=["hand-me"], **conflicted())
    w.later()
    w.round()
    assert w.item(9)["skip_code"] == "handoff" and w.item(1)["skip_code"] == ""


def test_two_sessions_at_a_time_and_the_mrs_open_before_patrol_started_go_first(bk, tmp_path):
    w = World(tmp_path)
    gl = w.gl
    start = w.clock.t
    for iid, created, updated in ((1, 1, 30), (2, 2, 10), (3, 3, 20)):
        gl.mr(P, iid, created=start - 1000 + created, **conflicted())
        gl.mrs[(P, iid)]["updated_at"] = w.clock.iso(start - 1000 + updated)
    w.round()                           # patrol starts in grp/repo now: !1, !2 and !3 were open before
    assert w.slots() == ["patrol-42-2", "patrol-42-3"] and w.item(1)["state"] == "queued"
    view = w.patrol.view()
    assert (view["cap"], view["working"]) == (2, 2)
    w.later(1)
    gl.mr(P, 4, **conflicted())         # opened after patrol started
    w.later(1)
    gl.thread(P, 1, "carol", "顺便看一下")  # !1 changes after !4 was opened, and still goes first
    w.sessions.turns[w.sessions.sent[0][2]] = "ended"   # !2's turn ends: one session free
    w.later(1)
    w.round()
    assert w.slots() == ["patrol-42-2", "patrol-42-3", "patrol-42-1"] and w.item(4)["state"] == "queued"
    w.sessions.end()
    w.later()
    w.round()
    assert w.slots()[-1] == "patrol-42-4"


def test_a_session_at_work_in_a_project_this_round_could_not_read_still_counts(bk, tmp_path):
    w = World(tmp_path, kits={P: kit(), "grp/other": kit()})
    gl = w.gl
    gl.mr(P, 1, created=w.clock.t - 300, **conflicted())
    gl.mr("grp/other", 1, created=w.clock.t - 200, **conflicted())
    gl.mr("grp/other", 2, created=w.clock.t - 100, **conflicted())
    w.round()
    assert w.slots() == ["patrol-42-1", "patrol-43-1"]

    def kit_of(project):
        if project == P:
            raise bk.brain_error()("grp/repo: .brain/kit.json not found on main")
        return w.kits[project]
    w.patrol.kit = kit_of
    w.later()
    with pytest.raises(bk.brain_error(), match="grp/repo"):
        w.round()
    assert w.slots() == ["patrol-42-1", "patrol-43-1"] and w.item(2, "grp/other")["state"] == "queued"
    assert w.patrol.view()["projects"][0] == {"project": P, "on": False, "error": "grp/repo: .brain/kit.json not found on main"}


def test_the_fix_sessions_of_staging_errors_count_toward_the_two(bk, tmp_path):
    """测试环境报错 counts MR 巡检's sessions toward CAP, and MR 巡检 counts its fixes: two brain-patrol sessions at most."""
    w = World(tmp_path)
    w.staging[0] = 1
    for iid in (1, 2):
        w.gl.mr(P, iid, created=w.clock.t - 300 + iid, **conflicted())
    w.round()
    assert w.slots() == ["patrol-42-1"] and w.item(2)["state"] == "queued"
    w.staging[0] = 0                    # its fix ended
    w.later()
    w.round()
    assert w.slots() == ["patrol-42-1", "patrol-42-2"]


def test_two_failed_attempts_on_one_fingerprint_block_the_mr_with_one_comment(bk, tmp_path):
    patrol = mod("team.patrol")
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 9)
    pid = gl.fail(P, 9)
    w.round()
    w.sessions.end("failed")            # KiroCrew ended the turn with an error
    w.later()
    w.round()                           # a failed attempt: the second one goes out
    assert len(w.sessions.sent) == 2 and w.item(9)["attempts"] == 2
    w.later(patrol.TIMEOUT_S)
    w.round()                           # the second one times out: stopped, blocked, said once on the MR
    assert w.sessions.stopped == ["patrol-42-9"] and w.item(9)["state"] == "blocked"
    told = gl.posted("notes")
    assert len(told) == 1 and told[0]["body"].startswith("MR 巡检：运行机试了 2 次，没修好这些：\n- 流水线 #%d 没过：test" % pid)
    for _ in range(3):
        w.later()
        w.round()
    assert len(gl.posted("notes")) == 1 and len(w.sessions.sent) == 2 and w.item(9)["state"] == "blocked"
    gl.thread(P, 9, "carol", "我改了 CI 配置，再试试")   # its fingerprint changes: patrol looks again
    w.later()
    w.round()
    assert len(w.sessions.sent) == 3 and w.item(9)["state"] == "working" and w.item(9)["attempts"] == 1


@pytest.mark.parametrize("ranges", [True, False])     # False: a GitLab that lists no old..new range (its head's committer)
def test_three_dispatches_with_no_person_pushing_or_commenting_in_between_pause_the_mr_with_one_comment(
        bk, tmp_path, ranges):
    w = World(tmp_path)
    gl, heads = w.gl, iter(SHA[n] for n in range(2, 16))
    gl.ranges = ranges

    def the_runner_pushed_and_it_failed_again():
        w.sessions.end()
        gl.push(P, 7, next(heads))
        gl.fail(P, 7)
        w.later()
        w.round()
    gl.mr(P, 7)
    gl.fail(P, 7)
    w.round()
    for _ in range(5):
        the_runner_pushed_and_it_failed_again()
    assert len(w.sessions.sent) == 3 and w.item(7)["state"] == "blocked"
    assert w.item(7)["note"] == "连着派了 3 次，中间没有人推送或评论，在 MR 上说了：等人推送或评论"
    told = gl.posted("notes")
    assert len(told) == 1 and told[0]["body"].startswith("MR 巡检：运行机连着修了 3 次，中间没有人推送或评论，这些还没修好：")
    gl.thread(P, 7, "carol", "CI 的配置我改了，再试试")      # a person's comment: patrol looks again
    w.later()
    w.round()
    assert len(w.sessions.sent) == 4 and w.item(7)["state"] == "working"
    for _ in range(3):
        the_runner_pushed_and_it_failed_again()
    assert len(w.sessions.sent) == 6 and len(gl.posted("notes")) == 2
    gl.push(P, 7, next(heads), by="alice@example.com")    # a person's push: patrol looks again
    gl.fail(P, 7)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 7 and (w.item(7)["state"], w.item(7)["attempts"]) == ("working", 1)
    # GitLab is asked about each of the 9 pushes once, and about the person's once more: which of its commits main has
    assert gl.asked(r"/repository/commits$") == (10 if ranges else 9)
    assert gl.asked(r"/repository/commits/[0-9a-f]+$") == (0 if ranges else 9)


def test_the_runner_merging_the_target_branch_in_is_no_person_s_push(bk, tmp_path):
    """The MR's failed pipeline went out; then main moved, so the MR conflicts, and the session merges main in and
    pushes. The new head brings main's commits, people's, and the runner's merge commit: that is the runner's push,
    not a person's, so after the third dispatch in a row the MR is paused with one comment, not sent a fourth time
    (orders!4 on the test server: its streak started again at that merge). A person's push still counts."""
    w = World(tmp_path)
    gl = w.gl

    def main_moves(sha):
        gl.main(P, sha)
        gl.mrs[(P, 4)].update(**conflicted())
        gl.touch(P, 4)
        w.later()
        w.round()
    gl.mr(P, 4)
    gl.fail(P, 4)
    w.round()
    w.sessions.end()
    main_moves(SHA[8])
    assert len(w.sessions.sent) == 2 and "和目标分支 main 有冲突" in w.sessions.messages()[-1]
    gl.push(P, 4, SHA[3], merged=SHA[8], has_conflicts=False, detailed_merge_status="mergeable")
    gl.fail(P, 4)
    w.sessions.end()
    w.later()
    w.round()
    assert len(w.sessions.sent) == 3 and SHA[3][:12] in w.sessions.messages()[-1]
    w.sessions.end()
    main_moves(SHA[9])
    assert len(w.sessions.sent) == 3 and w.item(4)["state"] == "blocked", w.item(4)
    told = gl.posted("notes")
    assert len(told) == 1 and told[0]["body"].startswith("MR 巡检：运行机连着修了 3 次，中间没有人推送或评论，这些还没修好：")
    gl.push(P, 4, SHA[5], by="alice@example.com")
    gl.fail(P, 4)
    w.later()
    w.round()
    assert len(w.sessions.sent) == 4 and w.item(4)["state"] == "working"


def test_a_person_s_follow_up_written_while_the_session_works_is_a_new_problem_after_its_reply(bk, tmp_path):
    """The session was told one note of the thread; the person adds to the thread while it works, and its reply lands
    after that: the reply answers the note it was told, not the one it never saw, which goes out next, and stays open
    when that next turn fails without a reply."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    d = gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 12)
    w.round()
    w.later()
    more = gl.reply(P, 7, d, "carol", "还有：超出范围要报错，不要截断")
    gl.reply(P, 7, d, "brain-bridge", "改了：加了 0-100 的检查")
    w.sessions.end()
    w.round()
    assert w.item(7)["problems"] == [{"kind": "review", "text": "@carol 的评审意见（src/discount.py:12）"}]
    assert len(w.sessions.sent) == 2
    again = w.sessions.messages()[-1]
    assert "评论 #%d" % more["id"] in again and "超出范围要报错" in again and "百分比要限制在 0-100" not in again
    w.sessions.end("failed")
    w.later()
    w.round()
    assert len(w.sessions.sent) == 3 and "评论 #%d" % more["id"] in w.sessions.messages()[-1]
    gl.reply(P, 7, d, "brain-bridge", "也改了：超出就报错")
    w.sessions.end()
    w.later()
    w.round()
    assert w.item(7)["state"] == "ok" and len(w.sessions.sent) == 3


def test_the_page_counts_every_session_patrol_holds_and_says_what_holds_one_no_row_shows_at_work(bk, tmp_path):
    """A turn the MR head moved past holds its session until it ends while its MR is no longer shown at work: the page
    counts every session patrol holds, as sources.at_work does, and says what holds the one no row shows."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 3)
    gl.fail(P, 3)
    gl.mr(P, 4, **conflicted())
    w.round()
    gl.push(P, 3, SHA[2])
    gl.pipeline(P, 3, "running")
    gl.mr(P, 5, **conflicted())
    w.later()
    w.round()
    view = w.patrol.view()
    assert [(i["iid"], i["state"]) for i in view["items"]] == [(3, "ok"), (4, "working"), (5, "queued")]
    assert view["working"] == w.patrol.busy() == 2
    assert view["hidden"] == [{"project": P, "text": "grp/repo!3 的会话还在做上一轮（MR 有了新的提交）"}]


def test_a_turn_the_head_moved_past_holds_the_session_until_it_ends(bk, tmp_path):
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 3)
    gl.fail(P, 3)
    w.round()
    gl.push(P, 3, SHA[2])               # the session pushed a fix, and is still writing its replies
    gl.fail(P, 3)                       # ... and the new head's pipeline fails as well
    w.later()
    w.round()
    assert len(w.sessions.sent) == 1 and w.item(3)["state"] == "waiting"
    w.sessions.end()
    w.later()
    w.round()
    assert len(w.sessions.sent) == 2 and w.item(3)["state"] == "working"


def test_an_mr_both_handed_off_and_patrolled_is_claimed_once(bk, tmp_path):
    """The hand-over's run holds its requirement (brain-kit's bridge ledger): patrol does not dispatch that MR, even
    with the labels gone, and stops its own turn on an MR the hand-over takes over. Other MRs go on as before."""
    sources = mod("team.sources")
    led = bk.m("bridge.ledger").Ledger(str(tmp_path / "bridge-ledger.sqlite"))
    rt = SimpleNamespace(host=SimpleNamespace(ledger=led), patrol=None,
                         mr_url=lambda p, i: "%s/%s/-/merge_requests/%d" % (BASE, p, int(i)))
    w = World(tmp_path, claims=lambda: sources.held(rt))
    rt.patrol = w.patrol
    gl = w.gl
    assert led.reserve_run("PAY-1", P, 5, SHA[1]) == "RUN-PAY-1-1"
    gl.mr(P, 5, source="ai/alice/PAY-1-refund", **conflicted())
    gl.mr(P, 6, source="fix/typo", **conflicted())
    gl.mr(P, 8, source="ai/alice/PAY-2-pay", **conflicted())
    w.round()
    assert w.item(5)["skip_code"] == "claimed" and "PAY-1" in w.item(5)["skip"]
    assert w.slots() == ["patrol-42-6", "patrol-42-8"]
    assert sorted((c["kind"], c["key"]) for c in sources.held(rt)) == [
        ("mr-handoff", "PAY-1"), ("mr-patrol", "PAY-2"), ("mr-patrol", "mr-patrol:grp/repo:mr!6")]
    assert led.reserve_run("PAY-2", P, 8, SHA[1]) == "RUN-PAY-2-1"      # PAY-2 is handed over meanwhile
    w.later()
    w.round()
    assert w.sessions.stopped == ["patrol-42-8"] and w.item(8)["skip_code"] == "claimed"
    assert [(c["kind"], c["key"]) for c in sources.held(rt) if c["key"] == "PAY-2"] == [("mr-handoff", "PAY-2")]
    led.close()


def test_a_claim_is_keyed_by_its_requirement_else_by_kind_project_and_ref(bk):
    sources = mod("team.sources")
    mr = {"iid": 12, "source_branch": "ai/alice/OMS-5-discount", "web_url": BASE + "/grp/repo/-/merge_requests/12",
          "author": {"username": "alice"}, "title": "折扣"}
    assert sources.mr_claim("mr-patrol", P, mr, "ai") == {
        "key": "OMS-5", "kind": "mr-patrol", "project": P, "link": BASE + "/grp/repo/-/merge_requests/12",
        "asked_by": "alice", "role": "", "title": "折扣", "ref": {"type": "mr", "iid": 12}}
    assert sources.mr_claim("mr-patrol", P, dict(mr, source_branch="fix/discount"), "ai")["key"] == "mr-patrol:grp/repo:mr!12"
    assert sources.mr_claim("mr-patrol", P, mr, "feat")["key"] == "mr-patrol:grp/repo:mr!12"   # not this kit's prefix
    assert sources.claim_key("issue-x", P, {"type": "issue", "iid": 3}) == "issue-x:grp/repo:issue!3"


def test_work_sources_switch_kinds_on_per_project_and_default_to_the_hand_over(bk, tmp_path):
    sources = mod("team.sources")
    assert sources.enabled({}) == ("mr-handoff",)
    assert sources.enabled({"work_sources": "mr-patrol"}) == ("mr-handoff",)                     # not a list
    assert sources.enabled({"work_sources": ["mr-handoff", "mr-patrol"]}) == ("mr-handoff", "mr-patrol")
    assert sources.enabled({"work_sources": ["mr-patrol", "issues-later", "mr-patrol"]}) == ("mr-patrol",)
    assert sources.enabled({"work_sources": []}) == ()
    cfg = bk.m("config")
    off = World(tmp_path / "off", kits={P: cfg.merged(cfg.DEFAULTS, {})})
    off.gl.mr(P, 1, **conflicted())
    assert off.round() == 0 and off.slots() == [] and not (tmp_path / "off" / "patrol.sqlite").exists()
    w = World(tmp_path, kits={P: cfg.merged(cfg.DEFAULTS, {}), "grp/other": kit()})
    w.gl.mr(P, 1, **conflicted())
    w.gl.mr("grp/other", 1, **conflicted())
    w.round()
    assert w.patrol.view()["projects"] == [{"project": P, "on": False, "error": ""},
                                           {"project": "grp/other", "on": True, "error": ""}]
    assert w.gl.asked(r"^/api/v4/projects/grp/repo/merge_requests") == 0 and w.slots() == ["patrol-43-1"]


REFUSED = (b"FAIL feature: the bot made commits in this MR (%s), but bot accounts push only ai/<owner>/<JIRA>-<slug> "
           b"run branches: the bot's work comes through its run branch's MR\nbrain-ci push-rules: feature by %s FAILED\n")


def test_brain_push_rules_refusing_the_runner_s_commit_leaves_the_mr_to_people_with_one_comment(bk, tmp_path):
    """The runner pushed its fix to the MR's own branch, and brain-kit's push rules, on in the project, fail the new
    pipeline for that commit: any commit it pushes there is refused, so the MR is never dispatched again, its MR is told
    once what a person does, and the page says so. One push-rules fails for a person's commit is CI as any other."""
    patrol = mod("team.patrol")
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    gl.fail(P, 7, {"test": b"FAILED test_discount\n"})
    w.round()
    gl.push(P, 7, SHA[2])
    gl.fail(P, 7, {"brain-push-rules": REFUSED % (SHA[2][:12].encode(), b"brain-bot")})
    w.sessions.end()
    for _ in range(3):
        w.later()
        w.round()
    assert len(w.sessions.sent) == 1 and w.item(7)["state"] == "rules"
    assert w.item(7)["note"] == ("brain-push-rules 不让运行机在这个分支上提交（运行机的提交 %s）：在 MR 上说了怎么改成人的；"
                                 "运行机不再修这个 MR" % SHA[2][:8])
    told, = gl.posted("notes")
    assert patrol.STOPPED_RE.match(told["body"]) and "运行机的提交 %s 让这个 MR 的流水线过不了" % SHA[2][:8] in told["body"]
    assert "git rebase %s~1 --exec 'git commit --amend --no-edit --reset-author'\n" % SHA[2][:8] in told["body"]
    assert "> feature: the bot made commits in this MR (%s)" % SHA[2][:12] in told["body"]
    gl.thread(P, 7, "carol", "再试一下")
    w.later()
    w.round()
    assert len(w.sessions.sent) == 1 and len(gl.posted("notes")) == 1 and w.patrol.held()[0]["key"].endswith("mr!7")
    gl.mr(P, 8)
    gl.push(P, 8, SHA[3], by="alice@example.com")
    gl.fail(P, 8, {"brain-push-rules": REFUSED % (SHA[3][:12].encode(), b"alice")})
    w.later()
    w.round()
    assert w.item(8)["state"] == "working" and "brain-ci push-rules: feature by alice FAILED" in w.sessions.messages()[-1]


def test_a_merged_mr_is_forgotten_its_turn_stopped_and_its_clone_deleted(bk, tmp_path):
    w = World(tmp_path)
    w.gl.mr(P, 4, **conflicted())
    w.round()
    w.gl.close(P, 4)
    w.later()
    w.round()
    assert w.sessions.stopped == ["patrol-42-4"] and w.ws.dropped == [(42, 4)]
    assert w.patrol.view()["items"] == [] and w.patrol.held() == [] and w.patrol.led.rows() == []


def test_a_project_bridge_json_now_spells_with_other_case_keeps_its_rows_and_sessions(bk, tmp_path):
    """bridge.json's project, written again with GitLab's own path in another case (一键建运行机 run again): the same
    project, so its rows move to that spelling: its turn at work is not stopped, its clone not deleted, and nothing is
    dispatched twice."""
    w = World(tmp_path)
    w.gl.mr(P, 1, **conflicted())
    w.round()
    w.kits = {"Grp/Repo": w.kits[P]}
    w.patrol.close()
    w.patrol = w.start(["Grp/Repo"])
    w.later()
    w.round()
    assert w.sessions.stopped == [] and w.ws.dropped == [] and w.slots() == ["patrol-42-1"]
    assert [(r.project, r.iid, r.state, r.slot) for r in w.patrol.led.rows()] == [("Grp/Repo", 1, "working", "patrol-42-1")]
    assert w.item(1, "Grp/Repo")["state"] == "working"


def test_a_project_patrol_is_no_longer_on_has_its_turns_stopped_its_clones_deleted_and_holds_no_session(bk, tmp_path):
    w = World(tmp_path, kits={P: kit(), "grp/other": kit(), "grp/third": kit()})
    w.gl.mr(P, 1, **conflicted())
    w.gl.mr(P, 2, **conflicted())
    w.round()
    assert w.slots() == ["patrol-42-1", "patrol-42-2"]
    w.kits[P] = kit(work_sources=["mr-handoff"])            # a person switches patrol off in grp/repo
    w.gl.mr("grp/other", 1, **conflicted())
    w.later()
    w.round()
    assert w.sessions.stopped == ["patrol-42-1", "patrol-42-2"] and w.ws.dropped == [(42, 1), (42, 2)]
    assert w.item(1, "grp/other")["state"] == "working" and w.sessions.running() == ["patrol-43-1"]
    w.gl.mr("grp/third", 1, **conflicted())
    w.patrol.close()
    w.patrol = w.start([P, "grp/third"])                      # bridge.json leaves grp/other out: team mode starts again
    w.later()
    w.round()
    assert w.sessions.stopped[2:] == ["patrol-43-1"] and w.ws.dropped[2:] == [(43, 1)]
    assert [(r.project, r.iid, r.state) for r in w.patrol.led.rows()] == [("grp/third", 1, "working")]


def test_a_workspace_git_keeps_failing_on_is_an_attempt_from_its_second_time_and_a_fresh_git_lock_waits(bk, tmp_path):
    """A clone git fails in (a corrupt clone, say) is said and prepared again next round; failing again for the same
    fingerprint, each time is a failed attempt, and MAX_ATTEMPTS block the MR with one comment. A git lock younger than
    LOCK_STALE_S only waits: no attempt however long; once it is gone the MR goes out."""
    patrol = mod("team.patrol")
    w = World(tmp_path)
    gl, ws = w.gl, w.ws
    gl.mr(P, 1, **conflicted())
    gl.mr(P, 2, **conflicted())
    ws.broken[(P, 1)] = "git checkout: fatal: bad object HEAD"
    ws.locked.add((P, 2))
    seen = []
    for _ in range(5):
        w.round()
        w.later()
        seen.append((w.item(1)["state"], w.item(1)["attempts"], w.item(2)["state"], w.item(2)["attempts"]))
    assert seen == [("error", 0, "waiting", 0), ("error", 1, "waiting", 0)] + [("blocked", 2, "waiting", 0)] * 3
    assert ws.tried.count((P, 1)) == 3 and w.slots() == []
    told, = gl.posted("notes")
    assert patrol.STOPPED_RE.match(told["body"]) and "工作区" in told["body"] and "bad object" not in told["body"]
    assert w.item(1)["note"] == "工作区一直没准备好（git checkout: fatal: bad object HEAD），在 MR 上说了：等它有新变化"
    assert w.item(2)["note"] == "工作区里有 git 没做完留下的锁 .git/index.lock（30 秒前留下的）：没有会话在用它，满 5 分钟就删掉"
    ws.locked.clear()
    w.round()
    assert w.slots() == ["patrol-42-2"] and w.item(2)["attempts"] == 1


def test_a_message_kiro_crew_refuses_is_an_attempt_on_the_mr_s_one_session_and_two_block_it_with_one_comment(
        bk, tmp_path):
    w = World(tmp_path)
    for iid in (1, 2, 3):
        w.gl.mr(P, iid, **conflicted())
    w.sessions.refuse = True
    asked = []
    for _ in range(6):
        before = len(w.sessions.asked)
        w.round()
        asked.append(len(w.sessions.asked) - before)
        w.later()
    assert asked == [2, 2, 1, 1, 0, 0]          # two at a time, two refusals each, then the MR is left alone
    assert [o[0] for o in w.sessions.opened] == ["patrol-42-1", "patrol-42-2", "patrol-42-3"]
    assert [(w.item(i)["state"], w.item(i)["attempts"]) for i in (1, 2, 3)] == [("blocked", 2)] * 3
    assert w.item(1)["note"] == "派了 2 次，会话都没收下，在 MR 上说了：等它有新变化"
    told = w.gl.posted("notes")
    assert len(told) == 3 and all(t["body"].startswith("MR 巡检：运行机派了 2 次，它的会话都没收下") for t in told)


def test_a_message_kiro_crew_did_not_answer_stays_at_work_on_its_session_and_is_never_sent_again(bk, tmp_path):
    patrol = mod("team.patrol")
    w = World(tmp_path)
    s = w.sessions
    land = s.patrol_send

    def landed_without_an_answer(name, message, send_id):
        land(name, message, send_id)                            # KiroCrew took it, the turn runs ...
        raise bk.brain_error()("KiroCrew 90 秒内没有回应")        # ... but its answer did not come back in time
    s.patrol_send = landed_without_an_answer
    for iid in range(1, 6):
        w.gl.mr(P, iid, **conflicted())
    w.round()
    assert w.item(1)["state"] == "working" and w.item(1)["note"].startswith("送没送到不清楚")
    for _ in range(3):
        w.later()
        w.round()
    assert s.running() == ["patrol-42-1", "patrol-42-2"] and s.asked == ["patrol-42-1", "patrol-42-2"]
    assert [w.item(i)["state"] for i in range(1, 6)] == ["working", "working", "queued", "queued", "queued"]
    s.patrol_send = land
    s.end()                             # both turns end: those two MRs were worked
    s.lose = True                       # the next two messages never land, and no answer comes back
    w.later()
    w.round()
    assert s.asked[2:] == ["patrol-42-3", "patrol-42-4"] and s.running() == ["patrol-42-3", "patrol-42-4"]
    s.lose = False
    w.later()
    w.round()
    assert len(s.asked) == 4 and w.item(5)["state"] == "queued"
    w.later(patrol.TIMEOUT_S)
    w.round()                           # stopped, a failed attempt, and the next message goes to the same session
    assert s.stopped == ["patrol-42-3", "patrol-42-4"] and s.asked[4:] == ["patrol-42-3", "patrol-42-4"]
    assert (w.item(3)["state"], w.item(3)["attempts"]) == ("working", 2)
    assert [o[0] for o in s.opened] == ["patrol-42-%d" % i for i in (1, 2, 3, 4)]


def test_a_workspace_is_cloned_once_then_reset_to_the_source_branch_on_origin(bk, home, tmp_path):
    patrol = mod("team.patrol")
    origin = tmp_path / "origin" / "grp" / "repo.git"
    origin.parent.mkdir(parents=True)
    sh("git", "init", "-q", "--bare", origin)
    src = git_repo(tmp_path / "src")
    commit_file(src, "a.txt", "1\n", "one")
    sh("git", "-C", src, "push", "-q", origin, "main")
    sh("git", "-C", src, "checkout", "-q", "-b", "feature")
    h1 = commit_file(src, "b.txt", "2\n", "two")
    sh("git", "-C", src, "push", "-q", origin, "feature")
    ws = patrol.Workspaces(str(home / "workplace" / "brain-team" / "patrol"), str(tmp_path / "origin") + "/{project}.git",
                           (("user.name", "brain-bot"), ("user.email", "brain-bot@noreply.invalid")))
    item = patrol.PatrolItem(P, PID, 7, "t", None, "feature", "main", h1, ())
    path = Path(ws.prepare(item))
    git = lambda *a: sh("git", "-C", path, *a).stdout.strip()  # noqa: E731
    assert path == home / "workplace" / "brain-team" / "patrol" / "42-7"
    assert (git("rev-parse", "HEAD"), git("rev-parse", "--abbrev-ref", "HEAD")) == (h1, "feature")
    assert git("config", "user.email") == "brain-bot@noreply.invalid"
    (path / ".git" / "cloned-once").write_text("x")
    (path / "b.txt").write_text("a turn cut off left this\n")
    (path / "junk.txt").write_text("x")
    h2 = commit_file(src, "c.txt", "3\n", "three")
    sh("git", "-C", src, "push", "-q", origin, "feature")
    assert ws.prepare(replace(item, head_sha=h2)) == str(path)
    assert (path / ".git" / "cloned-once").exists() and git("rev-parse", "HEAD") == h2
    assert (path / "b.txt").read_text() == "2\n" and not (path / "junk.txt").exists()
    with pytest.raises(patrol.Moved):
        ws.prepare(item)                # origin moved past the head the round read


def test_a_git_lock_a_turn_stopped_mid_commit_left_is_removed_once_it_is_stale(bk, home, tmp_path):
    """No turn works in the clone when it is prepared: a git lock younger than LOCK_STALE_S may still be a git of a turn
    just stopped, so the clone waits (Locked) and nothing is touched; once older, every lock goes and the clone is reset
    as ever."""
    patrol = mod("team.patrol")
    origin = tmp_path / "origin" / "grp" / "repo.git"
    origin.parent.mkdir(parents=True)
    sh("git", "init", "-q", "--bare", origin)
    src = git_repo(tmp_path / "src")
    commit_file(src, "a.txt", "1\n", "one")
    sh("git", "-C", src, "checkout", "-q", "-b", "feature")
    head = commit_file(src, "b.txt", "2\n", "two")
    sh("git", "-C", src, "push", "-q", origin, "main", "feature")
    ws = patrol.Workspaces(str(home / "workplace" / "brain-team" / "patrol"), str(tmp_path / "origin") + "/{project}.git",
                           (("user.name", "brain-bot"), ("user.email", "brain-bot@noreply.invalid")))
    item = patrol.PatrolItem(P, PID, 7, "t", None, "feature", "main", head, ())
    path = Path(ws.prepare(item))
    (path / "b.txt").write_text("a turn stopped half-way through its commit\n")
    locks = [path / ".git" / "index.lock", path / ".git" / "refs" / "heads" / "feature.lock"]
    for lock in locks:
        lock.write_text("")
    with pytest.raises(patrol.Locked, match="index.lock"):
        ws.prepare(item)
    assert all(lock.exists() for lock in locks) and (path / "b.txt").read_text().startswith("a turn")
    stale = time.time() - patrol.LOCK_STALE_S - 1
    for lock in locks:
        os.utime(lock, (stale, stale))
    assert ws.prepare(item) == str(path)
    assert not any(lock.exists() for lock in locks) and (path / "b.txt").read_text() == "2\n"


def reply_tool():
    spec = importlib.util.spec_from_file_location("patrol_reply_under_test", REPLY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def reply_world(tmp_path):
    gl = PatrolGitLab(Clock())
    gl.project(P, PID)
    gl.mr(P, 7)
    d = gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 12)
    token = tmp_path / "bridge-gitlab-token"
    token.write_text("bridge-tok\n")
    token.chmod(0o600)
    cfg = tmp_path / "bridge.json"
    cfg.write_text(json.dumps({"gitlab": {"base": BASE, "token_env": "PATROL_TEST_NO_ENV", "token_file": str(token)}}))
    return gl, d, ["--config", str(cfg), "--brainkit", str(BRAINKIT), "--project", P, "--mr", "7"]


def test_the_reply_helper_answers_in_the_thread_or_on_the_mr_as_the_bridge(bk, tmp_path, capsys):
    gl, d, args = reply_world(tmp_path)
    tool = reply_tool()
    assert tool.main(args + ["--discussion", d["id"], "改了：加了 0-100 的检查"], transport=gl) == 0
    assert tool.main(args + ["--note", "总结：折扣的范围检查加上了，测试过了"], transport=gl) == 0
    posts = [(c[1], c[3]) for c in gl.calls if c[0] == "POST"]
    assert posts == [("/api/v4/projects/grp/repo/merge_requests/7/discussions/%s/notes" % d["id"],
                      {"body": "改了：加了 0-100 的检查"}),
                     ("/api/v4/projects/grp/repo/merge_requests/7/notes", {"body": "总结：折扣的范围检查加上了，测试过了"})]
    assert [n["author"]["username"] for n in d["notes"]] == ["carol", "brain-bridge"]
    assert capsys.readouterr().out.split() == [str(d["notes"][1]["id"]), str(gl.discussions[(P, 7)][-1]["notes"][0]["id"])]
    before = len(gl.calls)
    assert tool.main(args + ["--discussion", "../../projects", "x"], transport=gl) == 2
    assert tool.main(args + ["--note", "  "], transport=gl) == 2
    assert len(gl.calls) == before


def test_the_reply_helper_runs_on_its_own_with_nothing_of_the_app(bk, tmp_path):
    from teamfix import Served
    gl, d, args = reply_world(tmp_path)
    served = Served(gl)
    cfg = Path(args[1])
    doc = json.loads(cfg.read_text())
    doc["gitlab"]["base"] = served.base
    cfg.write_text(json.dumps(doc))
    try:
        p = subprocess.run([sys.executable, "-I", str(REPLY)] + args + ["--discussion", d["id"], "-"],
                           input="改了\n两处都改了", capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    finally:
        served.stop()
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == str(d["notes"][1]["id"]) and d["notes"][1]["body"] == "改了\n两处都改了"


def test_the_session_runner_says_where_a_patrol_turn_is(tmp_path):
    sessions = mod("sessions")
    gw = FakeGateway()
    mgr = sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"))
    slot = "patrol-42-7"

    async def go():
        await mgr.adapter_create(slot, agent="brain-patrol", model="m", memory_mode="incognito", title="t", folder="巡检",
                                 parent="brain-patrol:grp/repo!7", key="patrol:grp/repo!7", root_folder="Brain 运行机")
        await mgr.adapter_set_project(slot, str(tmp_path))
        await mgr.adapter_send(slot, "修一下", "P-1")
        running = await mgr.adapter_turn(slot, "P-1")
        gw.finish(slot)
        for _ in range(4):
            await mgr.poll()
        ended, unknown = await mgr.adapter_turn(slot, "P-1"), await mgr.adapter_turn(slot, "P-2")
        await mgr.adapter_send(slot, "再修一下", "P-2")
        await mgr.adapter_stop(slot)
        return running, ended, unknown, await mgr.adapter_turn(slot, "P-2")

    assert asyncio.run(go()) == ("running", "ended", "unknown", "failed")


def test_a_patrol_turn_kiro_crew_cut_short_with_an_error_failed_whatever_it_wrote_first(tmp_path):
    sessions = mod("sessions")
    gw = FakeGateway()
    mgr = sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"))
    slot = "patrol-42-7"
    error = {"role": "error", "content": "model runtime failed", "meta": {"code": "runtime_failed"}}
    gw.script = [[{"role": "assistant", "content": "我先看一下 CI 日志。", "meta": {}}, error],
                 [error, {"role": "assistant", "content": "改好了，推上去了。", "meta": {}}]]

    async def go():
        await mgr.adapter_create(slot, agent="brain-patrol", model="m", memory_mode="incognito", title="t", folder="巡检",
                                 parent="brain-patrol:grp/repo!7", key="patrol:grp/repo!7", root_folder="Brain 运行机")
        await mgr.adapter_set_project(slot, str(tmp_path))
        out = []
        for sid in ("P-1", "P-2"):
            await mgr.adapter_send(slot, "修一下", sid)
            gw.finish(slot)
            for _ in range(4):
                await mgr.poll()
            out.append(await mgr.adapter_turn(slot, sid))
        return out

    assert asyncio.run(go()) == ["failed", "ended"]


def test_the_session_runner_follows_a_message_kiro_crew_did_not_answer_and_ends_one_it_refused(tmp_path):
    sessions = mod("sessions")
    gw = FakeGateway()
    mgr = sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"))
    slot = "patrol-42-7"
    land = gw.send

    async def landed_without_an_answer(key, message, send_id):
        await land(key, message, send_id)
        raise GatewayError(0, "gateway unreachable (TimeoutError)", "unreachable")

    async def refused(key, message, send_id):
        raise GatewayError(409, "this slot is busy", "slot_busy")

    async def send(sid, how):
        gw.send = how
        try:
            await mgr.adapter_send(slot, "修一下", sid)
        except GatewayError:
            pass
        finally:
            gw.send = land

    async def go():
        await mgr.adapter_create(slot, agent="brain-patrol", model="m", memory_mode="incognito", title="t", folder="巡检",
                                 parent="brain-patrol:grp/repo!7", key="patrol:grp/repo!7", root_folder="Brain 运行机")
        await mgr.adapter_set_project(slot, str(tmp_path))
        await send("P-1", landed_without_an_answer)
        out = [await mgr.adapter_turn(slot, "P-1")]
        gw.finish(slot)
        for _ in range(4):
            await mgr.poll()
        out.append(await mgr.adapter_turn(slot, "P-1"))
        gw.fail_send = True                     # KiroCrew fails before it takes the message: no row of it, ever
        await send("P-2", land)
        gw.fail_send = False
        for _ in range(4):
            await mgr.poll()
        out.append(await mgr.adapter_turn(slot, "P-2"))
        await mgr.adapter_stop(slot)
        out.append(await mgr.adapter_turn(slot, "P-2"))
        await send("P-3", refused)
        out.append(await mgr.adapter_turn(slot, "P-3"))
        return out

    assert asyncio.run(go()) == ["running", "ended", "running", "failed", "failed"]


def test_the_adapter_keeps_patrol_slots_and_run_slots_apart(bk):
    crew = mod("team.crewadapter")
    err = bk.brain_error()
    kc = crew.KiroCrewAdapter(None, SimpleNamespace(app="brain"), err)
    for call in (lambda: kc.patrol_send("run-pay-1-1", "x", "P-1"), lambda: kc.patrol_turn("patrol-42", "P-1"),
                 lambda: kc.send("patrol-42-7", "x", "P-1"), lambda: kc.patrol_send("patrol-42-7", "x", "bad id")):
        with pytest.raises(err):
            call()
    assert crew.PATROL_SLOT_RE.match("patrol-42-7") and crew.PATROL_SLOT_RE.match("patrol-42-7-2")
    assert not crew.PATROL_SLOT_RE.match("patrol-42-7-1") and not crew.PATROL_SLOT_RE.match("patrol-0-7")


def test_the_adapter_says_refused_only_when_kiro_crew_answered_no(bk):
    crew, patrol, err = mod("team.crewadapter"), mod("team.patrol"), bk.brain_error()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    answer = {}

    async def fails(*_args):
        if answer["exc"] is None:
            await asyncio.sleep(5)              # no answer within the adapter's wait
        raise answer["exc"]
    kc = crew.KiroCrewAdapter(loop, SimpleNamespace(app="brain", adapter_send=fails, adapter_set_project=fails), err,
                              timeout=0.2)
    refused = []
    try:
        for exc in (GatewayError(409, "this slot is busy", "slot_busy"), GatewayError(403, "no", "forbidden"),
                    GatewayError(0, "gateway unreachable (TimeoutError)", "unreachable"), GatewayError(502, "bad"),
                    None):
            answer["exc"] = exc
            for call in (lambda: kc.patrol_send("patrol-42-7", "x", "P-1"), lambda: kc.patrol_project("patrol-42-7", "/w")):
                with pytest.raises((patrol.Refused, err)) as got:
                    call()
                refused.append(isinstance(got.value, patrol.Refused))
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert refused == [True] * 4 + [False] * 6


def test_a_run_s_message_is_held_for_trust_only_when_kiro_crew_refuses_the_app_session_approval(bk):
    """brain-kit's delivery holds a message the run's slot could not be set to trust for (TrustRefused) and tells
    the MR to grant the bridge's app sessionApproval. Only KiroCrew's 403 session_approval_not_granted is that: a slot
    this app did not open for the bridge (409 not_a_runner_slot), one KiroCrew does not have (404), KiroCrew asking to
    slow down (429) or another 403 is an ordinary error, which the delivery settles by reading the slot. Nothing is
    sent after a failed trust call."""
    crew, err = mod("team.crewadapter"), bk.brain_error()
    trust_refused = bk.m("bridge.kirocrew").TrustRefused
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    answer, sent = {}, []

    async def trust(_name):
        raise answer["exc"]

    async def send(*args):
        sent.append(args)
        return {"ok": True}
    kc = crew.KiroCrewAdapter(loop, SimpleNamespace(app="brain", adapter_trust=trust, adapter_send=send), err)
    held = []
    try:
        for exc in (GatewayError(403, 'App "brain" cannot approve tool requests …', "session_approval_not_granted"),
                    GatewayError(409, "会话 run-pay-1-1 不是桥开的", "not_a_runner_slot"),
                    GatewayError(404, "no such slot", "slot_not_found"), GatewayError(429, "slow down", "rate_limited"),
                    GatewayError(403, "not yours", "folder_not_owned")):
            answer["exc"] = exc
            with pytest.raises(err) as got:
                kc.send("run-pay-1-1", "决策 Q-1 已答", "V-1")
            held.append(isinstance(got.value, trust_refused))
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert held == [True, False, False, False, False] and sent == []


def test_patrol_s_own_reply_answers_a_review_thread_while_the_mr_s_pipeline_failed(bk, tmp_path):
    """The session replies in a review thread and its turn fails; the pipeline is run again and fails again. The next
    dispatch is about the pipeline only: patrol's reply answered the note, a failed pipeline or not."""
    w = World(tmp_path)
    gl = w.gl
    gl.mr(P, 7)
    gl.fail(P, 7)
    d = gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 12)
    w.round()
    assert "百分比要限制在 0-100" in w.sessions.messages()[-1]
    w.later()
    gl.reply(P, 7, d, "brain-bridge", "改了：加了 0-100 的检查")
    w.sessions.end("failed")
    w.later()
    w.round()
    assert [p["kind"] for p in w.item(7)["problems"]] == ["ci_failed"]
    gl.fail(P, 7)
    gl.touch(P, 7)
    w.later()
    w.sessions.end("failed")
    w.later()
    w.round()
    assert [p["kind"] for p in w.item(7)["problems"]] == ["ci_failed"]
    assert "百分比要限制在 0-100" not in w.sessions.messages()[-1]
