"""需求看板's stage graph, cut from the records: each node's rounds (第 N 轮), how each ended and why the next one
happened, the gates on a node, what a node says it does; and who did each round, told by the ledger file that holds it
(one per clone: the owner's machine, each run on the team runner, a small requirement of a split). On a real Lean
0.3.1 run past its release (its records copied below) and on the shapes the fixtures' requirements have."""

import ast

from appkit import BRAINKIT, ROOT, mod
from brainfix import needs_brainkit

stages, runs, lean_view = mod("stages"), mod("runs"), mod("lean_view")
LEAN = BRAINKIT / "vendor" / "aidlc-lean" / "kit" / ".kiro" / "aidlc-lean" / "aidlc_lean.py"
HERE = "aidlc-docs/audit/laptop-1a2b3c.jsonl"         # the owner's clone
RUNNER = "aidlc-docs/audit/runner-4d5e6f.jsonl"       # a run on the team runner: a fresh worktree, a fresh clone
RUNNER2 = "aidlc-docs/audit/runner-7a8b9c.jsonl"

I = "261110-pay"
D = "aidlc-docs/261110-pay/"
REQ, DES = D + "inception/requirements/requirements.md", D + "inception/application-design/application-design.md"
UOW = D + "inception/application-design/unit-of-work.md"
PLAN_API, PLAN_WEB = D + "construction/plans/api-code-generation-plan.md", D + "construction/plans/web-code-generation-plan.md"
REL, SUM = D + "operations/release.md", D + "construction/build-and-test/build-and-test-summary.md"
REVIEW = D + "construction/build-and-test/review.md"
H_REQ, H_DES = "b9c969b617f5d58c82bf03672e2ad859e03c398f448633fe06c845b05eac8594", \
    "0a04affb482a18deb3d69249a84d266c2d20709d7093b69f49d97ac429c5cab7"
H_INC = "fd042ec0a7c1413d1d0121347f1b36f371fad6a11778325daf33e19136565ff7"
H_REL1, H_REL2 = "db39bc8853fc8b997c3f695c9210d10066321ea9b84e3d0237db32685b401d9f", \
    "73b506101ae7b1b9eb249ad9126724043d30d23c3c0521f67838ee064afc37c3"
FP1, FP2 = "2ab43fb6eb37466329dd88c0546e9c7dcce508a76e3c8884a94b46f80f40371e", \
    "54f6cdd1c3d808432322d772393afa6482329b5362fa7b444946b10be8ff9fd0"
OPS = ["deployment-pipeline", "environment-provisioning", "deployment-execution", "observability-setup",
       "incident-response", "performance-validation", "feedback-optimization"]
