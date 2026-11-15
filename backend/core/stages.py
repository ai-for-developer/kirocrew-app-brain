"""The app's one stage table: AI-DLC Lean 0.3.1's 26 stages in Lean's order, the two steps brain records by other means
(审查: Lean's release review, review.md; 回写 Brain: brain reflect's branch), and the phases they sit in. Every view
that names, orders or counts a stage reads it: Lean's progress (lean_view), 需求详情, 交付总览 and the 需求看板.

It is a copy of Lean's own table (aidlc_lean.py STAGES), which the app never imports (lean_view.py says why), so
tests/test_stages_contract.py parses brain-kit's vendored aidlc_lean.py and fails when an id, phase, per-unit flag or
document differs. The names are brain-kit's (mrdesc.py STAGE_NAMES): the 变更说明 names a stage the same way.

The 需求看板's stage graph is cut from the same table: a node per stage (per unit for a per-unit stage), its rounds (第
N 轮) derived from append-only records on every build (the round machine DOC_MACHINE, and one round per VERIFY, per
commit of review.md, per reflect branch), the gates on it, and its status, which node_status alone writes. Pure: the
records come in, words for a person go out only as node_status's short lines (the board says the rest).
"""

from __future__ import annotations

import bisect
import enum
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Literal, Mapping, NewType, Optional, Sequence, Tuple

Phase = Literal["init", "ideation", "inception", "construction", "release", "operations"]
GateKind = Literal["IDEATION", "REQUIREMENTS", "DESIGN", "INCEPTION", "PLAN", "RELEASE"]
# how the stage graph cuts a stage's rounds: doc (a Lean document stage), verify (each VERIFY), release (each
# `lean release`), review (each commit of review.md), reflect (each brain/<KEY>-reflect-<n> branch)
RoundKind = Literal["doc", "verify", "release", "review", "reflect"]


@dataclass(frozen=True)
class PhaseDef:
    key: Phase
    n: str        # the band's number: 0 1 2 3 ✓ 4
    title: str    # the band's title
    name: str     # how a sentence names it: 构思、运维两段整段不需要
    sub: str      # the band's subtitle, its main stages


@dataclass(frozen=True)
class StageDef:
    id: str
    n: str                          # the AI-DLC number shown with the name: 2.3 需求分析
    label: str
    phase: Phase
    per_unit: bool
    doc: Optional[str]              # its document under aidlc-docs/<intent>/, {unit} for a per-unit stage
    gates: Tuple[GateKind, ...] = ()   # the approvals whose files cover that document
    rounds: RoundKind = "doc"
    icon: str = ""
    lean: bool = True               # False: not a Lean stage, recorded by other means
    # Lean never asks for it (Ideation's stages, ci-pipeline, Operations': aidlc_lean.py open_stages): it is on a run's
    # way only once recorded, Ideation's also while Ideation is in use
    optional: bool = False


PHASES: Tuple[PhaseDef, ...] = (
    PhaseDef("init", "0", "初始化", "初始化", "认领 · 开工 · 带上 Brain 的知识"),
    PhaseDef("ideation", "1", "构思", "构思", "1.1 意图捕获 · 1.4 范围定义 · 1.7 审批交接"),
    PhaseDef("inception", "2", "启动", "启动", "2.3 需求分析 · 2.6 应用设计 · 2.7 拆分单元"),
    PhaseDef("construction", "3", "构建", "构建", "3.5 代码生成 · 3.6 构建测试 · 审查"),
    PhaseDef("release", "✓", "发布", "发布", "发布审批 · 产品验收"),
    PhaseDef("operations", "4", "运维 · 回写", "运维", "4.1 部署流水线 · 回写 Brain"),
)

STAGES: Tuple[StageDef, ...] = (
    StageDef("intent-capture", "1.1", "意图捕获", "ideation", False, "ideation/intent-capture/intent-statement.md",
             icon="idea", optional=True),
    StageDef("market-research", "1.2", "市场调研", "ideation", False, "ideation/market-research/market-research.md",
             icon="search", optional=True),
    StageDef("feasibility", "1.3", "可行性评估", "ideation", False, "ideation/feasibility/feasibility-assessment.md",
             icon="scale", optional=True),
    StageDef("scope-definition", "1.4", "范围定义", "ideation", False, "ideation/scope-definition/scope-document.md",
             icon="scope", optional=True),
    StageDef("team-formation", "1.5", "团队组建", "ideation", False, "ideation/team-formation/team-assessment.md",
             icon="team", optional=True),
    StageDef("rough-mockups", "1.6", "草图原型", "ideation", False, "ideation/rough-mockups/wireframes.md",
             icon="sketch", optional=True),
    StageDef("approval-handoff", "1.7", "审批交接", "ideation", False, "ideation/approval-handoff/initiative-brief.md",
             ("IDEATION",), icon="handoff", optional=True),
    StageDef("reverse-engineering", "2.1", "逆向工程", "inception", False, "inception/reverse-engineering/architecture.md",
             icon="search"),
    StageDef("requirements", "2.3", "需求分析", "inception", False, "inception/requirements/requirements.md",
             ("REQUIREMENTS", "INCEPTION"), icon="req"),
    StageDef("user-stories", "2.4", "用户故事", "inception", False, "inception/user-stories/stories.md", icon="stories"),
    StageDef("application-design", "2.6", "应用设计", "inception", False,
             "inception/application-design/application-design.md", ("DESIGN", "INCEPTION"), icon="design"),
    StageDef("units", "2.7", "拆分单元", "inception", False, "inception/application-design/unit-of-work.md", icon="units"),
    StageDef("functional-design", "3.1", "功能设计", "construction", True,
             "construction/{unit}/functional-design/functional-design.md", icon="design"),
    StageDef("nfr-design", "3.2", "非功能设计", "construction", True, "construction/{unit}/nfr-design/nfr-design.md",
             icon="shield"),
    StageDef("infrastructure-design", "3.4", "基础设施设计", "construction", True,
             "construction/{unit}/infrastructure-design/infrastructure-design.md", icon="server"),
    StageDef("code-generation", "3.5", "代码生成", "construction", True, "construction/plans/{unit}-code-generation-plan.md",
             ("PLAN",), icon="code"),
    StageDef("build-and-test", "3.6", "构建测试", "construction", False,
             "construction/build-and-test/build-and-test-summary.md", rounds="verify", icon="ci"),
    StageDef("review", "3.6", "审查", "construction", False, "construction/build-and-test/review.md", rounds="review",
             icon="review", lean=False),
    StageDef("ci-pipeline", "3.7", "CI 流水线", "construction", False, "construction/ci-pipeline/ci-config.md",
             icon="pipeline", optional=True),
    StageDef("release", "✓", "发布", "release", False, "operations/release.md", ("RELEASE",), rounds="release",
             icon="release"),
    StageDef("deployment-pipeline", "4.1", "部署流水线", "operations", False,
             "operations/deployment-pipeline/deployment-strategy.md", icon="pipeline", optional=True),
    StageDef("environment-provisioning", "4.2", "环境准备", "operations", False,
             "operations/environment-provisioning/environment-inventory.md", icon="server", optional=True),
    StageDef("deployment-execution", "4.3", "部署执行", "operations", False,
             "operations/deployment-execution/deployment-log.md", icon="rocket", optional=True),
    StageDef("observability-setup", "4.4", "可观测性", "operations", False,
             "operations/observability-setup/observability.md", icon="eye", optional=True),
    StageDef("incident-response", "4.5", "故障响应", "operations", False, "operations/incident-response/runbooks.md",
             icon="alarm", optional=True),
    StageDef("performance-validation", "4.6", "性能验证", "operations", False,
             "operations/performance-validation/load-test-plan.md", icon="gauge", optional=True),
    StageDef("feedback-optimization", "4.7", "反馈优化", "operations", False,
             "operations/feedback-optimization/feedback-loop.md", icon="loop", optional=True),
    StageDef("reflect", "4", "回写 Brain", "operations", False, None, rounds="reflect", icon="brain", lean=False),
)
BY_ID: Mapping[str, StageDef] = {s.id: s for s in STAGES}
LEAN: Tuple[StageDef, ...] = tuple(s for s in STAGES if s.lean)


