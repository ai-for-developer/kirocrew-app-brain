"""交接给运行机 (backend/core/handover.py): what `brain handoff` (brain-kit 223ced5 cli.cmd_handoff) said, as the person
reads it: how far it got (state), one plain line, what to do next, the MR, its warnings as rows, and its own words with
credentials hidden. The lines are brain-kit's own, copied from cli.py."""

import pytest

from appkit import mod

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
    (fact(), row(), {"me": None}, "not_owner", 403),
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
