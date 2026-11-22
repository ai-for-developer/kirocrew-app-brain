"""A hand-over the runner works on is 运行机在跑 by its bot's commits after the hand-over whenever its MR does not say so
itself, on a person's machine and in 全队交付 alike (deliveries.runner_commits_from). brain-kit's bridge takes a hand-over
whose labels its account may not change (it says so once: _no_label_rights), so the MR keeps the hand-over label alone
while the runner works. Before the runner's first commit both say 等桥核对交接.

Offline: alice's real hand-over (brain handoff) pushed to a bare origin that a fake GitLab serves (relayfix.RepoGitLab),
the runner's commit made as its bot; alice's app reads the MR with her token, the relay with the bridge's."""

import json
from types import SimpleNamespace

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import brain, filled_notes, make_project, make_service, needs_brainkit, run, start_in_worktree
from relayfix import BASE, RepoGitLab
from teamfix import BOT_ENV

pytestmark = needs_brainkit
P = "shop/pay"
KIT = {"gitlab": {"base": BASE, "project": P}, "bridge_user": "brain-bridge"}


@pytest.fixture
def world(tmp_path, home, monkeypatch):
    repo = make_project(tmp_path, origin=True, kit=KIT)
    bare = tmp_path / "pay-origin.git"
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "notify", "notify", "退款通知")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text(encoding="utf-8"))["intent"]
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(encoding="utf-8"), "Status: Construction step 2 of 4"), encoding="utf-8")
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    branch = sh("git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    sh("git", "-C", repo, "push", "-q", "origin", "refs/heads/claim/*:refs/heads/claim/*", branch)
    gl = RepoGitLab()
    gl.project(P, 7, bare)
    gl.mr(P, 20, branch, labels=["ai-handoff"])
    monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
    monkeypatch.setattr(mod("mrinfo"), "TTL_S", 0.0)
    return SimpleNamespace(repo=repo, bare=bare, gl=gl, branch=branch, tmp=tmp_path)


def runner_commits(w):
    """The runner's first commit on the branch, pushed as runnerd's bot; the MR's head moves with it, its labels not."""
    work = w.tmp / "runner"
    sh("git", "clone", "-q", "-b", w.branch, w.bare, work)
    (work / "src").mkdir()
    (work / "src" / "notify.py").write_text("SMS = True\n", encoding="utf-8")
    sh("git", "-C", work, "add", "-A")
    sh("git", "-C", work, "commit", "-qm", "PAY-1: send the SMS", env=BOT_ENV)
    sh("git", "-C", work, "push", "-q", "origin", w.branch)
    w.gl.mr(P, 20, w.branch, labels=["ai-handoff"])


def both(w):
    """PAY-1's (state, waits.text) on alice's 交付总览 and in 全队交付."""
    sh("git", "-C", w.repo, "fetch", "-q", "origin")
    svc = make_service(w.tmp / ("alice-%d" % len(w.gl.calls)), [w.repo], transport=w.gl)

    async def go():
        await svc.start()
        try:
            await svc.overview("all")
            await svc.refresh_mrs()
            for r in svc.repos.values():
                r.invalidate()
            return await svc.overview("all")
        finally:
            await svc.stop()
    mine = next(r for r in run(go())["rows"] if r["key"] == "PAY-1")
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    try:
        d = mod("team.relay").Deliveries(bk.m("rest").GitLab(BASE, "bridge-tok", w.gl), BASE,
                                         lambda p: bk.m("config").load(str(w.repo)))
        team = next(r for r in d.read(P, deployed="", roles={}).rows if r["key"] == "PAY-1")
    finally:
        bk.unload()
    return (mine["state"], mine["waits"]["text"]), (team["state"], team["waits"]["text"])


def test_the_runners_commits_after_a_hand_over_say_it_runs_when_its_mr_has_no_running_label(world):
    assert both(world) == (("handed_over", "等桥核对交接"),) * 2, "nothing from the runner yet: the bridge has it"
    runner_commits(world)
    assert both(world) == (("running", "运行机在跑"),) * 2, "its bot's commit after the hand-over: the runner works on it"