def label(sid: object) -> str:
    """The name of a stage id read from a record; 一个阶段 for one this table does not know (a newer Lean's)."""
    s = BY_ID.get(str(sid))
    return s.label if s else "一个阶段"


def check_name(cmd: Any) -> str:
    """A check's short name: its program (pytest, ruff, npm test; python -m pytest is pytest)."""
    words = [w for w in re.split(r"\s+", str(cmd or "").strip()) if w and not re.match(r"^[A-Z_]+=", w)]
    if not words:
        return "检查"
    if len(words) >= 3 and words[0].startswith("python") and words[1] == "-m":
        return words[2][:16]
    name = words[0].rsplit("/", 1)[-1]
    if name in ("npm", "pnpm", "yarn", "npx") and len(words) > 1:
        return ("%s %s" % (name, words[2] if words[1] == "run" and len(words) > 2 else words[1]))[:16]
    return name[:16]


# ====================================================================================================== the stage graph

NodeId = NewType("NodeId", str)
RunKey = str            # who did a round: local · runner-<n> · child-<KEY> · parent-<KEY> (runs.attribute names them)
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")   # Lean's UNIT_NAME_RE
RESERVED_UNITS = ("plans", "build-and-test")                   # folders under construction/ Lean never takes for a unit
ONE_UNIT = "app"                                               # Lean's one unit when unit-of-work.md names none
MAX_ROUNDS = 40
MAX_COMMITS = 20


def node_id(stage: str, unit: Optional[str]) -> NodeId:
    """`stage`, or `stage/unit` for a per-unit stage: code-generation/api."""
    return NodeId("%s/%s" % (stage, unit) if unit else stage)


@dataclass(frozen=True)
class UnitPlan:
    """A unit as unit-of-work.md plans it (its first table, Lean's rules): its name, and its Paths when it has them."""
    name: str
    paths: Tuple[str, ...] = ()


def _data(e: Mapping[str, Any]) -> Mapping[str, Any]:
    d = e.get("data")
    return d if isinstance(d, dict) else {}


def units_of(evs: Sequence[Mapping[str, Any]], planned: Sequence[UnitPlan] = ()) -> List[UnitPlan]:
    """The construction band's rows: the planned units in their order, then each unit only the ledger names, in the
    order of its first record; neither: Lean's one unit, app. A name Lean cannot use as a folder is left out, as Lean
    leaves it out."""
    out: List[UnitPlan] = []
    for name, paths in [(u.name, u.paths) for u in planned] + [(_data(e).get("unit"), ()) for e in evs]:
        if isinstance(name, str) and UNIT_RE.match(name) and name not in RESERVED_UNITS and \
                all(u.name != name for u in out):
            out.append(UnitPlan(name, tuple(paths)))
    return out or [UnitPlan(ONE_UNIT)]


# What a Lean ledger record feeds on the board, by its event; its run is always its ledger file's (runs.attribute).
# Every kind Lean writes has a row, so a kind a newer Lean adds fails tests/test_rounds.py instead of passing unseen:
#   stage   the node of its stage (and unit)        phase   the node of each stage it names
#   gate    the nodes whose documents it covers     verify  build-and-test: a round each
#   mark    a mark on the node its run is at       turn    its run's session turns
#   run     its run, no node                        none    nothing: no intent (INIT), or a change request (CR gates
#                                                           are left out of the board)
ROUTE: Mapping[str, str] = {
    "STAGE_STARTED": "stage", "STAGE_COMPLETED": "stage", "STAGE_SKIPPED": "stage", "PHASE_SKIPPED": "phase",
    "APPROVAL_REQUESTED": "gate", "APPROVED": "gate", "REJECTED": "gate", "APPROVAL_REFUSED": "gate",
    "VERIFY": "verify", "BLOCKED": "mark", "PROTECTED_CHANGE": "mark",
    "HUMAN_TURN": "turn", "HOST_TURN": "turn", "SUBAGENT_PROMPT": "turn",
    "SESSION_START": "run", "SUBAGENT_START": "run", "INTENT_CREATED": "run", "INTENT_SWITCHED": "run",
    "INIT": "none", "CR_OPENED": "none", "CR_CLOSED": "none",
}


# ---------------------------------------------------------------- rounds

class Outcome(enum.Enum):
    OPEN = "open"
    DONE = "done"
    APPROVED = "approved"
    SENT_BACK = "sent_back"
    REFUSED = "refused"
    SUPERSEDED = "superseded"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CheckResult:
    cmd: str                 # as Lean ran it; check_name says it short
    ok: bool
    secs: Optional[float]


@dataclass(frozen=True)
class RoundEnd:
    """How a round ended, or the decision on its request, with what only that end has (wire: RoundOutcome)."""
    outcome: Outcome
    at: Optional[float] = None
    gate: Optional[GateKind] = None         # approved, sent_back, refused: which approval
    by: Optional[str] = None                # approved, sent_back: 你 · a user name · 产品在 MR 上 (never an email)
    decision: Optional[str] = None          # sent_back on the MR: its D-n
    check: Optional[str] = None             # refused: Lean's check (lean_view.refused_why says it)
    reason: Optional[str] = None            # skipped: Lean's reason (the agent's words)
    sha: Optional[str] = None               # superseded: the commit after the request (12 hex)
    subject: Optional[str] = None
    checks: Tuple[CheckResult, ...] = ()    # passed, failed


@dataclass(frozen=True)
class Why:
    """Why a round happened (wire: RoundWhy)."""
    kind: Literal["first", "sent_back", "verify_failed", "ci_failed", "code_changed", "reopened"]
    round: Optional[int] = None             # sent_back, verify_failed: the round it follows
    pipeline: Optional[int] = None          # ci_failed (team mode): the pipeline
    sha: Optional[str] = None               # code_changed: the commit (12 hex)
    subject: Optional[str] = None
    at: Optional[float] = None


@dataclass(frozen=True)
class Ask:
    """An approval request out on a round: its kind, every document it covers (path, sha256; INCEPTION: two), when,
    and the run that asked."""
    kind: str
    files: Tuple[Tuple[str, str], ...]
    at: float
    run: RunKey


@dataclass(frozen=True)
class Round:
    n: int                                  # 第 n 轮, from 1, over all runs
    run: RunKey                             # who did it: the run of its latest record
    started: float
    ended: Optional[float]                  # None: still open
    end: RoundEnd                           # Outcome.OPEN while it goes on
    why: Why
    note: Optional[str]                     # the agent's own note of it (STAGE_COMPLETED), as Lean recorded it
    outputs: Tuple[Tuple[str, str], ...]    # (path, sha256) it declared, as Lean recorded them
    commits: Tuple[str, ...] = ()           # its run's commits inside it, full shas, oldest first
    ask: Optional[Ask] = None               # its latest approval request
    decided: Optional[RoundEnd] = None      # that request's decision: approved, sent back
    refused: Optional[RoundEnd] = None      # an approval line Lean did not take while the request was out


