"""Teammates see each other's runs (backend/core/progress.py): a run branch goes to origin at Lean's checkpoints, and
every repo with a remote is fetched every few minutes. Real git: a bare origin, alice's checkout in her app (her run
started the app's way, Lean's records written by the real AI-DLC Lean), bob's clone in his; GitLab is a transport that
records every call (none is made)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from appkit import commit_file, mod, sh
from brainfix import brain, filled_notes, lean, make_project, make_service, needs_brainkit, person, run, \
    start_in_worktree

progress = mod("progress")
settings_mod = mod("settings")
GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "shop/pay"}}
BRANCH = "ai/alice/PAY-1-refund"


class NoGitLab:
    """The GitLab transport of the app under test: it records each call and answers 404."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        return 404, b'{"message": "no GitLab here"}'


def head(path: Path, ref: str = "HEAD") -> str:
    return sh("git", "-C", path, "rev-parse", ref).stdout.strip()


def on_origin(origin: Path, branch: str = BRANCH) -> str:
    return sh("git", "-C", origin, "rev-parse", "-q", "--verify", "refs/heads/" + branch, check=False).stdout.strip()


def checkpoint(wt: Path, *lean_args: str) -> str:
    """A Lean record, committed as the session would commit it; the new tip."""
    lean(wt, *lean_args)
    sh("git", "-C", wt, "add", "-A")
    sh("git", "-C", wt, "commit", "-q", "-m", "lean: " + " ".join(lean_args[:2]))
    return head(wt)


def requirements_doc(wt: Path) -> None:
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text(encoding="utf-8"))["intent"]
    (wt / "aidlc-docs" / intent / "inception" / "requirements" / "requirements.md").write_text(
        "# Requirements\n\nSize: small\n\n- AC-1 退款\n", encoding="utf-8")


def tick(svc) -> None:
    """One round of the background loop, on facts read now (a scan is otherwise reused for a few seconds)."""
    for r in svc.repos.values():
        r.invalidate()
    svc.sync_tick()


def rows_of(view):
    return {r["key"]: r for r in view["rows"]}


def started(svc, fn):
    async def go():
        await svc.start()
        try:
            return await fn()
        finally:
            await svc.stop()
    return run(go())


@pytest.fixture
def team(tmp_path, home):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    gl = NoGitLab()
    svc = make_service(tmp_path / "alice", [repo], transport=gl)
    return SimpleNamespace(repo=repo, wt=wt, origin=tmp_path / "pay-origin.git", tmp=tmp_path, svc=svc, gl=gl,
                           marks=tmp_path / "alice" / "data" / "pushes.json")


# ---- the decision, pure ------------------------------------------------------------------------------------------ #

CTX = progress.Ctx(me="alice", prefix="ai", default_branch="main", team_repo=True, auto_push=True, runner=False)


def branch(**kw):
    base = dict(name=BRANCH, owner="alice", worktree="/w/refund", local_sha="b" * 40, origin_sha="a" * 40, merged=False,
                closed=False, handed_over=False, checkpoint=lambda: "c2", behind=lambda: False)
    base.update(kw)
    return progress.Branch(**base)


def never():
    raise AssertionError("read although an earlier rule answered")


@pytest.mark.parametrize("why, b, mark, ctx", [
    ("RUNNER", branch(), None, replace(CTX, runner=True)),
    ("LOCAL_ONLY", branch(), None, replace(CTX, team_repo=False)),
    ("OFF", branch(), None, replace(CTX, auto_push=False)),
    ("NOT_RUN_BRANCH", branch(name="claim/PAY-1"), None, CTX),
    ("NOT_RUN_BRANCH", branch(name="main"), None, CTX),
    ("NOT_MINE", branch(owner="bob", checkpoint=never, behind=never), None, CTX),
    ("NOT_MINE", branch(), None, replace(CTX, me=None)),
    ("MERGED", branch(merged=True), None, CTX),
    ("CLOSED", branch(closed=True), None, CTX),
    ("NOT_HERE", branch(worktree=None), None, CTX),
    ("HANDED_OVER", branch(handed_over=True), None, CTX),
    ("NOT_ON_ORIGIN", branch(origin_sha=None), None, CTX),
    ("UP_TO_DATE", branch(origin_sha="b" * 40, checkpoint=never, behind=never), None, CTX),
    ("NO_CHECKPOINT", branch(checkpoint=lambda: None), None, CTX),
    ("NOTHING_NEW", branch(behind=never), progress.Mark(checkpoint="c2"), CTX),
    ("BEHIND", branch(behind=lambda: True), progress.Mark(checkpoint="c1"), CTX),
])
def test_push_due_says_why_not_in_the_order_of_its_rules(why, b, mark, ctx):
    assert progress.push_due(b, mark, ctx) is progress.Skip[why]


