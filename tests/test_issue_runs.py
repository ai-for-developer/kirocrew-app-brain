"""A run 从 issue 接活 started is the runner's from its first commit (run.json origin remote, no hand-over), on every page
of a person's app, the labeler's and a teammate's: 需求现状 says its question once, as the bridge put it on the MR, and
the labeler answers it there; its credits say the runner's sessions are left out; 运行过程 draws it on the runner's lane;
the app opens no session of it, pushes nothing onto its branch at a checkpoint and hands nothing over.

Offline: the issue source's own code makes the run (issuefix.started_from_issue) in a bare origin that a fake GitLab
serves (relayfix.RepoGitLab), the runner goes on in a workspace of its own and commits as runnerd's bot, and the bridge
asks its question on the MR as D-1. alice labelled the issue; she and bob fetched the branch and checked it out."""

import hashlib
import json
import re
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import lean, make_project, make_service, needs_brainkit, person, run
from issuefix import RUNNER, started_from_issue
from relayfix import API, BASE, RepoGitLab

pytestmark = needs_brainkit
P = "shop/pay"
KIT = {"gitlab": {"base": BASE, "project": P}, "bridge_user": "brain-bridge", "bot_emails": [RUNNER["GIT_AUTHOR_EMAIL"]]}
PARK = {"schema": 1, "status": "waiting", "kind": "clarify", "ask": "owner", "question": "按状态筛选的结果要不要分页？",
        "options": [{"id": "A", "text": "要，每页 50 条"}, {"id": "B", "text": "不要"}], "recommend": "A"}


class GitLab(RepoGitLab):
    """RepoGitLab that also takes a note someone posts on an MR with their own token (an answer)."""

    NOTES = re.compile(r"%s/projects/%s/merge_requests/(\d+)/notes" % (API, re.escape(P)))

    def __call__(self, method, url, headers, body):
        m = self.NOTES.fullmatch(unquote(urlsplit(url).path))
        who = self.who.get(headers.get("PRIVATE-TOKEN"))
        if method == "POST" and m and who:
            self.calls.append((method, unquote(urlsplit(url).path), {}, who))
            return 201, json.dumps(self.note(P, int(m.group(1)), who, json.loads(body.decode())["body"])).encode()
        return super().__call__(method, url, headers, body)


@pytest.fixture
def world(tmp_path, home, monkeypatch):
    bk = mod("bk")
    repo = make_project(tmp_path, origin=True, kit=KIT)
    bare = tmp_path / "pay-origin.git"
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    gl = GitLab()
    gl.project(P, 7, bare)
    assert bk.load(str(BRAINKIT))["ok"]         # the issue source's code runs here, outside any app
    try:
        key, branch, start = started_from_issue(bare, tmp_path / "issues", P, "alice", base=BASE, iid=7)
        work = tmp_path / "runner"                # the runner's own workspace: Lean's first stages, then its question
        sh("git", "clone", "-q", "-b", branch, bare, work)
        lean(work, "stage", "skip", "reverse-engineering", "--reason", "新接口，没有旧代码")
        lean(work, "stage", "start", "requirements")
        (work / "runs" / key / "park.json").write_text(json.dumps(PARK, ensure_ascii=False), encoding="utf-8")
        sh("git", "-C", work, "add", "-A")
        sh("git", "-C", work, "commit", "-qm", "%s: ask the owner" % key, env=RUNNER)
        parked = sh("git", "-C", work, "rev-parse", "HEAD").stdout.strip()
        sh("git", "-C", work, "push", "-q", "origin", branch)
        gl.mr(P, 15, branch, labels=["ai-running"])
        raw = (work / "runs" / key / "park.json").read_bytes()
        gl.note(P, 15, "brain-bridge", bk.m("bridge.decisions").question_text(
            "D-1", "RUN-%s-1" % key, PARK, ["alice"], "clarify", parked, hashlib.sha256(raw).hexdigest()))
    finally:
        bk.unload()
    places = home / "workplace" / "brain-app"
    sh("git", "-C", repo, "fetch", "-q", "origin")
    sh("git", "-C", repo, "worktree", "add", "-q", "-b", branch, places / "pay" / "issue-7", "origin/" + branch)
    bob = person(tmp_path, bare, "bob")
    sh("git", "-C", bob, "worktree", "add", "-q", "-b", branch, places / "bob" / "issue-7", "origin/" + branch)
    monkeypatch.setattr(mod("mrinfo"), "TTL_S", 0.0)
    return SimpleNamespace(repo=repo, bob=bob, bare=bare, gl=gl, key=key, branch=branch, start=start, parked=parked,
                           wt=places / "pay" / "issue-7", tmp=tmp_path)


def app_of(w, monkeypatch, who):
    """*who*'s app on their own checkout, reading GitLab with their own token."""
    monkeypatch.setenv("GITLAB_TOKEN", "%s-tok" % who)
    return make_service(w.tmp / who, [w.repo if who == "alice" else w.bob], transport=w.gl)


