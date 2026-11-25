"""Brain takes work from a GitLab issue (team/issues.py) inside the app's team mode, offline (issuefix.py): a Developer's
label becomes claim/<KEY> for them, one start commit on ai/<owner>/<KEY>-issue-<iid>, an MR that closes the issue, a
start contract the separate signer checks again on GitLab, and a run that runnerd prepares and the bridge dispatches
into a brain-run session, with the issue and the MR told. A round cut off after any step converges on the next one to
one claim, one MR and one runnerd-ready request. A Reporter's label, an issue whose text fails the Brain's content
checks, and one edited after its label start nothing and say why on the issue; a label taken off before the contract
closes the MR and leaves the claim to its owner.
"""

import json
import subprocess

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import FakeGateway, make_service, needs_brainkit, run
from http_fakes import AppRoute, FakeWeb, call
from issuefix import BASE, BODY, CHECKS, IID, P, TITLE, make_issue_world, set_checks
from test_contract import diff_keys, fixture

pytestmark = [needs_brainkit, pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode,
                                                 reason="ssh-keygen not found")]
BRANCH = "ai/alice/PAY-1-issue-3"


@pytest.fixture(autouse=True)
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


@pytest.fixture
def w(tmp_path, home, monkeypatch):
    return make_issue_world(tmp_path, home, monkeypatch)


def runtime(w, kick=True, sign_timeout=5):
    """The app's TeamRuntime on the world's bridge.json, its signer answering while brain-kit's SpoolSigner waits."""
    rt = mod("team.bridgehost").TeamRuntime(w["team"], None, None, None, transport=w["gl"],
                                            kick=(lambda: w["signer"].once()) if kick else (lambda: None),
                                            sign_timeout=sign_timeout)
    assert rt.start(), rt.error
    return rt


def rounds(rt, n=1):
    """*n* rounds of the issue source; the error each one raised (None: none)."""
    out = []
    for _ in range(n):
        try:
            rt.issues.round()
            out.append(None)
        except mod("bk").brain_error() as exc:
            out.append(str(exc))
    return out


def git(w, *args):
    return sh("git", "-C", w["bare"], *args).stdout.strip()


def claims(w):
    """Every claim on origin: {key: claim.json}."""
    out = {}
    for ref in git(w, "for-each-ref", "--format=%(refname:short)", "refs/heads/claim/").split():
        out[ref[len("claim/"):]] = json.loads(git(w, "show", ref + ":claim.json"))
    return out


def requests(w):
    d = w["spool"] / "requests"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def assert_started_once(w, rt):
    """One claim for the issue's text, one start commit, one open MR, one request runnerd can prepare, the issue told
    once of each."""
    gl, intents = w["gl"], mod("bk").m("intents")
    assert list(claims(w)) == ["PAY-1"] and claims(w)["PAY-1"]["hash"] == intents.digest("product", TITLE, BODY)
    assert gl.branches("ai/") == [BRANCH] and git(w, "rev-list", "--count", "main.." + BRANCH) == "1"
    assert [(m["source_branch"], m["state"]) for m in gl.mrs.values()] == [(BRANCH, "opened")]
    assert requests(w) == ["RUN-PAY-1-1.json"]
    req = json.loads((w["spool"] / "requests" / "RUN-PAY-1-1.json").read_text())
    ready = mod("bk").m("bridge.runnerd").prepare(json.loads(w["runnerd_cfg"].read_text()), req, out=lambda *_: None)
    assert ready["head"] == gl.head(BRANCH)
    told = gl.notes_on(IID)
    assert [t.split("：")[0] for t in told] == ["AI 已接手", "MR !1 已开，问题在 MR 上问"], told
    row = rt.issues.led.get(P, IID)
    assert (row.state, row.key, row.run_id, row.mr_iid) == ("started", "PAY-1", "RUN-PAY-1-1", 1)