INCEPTION = {"kind": "INCEPTION", "paths": [REQ, DES], "files": {REQ: H_REQ, DES: H_DES}, "sha256": H_INC}
TESTS = "python3 -m unittest discover -s tests -q"
# A Lean 0.3.1 run past its release (a scripted QA run, aidlc-docs/audit seq 4-47): each record's time, event and data.
# Left out: the clone's identity (actor), what the person typed (words), the sessions, and a refusal's own reason
# (Lean writes the approver's email into it).
QA = [
    ("2026-11-10T06:01:54.249Z", "STAGE_STARTED", {"stage": "requirements", "unit": None, "path": REQ, "created": True}),
    ("2026-11-10T06:01:56.112Z", "STAGE_SKIPPED", {"stage": "reverse-engineering", "unit": None, "reason": "greenfield"}),
    ("2026-11-10T06:02:01.166Z", "STAGE_COMPLETED", {"stage": "requirements", "unit": None, "note": "goal, ACs",
                                                    "outputs": {REQ: H_REQ}}),
    ("2026-11-10T06:02:02.610Z", "STAGE_SKIPPED", {"stage": "user-stories", "unit": None, "reason": "one persona"}),
    ("2026-11-10T06:02:04.221Z", "STAGE_STARTED", {"stage": "application-design", "unit": None, "path": DES,
                                                  "created": True}),
    ("2026-11-10T06:02:06.456Z", "STAGE_COMPLETED", {"stage": "application-design", "unit": None, "note": "components",
                                                    "outputs": {DES: H_DES}}),
    ("2026-11-10T06:02:07.978Z", "STAGE_STARTED", {"stage": "units", "unit": None, "path": UOW, "created": True}),
    ("2026-11-10T06:02:10.028Z", "STAGE_COMPLETED", {"stage": "units", "unit": None, "note": "two units", "outputs": {
        UOW: "c660088df13833683b00720387f79cc86047e06c8fe076cbf58138f92e2cafd1"}}),
    ("2026-11-10T06:02:11.377Z", "APPROVAL_REQUESTED", INCEPTION),
    ("2026-11-10T06:02:13.409Z", "HUMAN_TURN", {"turn": 1, "chars": 26}),
    ("2026-11-10T06:02:13.745Z", "APPROVAL_REFUSED", {"kind": "INCEPTION", "check": "approvers", "paths": [REQ, DES],
                                                     "sha256": H_INC}),
    ("2026-11-10T06:02:14.673Z", "HUMAN_TURN", {"turn": 2, "chars": 26}),
    ("2026-11-10T06:02:15.101Z", "APPROVED", dict(INCEPTION, protected_baseline={})),
    ("2026-11-10T06:02:16.594Z", "STAGE_SKIPPED", {"stage": "functional-design", "unit": "api", "reason": "nothing new"}),
    ("2026-11-10T06:02:17.950Z", "STAGE_SKIPPED", {"stage": "nfr-design", "unit": "api", "reason": "nothing new"}),
    ("2026-11-10T06:02:19.410Z", "STAGE_SKIPPED", {"stage": "infrastructure-design", "unit": "api", "reason": "nothing new"}),
    ("2026-11-10T06:02:21.116Z", "STAGE_SKIPPED", {"stage": "functional-design", "unit": "web", "reason": "nothing new"}),
    ("2026-11-10T06:02:22.591Z", "STAGE_SKIPPED", {"stage": "nfr-design", "unit": "web", "reason": "nothing new"}),
    ("2026-11-10T06:02:24.018Z", "STAGE_SKIPPED", {"stage": "infrastructure-design", "unit": "web", "reason": "nothing new"}),
    ("2026-11-10T06:02:25.568Z", "STAGE_STARTED", {"stage": "code-generation", "unit": "api", "path": PLAN_API,
                                                  "created": True}),
    ("2026-11-10T06:02:29.397Z", "APPROVAL_REQUESTED", {"kind": "PLAN", "path": PLAN_API, "unit": "api", "sha256":
                                                       "67efb4d1f040598161c162332d80bde05c29f39cfbbf00a14d0f8bd5f223c5db"}),
    ("2026-11-10T06:02:32.230Z", "APPROVED", {"kind": "PLAN", "path": PLAN_API, "unit": "api", "sha256":
                                             "67efb4d1f040598161c162332d80bde05c29f39cfbbf00a14d0f8bd5f223c5db"}),
    ("2026-11-10T06:02:38.181Z", "VERIFY", {"ok": True, "fingerprint": FP1, "results": [{"cmd": TESTS, "exit": 0, "secs": 1.0}],
                                           "base_commit": "5f4ccf0c02b1edae32b7024e9c62b7020b401839", "summary": SUM,
                                           "summary_sha256": "702c803914019f85fc15dc27e4738117f0dffc6cc85198c3464fa22befd136a1"}),
    ("2026-11-10T06:02:38.399Z", "STAGE_COMPLETED", {"stage": "build-and-test", "unit": None, "fingerprint": FP1,
                                                    "note": "verify PASS: 1 check on code fingerprint 2ab43fb6", "outputs": {
                                                        SUM: "702c803914019f85fc15dc27e4738117f0dffc6cc85198c3464fa22befd136a1"}}),
    ("2026-11-10T06:02:43.627Z", "STAGE_COMPLETED", {"stage": "code-generation", "unit": "api", "note": "api", "outputs": {
        PLAN_API: "206b55923a144452f0f374791295e997ff699c2c55f9ac906c52f2834b247578"}, "fingerprint": FP1}),
    ("2026-11-10T06:02:45.531Z", "STAGE_STARTED", {"stage": "code-generation", "unit": "web", "path": PLAN_WEB,
                                                  "created": True}),
    ("2026-11-10T06:02:49.598Z", "APPROVAL_REQUESTED", {"kind": "PLAN", "path": PLAN_WEB, "unit": "web", "sha256":
                                                       "fb2feeb22b185dbb36fba6430f94c81ada22223f611493c4ed582d446436cf55"}),
    ("2026-11-10T06:02:51.642Z", "APPROVED", {"kind": "PLAN", "path": PLAN_WEB, "unit": "web", "sha256":
                                             "fb2feeb22b185dbb36fba6430f94c81ada22223f611493c4ed582d446436cf55"}),
    ("2026-11-10T06:02:57.403Z", "VERIFY", {"ok": True, "fingerprint": FP2, "results": [{"cmd": TESTS, "exit": 0, "secs": 0.8}],
                                           "base_commit": "860f2a616f955f7b32fb76e9318285546bb7a4d5", "summary": SUM,
                                           "summary_sha256": "7307ed790e5a05df9f37af7d39b6c5f6d0a9f3bd56e0b0c425af5879c26ebf1e"}),
    ("2026-11-10T06:02:57.596Z", "STAGE_COMPLETED", {"stage": "build-and-test", "unit": None, "fingerprint": FP2,
                                                    "note": "verify PASS: 1 check on code fingerprint 54f6cdd1", "outputs": {
                                                        SUM: "7307ed790e5a05df9f37af7d39b6c5f6d0a9f3bd56e0b0c425af5879c26ebf1e"}}),
    ("2026-11-10T06:03:03.407Z", "STAGE_COMPLETED", {"stage": "code-generation", "unit": "web", "note": "web", "outputs": {
        PLAN_WEB: "c6e73b22b56cf2a323294712878b667a129114538b973ff05046e8f820c7cc55"}, "fingerprint": FP2}),
    ("2026-11-10T06:03:10.264Z", "STAGE_STARTED", {"stage": "release", "unit": None, "path": REL, "sha256": H_REL1,
                                                  "fingerprint": FP2, "head": "4d3f857606d991c608fb6545a9ecf26909cccd20"}),
    ("2026-11-10T06:03:18.059Z", "APPROVAL_REQUESTED", {"kind": "RELEASE", "path": REL, "sha256": H_REL1}),
    ("2026-11-10T06:03:20.127Z", "PHASE_SKIPPED", {"phase": "operations", "stages": OPS,
                                                  "reason": "platform team deploys and monitors"}),
    ("2026-11-10T06:03:34.275Z", "APPROVAL_REFUSED", {"kind": "RELEASE", "check": "release", "path": REL, "sha256": H_REL1}),
    ("2026-11-10T06:03:37.917Z", "STAGE_STARTED", {"stage": "release", "unit": None, "path": REL, "sha256": H_REL2,
                                                  "fingerprint": FP2, "head": "d460949881f1198ff93164282e9563eb62ed04b6"}),
    ("2026-11-10T06:03:47.799Z", "APPROVAL_REQUESTED", {"kind": "RELEASE", "path": REL, "sha256": H_REL2}),
    ("2026-11-10T06:03:54.985Z", "APPROVED", {"kind": "RELEASE", "path": REL, "sha256": H_REL2}),
    ("2026-11-10T06:03:55.426Z", "STAGE_COMPLETED", {"stage": "release", "unit": None,
                                                    "note": "RELEASE approved (sha 73b50610)", "outputs": {REL: H_REL2}}),
    ("2026-11-10T06:04:05.159Z", "STAGE_SKIPPED", {"stage": "ci-pipeline", "unit": None, "reason": "existing CI"}),
]
T = lean_view.epoch("2026-11-10T06:00:00Z")
M, H = 60, 3600
WAITING = stages.NodeStatus("waiting", "等前一个阶段", None)