def seen(svc, fn):
    """*fn*'s answer once the app has started and read the MRs its rows asked for (refresh_mrs)."""
    async def go():
        await svc.start()
        try:
            await svc.overview("all")
            await svc.refresh_mrs()
            for r in svc.repos.values():
                r.invalidate()
            return await fn()
        finally:
            await svc.stop()
    return run(go())


def pages(svc, key):
    async def go():
        return await svc.overview("all"), await svc.requirement("r1", key), await svc.board("r1", key)
    return seen(svc, go)


def refused(svc, key, *calls):
    """The AppError each of *calls* (a service method's name) raises for *key*, or what it returned."""
    async def go():
        out = []
        for name in calls:
            try:
                out.append(await getattr(svc, name)("r1", key, *((None,) if name == "reopen_session" else ())))
            except mod("httpkit").AppError as exc:
                out.append((exc.code, exc.status))
        return out
    return seen(svc, go)


def test_the_labelers_page_says_the_runners_question_once_and_its_credits_and_lanes_are_the_runners(world, monkeypatch):
    w = world
    view, d, b = pages(app_of(w, monkeypatch, "alice"), w.key)
    row = {r["key"]: r for r in view["rows"]}[w.key]
    assert (row["owner"], row["state"], row["waits"]["who"], row["waits"]["where"]) == ("alice", "parked", "you", "mr")
    assert [(q["source"], q["id"]) for q in d["questions"]] == [("mr", "D-1")], \
        "park.json is the runner's: the bridge's D-1 on the MR is that very question, said once"
    assert (d["questions"][0]["answerable_here"], d["questions"][0]["answer_via"]) == (True, "mr")
    assert d["credits"]["whole"] is False and d["credits"]["missing"] == [mod("service").RUNNER_STARTED], \
        "its sessions are on the runner, which this machine does not read"
    assert [r["kind"] for r in b["runs"]] == ["runner"], "运行过程 has no local half: the runner has it from its start"
    assert b["runs"][0]["label"] == "运行机 第 1 次" and b["relays"] == []
    desc = d["mr_desc"]
    assert "从开工起就在团队运行机上" in desc["first"] and "还没交接" not in desc["text"], \
        "its 变更说明 (brain-kit aab2c1d) says it is the runner's from its start, not that it is developed locally"


def test_the_labelers_answer_goes_onto_the_mr(world, monkeypatch):
    w = world
    svc = app_of(w, monkeypatch, "alice")

    async def go():
        return await svc.answer("r1", w.key, "A", "每页 50 条够了", "D-1")
    got = seen(svc, go)
    assert (got["ok"], got["via"]) == (True, "mr")
    assert [n["body"] for n in w.gl.notes[(P, 15)] if n["author"]["username"] == "alice"] == ["answer D-1 A 每页 50 条够了"]


def test_the_labelers_app_opens_no_session_of_it_and_hands_nothing_over(world, monkeypatch):
    w = world
    ran = []
    monkeypatch.setattr(mod("handover"), "run", lambda *a, **kw: ran.append(a) or {
        "ok": True, "state": "handed", "text": "交出去了", "todo": "", "mr": None, "warnings": [], "raw": "", "code": 0})
    got = refused(app_of(w, monkeypatch, "alice"), w.key, "open_session", "reopen_session", "handoff")
    assert got[:2] == [("not_local", 409), ("not_local", 409)], "the runner has its session, not her worktree"
    assert got[2] == ("on_runner", 409) and ran == [], "it was the runner's from its first commit: nothing to hand over"


def test_the_labelers_app_never_pushes_her_commit_onto_the_runners_branch(world, monkeypatch):
    w = world
    lean(w.wt, "stage", "skip", "user-stories", "--reason", "一个接口")       # a checkpoint, committed in her worktree
    sh("git", "-C", w.wt, "add", "-A")
    sh("git", "-C", w.wt, "commit", "-qm", "lean: skip user-stories")
    svc = app_of(w, monkeypatch, "alice")

    async def go():
        svc.sync_tick()
    seen(svc, go)
    assert sh("git", "-C", w.bare, "rev-parse", "refs/heads/" + w.branch).stdout.strip() == w.parked, \
        "the branch on origin is the runner's: her checkpoint stays on her machine"


def test_a_teammate_sees_it_as_the_runners_and_opens_no_session_of_it(world, monkeypatch):
    w = world
    svc = app_of(w, monkeypatch, "bob")
    view, d, b = pages(svc, w.key)
    assert w.key not in {r["key"] for r in view["rows"]}, "alice's: on 全队交付, not on his 交付总览"
    row = d["row"]
    assert (row["state"], row["waits"]["who"], row["waits"]["text"]) == ("parked", "people", "等 alice 在 MR 上回答 D-1（澄清）")
    assert [(q["source"], q["answerable_here"]) for q in d["questions"]] == [("mr", False)]
    assert d["credits"]["missing"] == [mod("service").RUNNER_STARTED]
    assert [r["kind"] for r in b["runs"]] == ["runner"]
    assert refused(app_of(w, monkeypatch, "bob"), w.key, "open_session", "reopen_session") == [("not_local", 409)] * 2
