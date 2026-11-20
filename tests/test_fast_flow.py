"""A fast run (快速) through the app on a real brain-kit project: 开需求 starts it with `brain start --fast`, its session
is told it opens no Lean intent, its row follows its commits and its `lean verify` to 开 MR, its verified commit is
pushed like a Lean checkpoint, 开审查会话 is refused, and a risk path sends it to 转完整流程."""

import json

import pytest

from appkit import mod, sh
from brainfix import FakeGateway, install_brain_dev, lean, make_project, make_service, needs_brainkit, run

pytestmark = needs_brainkit
intake = mod("intake")
AppError = mod("httpkit").AppError
GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "grp/pay"}}
START = {"repo": "r1", "slug": "coupon", "title": "订单优惠码", "docs": [], "consent": False, "fast": True,
         "open_session": True, "trust": True}


def project(tmp_path, risk=None):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    lean_json = repo / "aidlc-docs" / "lean.json"
    lean_json.write_text(json.dumps(dict(json.loads(lean_json.read_text()), checks=["python3 -c pass"]), indent=2),
                         encoding="utf-8")
    if risk:
        (repo / "gates").mkdir(exist_ok=True)
        (repo / "gates" / "risk-paths").write_text(risk, encoding="utf-8")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "checks")
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    sh("git", "-C", repo, "fetch", "-q", "origin")
    return repo


def verified_commit(wt, path, text):
    """What the session does on a fast run: the code with its test, `lean verify` green, code and Lean's ledger in one
    commit."""
    f = wt / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    lean(wt, "verify")
    sh("git", "-C", wt, "add", "-A")
    sh("git", "-C", wt, "commit", "-qm", "coupon codes")
    return sh("git", "-C", wt, "rev-parse", "HEAD").stdout.strip()


def service(tmp_path, home, repo):
    install_brain_dev(home)
    gw = FakeGateway()
    gw.templates = {"brain-dev-crew"}
    return make_service(tmp_path / "alice", [repo], gw=gw), gw


async def rows(svc):
    for r in svc.repos.values():
        r.invalidate()
    return {r["key"]: r for r in (await svc.overview("all"))["rows"]}


def test_a_fast_run_goes_from_its_start_to_open_mr(tmp_path, home):
    repo = project(tmp_path)
    svc, gw = service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            wt = tmp_path / "home" / "workplace" / "brain-app" / "pay" / "coupon"
            first = (await rows(svc))[out["key"]]
            tip = verified_commit(wt, "shop/coupon.py", "def apply(total, code):\n    return total\n")
            ready = (await rows(svc))[out["key"]]
            svc.sync_tick()
            origin = sh("git", "-C", tmp_path / "pay-origin.git", "rev-parse", out["branch"]).stdout.strip()
            with pytest.raises(AppError) as review:
                await svc.open_session("r1", out["key"], review=True)
            facts = svc._facts(svc._repo("r1"), out["key"])
            flows = mod("bk").m("flows")
            said = flows.action_problem(flows.Flow.FAST, flows.Action.REVIEW)
            return out, wt, first, ready, tip, origin, (str(review.value), said), facts
        finally:
            await svc.stop()

    out, wt, first, ready, tip, origin, review, facts = run(go())
    assert out["ok"] and out["intent"] is None and out["branch"] == "ai/alice/PAY-1-coupon"
    rec = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())
    assert rec["mode"] == "fast" and rec["intent"] is None
    assert gw.msgs[out["slot"]][0]["content"] == intake.kickoff("PAY-1", out["branch"], None, [], "fast")
    assert first["flow"] == {"id": "fast", "label": "快速", "intent": False, "actions": ["full", "mr"]}
    assert (first["state"], first["waits"]["do"], first["waits"]["text"]) == (
        "local", "reply", "会话停下了：看它最后说了什么，回它")
    assert (ready["waits"]["who"], ready["waits"]["do"], ready["waits"]["text"]) == (
        "you", "open_mr", mod("fastrow").READY)
    assert origin == tip, "the verified commit is a checkpoint: pushed as a Lean one is"
    assert review[0] == review[1] and review[1]
    verifies = [e for e in facts["events"] if e.get("event") == "VERIFY"]
    assert verifies and all(e["intent"] is None and e["data"]["branch"] == out["branch"] for e in verifies)


def test_a_fast_run_that_changes_a_risk_path_waits_for_the_full_flow(tmp_path, home):
    repo = project(tmp_path, risk="src/pay/\n")
    svc, _ = service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            wt = tmp_path / "home" / "workplace" / "brain-app" / "pay" / "coupon"
            verified_commit(wt, "src/pay/refund.py", "def refund():\n    pass\n")
            return (await rows(svc))[out["key"]]
        finally:
            await svc.stop()

    r = run(go())
    assert (r["waits"]["who"], r["waits"]["do"], r["waits"]["where"]) == ("you", "full", "app")
    assert r["waits"]["text"] == mod("fastrow").RISKY % "src/pay/refund.py"