def at(ts):
    return lean_view.epoch(ts)


def ledger(rows, file=HERE):
    return [{"event": ev, "ts": ts, "_at": at(ts), "_file": file, "intent": I, "data": dict(d)} for ts, ev, d in rows]


def rec(t, event, file=HERE, **data):
    return {"event": event, "ts": "", "_at": t, "_file": file, "intent": I, "data": data}


def commit(t, subject, by="bot", records_only=False, paths=(), after_handover=None):
    return {"sha": ("%08x" % int(t)) * 5, "at": t, "by": by, "records_only": records_only, "subject": subject,
            "paths": list(paths), "after_handover": by == "bot" if after_handover is None else after_handover}


def records(evs, commits=(), bounds=(), run_ids=(), family=None, **kw):
    """The records as the board reads them (oldest first, as lean_view.events gives them) and who did each."""
    evs = sorted(evs, key=lambda e: e["_at"])
    who = runs.attribute(evs, runs.file_roles(evs, family), bounds, run_ids, commits)
    return stages.Records(evs, I, who.run_of, list(commits), **kw), who


def rounds_of(stage, evs, unit=None, **kw):
    r, _ = records(evs, **kw)
    return stages.rounds_of(stages.BY_ID[stage], unit, r)


def told(rs):
    """The rounds as the node's card lists them: 第 n 轮 · who · how it ended · why it happened."""
    return [(r.n, r.run, r.end.outcome.value, r.why.kind) for r in rs]