@dataclass(frozen=True)
class Records:
    """What one requirement's rounds are cut from, gathered once per board: Lean's records of the intent, oldest first
    (lean_view.events: each with _at and _file); the run branch's commits, oldest first (sha, at, by, records_only,
    subject, paths: the files it changed); brain reflect's branches (ref, made, merged); who did a record
    (runs.attribute); who decided an approval (你 · a user name · 产品在 MR 上); the MR question a decision answered."""
    events: Sequence[Mapping[str, Any]]
    intent: Optional[str]
    run_of: Callable[[Mapping[str, Any]], RunKey]
    commits: Sequence[Mapping[str, Any]] = ()
    reflect: Sequence[Tuple[str, float, Optional[float]]] = ()
    who: Callable[[Mapping[str, Any]], Optional[str]] = lambda e: None
    decision: Callable[[Mapping[str, Any]], Optional[str]] = lambda e: None


@dataclass(frozen=True)
class Turn:
    """A session turn as Lean recorded it (HUMAN_TURN: a message its hook saw arrive, a person's or the app's): when,
    whose run, and the sha256 of the message, which finds its row in the session's transcript. Never its words."""
    at: float
    run: RunKey
    sha256: str


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def turns_of(rec: Records) -> Tuple[Turn, ...]:
    """The session turns in *rec*'s records, oldest first."""
    return tuple(Turn(float(e["_at"]), rec.run_of(e), str(_data(e)["sha256"])) for e in rec.events
                 if e.get("event") == "HUMAN_TURN" and isinstance(e.get("_at"), (int, float))
                 and SHA256_RE.match(str(_data(e).get("sha256") or "")))


class RoundState(enum.Enum):
    IDLE = "idle"                # no round yet
    OPEN = "open"                # at work on it
    DONE = "done"                # Lean has it done, nothing asked: a request may still come in the same round
    ASKED = "asked"              # a request covering its document is out while it is at work
    DONE_ASKED = "done_asked"    # the same, once it is done
    GRANTED = "granted"          # approved while at work: code generation writes its code after its PLAN
    APPROVED = "approved"        # approved and done
    ENDED = "ended"              # sent back, superseded, refused or skipped: the next opening event starts round n+1
    DONE_ENDED = "done_ended"    # the same while Lean still has it done, so a request alone asks it again


S = RoundState
# The round machine of a doc or release node: (state, event) -> (actions, next state). Its events are the node's own
# records (ROUTE stage, phase), the approval records covering its document (gate) and, for the release only,
# COMMIT_AFTER_ASK: a product commit (not records-only) by the round's run while its request has no decision, so
# release.md is for older code and Lean refuses its approval. Anything not in the table changes nothing: Lean 0.2
# recorded a second STAGE_STARTED while one was open. The actions:
#   open          start round n+1, its why from the previous round's end
#   note          keep STAGE_COMPLETED's note and outputs on the current round
#   ask           keep the request on it (asked again before any answer: the same round, nobody decided anything)
#   approve       the request is approved; the round ends approved once Lean has it done
#   refused       mark it: an approval line Lean did not take (the request stays out)
#   end:<kind>    end it as done, approved, sent_back, superseded (refused, when marked so and no commit came), skipped
DOC_MACHINE: Mapping[Tuple[RoundState, str], Tuple[Tuple[str, ...], RoundState]] = {
    (S.IDLE, "STAGE_STARTED"): (("open",), S.OPEN),
    (S.IDLE, "STAGE_COMPLETED"): (("open", "note"), S.DONE),                    # Lean 0.2: done with no start record
    (S.IDLE, "STAGE_SKIPPED"): (("open", "end:skipped"), S.ENDED),
    (S.IDLE, "PHASE_SKIPPED"): (("open", "end:skipped"), S.ENDED),
    (S.IDLE, "APPROVAL_REQUESTED"): (("open", "ask"), S.DONE_ASKED),            # no stage record: Lean asks only for a
                                                                                # document that is there
    (S.OPEN, "STAGE_COMPLETED"): (("note",), S.DONE),
    (S.OPEN, "STAGE_SKIPPED"): (("end:skipped",), S.ENDED),
    (S.OPEN, "APPROVAL_REQUESTED"): (("ask",), S.ASKED),
    (S.DONE, "STAGE_COMPLETED"): (("note",), S.DONE),
    (S.DONE, "APPROVAL_REQUESTED"): (("ask",), S.DONE_ASKED),
    (S.DONE, "STAGE_STARTED"): (("end:done", "open"), S.OPEN),                  # started again after done
    (S.ASKED, "STAGE_COMPLETED"): (("note",), S.DONE_ASKED),
    (S.ASKED, "APPROVED"): (("approve",), S.GRANTED),
    (S.ASKED, "REJECTED"): (("end:sent_back",), S.ENDED),
    (S.ASKED, "APPROVAL_REFUSED"): (("refused",), S.ASKED),
    (S.ASKED, "APPROVAL_REQUESTED"): (("ask",), S.ASKED),
    (S.ASKED, "COMMIT_AFTER_ASK"): (("end:superseded",), S.ENDED),
    (S.ASKED, "STAGE_STARTED"): (("end:superseded", "open"), S.OPEN),           # `lean release` again
    (S.DONE_ASKED, "STAGE_COMPLETED"): (("note",), S.DONE_ASKED),
    (S.DONE_ASKED, "APPROVED"): (("approve", "end:approved"), S.APPROVED),
    (S.DONE_ASKED, "REJECTED"): (("end:sent_back",), S.DONE_ENDED),
    (S.DONE_ASKED, "APPROVAL_REFUSED"): (("refused",), S.DONE_ASKED),
    (S.DONE_ASKED, "APPROVAL_REQUESTED"): (("ask",), S.DONE_ASKED),
    (S.DONE_ASKED, "COMMIT_AFTER_ASK"): (("end:superseded",), S.DONE_ENDED),
    (S.DONE_ASKED, "STAGE_STARTED"): (("end:superseded", "open"), S.OPEN),
    (S.GRANTED, "STAGE_COMPLETED"): (("note", "end:approved"), S.APPROVED),
    (S.GRANTED, "APPROVAL_REQUESTED"): (("end:approved", "open", "ask"), S.ASKED),  # the plan changed: asked again
    (S.APPROVED, "STAGE_COMPLETED"): (("note",), S.APPROVED),
    (S.APPROVED, "APPROVAL_REQUESTED"): (("open", "ask"), S.DONE_ASKED),        # asked again: DESIGN via the bridge
    (S.APPROVED, "STAGE_STARTED"): (("open",), S.OPEN),
    (S.ENDED, "STAGE_STARTED"): (("open",), S.OPEN),
    (S.ENDED, "STAGE_COMPLETED"): (("open", "note"), S.DONE),
    (S.ENDED, "APPROVAL_REQUESTED"): (("open", "ask"), S.ASKED),                # sent back, then asked again
    (S.DONE_ENDED, "STAGE_STARTED"): (("open",), S.OPEN),
    (S.DONE_ENDED, "STAGE_COMPLETED"): (("open", "note"), S.DONE),
    (S.DONE_ENDED, "APPROVAL_REQUESTED"): (("open", "ask"), S.DONE_ASKED),
}


@dataclass
class _Draft:
    """A round while the machine walks it."""
    n: int
    run: RunKey
    started: float
    why: Why
    note: Optional[str] = None
    done_at: Optional[float] = None
    outputs: Dict[str, str] = field(default_factory=dict)
    ask: Optional[Ask] = None
    decided: Optional[RoundEnd] = None
    refused: Optional[RoundEnd] = None
    end: Optional[RoundEnd] = None
    ended: Optional[float] = None

    def frozen(self) -> Round:
        return Round(n=self.n, run=self.run, started=self.started, ended=self.ended,
                     end=self.end or RoundEnd(Outcome.OPEN), why=self.why, note=self.note,
                     outputs=tuple(self.outputs.items()), ask=self.ask, decided=self.decided, refused=self.refused)