def test_a_developers_label_becomes_a_run_on_the_runner_with_the_issue_and_the_mr_told(w, tmp_path):
    gl, intents, lean = w["gl"], mod("bk").m("intents"), mod("bk").m("lean")
    ev = gl.label("alice")
    gw = FakeGateway()
    svc = make_service(tmp_path / "app", [], gw=gw, team=w["team"], transport=gl, kick=lambda: w["signer"].once())
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)

    async def go():
        await svc.start()
        try:
            assert svc.team is not None and svc.team.host is not None, svc.team and svc.team.error
            first = await svc.team_loop()
            seen = await call(routes, "GET", "/team/issues")
            again = await svc.team_loop()
            return first, seen, again
        finally:
            await svc.stop()

    first, seen, again = run(go())
    assert first["ok"] and first["summary"]["issues"] == 1 and first["summary"]["prepared"] == 1, first
    # claim/PAY-1: alice's product requirement with the issue's text and address
    doc = claims(w)["PAY-1"]
    url = "%s/%s/-/issues/%d" % (BASE, P, IID)
    assert (doc["owner"], doc["source"], doc["title"], doc["refs"]) == ("alice", "product", TITLE, [url])
    assert doc["hash"] == intents.digest("product", TITLE, BODY) and doc["body"] == BODY.strip()
    # one commit on main, the runner's, adding the start only
    sha = gl.head(BRANCH)
    assert git(w, "rev-list", "--count", "main.." + BRANCH) == "1"
    assert git(w, "show", "-s", "--format=%an <%ae> %cn <%ce>", sha) == \
        "brain-bot <brain-bot@noreply.invalid> brain-bot <brain-bot@noreply.invalid>"
    rec = json.loads(git(w, "show", sha + ":runs/PAY-1/run.json"))
    assert (rec["origin"], rec["owner"], rec["branch"], rec["handoff"], rec["materials"]) == (
        "remote", "alice", BRANCH, None, [])
    files = git(w, "diff-tree", "-r", "--no-commit-id", "--name-status", sha).splitlines()
    assert all(f.startswith("A\t") for f in files), files
    paths = sorted(f.split("\t")[1] for f in files)
    assert "runs/PAY-1/run.json" in paths and "runs/PAY-1/intent.md" in paths
    assert all(p.startswith(("runs/PAY-1/", "aidlc-docs/%s/" % rec["intent"], "aidlc-docs/audit/")) for p in paths)
    assert git(w, "show", sha + ":runs/PAY-1/intent.md") == intents.render_md(doc).strip()
    state = git(w, "show", "%s:aidlc-docs/%s/aidlc-state.md" % (sha, rec["intent"]))
    assert lean.handover_text_problems(state) == [] and "GitLab issue #3" in state
    # the MR closes the issue at merge; the bridge said it took it over and labelled it
    (mr,) = gl.mrs.values()
    assert (mr["source_branch"], mr["target_branch"], mr["title"]) == (BRANCH, "main", "PAY-1 " + TITLE)
    assert "Closes #3" in mr["description"].splitlines() and "ai-running" in mr["labels"]
    assert any(b.startswith("AI 已接手：RUN-PAY-1-1，凭证 V-") for b in gl.mr_bodies(1))
    assert gl.notes_on(IID) == [
        "AI 已接手：需求号 PAY-1。运行机在开分支和 MR，问题会在 MR 上问。",
        "MR !1 已开，问题在 MR 上问：%s/%s/-/merge_requests/1（RUN-PAY-1-1）" % (BASE, P)]
    # the contract: brain-kit's hand-over voucher with route issue, signed by the signer
    (vf,) = (tmp_path / "signer-vouchers" / "RUN-PAY-1-1").iterdir()
    v = json.loads(vf.read_text())["voucher"]
    assert (v["kind"], v["route"], v["issue"], v["owner"], v["sha"], v["branch"]) == (
        "handoff", "issue", {"iid": IID, "label_event": ev["id"]}, "alice", sha, BRANCH)
    # runnerd built the workspace at the start commit and the bridge sent brain-run there
    ws = w["runs_root"] / "RUN-PAY-1-1"
    assert sh("git", "-C", ws, "rev-parse", "HEAD").stdout.strip() == sha
    assert gw.slot_rows["run-pay-1-1"]["project"] == str(ws) and ("agent", "brain-run", "template") in gw.calls
    assert gw.msgs["run-pay-1-1"][0]["content"].startswith("接手合同 %s（RUN-PAY-1-1" % v["voucher_id"])
    # the page: the issue, who labelled it with their role, its requirement, MR and run, in the fixture's keys
    item, = seen.body["items"]
    assert (item["state"], item["key"], item["mr_iid"], item["run_id"], item["labeler"], item["role"]) == (
        "started", "PAY-1", 1, "RUN-PAY-1-1", "alice", "Developer")
    assert seen.body["projects"] == [{"project": P, "on": True, "label": "brain", "error": ""}]
    assert diff_keys(fixture("team-issues.json"), seen.body) == []
    # the next round makes nothing new
    assert again["ok"] and again["summary"]["issues"] == 1, again
    assert list(claims(w)) == ["PAY-1"] and len(gl.mrs) == 1 and len(gl.notes_on(IID)) == 2
    assert requests(w) == ["RUN-PAY-1-1.json"]