def status(stage, rs, gates=None, unit=None, run_now=None, over=None, waits=WAITING):
    kind = stages.BY_ID[stage].rounds
    st = stages.node_status(kind, rs, stages.gates_of(rs, unit) if gates is None else gates, run_now, over, waits)
    return st.status, st.doing


# ---------------------------------------------------------------- a real Lean 0.3.1 run


def test_a_refused_inception_line_stays_inside_round_one():
    evs = ledger(QA)
    for stage, doc, sha in (("requirements", REQ, H_REQ), ("application-design", DES, H_DES)):
        rs = rounds_of(stage, evs, who=lambda e: "你")
        assert told(rs) == [(1, "local", "approved", "first")], stage
        assert (rs[0].end.gate, rs[0].end.by) == ("INCEPTION", "你")
        assert dict(rs[0].outputs) == {doc: sha}
        gates = stages.gates_of(rs, None)
        assert [(g.kind, g.state, g.round, g.docs) for g in gates] == [("INCEPTION", "approved", 1, (REQ, DES))]
        assert status(stage, rs) == ("merged", "你批了")
    assert rounds_of("requirements", evs)[0].note == "goal, ACs"


def test_release_round_one_refused_and_round_two_approved():
    rs = rounds_of("release", ledger(QA))
    assert told(rs) == [(1, "local", "refused", "first"), (2, "local", "approved", "reopened")]
    assert lean_view.refused_why(rs[0].end.check) == "发布还没准备好"
    assert rs[0].ended == at("2026-11-10T06:03:34.275Z") and rs[1].started == at("2026-11-10T06:03:37.917Z")
    assert dict(rs[0].outputs) == {REL: H_REL1} and dict(rs[1].outputs) == {REL: H_REL2}
    assert lean_view.say_note(rs[1].note) == "发布审批通过了"
    assert [(g.kind, g.state, g.round) for g in stages.gates_of(rs, None)] == [("RELEASE", "refused", 1),
                                                                               ("RELEASE", "approved", 2)]
    # between the refusal and the next `lean release`: the line was not taken, the release is to be made again
    then = rounds_of("release", ledger(QA[:-5]))
    assert status("release", then) == ("stalled", "批准的那一行没被接受")


def test_each_verify_is_a_round_of_build_and_test():
    rs = rounds_of("build-and-test", ledger(QA))
    assert told(rs) == [(1, "local", "passed", "first"), (2, "local", "passed", "reopened")]
    assert [(stages.check_name(c.cmd), c.ok, c.secs) for c in rs[1].end.checks] == [("unittest", True, 0.8)]
    assert lean_view.say_note(rs[1].note) == "自动检查通过（1 项）"
    assert dict(rs[1].outputs) == {SUM: "7307ed790e5a05df9f37af7d39b6c5f6d0a9f3bd56e0b0c425af5879c26ebf1e"}
    assert status("build-and-test", rs) == ("done", "检查都过了")


def test_code_generation_is_at_work_after_its_plan_is_approved_until_its_code_is_done():
    evs = ledger(QA)
    for unit, done in (("api", "2026-11-10T06:02:43.627Z"), ("web", "2026-11-10T06:03:03.407Z")):
        rs = rounds_of("code-generation", evs, unit)
        assert told(rs) == [(1, "local", "approved", "first")]
        assert rs[0].ended == at(done) and rs[0].end.gate == "PLAN"
    r, _ = records(evs, who=lambda e: "你")
    mine = lambda a: stages.Pending("you", "你", line_ok=True)  # noqa: E731
    nodes = stages.graph(r, stages.units_of(evs), stages.Way(), pending=mine)
    stages.with_status(nodes, r, {}, stages.Way(), mine)
    api = next(n for n in nodes if n.id == "code-generation/api")
    assert [(h["at"], h["status"], h["doing"]) for h in api.history] == [
        (at("2026-11-10T06:02:25.568Z"), "running", "在做"),
        (at("2026-11-10T06:02:29.397Z"), "awaiting_approval", "等你批代码生成计划"),
        (at("2026-11-10T06:02:32.230Z"), "running", "在做"),
        (at("2026-11-10T06:02:43.627Z"), "merged", "你批了")]
    assert [(g.kind, g.state) for g in api.gates] == [("PLAN", "approved")]