def test_push_due_pushes_the_tip_with_a_new_checkpoint():
    assert progress.push_due(branch(), progress.Mark(checkpoint="c1"), CTX) == progress.Push("b" * 40, "c2")
    assert progress.push_due(branch(), None, CTX) == progress.Push("b" * 40, "c2"), "nothing pushed from here yet"


def test_the_settings_check_the_interval_and_keep_auto_push_for_listed_repos_only(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    clean, probs = settings_mod.check({"repos": [str(a)], "fetch_every_min": 0, "auto_push": {str(a): False, str(b): False}})
    assert probs == [] and clean["fetch_every_min"] == 0 and clean["auto_push"] == {str(a.resolve()): False}
    assert settings_mod.defaults()["fetch_every_min"] == 5 and settings_mod.defaults()["auto_push"] == {}
    for bad in (-1, 121, 2.5, "5", True):
        _, probs = settings_mod.check({"fetch_every_min": bad})
        assert probs == ["「每隔几分钟从远端取最新」要填 0 到 120 的整数（填 0 就不自动取）"], bad
    _, probs = settings_mod.check({"repos": [str(a)], "auto_push": {str(a): "no"}})
    assert probs == ["「过关卡时把进度推到远端」的格式不对：刷新页面再存一次"]


# ---- the fetch turns, on a fake clock ---------------------------------------------------------------------------- #

class SlowRepo:
    """A repo whose fetch waits until the test lets it finish."""

    def __init__(self, answers=None) -> None:
        self.path, self.name = "/w/pay", "pay"
        self.calls = 0
        self.go = threading.Event()
        self.answers = list(answers or [])

    def has_origin(self):
        return True

    def fetch(self):
        self.calls += 1
        self.go.wait(5)
        return self.answers.pop(0) if self.answers else {"ok": True, "text": "已经从远端取到最新。", "raw": ""}


def test_a_fetch_turn_comes_every_n_minutes_and_a_repo_still_fetching_is_skipped(caplog):
    t = [0.0]
    f = progress.Fetches(logging.getLogger("brain-test"), clock=lambda: t[0], now=lambda: 1000.0)
    repo = SlowRepo()
    assert f.take_turn(repo, 300)
    first = threading.Thread(target=f.fetch, args=(repo, False))
    first.start()
    for _ in range(200):
        if repo.calls:
            break
        time.sleep(0.01)
    t[0] = 299.0
    assert not f.take_turn(repo, 300)
    t[0] = 300.0
    assert f.take_turn(repo, 300)
    assert f.fetch(repo, False) is None and repo.calls == 1, "still fetching: this turn is skipped"
    repo.go.set()
    first.join(5)
    assert f.last(repo.path) == {"at": 1000.0, "ok": True, "text": "已经从远端取到最新。", "raw": ""}
    t[0] = 600.0
    assert f.take_turn(repo, 300) and f.fetch(repo, False)["ok"] and repo.calls == 2
    t[0] = 9999.0
    assert not f.take_turn(repo, 0), "0 minutes: never by itself"


def test_a_failed_fetch_is_said_once_until_it_works_again(caplog):
    down = {"ok": False, "text": "从远端取最新没成功：连不上远端：检查网络或 VPN，再点一次。", "raw": "fatal: unable to access"}
    up = {"ok": True, "text": "已经从远端取到最新。", "raw": ""}
    repo = SlowRepo([down, down, down, up, down])
    repo.go.set()
    f = progress.Fetches(logging.getLogger("brain-test"), clock=lambda: 0.0)
    with caplog.at_level(logging.WARNING, logger="brain-test"):
        for _ in range(5):
            f.fetch(repo, False)
    said = [r.getMessage() for r in caplog.records if "fetching" in r.getMessage()]
    assert len(said) == 2 and all("连不上远端" in s for s in said), said
    assert f.last(repo.path)["ok"] is False and f.last(repo.path)["text"] == down["text"]


# ---- the pushes, with real git ----------------------------------------------------------------------------------- #

@needs_brainkit
def test_a_checkpoint_pushes_the_run_branch_once_and_a_commit_without_one_does_not(team):
    w = team
    start = on_origin(w.origin)

    async def go():
        tick(w.svc)
        commit_file(w.wt, "src/refund.py", "def refund():\n    pass\n", "code")
        tick(w.svc)
        code_only = on_origin(w.origin)
        tip = checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        tick(w.svc)
        pushed, marks = on_origin(w.origin), w.marks.read_text(encoding="utf-8")
        tick(w.svc)
        again = on_origin(w.origin), w.marks.read_text(encoding="utf-8")
        return tip, code_only, pushed, marks, again, rows_of(await w.svc.overview("all"))["PAY-1"]

    tip, code_only, pushed, marks, again, row = started(w.svc, go)
    assert start and code_only == start, "a commit with no checkpoint stays here"
    assert pushed == tip == head(w.wt), "the checkpoint's commit is on origin"
    assert again == (pushed, marks), "nothing new: the second round pushes nothing and writes nothing"
    mark = json.loads(marks)[str(w.repo.resolve())][BRANCH]
    assert mark["sha"] == tip and mark["error"] is None and isinstance(mark["at"], float)
    assert row["progress_pushed_at"] == mark["at"]
    assert w.gl.calls == [], "no GitLab call: no label, no MR"
    refs = sh("git", "-C", w.origin, "for-each-ref", "--format=%(refname)").stdout.split()
    assert sorted(refs) == ["refs/heads/" + BRANCH, "refs/heads/claim/PAY-1", "refs/heads/main"], refs
    w.svc.sync.push_tick([], time.time(), set(w.svc.settings["repos"]))      # a round that saw no repo loaded yet
    assert json.loads(w.marks.read_text(encoding="utf-8")) == json.loads(marks), "a listed repo keeps its marks"
    w.svc.sync.push_tick([], time.time(), set())
    assert json.loads(w.marks.read_text(encoding="utf-8")) == {}, "a repo taken off the list takes its marks along"


@needs_brainkit
def test_the_running_app_pushes_a_checkpoint_and_fetches_by_itself(team, monkeypatch):
    w = team
    monkeypatch.setattr(progress, "TICK_S", 0.1)

    async def go():
        tip = checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        for _ in range(200):
            if on_origin(w.origin) == tip and w.svc.sync.fetches.last(str(w.repo.resolve())):
                break
            await asyncio.sleep(0.1)
        return tip

    tip = started(w.svc, go)
    assert on_origin(w.origin) == tip, "the loop pushed the checkpoint with no page open"
    assert w.svc.sync.fetches.last(str(w.repo.resolve()))["ok"] is True, "and fetched on its first round"


@needs_brainkit
def test_someone_elses_run_branch_checked_out_here_is_never_pushed(team):
    w = team
    bob = person(w.tmp, w.origin, "bob")
    brain(bob, "start", "export", "--title", "导出")
    theirs = sh("git", "-C", bob, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert theirs.startswith("ai/bob/")
    sh("git", "-C", w.repo, "fetch", "-q", "origin")
    other = w.tmp / "home" / "workplace" / "brain-app" / "pay" / "bobs"
    sh("git", "-C", w.repo, "worktree", "add", "-q", other, theirs)
    before = on_origin(w.origin, theirs)

    async def go():
        tick(w.svc)
        checkpoint(other, "stage", "skip", "reverse-engineering", "--reason", "alice looked at it")
        mine = checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        tick(w.svc)
        return mine

    mine = started(w.svc, go)
    assert on_origin(w.origin, theirs) == before, "bob's branch is bob's to push"
    assert head(other) != before
    assert on_origin(w.origin) == mine, "the same round pushes alice's own"


@needs_brainkit
def test_auto_push_off_a_repo_done_only_here_or_an_unknown_name_never_pushes(tmp_path, home):
    pay = make_project(tmp_path, origin=True, kit=GITLAB)
    tools = make_project(tmp_path, name="tools", prefix="TOOL", origin=True)     # a remote, no GitLab: done only here
    root = home / "workplace" / "brain-app"
    wt_pay = start_in_worktree(pay, root / "pay" / "refund", "refund", "退款")
    wt_tools = start_in_worktree(tools, root / "tools" / "lint", "lint", "检查")
    svc = make_service(tmp_path, [pay, tools], transport=NoGitLab())
    tools_branch = sh("git", "-C", wt_tools, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    starts = on_origin(tmp_path / "pay-origin.git"), on_origin(tmp_path / "tools-origin.git", tools_branch)

    async def go():
        saved = await svc.save_settings({"auto_push": {str(pay): False}})
        checkpoint(wt_pay, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        checkpoint(wt_tools, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        tick(svc)
        held = on_origin(tmp_path / "pay-origin.git"), on_origin(tmp_path / "tools-origin.git", tools_branch)
        await svc.save_settings({"auto_push": {}})
        tick(svc)
        let_go = on_origin(tmp_path / "pay-origin.git")
        sh("git", "-C", pay, "config", "--unset", "brain.user")
        requirements_doc(wt_pay)
        nameless = checkpoint(wt_pay, "stage", "done", "requirements", "--note", "AC-1")
        tick(svc)
        return saved, held, let_go, nameless, await svc.status()

    saved, held, let_go, nameless, st = started(svc, go)
    assert saved["settings"]["auto_push"] == {str(pay.resolve()): False}
    assert all(starts) and held == starts, "off, and done only here: nothing pushed"
    assert on_origin(tmp_path / "tools-origin.git", tools_branch) == starts[1] != head(wt_tools)
    assert let_go != held[0], "turned on again, the checkpoint goes up"
    assert on_origin(tmp_path / "pay-origin.git") == let_go != nameless, "a name the app cannot tell: not pushed"
    sync = {r["name"]: r["sync"] for r in st["repos"]}
    assert sync["tools"]["auto_push"] is None and sync["tools"]["push_why"] == "", "no push setting for it"
    assert sync["pay"]["auto_push"] is True and sync["pay"]["push_why"] == progress.NO_NAME
    assert sync["pay"]["push_cmd"] == "git -C %s config brain.user 你的GitLab用户名" % pay.resolve()


@needs_brainkit
def test_a_hand_over_is_left_to_brain_handoff_and_handoff_still_works_after_an_early_push(team):
    w = team
    intent = json.loads((w.wt / "runs" / "PAY-1" / "run.json").read_text(encoding="utf-8"))["intent"]

    async def go():
        early = checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        tick(w.svc)
        on_early = on_origin(w.origin)
        requirements_doc(w.wt)
        checkpoint(w.wt, "stage", "done", "requirements", "--note", "AC-1")     # not pushed yet when the hand-over comes
        st = w.wt / "aidlc-docs" / intent / "aidlc-state.md"
        st.write_text(filled_notes(st.read_text(encoding="utf-8"), "Status: Construction step 2 of 4"), encoding="utf-8")
        sh("git", "-C", w.wt, "commit", "-qam", "notes")
        kept = brain(w.wt, "handoff", "--no-push")
        held = head(w.wt)
        tick(w.svc)
        on_held = on_origin(w.origin)
        handoff = brain(w.wt, "handoff", check=False)
        tick(w.svc)
        return early, on_early, kept, held, on_held, handoff

    early, on_early, kept, held, on_held, handoff = started(w.svc, go)
    assert on_early == early, "the checkpoint went up before the hand-over"
    assert "not pushed" in kept.stdout and on_held == early, \
        "brain handoff --no-push: the hand-over, and the checkpoint under it, stay here"
    assert handoff.returncode == 1 and "NOT labelled" in handoff.stdout, handoff.stdout + handoff.stderr
    tip = head(w.wt)
    rec = json.loads((w.wt / "runs" / "PAY-1" / "run.json").read_text(encoding="utf-8"))
    assert on_origin(w.origin) == tip and head(w.wt, "HEAD^") == rec["handoff"]["local_head"] == held
    assert sh("git", "-C", w.origin, "merge-base", "--is-ancestor", early, tip, check=False).returncode == 0
    assert w.gl.calls == []


@needs_brainkit
def test_a_refused_push_is_kept_once_listed_in_todo_never_forced_and_tried_again_at_the_next_checkpoint(team, caplog):
    w = team
    bob = person(w.tmp, w.origin, "bob")
    sh("git", "-C", bob, "switch", "-q", BRANCH)
    commit_file(bob, "NOTE.md", "bob was here\n", "bob's commit")
    sh("git", "-C", bob, "push", "-q", "origin", "HEAD")
    bobs = head(bob)

    async def go():
        tick(w.svc)
        checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        w.svc.now = lambda: 1000.0
        with caplog.at_level(logging.WARNING, logger="brain-test"):
            tick(w.svc)
            w.svc.now = lambda: 2000.0
            tick(w.svc)
            first = await w.svc.todo()
            w.svc.now = lambda: 3000.0
            requirements_doc(w.wt)
            checkpoint(w.wt, "stage", "done", "requirements", "--note", "AC-1")
            tick(w.svc)
        second = await w.svc.todo()
        sh("git", "-C", w.wt, "pull", "-q", "--no-rebase", "origin", BRANCH)
        checkpoint(w.wt, "request", "REQUIREMENTS")
        tick(w.svc)
        return first, second, await w.svc.todo(), (await w.svc.status())["counts"]["todo"]

    first, second, after, count = started(w.svc, go)
    refused = [r.getMessage() for r in caplog.records if "origin refused" in r.getMessage()]
    assert len(refused) == 2, "one try per checkpoint, none in between: %s" % refused
    items = [i for i in first["items"] if i["kind"] == "push_failed"]
    assert len(items) == 1 and items[0]["key"] == "PAY-1" and items[0]["since"] == 1000.0, first["items"]
    it = items[0]
    assert it["text"] == progress.PUSH_WHY["rejected"][0] and it["todo"] == progress.PUSH_WHY["rejected"][1]
    assert "[rejected]" in it["raw"] and it["raw"].endswith("pull --no-rebase origin %s" % BRANCH) and "做法：git -C " in it["raw"]
    assert [i["since"] for i in second["items"] if i["kind"] == "push_failed"] == [3000.0]
    assert not [i for i in after["items"] if i["kind"] == "push_failed"] and count == len(after["items"])
    tip = on_origin(w.origin)
    assert tip == head(w.wt) and sh("git", "-C", w.origin, "merge-base", "--is-ancestor", bobs, tip,
                                    check=False).returncode == 0, "bob's commit is still there: nothing was forced"


@needs_brainkit
def test_bobs_app_fetches_by_itself_and_shows_alices_progress_on_her_own_machine(team):
    w = team
    bob = person(w.tmp, w.origin, "bob")
    bsvc = make_service(w.tmp / "bob-app", [bob], transport=NoGitLab())

    async def alice():
        lean(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        requirements_doc(w.wt)
        checkpoint(w.wt, "stage", "done", "requirements", "--note", "AC-1")
        tick(w.svc)
        return rows_of(await w.svc.overview("all"))["PAY-1"]

    async def bobs():
        before = rows_of(await bsvc.overview("all"))["PAY-1"]
        tick(bsvc)          # its first turn: a fetch, in the background
        for _ in range(500):
            if bsvc.sync.fetches.last(str(bob.resolve())):
                break
            await asyncio.sleep(0.02)
        return before, rows_of(await bsvc.overview("all"))["PAY-1"], await bsvc.status()

    hers = started(w.svc, alice)
    before, after, st = started(bsvc, bobs)
    assert hers["stage"] and before["stage"] != hers["stage"]
    assert after["stage"] == hers["stage"] and after["lean"] == hers["lean"], (after["lean"], hers["lean"])
    assert after["owner"] == "alice" and after["waits"]["text"] == "alice 在自己的机器上做"
    assert st["repos"][0]["sync"]["fetch"]["ok"] is True
