"""The signer's route issue (signer/brain_signer.py): the start contract team/issues.py asks for when it starts a run
from a GitLab issue, checked again on GitLab with the signer's own token, identities and ledger. Each check is broken
alone and refused: the label event not the newest, someone else's label, a Reporter's, an edit after it, a labeler
outside brain-authz, a claim that is not the issue's text or not the owner's, a head that is not one start commit on
main (an extra file, a second commit, run.json not the runner's, intent.md not the claim's), another open run of the
requirement or of the issue, the issue closed, the MR going elsewhere, an unknown route. The contract the app built is
signed once all of them hold.
"""

import itertools
import json
import subprocess
from datetime import timedelta

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import needs_brainkit
from issuefix import BODY, IID, P, commit_as_runner, make_issue_world, other_signer, scratch
from teamfix import signer_ask

pytestmark = [needs_brainkit, pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode,
                                                 reason="ssh-keygen not found")]
BRANCH = "ai/alice/PAY-1-issue-3"


@pytest.fixture
def opened(tmp_path, home, monkeypatch):
    """alice's label taken to the contract by the app, whose signer did not answer: (world, the voucher it asked
    for)."""
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    w = make_issue_world(tmp_path, home, monkeypatch)
    w["gl"].label("alice")
    rt = mod("team.bridgehost").TeamRuntime(w["team"], None, None, None, transport=w["gl"], kick=lambda: None,
                                            sign_timeout=0.2)
    assert rt.start(), rt.error
    try:
        try:
            rt.issues.round()
        except b.brain_error():
            pass
        assert rt.issues.led.get(P, IID).state == "opened"
        (row,) = rt.host.ledger.db.execute("SELECT voucher FROM pending_vouchers").fetchall()
        voucher = json.loads(row["voucher"])
    finally:
        rt.stop()
    yield w, voucher
    b.unload()


ASKED = itertools.count(1)


def ask(w, v, **over):
    """The signer asked for *v* (*over* changed), issued a second later than the last ask: the same voucher asked again
    is answered from the answer it got before. -> (signed | refused, its reasons or None)."""
    common = mod("bk").m("common")
    issued = common.iso(common.utcnow() + timedelta(seconds=next(ASKED)))
    d, r = signer_ask(w, dict(v, issued_at=issued, **over))
    return d, (r or {}).get("reasons")


def refused(w, v, want, **over):
    d, reasons = ask(w, v, **over)
    assert d == "refused" and any(want in x for x in reasons), reasons
    return reasons


def rewrite(w, sha, change, amend=True):
    """The run branch moved to a commit made from *sha* (amended, or one on top), as someone with push rights could:
    -> its sha. ``put_back`` moves it back."""
    path = scratch(w, "rewrite", sha)
    change(path)
    new = commit_as_runner(path, "start", amend=amend)
    sh("git", "-C", path, "push", "-q", "-f", "origin", "HEAD:refs/heads/" + BRANCH)
    return new


def put_back(w, sha):
    sh("git", "-C", w["bare"], "update-ref", "refs/heads/" + BRANCH, sha)


def test_the_contract_the_app_built_is_signed_once_every_check_holds(opened):
    w, v = opened
    assert (v["route"], v["issue"]["iid"], v["owner"], v["branch"]) == ("issue", IID, "alice", BRANCH)
    d, _ = ask(w, v)
    assert d == "signed"
    run = w["signer"].ledger.run(v["run_id"])
    assert (run["req"], run["state"], json.loads(run["voucher"])["route"]) == ("PAY-1", "spooled", "issue")


def test_the_label_is_checked_again(opened):
    w, v = opened
    gl, event = opened[0]["gl"], v["issue"]["label_event"]
    refused(w, v, "最新的一次不是合同里的那次加上", issue={"iid": IID, "label_event": event - 1})
    gl.levels["alice"] = 20
    refused(w, v, "alice 在项目里不是 Developer 以上")
    gl.levels["alice"] = 30
    note = gl._issue_note(IID, "carol", "changed the description", system=True)
    refused(w, v, "issue #3 在打标签以后改过")
    gl.issue_notes[IID].remove(note)
    gl.issues[IID]["state"] = "closed"
    refused(w, v, "issue #3 不是打开的")
    gl.issues[IID]["state"] = "opened"
    gl.label("alice", action="remove")
    bobs = gl.label("bob")
    refused(w, v, "label 'brain' was added by bob, not by the branch owner alice",
            issue={"iid": IID, "label_event": bobs["id"]})
    gl.label("bob", action="remove")
    again = gl.label("alice")
    assert ask(w, v, issue={"iid": IID, "label_event": again["id"]})[0] == "signed"