def test_a_skipped_stage_has_one_skipped_round_and_its_reason():
    evs = ledger(QA)
    for stage, unit, reason in (("reverse-engineering", None, "greenfield"), ("functional-design", "web", "nothing new"),
                                ("deployment-pipeline", None, "platform team deploys and monitors")):
        rs = rounds_of(stage, evs, unit)
        assert told(rs) == [(1, "local", "skipped", "first")] and rs[0].end.reason == reason
        assert status(stage, rs) == ("skipped", "跳过了")
    assert rounds_of("feasibility", evs) == []


def test_the_units_are_the_planned_ones_then_the_ones_only_the_ledger_names():
    evs = ledger(QA)
    assert [u.name for u in stages.units_of(evs)] == ["api", "web"]
    planned = [stages.UnitPlan("web", ("web/**",)), stages.UnitPlan("jobs")]
    assert [(u.name, u.paths) for u in stages.units_of(evs, planned)] == [("web", ("web/**",)), ("jobs", ()), ("api", ())]
    assert [u.name for u in stages.units_of(evs[:3])] == ["app"]
    odd = [rec(T, "STAGE_STARTED", stage="code-generation", unit="plans"), rec(T, "STAGE_STARTED", stage="code-generation",
                                                                              unit="../x")]
    assert [u.name for u in stages.units_of(odd)] == ["app"]


# ---------------------------------------------------------------- the shapes of the fixtures' requirements


def pay2():
    """PAY-2's design, sent back once (the rejection's words are the person's: never read)."""
    req, des = {"path": REQ, "sha256": "a" * 64}, {"path": DES, "sha256": "b" * 64}
    return [rec(T, "STAGE_STARTED", stage="requirements", path=REQ, created=True),
            rec(T + 1 * H, "APPROVAL_REQUESTED", kind="REQUIREMENTS", **req),
            rec(T + 2 * H, "APPROVED", kind="REQUIREMENTS", **req),
            rec(T + 3 * H, "STAGE_COMPLETED", stage="requirements", note="需求写好了", outputs={REQ: "a" * 64}),
            rec(T + 4 * H, "STAGE_STARTED", stage="application-design", path=DES, created=True),
            rec(T + 5 * H, "APPROVAL_REQUESTED", kind="DESIGN", **des),
            rec(T + 6 * H, "REJECTED", kind="DESIGN", words="reject DESIGN 接口要分页", **des),
            rec(T + 7 * H, "APPROVAL_REQUESTED", kind="DESIGN", path=DES, sha256="c" * 64),
            rec(T + 8 * H, "APPROVED", kind="DESIGN", path=DES, sha256="c" * 64),
            rec(T + 9 * H, "STAGE_COMPLETED", stage="application-design", note="设计过了", outputs={DES: "c" * 64})]


def test_a_design_sent_back_then_approved_is_two_rounds_of_one_node():
    evs = pay2()
    assert told(rounds_of("requirements", evs)) == [(1, "local", "approved", "first")]
    rs = rounds_of("application-design", evs, who=lambda e: "你")
    assert told(rs) == [(1, "local", "sent_back", "first"), (2, "local", "approved", "sent_back")]
    assert (rs[0].end.by, rs[1].why.round, dict(rs[1].outputs)) == ("你", 1, {DES: "c" * 64})
    assert [(g.state, g.round, g.by) for g in stages.gates_of(rs, None)] == [("sent_back", 1, "你"), ("approved", 2, "你")]
    assert "接口要分页" not in repr(rs)
    back = rounds_of("application-design", evs[:7], who=lambda e: "你")
    assert status("application-design", back) == ("fixing", "你退回了：等它改好再请批")
    asked = rounds_of("application-design", evs[:8])
    assert dict(asked[1].outputs) == {DES: "c" * 64}         # the version asked for, before Lean has it done
    assert status("application-design", asked, gates=stages.gates_of(asked, None, pending=lambda a: stages.Pending(
        "you", "你", line_ok=True))) == ("awaiting_approval", "等你批设计")


def test_a_second_start_while_open_is_still_round_one():
    """Lean 0.2 recorded a second STAGE_STARTED while a stage was open, and a done record with no start at all."""
    evs = [rec(T, "STAGE_STARTED", stage="requirements"), rec(T + M, "STAGE_STARTED", stage="requirements"),
           rec(T + 2 * M, "STAGE_COMPLETED", stage="requirements"), rec(T + 3 * M, "STAGE_COMPLETED", stage="units")]
    assert told(rounds_of("requirements", evs)) == [(1, "local", "done", "first")]
    assert told(rounds_of("units", evs)) == [(1, "local", "done", "first")]
    again = evs + [rec(T + 4 * M, "STAGE_STARTED", stage="requirements", created=False)]
    assert told(rounds_of("requirements", again)) == [(1, "local", "done", "first"), (2, "local", "open", "reopened")]
    assert status("requirements", rounds_of("requirements", again)) == ("fixing", "第 2 轮 · 又做一遍")


