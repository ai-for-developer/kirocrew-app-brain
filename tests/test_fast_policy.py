"""A team's kit.json on origin's default branch that brain-kit cannot read (a value of the wrong type, valid JSON) turns
快速 off for that repo, said in plain words, and nothing else: /status, 开需求 and a Lean start of the same repo still
answer, another repo's switch is untouched, and a start that fails leaves no worktree behind. A fast row whose branch
git cannot diff still answers too, with its risk paths not known."""

import json

import pytest

from appkit import mod, sh
from brainfix import FakeGateway, install_brain_dev, make_project, make_service, needs_brainkit, person, run

pytestmark = needs_brainkit
flowview = mod("flowview")
AppError = mod("httpkit").AppError
GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "grp/pay"}}
LEAN_START = {"repo": "r1", "slug": "coupon", "title": "优惠码", "docs": [], "consent": False, "open_session": False}


def started(svc, fn):
    async def go():
        await svc.start()
        try:
            return await fn()
        finally:
            await svc.stop()
    return run(go())


def worktrees(repo):
    return [ln for ln in sh("git", "-C", repo, "worktree", "list", "--porcelain").stdout.splitlines()
            if ln.startswith("worktree ")]


def typo_on_origin(tmp_path, repo):
    """bob pushes a kit.json with "allowed_models": null to main; alice's app fetches it."""
    bob = person(tmp_path, tmp_path / "pay-origin.git", "bob")
    p = bob / ".brain" / "kit.json"
    p.write_text(json.dumps(dict(json.loads(p.read_text()), allowed_models=None), indent=2), encoding="utf-8")
    sh("git", "-C", bob, "commit", "-qam", "a typo in kit.json")
    sh("git", "-C", bob, "push", "-q", "origin", "main")
    sh("git", "-C", repo, "fetch", "-q", "origin")


def test_a_kit_json_brain_kit_cannot_read_turns_fast_off_for_that_repo_only(tmp_path, home):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    other = make_project(tmp_path, name="shop", prefix="SHOP", origin=True,
                         kit={"gitlab": {"base": "https://gitlab.example.com", "project": "grp/shop"}})
    typo_on_origin(tmp_path, repo)
    svc = make_service(tmp_path / "alice", [repo, other])
    before = worktrees(repo)

    async def go():
        status = await svc.status()
        nxt = await svc.intake_next("r1")
        with pytest.raises(AppError) as fast:
            await svc.intake_start(dict(LEAN_START, fast=True))
        lean = await svc.intake_start(dict(LEAN_START))
        return status, nxt, str(fast.value), lean

    status, nxt, fast, lean = started(svc, go)
    views = {r["id"]: r["fast"] for r in status["repos"]}
    assert views == {"r1": {"on": True, "why": flowview.POLICY_BROKEN, "pending": None},
                     "r2": {"on": True, "why": "", "pending": None}}
    assert nxt["fast"] == {"ok": False, "why": flowview.POLICY_BROKEN} and nxt["key"] == "PAY-1"
    assert fast == flowview.POLICY_BROKEN
    assert lean["ok"] is False and lean["output"], "brain start itself refuses the kit.json it branches from"
    assert worktrees(repo) == before, "no start leaves a worktree behind"


def test_a_fast_row_whose_branch_git_cannot_diff_still_answers(tmp_path, home, monkeypatch):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    install_brain_dev(home)
    gw = FakeGateway()
    gw.templates = {"brain-dev-crew"}
    svc = make_service(tmp_path / "alice", [repo], gw=gw)

    def broken(*a, **kw):
        raise mod("bk").brain_error()("git diff failed: fatal: bad object")

    async def go():
        out = await svc.intake_start(dict(LEAN_START, fast=True))
        wt = tmp_path / "home" / "workplace" / "brain-app" / "pay" / "coupon"
        (wt / "coupon.py").write_text("RATE = 0.9\n", encoding="utf-8")
        sh("git", "-C", wt, "add", "-A")
        sh("git", "-C", wt, "commit", "-qm", "coupon")
        svc._repo("r1").invalidate()
        monkeypatch.setattr(mod("bk").m("ci"), "changed_paths", broken)
        rows = (await svc.overview("all"))["rows"]
        todo = await svc.todo()
        status = await svc.status()
        board = await svc.board("r1", out["key"])
        return out, rows, todo, status, board

    out, rows, todo, status, board = started(svc, go)
    row = next(r for r in rows if r["key"] == out["key"])
    assert row["flow"]["id"] == "fast" and row["state"] == "local"
    assert status["repos"][0]["ok"] and isinstance(todo["items"], list)
    cell = next(c for c in board["checks"]["cells"] if c["id"] == "risk")
    assert (cell["status"], cell["detail"]) == ("skip", mod("fastboard").RISK_UNKNOWN)