@pytest.mark.parametrize("fault", ["new", "claimed", "pushed", "opened", "signed", "spooled", "told"])
def test_a_round_cut_off_anywhere_converges_to_one_claim_mr_and_request(w, monkeypatch, fault):
    gl = w["gl"]
    gl.label("alice")
    rt = runtime(w)
    issues, bk = mod("team.issues"), mod("bk")
    boom = bk.brain_error()("cut off")
    left = [1]

    def once():
        if left[0]:
            left[0] = 0
            raise boom

    if fault in issues.STEPS:               # the step did its work (GitLab, git), then the round stopped
        real = issues.STEPS[fault]

        def cut(src, e, row):
            out = real(src, e, row)
            once()
            return out
        monkeypatch.setitem(issues.STEPS, fault, cut)
    elif fault == "signed":                 # the signer signed, nothing spooled yet
        sign = rt.host.sign_voucher
        monkeypatch.setattr(rt.host, "sign_voucher", lambda ref, v: (sign(ref, v), once())[0])
    elif fault == "spooled":                # the request is in runnerd's spool, the bridge's ledger has no run yet
        tell = rt.host.tell_mr_due
        monkeypatch.setattr(rt.host, "tell_mr_due", lambda *a, **k: (once(), tell(*a, **k))[1])
    else:                                   # GitLab refuses the issue's notes once
        gl.fail.append(["POST", r"/issues/3/notes$", 1])
    try:
        errs = rounds(rt, 3)
        assert errs[-1] is None, errs
        assert_started_once(w, rt)
        assert rt.host.ledger.run("RUN-PAY-1-1")["state"] == "spooled"
    finally:
        rt.stop()


def test_two_issues_in_one_round_each_start_a_lean_ledger_of_their_own(w):
    gl = w["gl"]
    gl.issue(4, "退款单导出", "要一个导出退款单的接口。\n\n验收标准：\n- A-1 导出 CSV\n")
    gl.label("alice")
    gl.label("bob", iid=4)
    rt = runtime(w)
    try:
        assert rounds(rt) == [None]
        assert [rt.issues.led.get(P, i).state for i in (3, 4)] == ["started", "started"]
        heads = [gl.head("ai/alice/PAY-1-issue-3"), gl.head("ai/bob/PAY-2-issue-4")]
        ledgers = []
        for sha in heads:
            files = git(w, "diff-tree", "-r", "--no-commit-id", "--name-status", sha).splitlines()
            (audit,) = [f.split("\t")[1] for f in files if "/audit/" in f]
            assert files[[f.split("\t")[1] for f in files].index(audit)].startswith("A\t")
            first = json.loads(git(w, "show", "%s:%s" % (sha, audit)).splitlines()[0])
            ledgers.append((audit, first["seq"], first["event"]))
        assert ledgers[0][0] != ledgers[1][0] and [x[1:] for x in ledgers] == [(1, "INTENT_CREATED")] * 2
        assert sorted(requests(w)) == ["RUN-PAY-1-1.json", "RUN-PAY-2-1.json"]
    finally:
        rt.stop()


def test_a_reporters_label_starts_nothing_and_says_why(w):
    gl = w["gl"]
    gl.label("rita")
    rt = runtime(w)
    try:
        assert rounds(rt, 2) == [None, None]
        row = rt.issues.led.get(P, IID)
        assert (row.state, row.key) == ("refused", "")
        told, = gl.notes_on(IID)
        assert "@rita 在项目里是 Reporter：要 Developer 以上才能让 Brain 接 issue" in told and "再打一次标签 `brain`" in told
        assert claims(w) == {} and gl.branches("ai/") == [] and gl.mrs == {} and requests(w) == []
        item, = rt.issues_view()["items"]
        assert item["state"] == "refused" and "Reporter" in item["reason"]
        gl.label("alice", action="remove")              # a Developer labels it again: a new verdict
        gl.label("alice")
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "started" and list(claims(w)) == ["PAY-1"]
    finally:
        rt.stop()


