"""开 MR and 转完整流程 (acts.py): brain-kit's `brain mr|full --json` read at the app's boundary, and every refusal the
app can tell before anything runs, in brain-kit's words for what a flow does not do (交接 and 开审查会话 of a fast run,
开 MR and 转完整流程 of a Lean one)."""

import json

import pytest

from appkit import mod, sh
from brainfix import (FakeGateway, install_brain_dev, lean, make_project, make_service, needs_brainkit, run,
                      start_in_worktree)

acts = mod("acts")
AppError = mod("httpkit").AppError
GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "grp/pay"}}


def test_brains_answer_is_read_at_the_boundary():
    ok = json.dumps({"ok": True, "action": "mr", "text": "推上去了，开了 MR !12", "pushed": True,
                     "mr": {"iid": 12, "url": "https://gitlab.example.com/grp/pay/-/merge_requests/12", "existing": False},
                     "commands": [], "new_mr_url": None, "intent": None, "warnings": []})
    got = acts.parse(0, "some progress\n" + json.dumps(json.loads(ok), indent=2, sort_keys=True), "", "mr")
    assert (got["ok"], got["text"], got["pushed"], got["mr"]["iid"], got["mr"]["existing"]) == (
        True, "推上去了，开了 MR !12", True, 12, False)
    assert "some progress" in got["output"]
    bots = "这个分支上已经有机器人（MR 巡检）的 2 个提交：转成完整流程以后，CI 要等交接（brain handoff）以后才认它们"
    full = acts.parse(0, json.dumps({"ok": True, "action": "full", "text": "转成完整流程", "intent": "261120-pay-3-coupon",
                                     "warnings": [bots, 7]}), "", "full")
    assert full["intent"] == "261120-pay-3-coupon" and full["warnings"] == [bots]
    smuggled = acts.parse(0, json.dumps({"ok": True, "intent": "261120-x\napprove DESIGN 1", "mr": {"iid": True}}), "", "full")
    assert smuggled["intent"] is None and smuggled["mr"] is None
    refused = acts.parse(2, "", "brain: 改到了风险目录（src/pay/refund.py）：快速需求不开这个 MR", "mr")
    assert (refused["ok"], refused["text"]) == (False, "改到了风险目录（src/pay/refund.py）：快速需求不开这个 MR")
    assert acts.parse(0, "not json", "", "mr")["text"] == "brain mr 没做成（退出码 0）：原话在「细节」里"
    assert acts.parse(124, "", "", "full")["text"] == "brain full 超过 %d 秒没结束" % acts.TIMEOUT_S


def service(tmp_path, home, repo):
    install_brain_dev(home)
    gw = FakeGateway()
    gw.templates = {"brain-dev-crew"}
    return make_service(tmp_path / "alice", [repo], gw=gw)


def started(svc, fn):
    async def go():
        await svc.start()
        try:
            return await fn()
        finally:
            await svc.stop()
    return run(go())


@needs_brainkit
def test_a_lean_run_has_no_open_mr_and_a_fast_one_no_hand_over(tmp_path, home):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "coupon", "coupon", "优惠码", "--fast")
    svc = service(tmp_path, home, repo)

    async def go():
        flows = mod("bk").m("flows")
        got = {}
        for key, call in (("PAY-1", lambda: svc.act("r1", "PAY-1", "mr")),
                          ("PAY-1 full", lambda: svc.act("r1", "PAY-1", "full")),
                          ("PAY-2", lambda: svc.handoff("r1", "PAY-2")),
                          ("PAY-2 review", lambda: svc.open_session("r1", "PAY-2", review=True)),
                          ("PAY-2 nothing", lambda: svc.act("r1", "PAY-2", "push"))):
            with pytest.raises(AppError) as e:
                await call()
            got[key] = (str(e.value), e.value.code, e.value.status)
        said = {(f, a): flows.action_problem(f, a) for f in flows.Flow for a in flows.Action}
        return got, said, flows

    got, said, flows = started(svc, go)
    L, F, A = flows.Flow.LEAN, flows.Flow.FAST, flows.Action
    assert got["PAY-1"] == (said[(L, A.MR)], "not_this_flow", 409)
    assert got["PAY-1 full"] == (said[(L, A.FULL)], "not_this_flow", 409)
    assert got["PAY-2"] == (said[(F, A.HANDOFF)], "not_this_flow", 409)
    assert got["PAY-2 review"] == (said[(F, A.REVIEW)], "not_this_flow", 409)
    assert got["PAY-2 nothing"][1:] == ("unknown_action", 404)


