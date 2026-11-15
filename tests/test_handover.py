"""交接给运行机 (backend/core/handover.py): what `brain handoff` (brain-kit 223ced5 cli.cmd_handoff) said, as the person
reads it: how far it got (state), one plain line, what to do next, the MR, its warnings as rows, and its own words with
credentials hidden. The lines are brain-kit's own, copied from cli.py."""

import pytest

from appkit import BRAINKIT, mod

handover, problems = mod("handover"), mod("problems")

BASE, PROJECT = "https://gitlab.example.com", "grp/repo"
MR5 = BASE + "/grp/repo/-/merge_requests/5"
WROTE = "Wrote runs/PAY-1/MR.md from the records into the hand-over commit (`brain mr-desc` regenerates it).\n"
HANDED = "Handed off: MR !5 labelled 'ai-handoff' at 0123456789ab. The bridge checks it and the team runner continues.\n"
OLD_ADAPTER = (".kiro/brain/lean-hook.py is an older adapter (version 0, needs 3): it does not refuse tool calls after the "
               "kit's own files (.kiro/, .brain/kit.json, gates/) were changed, and the team runner could not write "
               "runs/<KEY>/park.json, its only way to ask a person, before the Lean approvals; run `brain install` and "
               "commit it on main through an MR")


def said(code, out="", err=""):
    r = handover.said(code, out, err, base=BASE, project=PROJECT)
    assert set(r) == {"ok", "state", "text", "todo", "mr", "warnings", "raw", "code"}, r
    assert r["state"] in handover.STATES and r["code"] == code and r["ok"] is (r["state"] == "handed")
    assert problems.chinese(r["text"]), r["text"]
    assert not r["todo"] or problems.chinese(r["todo"]), r["todo"]
    return r


def test_handed_over_names_the_mr_and_keeps_brain_kits_words():
    r = said(0, WROTE + HANDED)
    assert r["state"] == "handed" and r["mr"] == {"iid": 5, "url": MR5}
    assert "MR !5" in r["text"] and r["warnings"] == []
    assert "Handed off: MR !5" in r["raw"]


def test_committed_but_not_pushed_without_a_remote():
    r = said(0, WROTE + "Committed the hand-over; not pushed. Push ai/alice/PAY-1-refund and add label 'ai-handoff' to its "
                        "MR (its description is runs/PAY-1/MR.md).\n")
    assert (r["state"], r["mr"]) == ("committed", None) and r["todo"]


@pytest.mark.parametrize("missing, word", [
    ("a GitLab token on this machine (GITLAB_TOKEN, or ~/.brain/gitlab-token)", "令牌"),
    ("gitlab.base in .brain/kit.json; gitlab.project in .brain/kit.json", "GitLab"),
])
def test_pushed_but_not_labelled_for_what_this_machine_lacks(missing, word):
    r = said(1, WROTE + "Pushed ai/alice/PAY-1-refund, but NOT labelled: missing %s. Nothing was handed over: add the "
                        "label 'ai-handoff' to its MR by hand, or set that up and run `brain handoff` again.\n" % missing)
    assert r["state"] == "not_labelled" and word in r["text"] and r["todo"]


@pytest.mark.parametrize("why", [
    "GitLab has not recorded the push of 0123456789ab on the MR within 60s",
    "the MR head is now abcdef012345, not 0123456789ab which this hand-over pushed: someone pushed to the branch after you",
])
def test_pushed_but_not_labelled_on_the_mr(why):
    r = said(1, WROTE + "Pushed 0123456789ab, but NOT labelled MR !5: %s.\nNothing was handed over. Look at the MR: if its "
                        "latest version is 0123456789ab and nothing came after it, add the label 'ai-handoff' by hand; if "
                        "someone pushed on top, pull, look at their commit and run `brain handoff` again.\n" % why)
    assert r["state"] == "not_labelled" and r["mr"] == {"iid": 5, "url": MR5} and "MR !5" in r["text"] and r["todo"]


