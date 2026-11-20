"""开 MR and 转完整流程 (acts.py): brain-kit's `brain mr|full --json` read at the app's boundary, and every refusal the
app can tell before anything runs, in brain-kit's words for what a flow does not do (交接 and 开审查会话 of a fast run,
开 MR and 转完整流程 of a Lean one)."""

import json

import pytest

from appkit import mod
from brainfix import (FakeGateway, install_brain_dev, make_project, make_service, needs_brainkit, run,
                      start_in_worktree)

acts = mod("acts")
AppError = mod("httpkit").AppError
GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "grp/pay"}}


def test_brains_answer_is_read_at_the_boundary():
    ok = json.dumps({"ok": True, "action": "mr", "text": "推上去了，开了 MR !12", "pushed": True,
                     "mr": {"iid": 12, "url": "https://gitlab.example.com/grp/pay/-/merge_requests/12", "existing": False},
                     "commands": [], "new_mr_url": None, "intent": None, "warnings": []})
    got = acts.parse(0, "some progress\n" + ok, "", "mr")
    assert (got["ok"], got["text"], got["pushed"], got["mr"]["iid"], got["mr"]["existing"]) == (
        True, "推上去了，开了 MR !12", True, 12, False)
    assert "some progress" in got["output"]
    full = acts.parse(0, json.dumps({"ok": True, "action": "full", "text": "转成完整流程", "intent": "261120-pay-3-coupon"}),
                      "", "full")
    assert full["intent"] == "261120-pay-3-coupon"
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