def _covers(d: Mapping[str, Any]) -> Dict[str, str]:
    """{path: sha256} of the documents an approval record covers: its files (INCEPTION), else its path, else its
    paths (a refused INCEPTION line keeps only those)."""
    if isinstance(d.get("files"), dict):
        return {str(k): str(v) for k, v in d["files"].items()}
    if d.get("path"):
        return {str(d["path"]): str(d.get("sha256") or "")}
    return {str(p): "" for p in d.get("paths") or [] if isinstance(p, str)}


def doc_path(stage: StageDef, unit: Optional[str], intent: Optional[str]) -> Optional[str]:
    """The node's document, repo-relative: aidlc-docs/<intent>/<the stage's doc>."""
    return "aidlc-docs/%s/%s" % (intent, stage.doc.format(unit=unit or "")) if intent and stage.doc else None


def _feeds(stage: StageDef, unit: Optional[str], e: Mapping[str, Any], doc: Optional[str]) -> bool:
    """Is *e* one of the node's own records, or an approval covering its document? An approval record that names no
    document (a refused line with no request to answer) goes by its kind: the node whose gates column has it."""
    route, d = ROUTE.get(str(e.get("event"))), _data(e)
    if route == "stage":
        return d.get("stage") == stage.id and (d.get("unit") or None) == unit
    if route == "phase":
        return unit is None and stage.id in (d.get("stages") or ())
    if route == "gate":
        files = _covers(d)
        if files:
            return doc in files
        kind = str(d.get("kind") or "")
        return kind in stage.gates and (kind != "PLAN" or d.get("unit") == unit)
    return False


def _why(prev: Optional[_Draft]) -> Why:
    if prev is None or prev.end is None:
        return Why("first")
    end = prev.end
    if end.outcome is Outcome.SENT_BACK:
        return Why("sent_back", round=prev.n, at=end.at)
    if end.outcome is Outcome.SUPERSEDED and end.sha:
        return Why("code_changed", sha=end.sha, subject=end.subject, at=end.at)
    return Why("reopened", at=end.at)


Act = Callable[[List[_Draft], Mapping[str, Any], float, "Records", Optional[str]], None]