def test_the_two_reasons_a_pushed_mr_is_not_labelled_read_differently():
    lines = ["Pushed 0123456789ab, but NOT labelled MR !5: %s.\nNothing was handed over.\n" % why for why in (
        "GitLab has not recorded the push of 0123456789ab on the MR within 60s",
        "the MR head is now abcdef012345, not 0123456789ab which this hand-over pushed: someone pushed to the branch")]
    a, b = (said(1, x) for x in lines)
    assert a["text"] != b["text"] and a["todo"] != b["todo"]


@pytest.mark.parametrize("message, word", [
    ("this run belongs to bob; only the owner hands it off", "bob"),
    ("hand-over note not ready (aidlc-docs/261115-pay-1-refund/aidlc-state.md): Session notes still say 'Status: "
     "Inception: starting': update Status; Session notes' '### Next' is still Lean's template: write what the next "
     "session does", "交接记录"),
    ("commit your work first:\n   M src/refund.py\n  M  README.md", "2 个文件"),
    ("the team runner could not run this branch: " + OLD_ADAPTER, "运行机"),
    ("not handed over: Lean would refuse this run's RELEASE approval later. runs/ 下的文件（MR.md、park.json）算在 Lean "
     "的代码指纹里：verify 之后写它们，Lean 会当成代码改了。修法：`brain install <项目目录>` 会把 \"runs/**\" 加进 "
     "aidlc-docs/lean.json", "Lean"),
    ("current branch main is not a run branch (ai/<you>/<KEY>-<slug>)", "main"),
    ("package PKG-PAY-1-1 in /Users/alice/.brain/intake does not match run.json", "PKG-PAY-1-1"),
])
def test_a_refusal_says_why_and_what_to_do_nothing_changed(message, word):
    r = said(2, "", "brain: %s\n" % message)
    assert (r["state"], r["mr"]) == ("refused", None) and word in r["text"] and r["todo"]
    assert problems.GENERIC_TEXT["FAIL"] not in r["text"]
    assert message.splitlines()[0] in r["raw"]


def test_an_old_adapter_on_the_branch_is_fixed_on_the_default_branch_first():
    r = said(2, "", "brain: the team runner could not run this branch: %s\n" % OLD_ADAPTER)
    assert "旧版" in r["text"]
    assert "「%s」" % problems.FIX_BUTTON["kit"] in r["todo"] and "默认分支" in r["todo"]


def test_brain_kit_not_knowing_the_person_points_at_the_settings_not_at_an_input_here():
    r = said(2, "", "brain: who are you? this repo uses GitLab (https://gitlab.example.com): set GITLAB_TOKEN (preferred) "
                    "or `git config brain.user <your GitLab username>`\n")
    assert r["state"] == "refused" and "设置" in r["todo"] and "下面" not in r["todo"]


def test_a_refusal_no_table_knows_is_said_by_problems_row():
    message = "something a newer brain-kit says"
    r = said(2, "", "brain: %s\n" % message)
    assert r["state"] == "refused" and problems.row("FAIL", message)["text"] in r["text"] and r["todo"]
    assert message in r["raw"]


def test_after_the_hand_over_commit_a_failed_push_left_it_committed():
    err = ("brain: command failed (1): git -C /w push -q -u origin ai/alice/PAY-1-refund\nTo /x.git\n ! [rejected]  "
           "ai/alice/PAY-1-refund -> ai/alice/PAY-1-refund (fetch first)\nerror: failed to push some refs to '/x.git'\n")
    r = said(2, WROTE, err)
    assert r["state"] == "committed" and r["todo"]


def test_after_the_push_a_gitlab_error_left_it_not_labelled():
    r = said(2, WROTE, "brain: PUT /projects/grp%2Frepo/merge_requests/5 -> HTTP 403: {\"message\":\"403 Forbidden\"}\n")
    assert r["state"] == "not_labelled" and "403" in r["text"] and r["todo"]


def test_a_gitlab_error_before_anything_changed_is_a_refusal():
    r = said(2, "", "brain: GET /user -> HTTP 401: {\"message\":\"401 Unauthorized\"}\n")
    assert r["state"] == "refused" and "401" in r["text"]


