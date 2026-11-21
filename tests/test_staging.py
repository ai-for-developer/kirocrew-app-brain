"""测试环境报错 (team/staging.py, work source staging-errors), offline: brain-kit's GitLab client on an in-memory GitLab, a
real log file and VERSION file, the session surface in memory, one clock for all. One Issue per error signature, a
comment when its count jumps, the MR that merged the deployed commit, a fix given to a brain-patrol session two at a
time counting MR 巡检's, its prompt, two failed attempts that stop it with one comment, its MR followed to the merge, a
recurrence on a newer deploy that opens the Issue again (not the old build's last lines read as the deploy lands), an
Issue people closed or labelled wontfix left closed, a deleted Issue or fix MR, at most three new Issues a round and
twenty open, the log's cursor across restarts, and a project that does not switch it on left alone.
"""

import os

import pytest

from appkit import BRAINKIT, mod
from brainfix import needs_brainkit
from patrolfix import BASE, SHA, Clock, FakeSessions, FakeWorkspaces
from stagingfix import StagingGitLab

pytestmark = needs_brainkit
P, PID = "grp/repo", 42
ZERO = "ZeroDivisionError at orders/discount.py in apply"
GATEWAY = "app.pay: gateway timeout after <n>s (order <n>)"
STOCK = "app.stock: warehouse aaa is gone"
HELPER = ("/usr/bin/python3 -I /app/backend/core/team/patrol_reply.py --config /setup/bridge.json --brainkit /kit "
          "--project grp/repo --issue %d")


@pytest.fixture
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