def test_each_projects_snapshot_carries_its_labelled_issues_not_started_yet_and_why(w):
    """团队运行机 on a person's machine reads 从 issue 接活's issues from each project's snapshot: a Reporter's label is
    there, refused, with why, and the label the project takes is said; once a Developer's label starts its run the
    issue is the runner's (its hand-overs, 全队交付) and no longer listed."""
    import time
    snapshot = mod("snapshot")
    gl = w["gl"]
    gl.label("rita")
    rt = runtime(w)
    try:
        def snap():
            return snapshot.parse(rt.snapshot_of(P, time.time(), time.monotonic() + 20, None, rt._shared()))
        assert rounds(rt) == [None]
        got = snap()
        item, = got["runner"]["issues"]
        assert (item["project"], item["iid"], item["state"], item["labeler"]) == (P, IID, "refused", "rita")
        assert "Reporter" in item["reason"] and got["state"]["issue_label"] == "brain"
        gl.label("alice", action="remove")
        gl.label("alice")
        assert rounds(rt) == [None] and rt.issues.led.get(P, IID).state == "started"
        assert snap()["runner"]["issues"] == []
    finally:
        rt.stop()


@pytest.mark.parametrize("body, why", [
    (BODY + "\n联系人手机 13812345678\n", "personal data"),
    (BODY + "\n退款上限 ¥50000\n", "amount"),
    (BODY + "\napi_key = sk-live-abcdefghijklmnopqrstuvwxyz0123\n", "secret"),
])
def test_an_issue_the_content_checks_refuse_starts_nothing(w, body, why):
    gl = w["gl"]
    gl.issues[IID]["description"] = body
    gl.label("alice")
    rt = runtime(w)
    try:
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "refused"
        told, = gl.notes_on(IID)
        assert "需求的内容没过检查" in told and why in told, told
        assert claims(w) == {} and gl.branches("ai/") == [] and gl.mrs == {} and requests(w) == []
    finally:
        rt.stop()


def test_an_issue_waits_while_lean_verify_cannot_pass_on_main_and_is_claimed_once_it_can(tmp_path, home, monkeypatch):
    """main's lean.json has no checks: Lean's verify can never pass there, so a run started from it writes its code and
    cannot commit it (MR !6 note #100). Nothing is claimed; the issue hears why once, and 运行机 and the project's
    snapshot say it on the issue's row; once a person gives main its checks, the next round claims and starts it, with
    no new label."""
    import time
    snapshot = mod("snapshot")
    w = make_issue_world(tmp_path, home, monkeypatch, checks=[])
    gl = w["gl"]
    gl.label("alice")
    rt = runtime(w)
    try:
        assert rounds(rt, 2) == [None, None]
        assert claims(w) == {} and gl.branches("ai/") == [] and gl.mrs == {} and requests(w) == []
        told, = gl.notes_on(IID)
        assert told.startswith("Brain 先不接这个 issue：") and "checks 是空的" in told and "不用再打标签" in told, told
        item, = rt.issues_view()["items"]
        assert item["state"] == "new" and "checks 是空的" in item["note"] and "lean verify 过不了" in item["note"], item
        shown, = snapshot.parse(rt.snapshot_of(P, time.time(), time.monotonic() + 20, None, rt._shared()))["runner"]["issues"]
        assert (shown["state"], shown["note"]) == ("new", item["note"])
        set_checks(w["repo"], CHECKS, "lean verify runs the tests")
        sh("git", "-C", w["repo"], "push", "-q", "origin", "main")
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "started" and list(claims(w)) == ["PAY-1"]
        assert [t.split("：")[0] for t in gl.notes_on(IID)] == ["Brain 先不接这个 issue", "AI 已接手", "MR !1 已开，问题在 MR 上问"]
    finally:
        rt.stop()