def _open(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = _Draft(len(rs) + 1, rec.run_of(e), at, _why(rs[-1] if rs else None))
    d = _data(e)
    if e.get("event") == "STAGE_STARTED" and d.get("path") and d.get("sha256"):   # `lean release` writes release.md
        r.outputs[str(d["path"])] = str(d["sha256"])
    rs.append(r)


def _note(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r, d = rs[-1], _data(e)
    if isinstance(d.get("note"), str) and d["note"].strip():
        r.note = d["note"]
    r.done_at = at
    if isinstance(d.get("outputs"), dict):
        r.outputs.update({str(k): str(v) for k, v in d["outputs"].items()})


def _ask(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r, d = rs[-1], _data(e)
    files = _covers(d)
    r.ask, r.refused = Ask(str(d.get("kind") or ""), tuple(files.items()), at, rec.run_of(e)), None
    if doc in files and files[doc]:
        r.outputs[doc] = files[doc]


def _approve(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    rs[-1].decided = RoundEnd(Outcome.APPROVED, at, str(_data(e).get("kind") or "") or None, rec.who(e),
                              rec.decision(e))


def _refused(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    d = _data(e)
    rs[-1].refused = RoundEnd(Outcome.REFUSED, at, str(d.get("kind") or "") or None, check=str(d.get("check") or "") or None)


def _end_done(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = rs[-1]
    r.end, r.ended = RoundEnd(Outcome.DONE, r.done_at), r.done_at if r.done_at is not None else at


def _end_approved(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = rs[-1]
    r.end, r.ended = r.decided, at


def _end_sent_back(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = rs[-1]
    r.decided = r.end = RoundEnd(Outcome.SENT_BACK, at, str(_data(e).get("kind") or "") or None, rec.who(e),
                                 rec.decision(e))
    r.ended = at


def _end_superseded(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = rs[-1]
    if "event" not in e:       # the commit that superseded it
        r.end, r.ended = RoundEnd(Outcome.SUPERSEDED, at, sha=str(e["sha"])[:12], subject=e.get("subject") or None), at
    elif r.refused is not None:
        r.end, r.ended = r.refused, r.refused.at
    else:
        r.end, r.ended = RoundEnd(Outcome.SUPERSEDED, at), at


def _end_skipped(rs: List[_Draft], e: Mapping[str, Any], at: float, rec: Records, doc: Optional[str]) -> None:
    r = rs[-1]
    reason = _data(e).get("reason")
    r.end, r.ended = RoundEnd(Outcome.SKIPPED, at, reason=reason if isinstance(reason, str) and reason else None), at


ACTIONS: Mapping[str, Act] = {
    "open": _open, "note": _note, "ask": _ask, "approve": _approve, "refused": _refused, "end:done": _end_done,
    "end:approved": _end_approved, "end:sent_back": _end_sent_back, "end:superseded": _end_superseded,
    "end:skipped": _end_skipped,
}


class _Walk:
    """DOC_MACHINE over one doc or release node's own records, oldest first. Stopped at a moment, it has the node's
    rounds as the records stood then."""

    def __init__(self, stage: StageDef, unit: Optional[str], rec: Records) -> None:
        self.rec, self.doc = rec, doc_path(stage, unit, rec.intent)
        feed = [(e["_at"], 1, i, e) for i, e in enumerate(rec.events)
                if isinstance(e.get("_at"), (int, float)) and _feeds(stage, unit, e, self.doc)]
        if stage.rounds == "release":     # a commit and a record at the same second: the commit came first
            feed += [(c["at"], 0, i, c) for i, c in enumerate(rec.commits) if not c.get("records_only")]
        self.feed = sorted(feed, key=lambda x: x[:3])
        self.taken = 0
        self.state = RoundState.IDLE
        self.rs: List[_Draft] = []

    def until(self, t: float) -> "_Walk":
        """Take in the records up to *t*."""
        rec, rs = self.rec, self.rs
        while self.taken < len(self.feed) and self.feed[self.taken][0] <= t:
            at, _, _, e = self.feed[self.taken]
            self.taken += 1
            if "event" in e:
                event = str(e["event"])
            elif rs and rec.run_of(e) == rs[-1].run:
                event = "COMMIT_AFTER_ASK"
            else:
                continue
            hit = DOC_MACHINE.get((self.state, event))
            if hit is None:
                continue
            acts, self.state = hit
            for a in acts:
                ACTIONS[a](rs, e, at, rec, self.doc)
            if "event" in e:
                rs[-1].run = rec.run_of(e)
        return self

    def latest(self) -> Optional[Round]:
        """The newest round so far: done, if Lean has it done and nothing else came."""
        if not self.rs:
            return None
        r = self.rs[-1]
        if r.end is None and self.state is RoundState.DONE:
            r = replace(r)          # the walk may go on
            _end_done([r], {}, r.done_at or r.started, self.rec, self.doc)
        return r.frozen()

    def rounds(self) -> List[Round]:
        return [r.frozen() for r in self.rs[:-1]] + [self.latest()] if self.rs else []


def _doc_rounds(stage: StageDef, unit: Optional[str], rec: Records) -> List[Round]:
    return _Walk(stage, unit, rec).until(float("inf")).rounds()


def _secs(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _verify_rounds(stage: StageDef, unit: Optional[str], rec: Records) -> List[Round]:
    """A round per VERIFY: passed or failed, with its checks. After a failed one the next round is the fix and the
    check again, so it starts at the failure; otherwise it is the check itself (its run time)."""
    verifies = [e for e in rec.events if ROUTE.get(str(e.get("event"))) == "verify" and isinstance(e.get("_at"), (int, float))]
    dones = [e for e in rec.events if e.get("event") == "STAGE_COMPLETED" and _data(e).get("stage") == stage.id
             and isinstance(e.get("_at"), (int, float))]
    out: List[Round] = []
    for i, e in enumerate(verifies):
        d, at, run = _data(e), float(e["_at"]), rec.run_of(e)
        checks = tuple(CheckResult(str(x.get("cmd") or ""), x.get("exit") in (0, None), _secs(x.get("secs")))
                       for x in d.get("results") or [] if isinstance(x, dict))
        prev = out[-1] if out else None
        after_red = prev is not None and prev.end.outcome is Outcome.FAILED
        nxt = verifies[i + 1]["_at"] if i + 1 < len(verifies) else float("inf")
        done = next((x for x in dones if at <= x["_at"] < nxt and rec.run_of(x) == run), None)
        outputs: Dict[str, str] = {}
        if d.get("summary") and d.get("summary_sha256"):
            outputs[str(d["summary"])] = str(d["summary_sha256"])
        if done is not None and isinstance(_data(done).get("outputs"), dict):
            outputs.update({str(k): str(v) for k, v in _data(done)["outputs"].items()})
        note = _data(done).get("note") if done is not None else None
        out.append(Round(
            n=i + 1, run=run, ended=at, outputs=tuple(outputs.items()), note=note if isinstance(note, str) and note else None,
            started=prev.ended if after_red and prev.ended is not None else at - sum(c.secs or 0 for c in checks),
            end=RoundEnd(Outcome.PASSED if d.get("ok") else Outcome.FAILED, at, checks=checks),
            why=Why("first") if prev is None else Why("verify_failed", round=prev.n, at=prev.ended) if after_red
            else Why("reopened", at=prev.ended)))
    return out


def _review_rounds(stage: StageDef, unit: Optional[str], rec: Records) -> List[Round]:
    """A round per commit that changed review.md (the release review, written in a session of its own)."""
    doc = doc_path(stage, unit, rec.intent)
    out: List[Round] = []
    for c in rec.commits:
        if doc and doc in (c.get("paths") or ()):
            at = float(c["at"])
            out.append(Round(n=len(out) + 1, run=rec.run_of(c), started=at, ended=at, end=RoundEnd(Outcome.DONE, at),
                             why=Why("reopened", at=out[-1].ended) if out else Why("first"), note=None, outputs=()))
    return out


def _reflect_rounds(stage: StageDef, unit: Optional[str], rec: Records) -> List[Round]:
    """A round per brain/<KEY>-reflect-<n> branch: written (done), then approved once it is merged into the Brain."""
    out: List[Round] = []
    for ref, at, merged in sorted(rec.reflect, key=lambda x: x[1]):
        end = RoundEnd(Outcome.APPROVED, merged) if merged is not None else RoundEnd(Outcome.DONE, at)
        out.append(Round(n=len(out) + 1, run=rec.run_of({"reflect": ref, "at": at}), started=at,
                         ended=merged if merged is not None else at, end=end,
                         why=Why("reopened", at=out[-1].ended) if out else Why("first"), note=None, outputs=()))
    return out


CUT: Mapping[str, Callable[[StageDef, Optional[str], Records], List[Round]]] = {
    "doc": _doc_rounds, "release": _doc_rounds, "verify": _verify_rounds, "review": _review_rounds,
    "reflect": _reflect_rounds,
}


def rounds_of(stage: StageDef, unit: Optional[str], rec: Records) -> List[Round]:
    """The node's rounds, oldest first: a doc or release node's by DOC_MACHINE; build-and-test one per VERIFY; review
    one per commit of review.md; reflect one per reflect branch. Each round lists its run's commits inside it (at
    most MAX_COMMITS, the newest); at most MAX_ROUNDS rounds, the newest, still numbered from the first. Pure and
    total: the ledger is append-only and hash-chained and the rest is git's, so the same records give the same rounds
    and nothing about them is stored."""
    out = []
    for r in CUT[stage.rounds](stage, unit, rec):
        end = r.ended if r.ended is not None else float("inf")
        shas = [str(c["sha"]) for c in rec.commits if r.started <= c["at"] <= end and rec.run_of(c) == r.run]
        out.append(replace(r, commits=tuple(shas[-MAX_COMMITS:])))
    return out[-MAX_ROUNDS:]


def _walked(stage: StageDef, unit: Optional[str], rec: Records, moments: Sequence[float]) -> List[Optional[Round]]:
    w = _Walk(stage, unit, rec)
    return [w.until(t).latest() for t in moments]


def _kept(stage: StageDef, unit: Optional[str], rec: Records, moments: Sequence[float]) -> List[Optional[Round]]:
    """A VERIFY's round, and a review commit's, stays as it was cut (but for the note and outputs a later record
    brings, which no status reads): the newest one recorded by each moment."""
    rs = CUT[stage.rounds](stage, unit, rec)
    ends = [r.ended for r in rs]
    return [rs[k - 1] if k else None for k in (bisect.bisect_right(ends, t) for t in moments)]


def _merged_by(stage: StageDef, unit: Optional[str], rec: Records, moments: Sequence[float]) -> List[Optional[Round]]:
    """A write-back round is merged once its branch is merged into the Brain."""
    out = []
    for t in moments:
        made = [(ref, at, m if m is not None and m <= t else None) for ref, at, m in rec.reflect if at <= t]
        rs = _reflect_rounds(stage, unit, replace(rec, reflect=made))
        out.append(rs[-1] if rs else None)
    return out


# The node's newest round as the records stood at each of a node's moments (ascending), as node_status reads it: what
# rounds_of over the records up to that moment ends with, from one pass over the node's own records.
AS_OF: Mapping[str, Callable[[StageDef, Optional[str], Records, Sequence[float]], List[Optional[Round]]]] = {
    "doc": _walked, "release": _walked, "verify": _kept, "review": _kept, "reflect": _merged_by,
}


# ---------------------------------------------------------------- gates and status

GATE_WORDS = {"IDEATION": "构思", "REQUIREMENTS": "需求", "DESIGN": "设计", "INCEPTION": "需求和设计",
              "PLAN": "代码生成计划", "RELEASE": "发布"}
STALE = "文件改过了：要重新请求批准"


@dataclass(frozen=True)
class Pending:
    """Whom a request that is out waits on (Waits.who) and the words for them (你, 产品, carol); whether the person types
    its line in their own session (a request of this machine's run, before the hand-over); the MR question that asks
    it (D-n, the MR's link) after the hand-over."""
    who: Optional[str]
    asker: str
    line_ok: bool = False
    mr: Optional[Tuple[Optional[str], Optional[str]]] = None


@dataclass(frozen=True)
class GateView:
    kind: str
    state: Literal["pending", "queued", "approved", "sent_back", "refused", "stale"]
    round: int
    at: Optional[float]
    who: Optional[str] = None
    by: Optional[str] = None
    line: Optional[str] = None
    mr: Optional[Tuple[Optional[str], Optional[str]]] = None
    text: Optional[str] = None
    docs: Tuple[str, ...] = ()
    asker: str = ""                          # a request out: whom node_status names; not sent


def gates_of(rounds: Sequence[Round], unit: Optional[str], approvals: Sequence[Mapping[str, Any]] = (),
             pending: Optional[Callable[[Ask], Pending]] = None) -> List[GateView]:
    """The node's gates, oldest first: one per round whose request was decided (approved, sent back) or refused, and
    the one still out on an open round. That one's state now is lean_view.approvals' (*approvals*): pending, with the
    line to type when *pending* says the person types it; stale when the file changed since the request; refused when
    Lean did not take the line typed for it. Without *approvals* (the replay) a request out is pending."""
    out: List[GateView] = []
    for r in rounds:
        if r.ask is None:
            continue
        docs = tuple(p for p, _ in r.ask.files)
        end = r.decided or (r.end if r.end.outcome is Outcome.REFUSED else None)
        if end is not None:
            out.append(GateView(end.gate or r.ask.kind, end.outcome.value, r.n, end.at, by=end.by, docs=docs))
            continue
        if r.end.outcome is not Outcome.OPEN:
            continue
        p = pending(r.ask) if pending is not None else Pending(None, "")
        now = next((a for a in approvals if a.get("kind") == r.ask.kind
                    and (a.get("unit") or None) == (unit if r.ask.kind == "PLAN" else None)), None)
        if now is not None and now.get("state") == "stale":
            out.append(GateView(r.ask.kind, "stale", r.n, r.ask.at, text=STALE, docs=docs, asker=p.asker))
        elif r.refused is not None:
            out.append(GateView(r.ask.kind, "refused", r.n, r.refused.at, who=p.who, docs=docs, asker=p.asker))
        else:
            out.append(GateView(r.ask.kind, "pending", r.n, r.ask.at, who=p.who,
                                line=(now or {}).get("line") if p.line_ok else None, mr=p.mr, docs=docs,
                                asker=p.asker))
    return out


@dataclass(frozen=True)
class NodeStatus:
    status: str                     # ActorStatus
    doing: str
    since: Optional[float]
    who: Optional[str] = None       # whom a wait is on (Waits.who)


# what a node of the default way waits for before its first round
# the team runner's page of a requirement it has no workspace for yet (the bridge took it, the signer signs or the
# runner prepares one): what was done before the hand-over is behind it, and nothing of it is readable there
RUNNER_BLIND = "交接前的事：运行机还没建工作区，读不到它的记录"
WAITS = {"requirements": "等开工", "application-design": "等需求分析", "units": "等应用设计",
         "code-generation": "等批了设计", "build-and-test": "等代码生成", "review": "等构建测试",
         "release": "等审查和自动检查", "reflect": "等合并以后"}
# with the design skipped (Size: small), Lean asks for no design approval: what comes after the design waits on these
NO_DESIGN_WAITS = {"units": "等需求分析", "code-generation": "等批了需求"}
AGAIN = {"sent_back": "退回以后", "verify_failed": "检查没过以后", "ci_failed": "CI 没过以后",
         "code_changed": "代码改了以后", "reopened": "又做一遍", "first": "又做一遍"}
DONE_WORDS = {"doc": "做完了", "release": "做完了", "verify": "检查都过了", "review": "写好了审查",
              "reflect": "写好了回写分支：等合进 Brain"}


def _name(who: str) -> str:
    """A person's name in a sentence: a space each side of a Latin one (等 carol 批), none around 你 or 产品."""
    who = who.strip()
    return " %s " % who if who[:1].isascii() and who[:1].isalnum() else who


def _line(*parts: str) -> str:
    return " ".join("".join(parts).split())


def _asking(g: GateView) -> str:
    what, d = GATE_WORDS.get(g.kind, ""), (g.mr or (None, None))[0]
    if g.mr is not None and g.kind == "RELEASE":
        return _line("等", _name(g.asker or "产品"), "验收", "（%s）" % d if d else "")
    if g.mr is not None:
        return _line("等", _name(g.asker or "人"), "在 MR 上批", what, "（%s）" % d if d else "")
    return _line("等", _name(g.asker or "人"), "批", what)


def node_status(kind: str, rounds: Sequence[Round], gates: Sequence[GateView], run_now: Optional[NodeStatus],
                over: Optional[Tuple[str, float]], waits: NodeStatus, passed: bool = False) -> NodeStatus:
    """The one writer of a node's status (ActorStatus) and its line. *kind*: its stage's round kind. No round yet:
    *waits*, what the fold says it waits for. Then its latest round decides: a request out → awaiting_approval (stale →
    stalled, a refused line → stalled); open → running, fixing from round 2, unless *run_now* (its run's live state
    while the run is at this node; None in the replay) says the run waits on a person or is stuck; sent back,
    superseded or refused → fixing; a failed verify → failed; skipped → skipped; approved → merged; done or passed →
    merged once the way moved past it (*passed*: a later node's round began after it ended) or the requirement merged
    after it, else done. *over*: ("merged", at) or ("closed", at) when the requirement's way is over (merged, a small
    one merged into its big one, closed): nothing is at work on it then."""
    if not rounds:
        return waits
    last = rounds[-1]
    end = last.end
    gate = next((g for g in reversed(gates) if g.round == last.n), None)
    if over is not None and over[0] == "closed" and end.outcome is Outcome.OPEN:
        return NodeStatus("skipped", "需求已关闭", over[1])
    if over is not None and end.outcome is Outcome.OPEN:
        return NodeStatus("merged", "已合并", over[1])
    if end.outcome is Outcome.OPEN and gate is not None and gate.state == "pending":
        return NodeStatus("awaiting_approval", _asking(gate), gate.at, gate.who)
    if end.outcome is Outcome.OPEN and gate is not None and gate.state == "queued":
        return NodeStatus("queued", gate.text or _asking(gate), gate.at, gate.who)
    if end.outcome is Outcome.OPEN and gate is not None and gate.state == "stale":
        return NodeStatus("stalled", STALE, gate.at)
    if end.outcome is Outcome.OPEN and gate is not None and gate.state == "refused":
        return NodeStatus("stalled", "批准的那一行没被接受", gate.at, gate.who)
    if end.outcome is Outcome.OPEN:
        if run_now is not None and run_now.status not in ("running", "fixing", "done", "merged", "waiting"):
            return run_now
        if last.n == 1:
            return NodeStatus("running", "在做", last.started)
        return NodeStatus("fixing", "第 %d 轮 · %s" % (last.n, AGAIN[last.why.kind]), last.started)
    if end.outcome is Outcome.SENT_BACK:
        return NodeStatus("fixing", _line(_name(end.by or "有人"), "退回了：等它改好再请批"), end.at)
    if end.outcome is Outcome.SUPERSEDED:
        return NodeStatus("fixing", "请批以后代码又改了：等它重新请批", end.at)
    if end.outcome is Outcome.REFUSED:
        return NodeStatus("fixing", "批准的那一行没被接受：等它重新请批", end.at)
    if end.outcome is Outcome.FAILED:
        bad = [check_name(c.cmd) for c in end.checks if not c.ok]
        return NodeStatus("failed", "自动检查没过：%s" % ("、".join(bad[:3]) or "有检查失败"), end.at)
    if end.outcome is Outcome.SKIPPED:
        return NodeStatus("skipped", "跳过了", end.at)
    if end.outcome is Outcome.APPROVED:
        return NodeStatus("merged", "已合进 Brain" if kind == "reflect" else
                          _line(_name(end.by), "批了") if end.by else "批准了", end.at)
    merged = passed or over is not None and over[0] == "merged" and last.ended is not None and last.ended <= over[1]
    return NodeStatus("merged" if merged else "done", DONE_WORDS.get(kind, "做完了"), last.ended)


# ---------------------------------------------------------------- the graph

# the stages every way has, drawn waiting until reached
DEFAULT_WAY = ("requirements", "code-generation", "build-and-test", "review", "release", "reflect")
# a small requirement's big one releases and writes back: those stages and bands are not its own (phase -> the
# band's words)
BIG_ONES = ("release", "reflect")
CHILD_LEFT_OUT = {"release": "跟大需求一起发布", "operations": "跟大需求一起回写 Brain"}
CHILD_WAITS = {"unclaimed": ("waiting", "待认领"), "claimed": ("waiting", "%s认领了"), "in_progress": ("running", "%s在做"),
               "closed": ("skipped", "关了")}
FIX_WHY = ("sent_back", "verify_failed", "ci_failed", "code_changed")
MAX_NODES = 64


@dataclass(frozen=True)
class Way:
    """What decides a requirement's way besides its records. A small requirement's way has no release and no
    write-back. A split's unit that its small requirement has not merged in is that one's: unit -> (key, state,
    owner). *over*: ("merged", at) or ("closed", at) once the way is over (a small one: merged into its big one).
    *proposals*: the Brain proposals waiting to be written back after the merge, by *you* (你, or the owner's name).
    *blind*: the team runner's page of a requirement handed over before it has a workspace (board.runner_blind).
    *next*: the stage Lean does next (lean_view.summary), drawn waiting though it is not on the default way."""
    child: bool = False
    children: Mapping[str, Tuple[str, str, Optional[str]]] = field(default_factory=dict)
    over: Optional[Tuple[str, float]] = None
    proposals: int = 0
    you: str = "你"
    blind: bool = False
    next: Optional[str] = None


@dataclass
class Node:
    """A unit of work on the board: a stage, of a unit for a per-unit stage, with its rounds and gates. Its status,
    history and spans are with_status's, from node_status alone."""
    id: NodeId
    stage: StageDef
    unit: Optional[str]
    rounds: List[Round]
    gates: List[GateView]
    waits: NodeStatus                           # what it waits for while it has no round
    child: Optional[Tuple[str, str]] = None     # a split's unit a small requirement does: (its key, its state)
    status: Optional[NodeStatus] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    spans: List[Dict[str, Any]] = field(default_factory=list)


def _waits(stage: StageDef, kid: Optional[Tuple[str, str, Optional[str]]], way: Way, no_design: bool = False) -> NodeStatus:
    if kid is not None:
        key, state, owner = kid
        status, words = CHILD_WAITS.get(state, ("waiting", "等小需求"))
        return NodeStatus(status, _line(words % _name(owner or "有人") if "%s" in words else words, "（%s）" % key), None)
    if way.over is not None and way.over[0] == "closed":
        return NodeStatus("skipped", "需求已关闭", way.over[1])
    if way.over is not None and stage.id == "reflect" and way.proposals:
        return NodeStatus("awaiting_approval", _line("等", _name(way.you), "回写 %d 条新知识" % way.proposals),
                          way.over[1], "you" if way.you == "你" else "people")
    if way.over is not None:
        return NodeStatus("skipped", "这次没有要回写的知识" if stage.id == "reflect" else "这次不需要", way.over[1])
    if way.blind and stage.phase in ("ideation", "inception"):
        return NodeStatus("done", RUNNER_BLIND, None)
    if way.blind and stage.id == "code-generation":
        return NodeStatus("waiting", "等运行机开工", None)
    if no_design and stage.id in NO_DESIGN_WAITS:
        return NodeStatus("waiting", NO_DESIGN_WAITS[stage.id], None)
    return NodeStatus("waiting", WAITS.get(stage.id, "等前一个阶段"), None)


def graph(rec: Records, units: Sequence[UnitPlan], way: Way, approvals: Sequence[Mapping[str, Any]] = (),
          pending: Optional[Callable[[Ask], Pending]] = None) -> List[Node]:
    """The nodes on the requirement's way, in band order, then stage order, then unit order: every stage (per unit for
    a per-unit stage) that has a round, and the default way's stages not reached yet (DEFAULT_WAY; a small
    requirement's has no release and no write-back), waiting. A skipped stage keeps its node: the band's note and the
    replay name it. A split's unit its small requirement has not merged in is one code-generation node, of that small
    requirement. At most MAX_NODES."""
    out: List[Node] = []
    for s in STAGES:
        if way.child and s.id in BIG_ONES:
            continue
        no_design = any(n.stage.id == "application-design" and n.rounds[-1].end.outcome is Outcome.SKIPPED
                        for n in out if n.rounds)
        for u in [x.name for x in units] if s.per_unit else [None]:
            rs = rounds_of(s, u, rec)
            kid = way.children.get(u) if s.id == "code-generation" and u else None
            if not rs and kid is None and s.id not in DEFAULT_WAY and s.id != way.next:
                continue
            out.append(Node(node_id(s.id, u), s, u, rs, gates_of(rs, u, approvals, pending), _waits(s, kid, way, no_design),
                            (kid[0], kid[1]) if kid is not None else None))
    return out[:MAX_NODES]


def _skips(p: PhaseDef, mine: Sequence[Node]) -> Optional[str]:
    """A used band's foot line: what of it was skipped (跳过：逆向工程、用户故事 · 每个单元都跳过了功能设计), or, when
    every Lean stage of it was skipped and only the write-back is on the way, how many (运维 7 个阶段：这次不需要)."""
    skipped = [n for n in mine if n.rounds and n.rounds[-1].end.outcome is Outcome.SKIPPED]
    if not skipped:
        return None
    lean = [n for n in mine if n.stage.lean]
    if lean and all(n in skipped for n in lean):
        return "%s %d 个阶段：这次不需要" % (p.name, len({n.stage.id for n in lean}))
    stages_ = list(dict.fromkeys(n.stage for n in skipped))
    parts = []
    whole = [s.label for s in stages_ if not s.per_unit]
    if whole:
        parts.append("跳过：%s" % "、".join(whole))
    every = [s for s in stages_ if s.per_unit and all(n in skipped for n in mine if n.stage is s)]
    if every:
        parts.append("每个单元都跳过了%s" % "、".join(s.label for s in every))
    for s in stages_:
        if s.per_unit and s not in every:
            parts.append("%s 跳过了%s" % ("、".join(str(n.unit) for n in skipped if n.stage is s), s.label))
    return " · ".join(parts)


def phases(nodes: Sequence[Node], way: Way) -> List[Dict[str, Any]]:
    """The board's bands, PHASES in order (init first: always used): used (a node on the way; its foot line says what
    it skipped), unused (nothing of it on this way: every stage skipped, or it is the big one's), unrecorded (nothing
    recorded and the run is past it: a run older than Lean 0.3 has no record of a phase it never knew)."""
    recorded = [i for i, p in enumerate(PHASES) if any(n.rounds for n in nodes if n.stage.phase == p.key)]
    out = []
    for i, p in enumerate(PHASES):
        mine = [n for n in nodes if n.stage.phase == p.key]
        if p.key == "init":
            state, note = "used", None
        elif way.child and p.key in CHILD_LEFT_OUT:
            state, note = "unused", CHILD_LEFT_OUT[p.key]
        elif any(n.status is not None and n.status.status != "skipped" for n in mine):
            state, note = "used", _skips(p, mine)
        elif not any(n.rounds for n in mine) and recorded and i < recorded[-1]:
            state, note = "unrecorded", "Lean 没记这一段"
        else:
            state, note = "unused", "这次不需要"
        out.append({"key": p.key, "n": p.n, "title": p.title, "sub": p.sub, "state": state, "note": note})
    return out


Quiet = Mapping[RunKey, Sequence[Tuple[float, Optional[float]]]]


def _cut(span: Tuple[float, Optional[float], str, RunKey],
         quiet: Sequence[Tuple[float, Optional[float]]]) -> List[Tuple[float, Optional[float], str, RunKey]]:
    """A work or fix span with the stretches its run's session sat waiting for its person taken out as waits."""
    a, b, kind, run = span
    out = []
    for qa, qb in sorted(quiet, key=lambda q: q[0]):
        if (b is not None and qa >= b) or (qb is not None and qb <= a):
            continue
        if qa > a:
            out.append((a, qa, kind, run))
        end = b if qb is None or (b is not None and b <= qb) else qb
        out.append((max(a, qa), end, "wait", run))
        if end is None or end == b:
            return out
        a = end
    return out + [(a, b, kind, run)]


def _spans(n: Node, over: Optional[Tuple[str, float]], quiet: Quiet) -> List[Dict[str, Any]]:
    """The node's lane: each round's work (fix after a no), and the wait while its request was out; nothing of the
    requirement's time after its way is over. *quiet*: by run, when its session sat waiting for its person (before
    each reply the person typed, and now while it waits on them): those hours are a wait, not work (UI问题清单 #2)."""
    out = []
    for r in n.rounds:
        if r.end.outcome is Outcome.SKIPPED:
            continue
        kind = "fix" if r.why.kind in FIX_WHY else "work"
        # a round after a no is at work from that no: sent back, the design is rewritten before it is asked again
        a = min(r.started, r.why.at) if kind == "fix" and r.why.at is not None else r.started
        ask = r.ask.at if r.ask is not None and r.ask.at >= r.started else None
        if ask is None:
            out.append((a, r.ended, kind, r.run))
            continue
        out.append((a, ask, kind, r.run))
        out.append((ask, r.decided.at if r.decided is not None else r.ended, "wait", r.run))
        if r.decided is not None and r.decided.outcome is Outcome.APPROVED and (r.ended is None or r.ended > r.decided.at):
            out.append((r.decided.at, r.ended, kind, r.run))      # approved while at work: its code comes after its plan
    out = [x for sp in out for x in (_cut(sp, quiet.get(sp[3]) or ()) if sp[2] != "wait" else [sp])]
    end = over[1] if over is not None else None
    spans = []
    for a, b, kind, run in out:
        b = end if end is not None and (b is None or b > end) else b
        if b is None or b > a:
            spans.append({"a": a, "b": b, "kind": kind, "run": run})
    return spans


def _history(n: Node, rec: Records, way: Way, pending: Optional[Callable[[Ask], Pending]],
             later: Sequence[float] = ()) -> List[Dict[str, Any]]:
    """node_status at each moment its rounds changed, as the records stood then, and at each moment the way moved past
    it (*later*: when the rounds of the nodes after it began): what 回放 plays."""
    moments = {t for r in n.rounds for t in (r.started, r.ended, r.ask.at if r.ask else None,
                                            r.decided.at if r.decided else None, r.refused.at if r.refused else None)
               if t is not None}
    moments |= {next((s for s in later if s >= r.ended), None) for r in n.rounds if r.ended is not None} - {None}
    if way.over is not None:
        moments.add(way.over[1])
    ats = sorted(moments)
    out: List[Dict[str, Any]] = []
    for t, last in zip(ats, AS_OF[n.stage.rounds](n.stage, n.unit, rec, ats)):
        rs = [last] if last is not None else []
        over = way.over if way.over is not None and way.over[1] <= t else None
        moved = last is not None and last.ended is not None and any(last.ended <= s <= t for s in later)
        st = node_status(n.stage.rounds, rs, gates_of(rs, n.unit, (), pending), None, over, n.waits, moved)
        if not rs and st.since is None:
            continue
        if not out or (out[-1]["status"], out[-1]["doing"]) != (st.status, st.doing):
            out.append({"at": t, "status": st.status, "doing": st.doing, "who": st.who})
    return out


def with_status(nodes: Sequence[Node], rec: Records, now: Mapping[RunKey, Tuple[NodeStatus, Optional[str]]],
                way: Way, pending: Optional[Callable[[Ask], Pending]] = None, quiet: Optional[Quiet] = None) -> None:
    """Each node's status now (node_status, with its run's live state while the run is at it: *now*, run -> (its
    state, the node it is at); merged once a later node's round began after its latest one ended), its history (what
    回放 plays; it ends on the status now) and its lane's spans (*quiet*: _spans')."""
    later: List[List[float]] = []
    began: List[float] = []
    for n in reversed(nodes):
        later.append(sorted(began))
        began += [r.started for r in n.rounds]
    for n, moved in zip(nodes, reversed(later)):
        last = n.rounds[-1] if n.rounds else None
        live = now.get(last.run) if last is not None else None
        run_now = live[0] if live and live[1] == n.id else None
        passed = last is not None and last.ended is not None and any(s >= last.ended for s in moved)
        n.status = node_status(n.stage.rounds, n.rounds, n.gates, run_now, way.over, n.waits, passed)
        n.history = _history(n, rec, way, pending, moved)
        h = n.history[-1] if n.history else None
        if h is not None and (h["status"], h["doing"]) != (n.status.status, n.status.doing):
            n.history.append({"at": max(n.status.since or h["at"], h["at"]), "status": n.status.status,
                              "doing": n.status.doing, "who": n.status.who})
        n.spans = _spans(n, way.over, quiet or {})


def node_of(nodes: Sequence[Node], e: Mapping[str, Any], intent: Optional[str]) -> Optional[Node]:
    """The node a Lean record is about, by ROUTE: its stage's (and unit's); the node whose document an approval covers
    (INCEPTION covers two: the design's, where its request is drawn); build-and-test for a VERIFY; else none."""
    route, d = ROUTE.get(str(e.get("event"))), _data(e)
    if route == "stage":
        nid = node_id(str(d.get("stage") or ""), d.get("unit") or None)
        return next((n for n in nodes if n.id == nid), None)
    if route == "gate":
        return next((n for n in reversed(nodes) if _feeds(n.stage, n.unit, e, doc_path(n.stage, n.unit, intent))), None)
    if route == "verify":
        return next((n for n in nodes if n.stage.id == "build-and-test"), None)
    return None


def round_at(nodes: Sequence[Node], run: RunKey, t: float, began: bool = False) -> Optional[Tuple[Node, Round]]:
    """Where *run* was at *t*: of its rounds open then (begun, not ended), the latest begun, and its node; *began*:
    of all it had begun by then, the one it was at last (open then, else the latest to end: 逆向工程 done inside 需求分析
    leaves it at 需求分析). A stage it skipped is no place it was at: the graph draws no box for it."""
    hits = [(t if r.ended is None or t <= r.ended else r.ended, r.started, i, n, r) for i, n in enumerate(nodes)
            for r in n.rounds if r.run == run and r.end.outcome is not Outcome.SKIPPED and r.started <= t
            and (began or r.ended is None or t <= r.ended)]
    return max(hits, key=lambda x: x[:3])[3:] if hits else None


def round_on(node: Node, run: Optional[RunKey], t: float, opens: bool = False) -> Optional[Round]:
    """The round of *node* a moment *t* belongs to: the one holding it (*run*'s first), else the latest begun before.
    Where one round ends as the next begins, a record that opens a round (*opens*) is the new one's, any other the
    one it ends (a red VERIFY ends its round as the fix begins)."""
    held = [r for r in node.rounds if r.started <= t and (r.ended is None or t <= r.ended)]
    held = [r for r in held if r.run == run] or held
    held = [r for r in held if (r.started == t) == opens] or held
    before = [r for r in node.rounds if r.started <= t]
    return held[-1] if held else before[-1] if before else None