def test_warnings_are_rows_in_words_and_the_hand_over_still_happened():
    out = ("WARN: this run changes risk paths (src/pay/ledger.py; gates/risk-paths) and no DESIGN approval signed by the "
           "bridge covers it yet: brain-ci check-run will fail the MR until one does. A DESIGN approved in your own Lean "
           "chat does not count. The runner asks for it through the bridge (lean request DESIGN, then park.json with "
           "lean_request DESIGN) before it writes more code.\n"
           "WARN: intake store has no origin: packages are committed locally only\n"
           "WARN: something a newer brain-kit warns about\n" + WROTE + HANDED)
    r = said(0, out)
    assert r["state"] == "handed" and len(r["warnings"]) == 3
    for w in r["warnings"]:
        assert set(w) == {"level", "text", "todo", "fix", "repo", "raw"} and w["level"] == "WARN" and w["fix"] is None
        assert problems.chinese(w["text"]) and w["raw"]
    risk, intake, new = r["warnings"]
    assert "src/pay/ledger.py" in risk["text"] and "brain-intake" in intake["text"] + intake["todo"]
    assert new["text"] == problems.GENERIC_TEXT["WARN"] and new["raw"] == "something a newer brain-kit warns about"


def test_stopped_at_the_time_limit():
    r = said(124, WROTE, "\n（超过 300 秒，停掉了）")
    assert r["state"] == "timeout" and "300" in r["text"] and r["todo"]


@pytest.mark.parametrize("code, err", [
    (127, "跑不了 brain：[Errno 2] No such file or directory"),
    (1, "Traceback (most recent call last):\n  File \"brainkit/cli.py\", line 1, in <module>\nKeyError: 'x'\n"),
    (0, ""),
])
def test_brain_kit_that_could_not_run_or_said_nothing_known_failed(code, err):
    r = said(code, "", err)
    assert r["state"] == "failed" and r["todo"]


def test_credentials_in_brain_kits_words_are_hidden():
    err = ("brain: command failed (128): git -C /w push -q -u origin ai/alice/PAY-1-refund\nfatal: unable to access "
           "'https://alice:glpat-abcdefghijklmnopqrstu@gitlab.example.com/grp/repo.git/': Could not resolve host\n")
    r = said(2, WROTE, err)
    assert "glpat-abcdefghijklmnopqrstu" not in r["raw"] and "[已隐去]@gitlab.example.com" in r["raw"]


# ---- the app's own refusals, before brain-kit runs ------------------------------------------------------------------ #

RUN = {"owner": "alice", "branch": "ai/alice/PAY-1-refund", "intent": "261115-pay-1-refund"}


def fact(**kw):
    return dict({"key": "PAY-1", "run": dict(RUN), "owner": "alice", "merged": False, "worktree": "/w/refund"}, **kw)


def row(state="local", do="reply"):
    return {"state": state, "state_label": state, "waits": {"do": do, "text": "等桥核对交接"}, "local_only": False}


@pytest.mark.parametrize("f, r, kw, code, status", [
    (fact(run=None), row("claimed", "start"), {}, "not_started", 409),
    (fact(merged=True), row("merged", "watch"), {}, "merged", 409),
    (fact(run=dict(RUN, parent="PAY-9")), row(), {}, "is_child", 409),
    (fact(), row(), {"local_only": True}, "local_only", 409),
    (fact(run=dict(RUN, handoff={"by": "alice"})), row("handed_over", "none"), {}, "handed_over", 409),
    (fact(), row(), {"me": "bob"}, "not_owner", 403),
    (fact(worktree=None), row(), {}, "no_worktree", 409),
    (fact(), row(), {"busy": True}, "busy", 409),
])
def test_the_app_refuses_in_words_before_running_anything(f, r, kw, code, status):
    why = handover.why_not(f, r, **dict({"me": "alice", "local_only": False, "busy": False, "pushed": True}, **kw))
    assert why is not None and why[1:] == (code, status) and problems.chinese(why[0])


@pytest.mark.parametrize("state, do, pushed", [("local", "reply", True), ("local", "approve", False),
                                               ("handed_over", "label_handoff", True), ("rejected", "redo_handoff", True),
                                               ("handed_over", "none", False)])