def test_the_owner_must_be_in_the_signers_brain_authz(opened):
    w, v = opened
    ids = w["tmp"] / "signer-identities-2.json"
    ids.write_text(json.dumps({"users": {"bob": {}, "carol": {}}, "roles": {"product": ["carol"]}}))
    refused(dict(w, signer=other_signer(w, identities=str(ids))), v, "alice is not in brain-authz identities")


def test_the_claim_must_hold_the_issues_text_for_its_owner(opened):
    w, v = opened
    gl = w["gl"]
    gl.issues[IID]["description"] = BODY + "\n- A-5 也能按 paid 筛选\n"          # changed with no system note
    refused(w, v, "claim/PAY-1 的内容不是 issue #3 现在的标题和内容")
    gl.issues[IID]["description"] = BODY
    tip = sh("git", "-C", w["bare"], "rev-parse", "claim/PAY-1").stdout.strip()
    path = scratch(w, "claim", tip)
    doc = json.loads((path / "claim.json").read_text())
    (path / "claim.json").write_text(json.dumps(dict(doc, owner="bob"), ensure_ascii=False, indent=2) + "\n")
    sh("git", "-C", path, "commit", "-qam", "claim PAY-1 by bob")
    sh("git", "-C", path, "push", "-q", "origin", "HEAD:refs/heads/claim/PAY-1")
    refused(w, v, "claim.json owner 'bob' != alice")


@pytest.mark.parametrize("what, change, want", [
    ("an extra file", lambda p: (p / "src").mkdir() or (p / "src" / "x.py").write_text("X = 1\n"),
     "开工提交改了开工以外的文件：src/x.py"),
    ("run.json not the runner's", lambda p: _edit_json(p / "runs" / "PAY-1" / "run.json", origin="local"),
     "run.json 不是运行机开工写的"),
    ("intent.md not the claim's", lambda p: (p / "runs" / "PAY-1" / "intent.md").write_text("# PAY-1 · 别的\n"),
     "runs/PAY-1/intent.md 不是 claim/PAY-1 的需求原文"),
])
def test_the_head_must_be_the_start_commit_alone(opened, what, change, want):
    w, v = opened
    start = v["sha"]
    sha = rewrite(w, start, change)
    refused(w, v, want, sha=sha)
    put_back(w, start)
    assert ask(w, v)[0] == "signed"


def test_a_commit_on_top_of_the_start_is_not_a_start(opened):
    w, v = opened
    intent = v["intent"]
    sha = rewrite(w, v["sha"], lambda p: (p / "aidlc-docs" / intent / "notes.md").write_text("x\n"), amend=False)
    refused(w, v, "父提交不在 main 上", sha=sha)


def test_one_open_run_per_requirement_and_per_issue(opened):
    w, v = opened
    led = w["signer"].ledger
    led.add_run("RUN-PAY-9-1", P, "PAY-9", 9, "1" * 40, "V-x", owner="alice",
                voucher=dict(v, run_id="RUN-PAY-9-1", key="PAY-9", mr_iid=9))
    refused(w, v, "issue #3 已经有一个没结束的 run（RUN-PAY-9-1）")
    led.db.execute("DELETE FROM runs WHERE run_id='RUN-PAY-9-1'")
    gl = w["gl"]
    gl.mrs[8], gl.mr_notes[8], gl.versions[8] = dict(gl.mrs[1], source_branch="ai/alice/PAY-1-other"), [], []
    led.add_run("RUN-PAY-1-7", P, "PAY-1", 8, "2" * 40, "V-y", owner="alice", voucher={"route": "recheck"})
    refused(w, v, "PAY-1 already has an open run on the team runner")
    led.db.execute("DELETE FROM runs WHERE run_id='RUN-PAY-1-7'")
    assert ask(w, v)[0] == "signed"


def test_the_mr_and_the_route_are_the_starts(opened):
    w, v = opened
    w["gl"].mrs[1]["target_branch"] = "dev"
    refused(w, v, "MR targets dev, not main")
    w["gl"].mrs[1]["target_branch"] = "main"
    refused(w, v, "route 只能是 recheck（交接）或 issue（从 issue 开工）", route="patrol")
    refused(w, v, "issue: {iid, label_event}", issue={"iid": IID})
    d, reasons = ask(w, {k: x for k, x in v.items() if k != "issue"})       # an issue start without its issue
    assert d == "refused", reasons


def _edit_json(path, **over):
    path.write_text(json.dumps(dict(json.loads(path.read_text()), **over), indent=2, sort_keys=True) + "\n")