def release_on_the_runner(*commits_):
    hand = T + H
    evs = [rec(T, "STAGE_STARTED", stage="requirements"),
           rec(T + 2 * H, "STAGE_STARTED", RUNNER, stage="release", path=REL, sha256="a" * 64, head="1" * 40),
           rec(T + 2 * H + M, "APPROVAL_REQUESTED", RUNNER, kind="RELEASE", path=REL, sha256="a" * 64),
           rec(T + 4 * H, "STAGE_STARTED", RUNNER, stage="release", path=REL, sha256="b" * 64, head="2" * 40),
           rec(T + 4 * H + M, "APPROVAL_REQUESTED", RUNNER, kind="RELEASE", path=REL, sha256="b" * 64)]
    return rounds_of("release", evs, commits=list(commits_), bounds=[hand])


def test_a_records_only_commit_after_the_release_request_does_not_supersede_it_a_product_commit_does():
    asked = commit(T + 2 * H + 5 * M, "PAY-9：请批发布", records_only=True)
    rs = release_on_the_runner(asked)
    # `lean release` again with no new code: the first request was superseded by nothing but the new one
    assert told(rs) == [(1, "runner-1", "superseded", "first"), (2, "runner-1", "open", "reopened")]
    assert rs[0].end.sha is None and rs[0].ended == T + 4 * H
    fix = commit(T + 3 * H, "修好 CI：api 的 lint")
    rs = release_on_the_runner(asked, fix)
    assert told(rs) == [(1, "runner-1", "superseded", "first"), (2, "runner-1", "open", "code_changed")]
    assert (rs[0].end.sha, rs[0].end.subject, rs[0].ended) == (fix["sha"][:12], "修好 CI：api 的 lint", T + 3 * H)
    assert (rs[1].why.sha, rs[1].why.subject) == (fix["sha"][:12], "修好 CI：api 的 lint")
    assert rs[0].commits == (asked["sha"], fix["sha"])
    mr = ("D-3", "https://gitlab.example.com/p/-/merge_requests/41")
    gates = stages.gates_of(rs, None, pending=lambda a: stages.Pending("product", "产品", mr=mr))
    assert [(g.state, g.round, g.line, g.mr) for g in gates] == [("pending", 2, None, mr)]
    assert status("release", rs, gates) == ("awaiting_approval", "等产品验收（D-3）")
    # a commit of another run (the owner's, on their machine) supersedes nothing of the runner's
    mine = release_on_the_runner(commit(T + 3 * H, "本机改了一下", by="person", after_handover=False))
    assert told(mine)[0] == (1, "runner-1", "superseded", "first") and mine[0].end.sha is None


def test_a_review_round_per_commit_of_review_md_and_a_write_back_round_per_branch():
    cs = [commit(T + H, "PAY-9：代码", paths=["src/a.py"]), commit(T + 2 * H, "PAY-9：审查", paths=[REVIEW, "runs/PAY-9/MR.md"]),
          commit(T + 3 * H, "PAY-9：按审查改", paths=[REVIEW])]
    rs = rounds_of("review", [rec(T, "STAGE_STARTED", stage="requirements")], commits=cs, bounds=[T + 30 * M])
    assert told(rs) == [(1, "runner-1", "done", "first"), (2, "runner-1", "done", "reopened")]
    assert rs[0].commits == (cs[1]["sha"],)
    refl = rounds_of("reflect", [], reflect=[("brain/PAY-2-reflect-1", T, T + H), ("brain/PAY-2-reflect-2", T + 5 * H, None)])
    assert told(refl) == [(1, "local", "approved", "first"), (2, "local", "done", "reopened")]
    assert status("reflect", refl[:1]) == ("merged", "已合进 Brain")
    assert status("reflect", refl, over=("merged", T - H)) == ("done", "写好了回写分支：等合进 Brain")


# ---------------------------------------------------------------- who did each round