def test_the_app_lets_it_run_for_a_local_run_and_for_a_hand_over_to_do_again(state, do, pushed):
    """The last: the hand-over commit was made but its push failed. The row reads it as handed over (run.json says so),
    yet the remote has nothing: clicking again pushes and labels it."""
    f = fact(run=dict(RUN, handoff={"by": "alice"})) if state != "local" else fact()
    assert handover.why_not(f, row(state, do), me="alice", local_only=False, busy=False, pushed=pushed) is None


def test_a_person_the_app_cannot_name_is_left_to_brain_kit_which_checks_the_owner_itself():
    """A GitLab-token user has no brain.user: the app does not know the name, brain-kit asks GitLab."""
    assert handover.why_not(fact(), row(), me=None, local_only=False, busy=False, pushed=True) is None


# ---- through the app: a real `brain handoff` in temp repos ------------------------------------------------------- #

import asyncio  # noqa: E402
import json  # noqa: E402

from brainfix import (FakeGateway, filled_notes, install_brain_dev, make_project, make_service, needs_brainkit,  # noqa: E402
                      run, sh, start_in_worktree)
from http_fakes import AppRoute, FakeWeb, call  # noqa: E402
from teamfix import BRANCH, GitLab, P, Served  # noqa: E402

AppError = mod("httpkit").AppError
HANDOFF = "PAY-1: hand off to team runner"


def project(tmp_path, home, *, base="https://gitlab.example.com", origin=True, notes=True):
    """repo (r1) with brain-kit and, unless told otherwise, GitLab in kit.json and an origin; PAY-1 started by alice in
    a worktree (branch BRANCH, pushed), its hand-over note filled in and committed."""
    repo = make_project(tmp_path, name="repo", origin=origin, kit={"gitlab": {"base": base, "project": P}} if base else None)
    if origin:
        sh("git", "-C", repo, "push", "-q", "origin", "main")
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "repo" / "refund", "refund", "退款")
    if notes:
        intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
        st = wt / "aidlc-docs" / intent / "aidlc-state.md"
        st.write_text(filled_notes(st.read_text(), "Status: Construction step 2 of 4"))
        sh("git", "-C", wt, "commit", "-qam", "notes")
    return repo, wt


def head(path, ref="HEAD"):
    return sh("git", "-C", path, "rev-parse", ref, check=False).stdout.strip()


def subjects(wt):
    return sh("git", "-C", wt, "log", "--format=%s", "main..HEAD").stdout.splitlines()


def handoff(svc, *rounds):
    """svc.handoff("r1", "PAY-1") as often as each round says, the clicks of a round at once, a round after the one
    before it; an AppError is the answer, not raised. Returns the answers and the detail page after them."""
    async def one():
        try:
            return await svc.handoff("r1", "PAY-1")
        except AppError as exc:
            return exc

    async def go():
        await svc.start()
        try:
            got = []
            for n in rounds:
                got += await asyncio.gather(*(one() for _ in range(n)))
            return got, await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    return run(go())


def served_gitlab(tmp_path, monkeypatch):
    """The GitLab fake over HTTP on 127.0.0.1, knowing alice by her token."""
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(k, "127.0.0.1,localhost")
    gl = GitLab(tmp_path / "repo-origin.git")
    gl.labels, gl.events = [], []
    gl.who["alice-tok"] = "alice"
    return gl, Served(gl)


def alice_token(home):
    tok = home / ".brain" / "gitlab-token"
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text("alice-tok\n")
    tok.chmod(0o600)


@needs_brainkit
def test_handed_over_through_gitlab_once_for_two_clicks_and_not_again(tmp_path, home, monkeypatch):
    """The whole hand-over without a terminal: brain-kit pushes the branch and labels MR !5 on a GitLab (the fake,
    over HTTP on 127.0.0.1) as alice, by her token. Two clicks at once run it once and get the same answer; once the MR
    has its label, a third click is refused before anything runs."""
    gl, srv = served_gitlab(tmp_path, monkeypatch)
    try:
        repo, wt = project(tmp_path, home, base=srv.base)
        alice_token(home)
        svc = make_service(tmp_path, [repo])
        (first, second, third), detail = handoff(svc, 2, 1)
    finally:
        srv.stop()
    assert first == second, (first, second)
    assert first["state"] == "handed" and first["ok"] and first["code"] == 0, first
    assert first["mr"] == {"iid": 5, "url": srv.base + "/grp/repo/-/merge_requests/5"}
    assert "ai-handoff" in gl.labels
    assert subjects(wt).count(HANDOFF) == 1 and head(tmp_path / "repo-origin.git", BRANCH) == head(wt)
    assert isinstance(third, AppError) and (third.code, third.status) == ("handed_over", 409), third
    assert detail["row"]["state"] == "handed_over"
    assert any(i["kind"] == "app" and i["label"] == "交接：" + first["text"] for i in detail["timeline"]["items"])


