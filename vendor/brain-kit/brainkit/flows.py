"""A run's flow (run.json "mode") and every rule that differs by flow.

  LEAN  run.json without "mode": an AI-DLC Lean intent carries the run through Lean's stages and approvals. It ends
        with a hand-over to the team runner (`brain handoff`), or a release on this machine.
  FAST  "mode": "fast" (快速模式): no intent, no stages, no approvals. A commit still needs a green `lean verify` (Lean's
        commit gate), CI still holds the risk-path floor, and the run ends with an MR a person merges (`brain mr`;
        MR 巡检 may work it). `brain full` turns it into a LEAN run on the same branch.

TABLE holds what is decided per flow: its label, whether a Lean intent carries it, and what a person may do with it.
The other rules are functions here, derived from those, so no row can say something impossible (a run without an
intent that asks for a DESIGN approval, or goes to the runner, whose first step is `lean use <intent>`). Callers ask
this module and never read "mode" themselves: brain start, status, handoff, mr, full, split, reflect, mr-desc,
brain-ci, the bridge, and the Brain app.
"""
from __future__ import annotations

import enum
import json
import os
import subprocess
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, FrozenSet, List, Mapping, Optional, Tuple

from . import config, lean
from .common import JIRA_RE, BrainError


class Action(str, enum.Enum):
    """What a person may do with a run besides talking to its session. action_problem says why not, and what to do
    instead, on a flow that lacks it."""
    HANDOFF = "handoff"   # `brain handoff`: the team runner takes the run (bridge, signer, runnerd)
    REVIEW = "review"     # the Brain app's 开审查会话: a fresh session writes Lean's release review of the intent
    MR = "mr"             # `brain mr`: push, open the MR without a hand-over label; a person merges it
    FULL = "full"         # `brain full`: open a Lean intent on this branch; the run becomes LEAN


@dataclass(frozen=True)
class Row:
    label: str                   # how a person names the flow (CLI messages, the app)
    intent: bool                 # a Lean intent carries the run: run.json names its folder, else null
    actions: FrozenSet[Action]


class Flow(str, enum.Enum):
    """run.json "mode". A str enum: a member compares and hashes as its value, so the app keys its own tables by
    "lean" and "fast". Format it with .value: str() of a member is "Flow.FAST"."""
    LEAN = "lean"
    FAST = "fast"

    @property
    def label(self) -> str:
        return TABLE[self].label

    @property
    def intent(self) -> bool:
        return TABLE[self].intent

    @property
    def actions(self) -> FrozenSet[Action]:
        return TABLE[self].actions


TABLE: Mapping[Flow, Row] = MappingProxyType({
    # a fast run ends with an MR that a person merges (MR 巡检 may work it): it is never handed over to the runner
    Flow.LEAN: Row("完整流程", intent=True, actions=frozenset({Action.HANDOFF, Action.REVIEW})),
    Flow.FAST: Row("快速", intent=False, actions=frozenset({Action.MR, Action.FULL})),
})
DEFAULT = Flow.LEAN   # a run.json without "mode": runrec.new writes "mode" only for another flow


def of(rec) -> Flow:
    """The flow of a run record: its "mode", DEFAULT when it has none. BrainError for any other value: runrec.validate
    reports it as a problem, so a validated record never raises here."""
    if not isinstance(rec, Mapping):
        raise BrainError("run.json is not an object")
    mode = rec.get("mode", DEFAULT.value)
    try:
        return Flow(mode)
    except ValueError:
        raise BrainError("mode %r is not one of %s" % (mode, ", ".join(f.value for f in Flow))) from None


# ---------- where a run without an intent may start and merge ----------

@dataclass(frozen=True)
class Policy:
    """What one branch says about flows: the run's target (origin's default branch at a start, the MR's target in CI).
    policy_at reads it; refusals judges it."""
    fast_mode: bool       # .brain/kit.json "fast_mode" (missing: true, config.DEFAULTS)
    regulated: bool       # aidlc-docs/lean.json "regulated", as Lean reads it
    lean_version: str     # VERSION of .kiro/aidlc-lean/aidlc_lean.py there ('' when there is none)
    lean: bool            # aidlc-docs/lean.json is there and is a JSON object: AI-DLC Lean is set up on that branch