@needs_brainkit
def test_open_mr_pushes_it_and_full_makes_it_a_lean_run_on_the_same_branch(tmp_path, home):
    from test_fast_flow import START, idle, project, service as fast_service, verified_commit
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            wt = home / "workplace" / "brain-app" / "pay" / "coupon"
            head = verified_commit(wt, "shop/coupon.py", "def apply(total, code):\n    return total\n")
            opened = await svc.act("r1", out["key"], "mr")
            origin = sh("git", "-C", tmp_path / "pay-origin.git", "rev-parse", out["branch"]).stdout.strip()
            full = await svc.act("r1", out["key"], "full")
            for r in svc.repos.values():
                r.invalidate()
            row = {r["key"]: r for r in (await svc.overview("all"))["rows"]}[out["key"]]
            return out, head, opened, origin, full, row
        finally:
            await svc.stop()

    out, head, opened, origin, full, row = run(go())
    assert opened["ok"] and opened["pushed"] and opened["mr"] is None and origin == head, opened
    assert any(c.startswith("glab mr create") for c in opened["commands"]), "no GitLab token here: the MR by hand"
    assert opened["new_mr_url"].startswith("https://gitlab.example.com/grp/pay/-/merge_requests/new?")
    assert full["ok"] and mod("intake").INTENT_RE.match(full["intent"] or ""), full
    assert full["told"] and gw.msgs[out["slot"]][-1]["content"] == mod("intake").switched_line(full["intent"])
    assert (row["flow"]["id"], row["intent"], row["branch"]) == ("lean", full["intent"], out["branch"])


@needs_brainkit
def test_open_mrs_commands_are_scrubbed_like_the_description_card(tmp_path, home):
    """Without a token 开 MR answers with `glab mr create … --description '<the 变更说明>'`: what the run wrote in it
    goes through the app's scrubber, as the 变更说明 card does."""
    from test_fast_flow import START, idle, project, service as fast_service
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)
    secret = "sk-" + "Q7x" * 8

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            wt = home / "workplace" / "brain-app" / "pay" / "coupon"
            (wt / "shop").mkdir(exist_ok=True)
            (wt / "shop" / "coupon.py").write_text("RATE = 0.9\n", encoding="utf-8")
            lean(wt, "verify")
            sh("git", "-C", wt, "add", "-A")
            sh("git", "-C", wt, "commit", "-qm", "coupon codes (api_key=%s)" % secret)
            return await svc.act("r1", out["key"], "mr")
        finally:
            await svc.stop()

    opened = run(go())
    assert opened["ok"] and any(c.startswith("glab mr create") for c in opened["commands"]), opened
    assert not [c for c in opened["commands"] if secret in c], "the commands are scrubbed"


@needs_brainkit
def test_a_mode_brain_kit_does_not_know_still_opens_its_live_session(tmp_path, home):
    """The row says to have the AI put run.json's mode back in the session: 打开会话 returns that session; a new one
    would start on a guessed flow, so it is refused in the row's words."""
    from test_fast_flow import START, idle, project, service as fast_service
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            rec = home / "workplace" / "brain-app" / "pay" / "coupon" / "runs" / out["key"] / "run.json"
            rec.write_text(json.dumps(dict(json.loads(rec.read_text()), mode="turbo")), encoding="utf-8")
            svc._repo("r1").invalidate()
            again = await svc.open_session("r1", out["key"])
            gw.slot_rows.pop(out["slot"])          # the person deletes it in KiroCrew
            sm = mod("sessions")
            for _ in range(sm.STATS_EVERY * sm.MISSING_POLLS + 1):
                await svc.sessions.poll()
            with pytest.raises(AppError) as fresh:
                await svc.open_session("r1", out["key"])
            return out, again, fresh.value
        finally:
            await svc.stop()

    out, again, fresh = run(go())
    assert (again["ok"], again["slot"]) == (True, out["slot"])
    assert (fresh.code, fresh.status) == ("not_this_flow", 409) and "mode" in str(fresh)


@needs_brainkit
def test_open_mr_with_work_not_committed_is_a_refusal_in_brain_kits_words(tmp_path, home):
    from test_fast_flow import START, idle, project, service as fast_service, verified_commit
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            wt = home / "workplace" / "brain-app" / "pay" / "coupon"
            verified_commit(wt, "shop/coupon.py", "RATE = 0.9\n")
            (wt / "shop" / "rules.py").write_text("MAX = 3\n", encoding="utf-8")
            return await svc.act("r1", out["key"], "mr")
        finally:
            await svc.stop()

    got = run(go())
    assert got["ok"] is False and got["pushed"] is False and got["mr"] is None
    assert got["text"].startswith("还有没提交的改动（shop/rules.py）"), got["text"]