@needs_brainkit
def test_a_gitlab_token_user_without_brain_user_hands_over_from_the_page(tmp_path, home, monkeypatch):
    """Settings says the token names this person, and has no name to fill in: the hand-over runs, as in a terminal."""
    gl, srv = served_gitlab(tmp_path, monkeypatch)
    try:
        repo, wt = project(tmp_path, home, base=srv.base)
        sh("git", "-C", repo, "config", "--unset", "brain.user")
        alice_token(home)
        (r,), detail = handoff(make_service(tmp_path, [repo]), 1)
    finally:
        srv.stop()
    assert not isinstance(r, AppError), r
    assert r["state"] == "handed" and r["ok"] and "ai-handoff" in gl.labels, r
    assert detail["row"]["state"] == "handed_over"


def nothing_went_out(row):
    """The row of a hand-over commit that is on this machine only: not handed over, the button offered again."""
    w = row["waits"]
    return (row["state"] == "local" and w["do"] == "push_handoff" and w["who"] == "you" and "没推上去" in w["text"]
            and "「%s」" % handover.BUTTON in w["text"] and "等桥" not in w["text"])


@needs_brainkit
def test_after_a_failed_push_the_row_says_nothing_went_out_and_the_next_click_hands_it_over(tmp_path, home, monkeypatch):
    gl, srv = served_gitlab(tmp_path, monkeypatch)
    try:
        repo, wt = project(tmp_path, home, base=srv.base)
        alice_token(home)
        sh("git", "-C", wt, "remote", "set-url", "--push", "origin", str(tmp_path / "gone.git"))
        svc = make_service(tmp_path, [repo])

        async def go():
            await svc.start()
            try:
                first = await svc.handoff("r1", "PAY-1")
                d1 = await svc.requirement("r1", "PAY-1")
                sh("git", "-C", wt, "config", "--unset", "remote.origin.pushurl")
                return first, d1, await svc.handoff("r1", "PAY-1"), await svc.requirement("r1", "PAY-1")
            finally:
                await svc.stop()

        first, d1, second, d2 = run(go())
    finally:
        srv.stop()
    assert first["state"] == "committed" and "「%s」" % handover.BUTTON in first["todo"], first
    assert nothing_went_out(d1["row"]), d1["row"]
    assert second["state"] == "handed" and "ai-handoff" in gl.labels, second
    assert d2["row"]["state"] == "handed_over" and head(tmp_path / "repo-origin.git", BRANCH) == head(wt)


@needs_brainkit
def test_a_push_stopped_at_the_time_limit_leaves_the_button_for_the_next_click(tmp_path, home, monkeypatch):
    import functools
    gl, srv = served_gitlab(tmp_path, monkeypatch)
    try:
        repo, wt = project(tmp_path, home, base=srv.base)
        alice_token(home)
        sh("git", "-C", wt, "remote", "set-url", "--push", "origin", "ssh://git@127.0.0.1/x.git")
        monkeypatch.setenv("GIT_SSH_COMMAND", "sh -c 'sleep 30' --")
        monkeypatch.setattr(handover, "run", functools.partial(handover.run, timeout=5))
        (r,), detail = handoff(make_service(tmp_path, [repo]), 1)
    finally:
        srv.stop()
    assert r["state"] == "timeout" and subjects(wt)[0] == HANDOFF, r
    assert nothing_went_out(detail["row"]), detail["row"]