def _text(repo, ref, path) -> Optional[str]:
    if ref is None:
        try:
            with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None
    # bytes read as UTF-8, as the checkout's file above: git()'s text is in the locale's encoding (GBK on some machines)
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (ref, path)], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def _kit(repo, ref) -> dict:
    if ref is None:
        return config.load(repo)
    where = "%s on %s" % (config.KIT_CONFIG, ref)
    raw = _text(repo, ref, config.KIT_CONFIG)
    if raw is None:
        raise BrainError("%s is missing" % where)
    try:
        kit = json.loads(raw)
    except ValueError as e:
        raise BrainError("%s is not valid JSON: %s" % (where, e))
    if not isinstance(kit, dict):
        raise BrainError("%s is not a JSON object" % where)
    cfg = config.merged(config.DEFAULTS, kit)
    probs = config.validate(cfg)
    if probs:
        raise BrainError("%s: %s" % (where, "; ".join(probs)))
    return cfg


def policy_at(repo: str, ref: Optional[str]) -> Policy:
    """The Policy of `ref` (None: this checkout's own files, as an untrusted local check reads them). BrainError when
    kit.json is missing or not valid there."""
    cfg = _kit(repo, ref)
    try:
        lcfg = json.loads(_text(repo, ref, lean.CONFIG) or "")
    except ValueError:
        lcfg = None
    has = isinstance(lcfg, dict)
    return Policy(fast_mode=cfg["fast_mode"], regulated=has and bool(lcfg.get("regulated")),
                  lean_version=lean.version_in(_text(repo, ref, lean.SCRIPT.replace(os.sep, "/")) or ""), lean=has)


class Refusal(str, enum.Enum):
    """Why a run without an intent may not start, or its MR merge, on a branch. In the order refusals() lists them:
    what lasts longest first, then the team's switch, then what an upgrade fixes, then the request itself."""
    NO_LEAN = "no_lean"         # no aidlc-docs/lean.json: a fast run still needs Lean's commit gate
    REGULATED = "regulated"     # lean.json regulated: Lean refuses code without an intent
    OFF = "off"                 # kit.json fast_mode: false (the Brain app's 允许快速模式)
    LEAN_OLD = "lean_old"       # Lean older than 0.3.2: without `use --none` its single-intent fallback gates the run
    CHILD = "child"             # a child of a split continues its parent's Lean intent
    BIG = "big"                 # a big requirement (the app's 大需求) is split by Lean's design

    @property
    def en(self) -> str:
        """For the CLI and CI, whose other messages are English."""
        return _REASONS[self][0]

    @property
    def zh(self) -> str:
        """For the Brain app, which shows this sentence for any code."""
        return _REASONS[self][1]


_REASONS: Mapping[Refusal, Tuple[str, str]] = MappingProxyType({
    Refusal.NO_LEAN: ("AI-DLC Lean is not set up on the target branch (no aidlc-docs/lean.json), and a fast run still "
                      "needs its commit gate (lean verify)", "默认分支上没装 AI-DLC Lean：快速模式也要它的提交前检查"),
    Refusal.REGULATED: ("the target branch's aidlc-docs/lean.json is regulated: every change goes through a Lean "
                        "intent", "合规模式的仓库（lean.json 里 regulated 是 true）每个改动都走 Lean：不能用快速模式"),
    Refusal.OFF: ("the team switched fast mode off (fast_mode: false in .brain/kit.json on the target branch)",
                  "团队关了快速模式（默认分支上 .brain/kit.json 的 fast_mode 是 false）"),
    Refusal.LEAN_OLD: ("the target branch's AI-DLC Lean is older than 0.3.2, which fast runs need (`lean use "
                       "--none`): run `brain install --upgrade` and merge its MR",
                       "默认分支上的 kit 文件比快速模式旧（要 AI-DLC Lean 0.3.2）：先更新 kit 文件，合并它的 MR"),
    Refusal.CHILD: ("a child of a split builds one unit of its parent's Lean intent, so it cannot be a fast run",
                    "拆出来的小需求跟着大需求的 Lean intent 做：不能用快速模式"),
    Refusal.BIG: ("a big requirement is split by Lean's design, so it cannot be a fast run",
                  "大需求要靠 Lean 的设计拆成小需求：不能用快速模式"),
})