def test_an_issue_held_for_main_s_lean_json_is_looked_at_again_only_once_main_moves(tmp_path, home, monkeypatch):
    """While main's lean.json has no checks, a round asks GitLab nothing about who labelled the held issue or what was
    written on it, and fetches no clone for it. A commit on main that leaves the checks empty is looked at once, and
    the issue still hears why only once; the commit that fills them in has it claimed and started the next round."""
    w = make_issue_world(tmp_path, home, monkeypatch, checks=[])
    gl = w["gl"]
    gl.label("alice")
    fetched = tmp_path / "work" / "issues" / "7" / ".git" / "FETCH_HEAD"

    def admission(since):
        return [c[1] for c in gl.calls[since:] if "/members/all/" in c[1] or c[1].endswith("/issues/%d/notes" % IID)]

    def main_moves(msg, checks=None):
        if checks is None:
            (w["repo"] / "NOTES.md").write_text(msg + "\n", encoding="utf-8")
            sh("git", "-C", w["repo"], "add", "NOTES.md")
            sh("git", "-C", w["repo"], "commit", "-q", "-m", msg)
        else:
            set_checks(w["repo"], checks, msg)
        sh("git", "-C", w["repo"], "push", "-q", "origin", "main")
    rt = runtime(w)
    try:
        assert rounds(rt) == [None] and rt.issues.led.get(P, IID).state == "new"
        since, at = len(gl.calls), fetched.stat().st_mtime_ns
        assert rounds(rt, 3) == [None] * 3
        assert admission(since) == [] and fetched.stat().st_mtime_ns == at
        main_moves("notes for the team")
        since = len(gl.calls)
        assert rounds(rt, 2) == [None] * 2
        assert len([p for p in admission(since) if "/members/all/" in p]) == 1 and fetched.stat().st_mtime_ns != at
        assert rt.issues.led.get(P, IID).state == "new" and len(gl.notes_on(IID)) == 1
        item, = rt.issues_view()["items"]
        assert "checks 是空的" in item["note"], item
        main_moves("lean verify runs the tests", CHECKS)
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "started" and list(claims(w)) == ["PAY-1"]
    finally:
        rt.stop()


def test_an_issue_edited_after_its_label_waits_for_a_new_label(w):
    gl = w["gl"]
    gl.label("alice")
    gl.edit(body=BODY + "\n- A-5 也能按 paid 筛选\n")
    rt = runtime(w)
    try:
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "refused"
        assert "标签打上以后，issue 的标题或内容又改过" in gl.notes_on(IID)[0] and claims(w) == {}
        gl.label("alice", action="remove")
        gl.label("alice")
        assert rounds(rt) == [None] and rt.issues.led.get(P, IID).state == "started"
        assert "A-5 也能按 paid 筛选" in claims(w)["PAY-1"]["body"]
    finally:
        rt.stop()


def test_a_signer_slower_than_brain_kits_wait_holds_the_start_without_an_error(w):
    """The signer's first contract takes longer than brain-kit's SpoolSigner waits (its first look at GitLab is slow):
    the round has no error, the issue waits for the signer and says so; the next round, the signer answering, starts
    the run on that contract."""
    gl = w["gl"]
    gl.label("alice")
    on = []
    rt = mod("team.bridgehost").TeamRuntime(w["team"], None, None, None, transport=gl,
                                            kick=lambda: w["signer"].once() if on else None, sign_timeout=0.3)
    assert rt.start(), rt.error
    try:
        assert rounds(rt) == [None]
        item, = rt.issues_view()["items"]
        assert (item["state"], item["run_id"]) == ("opened", None) and "签名服务" in item["note"], item
        assert requests(w) == [] and rt.host.ledger.runs_in("spooled", "signing") == []
        on.append(True)
        assert rounds(rt) == [None]
        assert_started_once(w, rt)
    finally:
        rt.stop()


def test_a_label_taken_off_before_the_contract_closes_the_mr_and_leaves_the_claim(w):
    gl = w["gl"]
    gl.label("alice")
    rt = runtime(w, kick=False, sign_timeout=0.2)        # the signer does not answer: no contract yet
    try:
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID).state == "opened" and requests(w) == []
        gl.label("alice", action="remove")
        assert rounds(rt) == [None]
        assert rt.issues.led.get(P, IID) is None                    # told, then forgotten
        assert [(m["state"]) for m in gl.mrs.values()] == ["closed"]
        assert list(claims(w)) == ["PAY-1"] and gl.branches("ai/") == [BRANCH]
        assert gl.notes_on(IID)[-1] == ("标签去掉了（或者 issue 关了）：Brain 不做了，MR !1 关了；claim/PAY-1 和分支 %s 留给 @alice。"
                                        % BRANCH)
        assert requests(w) == [] and rt.host.ledger.runs_in("spooled", "signing") == []
    finally:
        rt.stop()


def test_issue_label_is_off_where_kit_json_leaves_it_out(tmp_path, home, monkeypatch):
    w = make_issue_world(tmp_path, home, monkeypatch, kit_extra={"work_sources": ["mr-handoff"]})
    w["gl"].label("alice")
    rt = runtime(w)
    try:
        assert rounds(rt) == [None]
        assert rt.issues_view()["projects"] == [{"project": P, "on": False, "label": "", "error": ""}]
        assert w["gl"].notes_on(IID) == [] and claims(w) == {} and not (tmp_path / "issues.sqlite").exists()
    finally:
        rt.stop()