def kit(**over):
    cfg = mod("bk").m("config")
    return cfg.merged(cfg.DEFAULTS, dict({"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]}, **over))


def zero(release="20261121", line=12):
    return ["2026-11-21 10:00:01,100 ERROR app.orders: discount failed for order 1234",
            "Traceback (most recent call last):",
            '  File "/srv/releases/%s/shop/orders/views.py", line 41, in create' % release,
            "    total = discount.apply(order)",
            '  File "/srv/releases/%s/shop/orders/discount.py", line %d, in apply' % (release, line),
            "    return order.total / order.rate",
            "ZeroDivisionError: division by zero"]


def gateway(n=77):
    return ["2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order %d)" % n]


def stock():
    return ["2026-11-21 10:00:03,000 CRITICAL app.stock: warehouse aaa is gone"]


class World:
    def __init__(self, tmp_path, kits=None, shared=None, staged=True):
        self.clock = Clock()
        self.gl = StagingGitLab(self.clock)
        self.kits = kits or {P: kit()}
        for n, path in enumerate(self.kits):
            self.gl.project(path, PID + n)
        self.gl.merged(P, SHA[1], 40, "旧的", state="closed")
        self.gl.merged(P, SHA[1], 41, "折扣按百分比算")
        self.gl.heads[(P, "main")] = SHA[9]
        env = tmp_path / "staging-env"
        env.mkdir(parents=True)
        self.log, self.version = env / "app.log", env / "VERSION"
        self.log.write_text("2026-11-21 09:00:00 ERROR app: from before the runner watched\n")
        self.version.write_text(SHA[1] + "\n")
        self.sessions = FakeSessions()
        self.ws = FakeWorkspaces("/Users/alice/workplace/brain-team/staging")
        self.ledger, self.busy, self.staged = str(tmp_path / "staging.sqlite"), shared or [0], staged
        self.watch = self.start()

    def entries(self):
        st = {"log": {"kind": "file", "path": str(self.log)}, "version": {"kind": "file", "path": str(self.version)}}
        return [dict({"project": p}, **({"staging": st} if self.staged else {})) for p in self.kits]

    def start(self):
        patrol = mod("team.patrol")
        return mod("team.staging").StagingWatch(
            gl=mod("bk").m("rest").GitLab(BASE, "bridge-tok", self.gl), sessions=self.sessions,
            kit=lambda p: self.kits[p], projects=self.entries(), ledger_path=self.ledger, workspaces=self.ws,
            helper=patrol.Helper("/usr/bin/python3", "/app/backend/core/team/patrol_reply.py", "/setup/bridge.json", "/kit"),
            claims=lambda: [], shared=lambda: self.busy[0], now=self.clock, out=lambda *_: None)

    def write(self, *chunks):
        with open(self.log, "a", encoding="utf-8") as fh:
            for lines in chunks:
                fh.write("\n".join(lines) + "\n")

    def round(self):
        self.clock.t += 60
        return self.watch.round()

    def item(self, signature):
        return next(i for i in self.watch.view()["items"] if i["signature"] == signature)

    def issue(self, iid):
        return self.gl.issues[(P, iid)]


def started(tmp_path, **kw):
    """A world whose first round has looked at the log (it starts from its end)."""
    w = World(tmp_path, **kw)
    assert w.round() == 1 and w.gl.opened(P) == []
    return w


def test_one_issue_per_signature_and_a_comment_only_when_its_count_jumps(bk, tmp_path):
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(zero(), ["INFO ok"], zero("20261122", 14), ["INFO ok"], gateway())
    w.round()
    z, g = w.gl.opened(P)
    assert (z["title"], g["title"]) == ("测试环境报错：" + ZERO, "测试环境报错：" + GATEWAY)
    assert z["labels"] == ["staging-error"] and w.item(ZERO)["count"] == 2 and w.item(GATEWAY)["count"] == 1
    text = z["description"]
    assert "- 特征：`%s`" % ZERO in text and "到现在 2 次" in text
    assert "- 部署的提交：[%s](%s/grp/repo/-/commit/%s)" % (SHA[1][:12], BASE, SHA[1]) in text
    assert "- 带进这个提交的 MR：[!41 折扣按百分比算](%s/grp/repo/-/merge_requests/41)" % BASE in text
    assert "```text\n2026-11-21 10:00:01,100 ERROR app.orders: discount failed for order 1234\nTraceback" in text
    assert '/srv/releases/20261122/shop/orders/discount.py", line 14' in text      # the newest one
    assert "from before the runner watched" not in text
    for more in (5, 6, 12, 1):                  # 7: up 5; 13: up 11 (one comment); 25: up 12 of 13; 26: up 13
        w.write(*[x for _ in range(more) for x in (zero(), ["INFO ok"])])
        w.round()
    assert len(w.gl.opened(P)) == 2 and w.item(ZERO)["count"] == 26
    assert w.gl.notes(P, z["iid"]) == [
        "这个报错又出现了 11 次，一共 13 次；最近一次在 %s，部署的提交 [%s](%s/grp/repo/-/commit/%s)。"
        % (staging._when(w.clock.t - 120), SHA[1][:12], BASE, SHA[1]),
        "这个报错又出现了 13 次，一共 26 次；最近一次在 %s，部署的提交 [%s](%s/grp/repo/-/commit/%s)。"
        % (staging._when(w.clock.t), SHA[1][:12], BASE, SHA[1])]
    assert w.gl.notes(P, g["iid"]) == []


def test_an_issue_whose_answer_was_lost_is_found_again_and_not_opened_twice(bk, tmp_path):
    w = started(tmp_path)
    w.gl.lose_issue_answer = True
    w.write(zero())
    with pytest.raises(bk.brain_error(), match="502"):
        w.round()
    assert w.item(ZERO)["state"] == "new" and w.item(ZERO)["note"].startswith("这一轮没做成")
    w.round()
    assert len(w.gl.opened(P)) == 1 and w.item(ZERO)["state"] == "fixing" and w.item(ZERO)["issue"]["iid"] == 1
    w.watch.close()
    os.remove(w.ledger)                          # the ledger is lost: the next start watches from the log's end ...
    w.watch = w.start()
    w.round()
    w.write(zero())                              # ... and the error, when it comes again, has its open Issue
    w.round()
    assert len(w.gl.opened(P)) == 1 and w.item(ZERO)["issue"]["iid"] == 1 and w.item(ZERO)["count"] == 1


def test_an_mr_from_the_state_relays_branch_is_never_an_errors_origin(bk, tmp_path):
    """Someone merged brain/status's MR into the deployed commit too: the origin is still the code's MR."""
    w = started(tmp_path)
    w.gl.merged_by[(P, SHA[1])].insert(0, {"iid": 99, "title": "团队状态", "state": "merged", "source_branch": "brain/status",
                                           "web_url": "%s/grp/repo/-/merge_requests/99" % BASE})
    w.write(zero())
    w.round()
    assert w.item(ZERO)["origin"]["iid"] == 41


def test_the_deployed_commit_maps_to_the_mr_that_merged_it_or_says_none_did(bk, tmp_path):
    w = started(tmp_path)
    w.write(zero())
    w.round()
    assert w.item(ZERO)["origin"] == {"iid": 41, "url": "%s/grp/repo/-/merge_requests/41" % BASE, "title": "折扣按百分比算"}
    assert w.gl.asked_staging("GET", r"/repository/commits/%s/merge_requests$" % SHA[1])
    w.version.write_text(SHA[2] + "\n")         # a commit no MR merged (pushed to main)
    w.write(gateway())
    w.round()
    assert w.item(GATEWAY)["origin"] is None and w.item(GATEWAY)["deployed_sha"] == SHA[2]
    assert "- 带进这个提交的 MR：没查到（GitLab 说没有 MR 合进过这个提交）" in w.gl.opened(P)[1]["description"]


def test_a_fix_goes_to_a_brain_patrol_session_of_its_own_two_at_a_time_counting_mr_patrol_s(bk, tmp_path):
    sources = mod("team.sources")
    w = started(tmp_path, shared=[1])            # MR 巡检 has one session at work
    w.write(zero(), ["INFO ok"], gateway(), stock())
    w.round()
    assert [i["iid"] for i in w.gl.opened(P)] == [1, 2, 3]
    assert w.sessions.opened == [("staging-42-1", "brain-patrol", "claude-opus-5.5", "grp/repo#1")]
    assert (w.item(GATEWAY)["state"], w.item(GATEWAY)["note"]) == ("issued", "排队：同时最多 2 个会话在修（MR 巡检的也算）")
    w.busy[0] = 0                                # MR 巡检's turn ended
    w.round()
    assert [s for s, _, _ in w.sessions.sent] == ["staging-42-1", "staging-42-2"] and w.item(STOCK)["state"] == "issued"
    view = w.watch.view()
    assert (view["cap"], view["working"]) == (2, 2) and w.ws.prepared == [(P, 1, SHA[9]), (P, 2, SHA[9])]
    branch = "fix/staging-" + mod("team.stagelog").short_id(ZERO)
    msg = w.sessions.messages("staging-42-1")[0]
    for said in ("测试环境报错：grp/repo#1「测试环境报错：%s」 %s/grp/repo/-/issues/1" % (ZERO, BASE),
                 "这一条不是 MR 巡检：还没有 MR。",
                 "测试环境部署的是提交 %s，它是 MR !41「折扣按百分比算」合进来的。" % SHA[1][:12],
                 "工作区：/Users/alice/workplace/brain-team/staging/42-1（已经检出 main，在它最新的提交上）。",
                 "   > ZeroDivisionError: division by zero",
                 "1. 从 main 开分支 %s：git checkout -B %s（" % (branch, branch),
                 "能复现就先写一个测试复现它，再修；跑项目的测试",
                 "git push origin HEAD:%s（不加 --force）" % branch,
                 "（命令会在描述末尾加上 Closes #1）",
                 "   %s --open-mr --source %s --target main --title '修复：%s' '描述'" % (HELPER % 1, branch, ZERO),
                 "   %s --note '回复的话'" % (HELPER % 1)):
        assert said in msg, said
    held = w.watch.held()
    assert [(c["kind"], c["key"], c["ref"]) for c in held] == [
        ("staging-errors", "staging-errors:grp/repo:issue!%d" % n, {"type": "issue", "iid": n}) for n in (1, 2, 3)]
    assert sources.SOURCES["staging-errors"].held(type("Rt", (), {"staging": w.watch})()) == held


def test_two_failed_fix_attempts_stop_it_with_one_issue_comment(bk, tmp_path):
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(zero())
    w.round()
    w.sessions.end()                            # the session ended its turn without an MR
    w.round()
    assert (w.item(ZERO)["state"], w.item(ZERO)["attempts"]) == ("fixing", 1)
    assert [o[0] for o in w.sessions.opened] == ["staging-42-1"] and len(w.sessions.sent) == 2
    assert "这是第 2 次修：上一次会话结束了，没开 MR。" in w.sessions.messages()[-1]
    w.clock.t += staging.TIMEOUT_S
    w.round()
    assert w.sessions.stopped == ["staging-42-1"] and w.item(ZERO)["state"] == "stopped"
    for _ in range(3):
        w.write(zero())
        w.round()
    assert w.gl.notes(P, 1) == ["运行机试了 2 次，没修好这个报错（最后一次：30 分钟没做完，停掉了）：它不再自己修，交给人。"
                                "人修好了或者不修了，就关掉这个 Issue：运行机不会再打开它，以后新的部署里又出现，只在这里说一次。"]
    assert len(w.sessions.sent) == 2 and w.item(ZERO)["note"] == "试了 2 次没修好（最后一次：30 分钟没做完，停掉了），在 Issue 上说了：交给人"


def test_messages_kiro_crew_refuses_are_failed_attempts_too(bk, tmp_path):
    w = started(tmp_path)
    w.sessions.refuse = True
    w.write(zero())
    for _ in range(4):
        w.round()
    assert w.sessions.asked == ["staging-42-1", "staging-42-1"] and w.item(ZERO)["state"] == "stopped"
    assert len(w.gl.notes(P, 1)) == 1 and "（最后一次：KiroCrew 没收下消息）" in w.gl.notes(P, 1)[0]


def test_the_fix_mr_is_followed_to_its_merge_and_a_recurrence_on_a_newer_deploy_opens_the_issue_again(bk, tmp_path):
    sl = mod("team.stagelog")
    w = started(tmp_path)
    w.write(zero())
    w.round()
    branch = "fix/staging-" + sl.short_id(ZERO)
    w.gl.mr(P, 52, sha=SHA[3], source=branch, title="修复：" + ZERO)       # the session opened its MR
    w.round()
    fix = {"iid": 52, "url": "%s/grp/repo/-/merge_requests/52" % BASE, "title": "修复：" + ZERO}
    assert (w.item(ZERO)["state"], w.item(ZERO)["fix_mr"]) == ("fix_mr", fix)
    w.gl.close(P, 52)                           # merged; its Closes #1 closes the Issue
    w.gl.close_issue(P, 1)
    w.round()
    assert w.item(ZERO)["state"] == "closed" and w.ws.dropped == [(42, 1)] and w.watch.held() == []
    w.write(zero(), ["INFO ok"], zero())        # the fix is not deployed yet: the same commit errs as before
    w.round()
    assert (w.item(ZERO)["state"], w.item(ZERO)["count"]) == ("closed", 3) and not w.gl.asked_staging("PUT", "/issues/1$")
    w.version.write_text(SHA[2] + "\n")         # deployed; the error is still there once the deploy has settled
    w.gl.merged(P, SHA[2], 52, "修复：" + ZERO)
    w.round()
    w.clock.t += mod("team.staging").REOPEN_S - 60
    w.write(zero("20261122", 13))
    w.round()
    assert [c[3] for c in w.gl.asked_staging("PUT", "/issues/1$")] == [{"state_event": "reopen"}]
    assert w.issue(1)["state"] == "opened" and len(w.gl.opened(P)) == 1
    again = w.gl.notes(P, 1)[-1]
    assert again.startswith("这个报错在新的部署里又出现了：部署的提交 [%s](%s/grp/repo/-/commit/%s)，带进它的 MR [!52 修复：%s]"
                            % (SHA[2][:12], BASE, SHA[2], ZERO))
    assert "修复分支是 %s-2。" % branch in again and 'discount.py", line 13, in apply' in again
    assert (w.item(ZERO)["state"], w.item(ZERO)["slot"]) == ("fixing", "staging-42-1-2")
    assert "git checkout -B %s-2（" % branch in w.sessions.messages("staging-42-1-2")[0]


def test_a_fix_mr_closed_without_merging_leaves_the_error_to_people(bk, tmp_path):
    w = started(tmp_path)
    w.write(zero())
    w.round()
    mr = w.gl.mr(P, 52, sha=SHA[3], source="fix/staging-" + mod("team.stagelog").short_id(ZERO))
    w.round()
    mr["state"] = "closed"
    w.round()
    w.round()
    assert w.item(ZERO)["state"] == "stopped" and w.item(ZERO)["note"] == "修复 MR 被关了，没有合并，在 Issue 上说了：交给人"
    assert w.gl.notes(P, 1) == ["修复 MR !52 被关了，没有合并：运行机不再自己修这个报错，交给人。人修好了或者不修了，就关掉这个 Issue：运行机不会"
                                "再打开它，以后新的部署里又出现，只在这里说一次。"]


def test_an_issue_a_person_closes_while_it_is_being_fixed_stops_its_turn_and_stays_closed_and_a_fixed_one_goes_a_month_on(
        bk, tmp_path):
    w = started(tmp_path)
    w.write(zero(), gateway())
    w.round()
    w.gl.close_issue(P, 1)                      # a person closes #1
    w.gl.mr(P, 52, sha=SHA[3], source="fix/staging-" + mod("team.stagelog").short_id(GATEWAY))
    w.gl.close(P, 52)                           # #2's fix MR merged at once; its Closes #2 closes it
    w.gl.close_issue(P, 2)
    w.round()
    assert w.sessions.stopped == ["staging-42-1", "staging-42-2"] and w.item(ZERO)["slot"] is None
    assert (w.item(ZERO)["state"], w.item(GATEWAY)["state"]) == ("dismissed", "closed") and w.watch.held() == []
    w.clock.t += mod("team.staging").FORGET_S
    w.round()
    assert [i["signature"] for i in w.watch.view()["items"]] == [ZERO]


def test_a_log_that_cannot_be_read_this_round_still_has_its_issues_and_fixes_followed(bk, tmp_path):
    w = started(tmp_path)
    w.write(zero())
    w.round()
    os.remove(w.log)
    w.gl.close_issue(P, 1)
    with pytest.raises(bk.brain_error(), match="app.log"):
        w.round()
    assert w.sessions.stopped == ["staging-42-1"] and w.item(ZERO)["state"] == "dismissed"
    assert w.watch.view()["projects"][0]["error"].startswith("[Errno 2] No such file or directory")


def test_the_cursor_survives_a_restart_and_a_traceback_still_being_written_waits_for_its_end(bk, tmp_path):
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    assert w.item(GATEWAY)["count"] == 1
    w.watch.close()
    w.watch = w.start()                         # team mode starts again on the same ledger
    w.round()
    assert w.item(GATEWAY)["count"] == 1        # what was read before is not read again
    w.write(gateway(78), zero()[:5])            # a traceback cut off where the round reads
    w.round()
    assert w.item(GATEWAY)["count"] == 2 and [i["signature"] for i in w.watch.view()["items"]] == [GATEWAY]
    w.write(zero()[5:])
    w.round()
    assert w.item(ZERO)["count"] == 1 and w.item(GATEWAY)["count"] == 2


def test_work_sources_without_staging_errors_do_nothing(bk, tmp_path):
    cfg = mod("bk").m("config")
    w = World(tmp_path, kits={P: cfg.merged(cfg.DEFAULTS, {"work_sources": ["mr-handoff", "mr-patrol"]})})
    w.write(zero())
    assert w.round() == 0 and w.round() == 0
    assert w.watch.view()["projects"] == [{"project": P, "on": False, "error": "", "deployed_sha": ""}]
    assert w.watch.view()["items"] == [] and not os.path.exists(w.ledger) and w.gl.issues == {}
    assert not [c for c in w.gl.calls if "/issues" in c[1] or "/repository/" in c[1]]


def test_a_project_without_a_staging_block_says_so_and_one_switched_off_is_forgotten(bk, tmp_path):
    bare = World(tmp_path / "bare", staged=False)
    with pytest.raises(bk.brain_error(), match="没写 staging"):
        bare.round()
    assert bare.watch.view()["projects"][0]["error"].startswith("bridge.json 的 projects 里 grp/repo 没写 staging")
    w = started(tmp_path)
    w.write(zero())
    w.round()
    assert w.item(ZERO)["state"] == "fixing"
    w.kits[P] = kit(work_sources=["mr-handoff"])
    w.round()
    assert w.sessions.stopped == ["staging-42-1"] and w.ws.dropped == [(42, 1)] and w.watch.view()["items"] == []
    w.kits[P] = kit()                           # on again: it watches from the log's end
    w.write(gateway())
    w.round()
    assert w.watch.view()["items"] == []


def fix_branch(signature):
    return "fix/staging-" + mod("team.stagelog").short_id(signature)


def test_lines_the_old_build_logged_just_before_a_deploy_do_not_open_the_fixed_issue_again(bk, tmp_path):
    """The fix MR merged and closed its Issue. The old build logs the error once more and the fix's deploy rewrites
    VERSION before the next round reads those lines: counted, not opened again. Lines read within REOPEN_S of the round
    that first read that VERSION are the same; one read after it opens the Issue again."""
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    w.gl.mr(P, 52, sha=SHA[3], source=fix_branch(GATEWAY))
    w.round()
    w.gl.close(P, 52)
    w.gl.close_issue(P, 1)
    w.round()
    assert w.item(GATEWAY)["state"] == "closed"
    w.write(gateway(78))
    w.version.write_text(SHA[2] + "\n")
    w.round()
    deployed = w.clock.t
    w.write(gateway(79))
    w.round()
    item = w.item(GATEWAY)
    assert (item["state"], item["count"], item["deployed_sha"]) == ("closed", 3, SHA[2])
    assert item["note"] == "新的部署里又看到了：部署 5 分钟以内的先不算（可能是旧版本最后写的），再看到就重新打开"
    assert w.issue(1)["state"] == "closed" and not w.gl.asked_staging("PUT", "/issues/1$")
    assert [o[0] for o in w.sessions.opened] == ["staging-42-1"]
    w.clock.t = deployed + staging.REOPEN_S - 60
    w.write(gateway(80))
    w.round()
    assert w.issue(1)["state"] == "opened" and w.item(GATEWAY)["state"] == "fixing"
    assert [o[0] for o in w.sessions.opened] == ["staging-42-1", "staging-42-1-2"]


def test_an_issue_people_close_stays_closed_and_its_error_coming_back_is_told_once_per_deploy(bk, tmp_path):
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    w.sessions.end()
    w.round()                                   # attempt 1 ended without an MR: attempt 2
    w.sessions.end()
    w.round()                                   # attempt 2 too: left to people, told once
    assert w.item(GATEWAY)["state"] == "stopped"
    w.gl.close_issue(P, 1)                      # people decide: won't fix
    w.round()
    assert (w.item(GATEWAY)["state"], w.item(GATEWAY)["note"]) == (
        "dismissed", "人关了这个 Issue，或者打了 wontfix 标签：不会再打开，也不修；新的部署里又出现，在 Issue 上说一次")
    for sha in (SHA[2], SHA[3]):                # two later deploys, the error still logged on each
        w.version.write_text(sha + "\n")
        w.round()
        w.clock.t += staging.REOPEN_S
        for n in (1, 2):
            w.write(gateway(100 + n))
            w.round()
    assert w.issue(1)["state"] == "closed" and not w.gl.asked_staging("PUT", "/issues/1$")
    assert [o[0] for o in w.sessions.opened] == ["staging-42-1"] and len(w.sessions.sent) == 2
    told = w.gl.notes(P, 1)[1:]
    assert [t.startswith("这个报错在新的部署里又出现了：部署的提交 [%s]" % sha[:12]) for t, sha in zip(told, (SHA[2], SHA[3]))] == [
        True, True] and len(told) == 2
    assert "这个 Issue 是人关的（或者打了 wontfix 标签）：运行机不会再打开它，也不修" in told[0]
    assert w.item(GATEWAY)["state"] == "dismissed" and w.watch.held() == []


def test_an_issue_labelled_wontfix_has_its_fix_stopped_and_is_left_to_people(bk, tmp_path):
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    assert w.item(GATEWAY)["state"] == "fixing"
    w.issue(1)["labels"].append("Won't Fix")
    w.round()
    assert w.sessions.stopped == ["staging-42-1"] and w.item(GATEWAY)["state"] == "dismissed"
    assert w.issue(1)["state"] == "opened" and w.gl.notes(P, 1) == []
    w.write(gateway(78))
    w.round()
    assert len(w.sessions.opened) == 1 and w.gl.notes(P, 1) == []


def test_people_opening_a_dismissed_issue_again_have_it_fixed_when_its_error_comes_back_on_a_deploy(bk, tmp_path):
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    w.gl.close_issue(P, 1)
    w.round()
    assert w.item(GATEWAY)["state"] == "dismissed"
    w.issue(1)["state"] = "opened"              # people change their mind
    w.version.write_text(SHA[2] + "\n")
    w.round()
    w.clock.t += staging.REOPEN_S
    w.write(gateway(78))
    w.round()
    assert w.item(GATEWAY)["state"] == "fixing" and [o[0] for o in w.sessions.opened] == ["staging-42-1", "staging-42-1-2"]
    assert w.gl.notes(P, 1) == [] and "git checkout -B %s-2（" % fix_branch(GATEWAY) in w.sessions.messages("staging-42-1-2")[0]


def test_a_deleted_issue_whose_error_comes_back_gets_a_new_one_and_a_deleted_fix_mr_leaves_it_to_people(bk, tmp_path):
    staging = mod("team.staging")
    w = started(tmp_path)
    w.write(gateway())
    w.round()
    w.sessions.end()
    del w.gl.issues[(P, 1)]                     # deleted on GitLab: 404 from now on
    w.round()
    assert (w.item(GATEWAY)["state"], w.item(GATEWAY)["issue"]) == ("closed", None)
    w.version.write_text(SHA[2] + "\n")
    w.round()
    w.clock.t += staging.REOPEN_S
    w.write(gateway(5))
    w.round()
    assert [i["iid"] for i in w.gl.opened(P)] == [2] and w.item(GATEWAY)["issue"]["iid"] == 2
    assert w.item(GATEWAY)["state"] == "fixing"
    w.gl.mr(P, 53, sha=SHA[4], source=fix_branch(GATEWAY) + "-2")
    w.round()
    assert w.item(GATEWAY)["state"] == "fix_mr"
    del w.gl.mrs[(P, 53)]                       # the fix MR deleted
    w.round()
    w.round()
    assert w.item(GATEWAY)["state"] == "stopped" and len(w.gl.notes(P, 2)) == 1


def flood(n):
    """*n* user names without a number or an id in them."""
    return ["user%s" % chr(97 + i % 26) * (1 + i // 26) for i in range(n)]


def test_one_error_logged_with_a_new_e_mail_each_time_is_one_issue(bk, tmp_path):
    w = started(tmp_path)
    w.write(["2026-11-21 10:00:%02d,000 ERROR app.auth: no account for %s@example.com" % (i, n)
             for i, n in enumerate(flood(60))])
    lists = len(w.gl.asked_staging("GET", r"/issues$"))
    w.round()
    issue, = w.gl.opened(P)
    assert issue["title"] == "测试环境报错：app.auth: no account for <email>"
    assert w.item("app.auth: no account for <email>")["count"] == 60
    assert len(w.gl.asked_staging("GET", r"/issues$")) - lists == 1


def test_new_issues_open_three_a_round_and_twenty_at_most_per_project_and_the_rest_say_why_they_wait(bk, tmp_path):
    w = started(tmp_path)
    w.write(["2026-11-21 10:00:%02d,000 ERROR app.auth: no account for %s" % (i, n) for i, n in enumerate(flood(60))])
    lists = len(w.gl.asked_staging("GET", r"/issues$"))
    w.round()
    assert len(w.gl.opened(P)) == 3
    assert len(w.gl.asked_staging("GET", r"/issues$")) - lists == 1       # one look for open Issues, not one per error
    waiting = [i for i in w.watch.view()["items"] if i["state"] == "new"]
    assert len(waiting) == 57 and {i["note"] for i in waiting} == {"排队：这一轮已经开了 3 个 Issue，下一轮再开"}
    for _ in range(7):
        w.round()
    assert len(w.gl.opened(P)) == 20
    waiting = [i for i in w.watch.view()["items"] if i["state"] == "new"]
    assert len(waiting) == 40 and {i["note"] for i in waiting} == {
        "排队：这个项目开着的测试环境报错 Issue 已经有 20 个：人关掉一些，剩下的再开"}