def test_two_runner_files_are_runner_1_and_runner_2():
    evs = [rec(T, "STAGE_STARTED", stage="requirements"), rec(T + 3 * H, "HUMAN_TURN"),
           rec(T + 2 * H, "VERIFY", RUNNER, ok=False, results=[{"cmd": "pytest -q", "exit": 1, "secs": 4.0}]),
           rec(T + 6 * H, "VERIFY", RUNNER2, ok=True, results=[{"cmd": "pytest -q", "exit": 0, "secs": 3.5}])]
    r, who = records(evs, bounds=[T + H, T + 5 * H], run_ids=["RUN-PAY-9-2", "RUN-PAY-9-1"])
    # the owner's session still writes after the hand-over: its clone stays the local run's
    assert [who.run_of(e) for e in evs] == ["local", "local", "runner-1", "runner-2"]
    assert [(x.n, x.key, x.run_id) for x in who.runners] == [(1, "runner-1", "RUN-PAY-9-1"), (2, "runner-2", "RUN-PAY-9-2")]
    assert told(stages.rounds_of(stages.BY_ID["build-and-test"], None, r)) == [
        (1, "runner-1", "failed", "first"), (2, "runner-2", "passed", "verify_failed")]
    _, who = records(evs, bounds=[T + H, T + 5 * H], run_ids=["RUN-PAY-9-1"])
    assert [x.run_id for x in who.runners] == [None, None]
    assert [who.run_of(c) for c in (commit(T + 30 * M, "本机", by="person", after_handover=False),
                                    commit(T + 2 * H, "运行机"), commit(T + 7 * H, "运行机"),
                                    commit(T + 2 * H, "交接以后有人推了", by="person", after_handover=True))] == \
        ["local", "runner-1", "runner-2", "runner-1"]


def test_a_runner_worktree_used_again_is_split_at_the_hand_over():
    evs = [rec(T, "STAGE_STARTED", stage="requirements"), rec(T + 2 * H, "VERIFY", RUNNER, ok=True, results=[]),
           rec(T + 6 * H, "VERIFY", RUNNER, ok=True, results=[])]
    _, who = records(evs, bounds=[T + 5 * H, T + H])
    assert [who.run_of(e) for e in evs] == ["local", "runner-1", "runner-2"]


def test_a_hand_over_the_bridge_refused_takes_no_runner_number():
    evs = [rec(T, "STAGE_STARTED", stage="requirements"), rec(T + 6 * H, "VERIFY", RUNNER, ok=True, results=[])]
    _, who = records(evs, bounds=[T + H, T + 5 * H], run_ids=["RUN-PAY-7-1"])
    assert [(x.n, x.key, x.after, x.run_id) for x in who.runners] == [(1, "runner-1", T + 5 * H, "RUN-PAY-7-1")]
    assert who.run_of(evs[1]) == "runner-1"


def test_a_small_requirement_is_told_by_its_clone_and_its_big_one_by_its_team():
    big = "aidlc-docs/audit/laptop-1a2b3c.jsonl"
    small = "aidlc-docs/audit/laptop-9f8e7d.jsonl"
    evs = [rec(T, "STAGE_COMPLETED", big, stage="requirements"),
           dict(rec(T + H, "STAGE_STARTED", small, stage="code-generation", unit="api"), actor={"team": "api"})]
    child = runs.family_of({"role": "child", "parent": "PAY-11", "unit": "api"}, {"lean_clone": "9f8e7d"})
    _, who = records(evs, family=child)
    assert [who.run_of(e) for e in evs] == ["parent-PAY-11", "local"]
    by_team = runs.family_of({"role": "child", "parent": "PAY-11", "unit": "api"}, {"lean_team": "api"})
    assert [records(evs, family=by_team)[1].run_of(e) for e in evs] == ["parent-PAY-11", "local"]
    parent = runs.family_of({"role": "parent", "children": [{"key": "PAY-12", "unit": "api", "state": "merged"},
                                                            {"key": "PAY-13", "unit": "web", "state": "in_progress"}]}, {})
    _, who = records(evs, family=parent)
    assert [who.run_of(e) for e in evs] == ["local", "child-PAY-12"]
    rs = rounds_of("code-generation", evs, "api", family=parent)
    assert told(rs) == [(1, "child-PAY-12", "open", "first")]
    assert runs.family_of({"role": "big"}, {}) is None and runs.family_of(None, {}) is None