def refusals(flow: Flow, policy: Callable[[], Policy], *, big: bool = False, child: bool = False) -> List[Refusal]:
    """Why a run of `flow` may not start, or its MR merge, on the branch `policy` reads ([]: it may). The one rule
    for "fast is allowed here": brain start --fast refuses with these, brain-ci check-run fails with them, the Brain
    app offers 快速 only when there are none. `policy` is called only for a flow without an intent, so a LEAN start
    reads nothing more than before. `big` (the app's 大需求) and `child` (a split child's start) are the request's."""
    if flow.intent:
        return []
    p = policy()
    holds = {Refusal.NO_LEAN: not p.lean, Refusal.REGULATED: p.regulated, Refusal.OFF: not p.fast_mode,
             Refusal.LEAN_OLD: p.lean and not lean.holds_no_intent(p.lean_version), Refusal.CHILD: child,
             Refusal.BIG: big}
    return [r for r in Refusal if holds[r]]


# ---------- the clone, the ledger, the decisions ----------

def prepare_clone(flow: Flow, repo: str) -> None:
    """Put this clone's Lean selection where a session of `flow` needs it. Without an intent: `lean use --none` (Lean
    records nothing when it is so already), so Lean's single-intent fallback cannot gate the run with another
    requirement's intent, in this clone or a new worktree of the branch. LEAN: nothing (brain start and brain full
    select the intent). Callers: brain start --fast, the Brain app before each local session."""
    if not flow.intent:
        lean.use_none(repo)


def belongs(flow: Flow, run: Mapping, record: Mapping) -> bool:
    """Is this Lean ledger record (aidlc-docs/audit/*.jsonl) one of the run's? With an intent: the records that name it
    (a run.json whose intent is not a name owns none). Without: the records that name no intent and carry the run's
    branch as data.branch (VERIFY and SESSION_START do; HUMAN_TURN and BLOCKED do not, and are not shown)."""
    if flow.intent:
        intent = run.get("intent")
        return isinstance(intent, str) and bool(intent) and record.get("intent") == intent
    branch, d = run.get("branch"), record.get("data")
    return (record.get("intent") is None and isinstance(branch, str) and bool(branch) and isinstance(d, Mapping)
            and d.get("branch") == branch)


def decisions_path(flow: Flow, run: Mapping, runs_dir: str) -> str:
    """The repo path of the run's decisions.md (its decision table, brain-proposal and brain-gap blocks): in its
    intent's folder, aidlc-docs/<intent>/decisions.md; without an intent runs/<KEY>/decisions.md, as the steering's
    fast section tells the agent. BrainError when run.json's intent or key cannot name a path (the file is
    agent-writable)."""
    if flow.intent:
        from .runrec import INTENT_RE
        intent = run.get("intent")
        if not isinstance(intent, str) or not INTENT_RE.match(intent):
            raise BrainError("run.json names no Lean intent folder (yymmdd-slug)")
        return "%s/%s/decisions.md" % (lean.DOCS, intent)
    key = run.get("key")
    if not isinstance(key, str) or not JIRA_RE.match(key):
        raise BrainError("run.json key %r is not a requirement key" % (key,))
    return "%s/%s/decisions.md" % (runs_dir, key)


_REFUSED: Mapping[Tuple[Flow, Action], str] = MappingProxyType({
    (Flow.FAST, Action.HANDOFF): "快速需求以 MR 结束，由人合并，不交给团队运行机：用 brain mr 推送并开 MR；要交给运行机，"
                                 "先 brain full 转完整流程",
    (Flow.FAST, Action.REVIEW): "快速需求没有 Lean 的审查：看 MR 上的改动和 CI",
    (Flow.LEAN, Action.MR): "完整流程的需求做完用 brain handoff 交给运行机（只在本机做的仓库：在本机发布、合并）；"
                            "brain mr 只给快速需求用",
    (Flow.LEAN, Action.FULL): "已经是完整流程了",
})


def action_problem(flow: Flow, action: Action) -> str:
    """'' when `flow` has `action`, else why not and what to do instead (the person reads it in the CLI, in the bridge's
    comment on the MR, and in the app)."""
    return "" if action in flow.actions else _REFUSED[(flow, action)]