@needs_brainkit
def test_a_note_not_ready_is_refused_by_brain_kit_and_nothing_changes(tmp_path, home):
    repo, wt = project(tmp_path, home, notes=False)
    before, pushed = head(wt), head(tmp_path / "repo-origin.git", BRANCH)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
            return await call(routes, "POST", "/requirements/r1/PAY-1/handoff")
        finally:
            await svc.stop()

    via = run(go())
    r = via.body
    assert via.status == 200 and r["state"] == "refused" and r["code"] == 2, r
    assert "交接记录" in r["text"] and r["todo"] and "hand-over note not ready" in r["raw"]
    assert head(wt) == before and head(tmp_path / "repo-origin.git", BRANCH) == pushed


@needs_brainkit
def test_uncommitted_work_is_refused_and_left_as_it_is(tmp_path, home):
    repo, wt = project(tmp_path, home)
    (wt / "README.md").write_text("# repo\n还没提交\n")
    before = head(wt)
    (r,), _ = handoff(make_service(tmp_path, [repo]), 1)
    assert r["state"] == "refused" and "1 个文件" in r["text"], r
    assert head(wt) == before and "还没提交" in (wt / "README.md").read_text()


@needs_brainkit
def test_pushed_but_not_labelled_without_gitlab_in_kit_json(tmp_path, home, monkeypatch):
    """brain-kit's exit 1: the branch is pushed with its hand-over commit, nothing is handed over. (No service here to
    load the test's brain-kit: BRAINKIT_HOME names it, else bk's fallbacks could find an older one on this machine.)"""
    monkeypatch.setenv("BRAINKIT_HOME", str(BRAINKIT))
    repo, wt = project(tmp_path, home, base=None)
    r = handover.run(str(wt))
    assert r["state"] == "not_labelled" and r["code"] == 1 and not r["ok"] and "GitLab" in r["text"], r
    assert subjects(wt)[0] == HANDOFF and head(tmp_path / "repo-origin.git", BRANCH) == head(wt)


@needs_brainkit
def test_pushed_but_not_labelled_without_a_token_says_so_through_the_app(tmp_path, home):
    repo, wt = project(tmp_path, home)
    (r,), detail = handoff(make_service(tmp_path, [repo]), 1)
    assert r["state"] == "not_labelled" and r["code"] == 1 and "令牌" in r["text"] and r["todo"], r
    assert head(tmp_path / "repo-origin.git", BRANCH) == head(wt)


@needs_brainkit
@pytest.mark.parametrize("how, code, status", [("no origin", "local_only", 409), ("no gitlab", "local_only", 409),
                                               ("bob", "not_owner", 403)])
def test_the_app_refuses_before_brain_kit_runs(tmp_path, home, monkeypatch, how, code, status):
    repo, wt = project(tmp_path, home, origin=how != "no origin", base=None if how == "no gitlab" else
                       "https://gitlab.example.com")
    if how == "bob":
        monkeypatch.setenv("BRAIN_USER", "bob")
    before = head(wt)
    (r,), _ = handoff(make_service(tmp_path, [repo]), 1)
    assert isinstance(r, AppError) and (r.code, r.status) == (code, status), r
    assert problems.chinese(str(r)) and head(wt) == before


@needs_brainkit
def test_a_busy_session_is_waited_for(tmp_path, home):
    """A turn of its session could commit meanwhile: the app says to wait until it stops, and runs nothing."""
    repo, wt = project(tmp_path, home)
    install_brain_dev(home)
    gw = FakeGateway()
    gw.templates = {"brain-dev-crew"}
    svc = make_service(tmp_path, [repo], gw=gw)
    before = head(wt)

    async def go():
        await svc.start()
        try:
            got = await svc.open_session("r1", "PAY-1")
            for _ in range(mod("sessions").STATS_EVERY):      # its first turn has not ended: busy, once read
                await svc.sessions.poll()
            busy = svc.sessions.slot_stats(got["slot"])["busy"]
            try:
                return busy, await svc.handoff("r1", "PAY-1")
            except AppError as exc:
                return busy, exc
        finally:
            await svc.stop()

    busy, r = run(go())
    assert busy is True
    assert isinstance(r, AppError) and (r.code, r.status) == ("busy", 409), r
    assert head(wt) == before