def lean_view_kinds():
    """The events lean_view tells apart: each record's event it compares (ev == "X", e.get("event") in (...)), and
    its tables of them."""
    tree = ast.parse((ROOT / "backend" / "core" / "lean_view.py").read_text(encoding="utf-8"))

    def is_event(n):
        return (isinstance(n, ast.Name) and n.id == "ev") or \
            (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get" and n.args
             and getattr(n.args[0], "value", None) == "event") or \
            (isinstance(n, ast.Subscript) and getattr(n.slice, "value", None) == "event")
    out = {x.value for n in ast.walk(tree) if isinstance(n, ast.Compare) and is_event(n.left)
           for c in n.comparators for x in ast.walk(c) if isinstance(x, ast.Constant) and isinstance(x.value, str)}
    return out | set(lean_view.DECIDES) | set(lean_view.APPROVAL_EVENTS)


@needs_brainkit
def test_every_event_kind_is_routed():
    """Every event Lean writes (each pj.append in brain-kit's vendored aidlc_lean.py, read with ast, never run) and
    every one lean_view tells apart has a row in stages.ROUTE, and each is its file's run: written by the runner's
    clone after the hand-over, it is the runner's, whatever it is. A kind a newer Lean adds fails here."""
    tree = ast.parse(LEAN.read_text(encoding="utf-8"))
    written = {n.args[0].value for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "pj" and n.args
               and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
    assert {"STAGE_STARTED", "VERIFY", "APPROVAL_REFUSED", "HUMAN_TURN", "INIT"} <= written
    kinds = written | lean_view_kinds()
    assert sorted(kinds - set(stages.ROUTE)) == []
    evs = [rec(T, "STAGE_STARTED", stage="requirements")] + [rec(T + 2 * H, k, RUNNER) for k in sorted(kinds)]
    _, who = records(evs, bounds=[T + H])
    assert {k: who.run_of(e) for k, e in zip(sorted(kinds), evs[1:]) if who.run_of(e) != "runner-1"} == {}


# ---------------------------------------------------------------- what a node says


def test_a_request_out_says_whom_it_waits_on_and_its_line_only_on_this_machines_run():
    evs = ledger(QA[:9])          # requirements, application design and units done; INCEPTION asked
    pending = [{"kind": "INCEPTION", "unit": None, "state": "pending", "line": "approve INCEPTION fd042ec0"}]
    for stage in ("requirements", "application-design"):
        rs = rounds_of(stage, evs)
        assert told(rs) == [(1, "local", "open", "first")]
        gates = stages.gates_of(rs, None, pending, lambda a: stages.Pending("you", "你", line_ok=a.run == "local"))
        assert [(g.kind, g.state, g.who, g.line, g.docs) for g in gates] == [
            ("INCEPTION", "pending", "you", "approve INCEPTION fd042ec0", (REQ, DES))]
        assert status(stage, rs, gates) == ("awaiting_approval", "等你批需求和设计")
        carol = stages.gates_of(rs, None, pending, lambda a: stages.Pending("people", "carol"))
        assert carol[0].line is None and status(stage, rs, carol) == ("awaiting_approval", "等 carol 批需求和设计")
        stale = stages.gates_of(rs, None, [dict(pending[0], state="stale", line=None)])
        assert [(g.state, g.text) for g in stale] == [("stale", "文件改过了：要重新请求批准")]
        assert status(stage, rs, stale) == ("stalled", "文件改过了：要重新请求批准")


def test_a_node_says_what_it_does_did_or_waits_for():
    assert status("code-generation", [], waits=stages.NodeStatus("waiting", stages.WAITS["code-generation"], None)) == \
        ("waiting", "等批了设计")
    red = rounds_of("build-and-test", [rec(T, "VERIFY", ok=False, results=[{"cmd": "python -m pytest -q", "exit": 1},
                                                                         {"cmd": "ruff check .", "exit": 0}])])
    assert status("build-and-test", red) == ("failed", "自动检查没过：pytest")
    green = rounds_of("build-and-test", [rec(T, "VERIFY", ok=True, results=[])])
    assert status("build-and-test", green, over=("merged", T + H)) == ("merged", "检查都过了")
    assert status("build-and-test", green) == ("done", "检查都过了")
    started = rounds_of("requirements", [rec(T, "STAGE_STARTED", stage="requirements")])
    assert status("requirements", started) == ("running", "在做")
    assert status("requirements", started, over=("merged", T + H)) == ("merged", "已合并")
    assert status("requirements", started, over=("closed", T + H)) == ("skipped", "需求已关闭")
    quiet = stages.NodeStatus("awaiting_approval", "会话空着：轮到你回话", T + M, "you")
    assert status("requirements", started, run_now=quiet) == ("awaiting_approval", "会话空着：轮到你回话")
    busy = stages.NodeStatus("running", "AI 在做", T + M)
    assert status("requirements", started, run_now=busy) == ("running", "在做")
