"""Who did each round of a requirement: its runs. Each Lean clone writes its own ledger file
(aidlc-docs/audit/<host>-<6 hex>.jsonl: aidlc_lean.py ledger_file), a run on the team runner works in a fresh worktree,
so in a fresh clone with a fresh file, and a small requirement of a split names its clone in run.json (lean_clone). So
a record's file says who wrote it: this requirement's own clone (the owner's session; after a hand-over, the runner's
runs, told apart by the hand-over requests), a small requirement's (child-<KEY>) or, on a small requirement's board, its
big one's (parent-<KEY>). A commit has no file: a bot's is the runner run of its time, a person's after the hand-over is
that run's too (someone pushed into it), any other is the local run's.

A file's name holds the clone's host: it is never sent. A run is local, runner-<n>, child-<KEY> or parent-<KEY>.
Pure: no I/O here.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Tuple

from . import lean_view, problems
from .stages import GATE_WORDS, Node, Outcome, RunKey, _line, _name

LOCAL: RunKey = "local"
# when the team runner took a run it started itself (从 issue 接活): before every record, as the first bound of its runs;
# no hand-over, so never a relay
FROM_START = float("-inf")
FileRole = Literal["own", "child", "parent"]
CLONE_RE = re.compile(r"^[0-9a-f]{6}$")                                    # runrec: lean_clone
RUN_ID_RE = re.compile(r"^RUN-[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}-([1-9][0-9]{0,5})$")


@dataclass(frozen=True)
class Child:
    """A small requirement of a split, as its big one's board sees it."""
    key: str
    unit: Optional[str]
    state: str                       # family.CHILD_STATES
    owner: Optional[str] = None
    clone: Optional[str] = None      # its run.json lean_clone, when known here
    team: Optional[str] = None       # its Lean team: the unit, as Lean stores a team


@dataclass(frozen=True)
class Family:
    """A split as one requirement's board sees it: a small requirement (role child) with its big one, its unit, its
    clone and Lean team; or a big one (role parent) with its small ones."""
    role: Literal["child", "parent"]
    parent: Optional[str] = None
    unit: Optional[str] = None
    clone: Optional[str] = None
    team: Optional[str] = None
    children: Tuple[Child, ...] = ()


def lean_team(unit: str) -> str:
    """The team `lean init --team <unit>` stores (brainkit.split.lean_team)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", unit)


def family_of(fam: Any, run: Any) -> Optional[Family]:
    """The row's family (family.families' ReqFamily) and run.json, as attribution reads them; None outside a split
    (a big requirement not split yet included)."""
    if not isinstance(fam, Mapping) or fam.get("role") not in ("child", "parent"):
        return None
    run = run if isinstance(run, Mapping) else {}
    if fam["role"] == "child":
        unit = fam.get("unit") or run.get("unit")
        unit = unit if isinstance(unit, str) and unit else None
        clone = str(run.get("lean_clone") or "")
        team = run.get("lean_team") if isinstance(run.get("lean_team"), str) and run.get("lean_team") else \
            lean_team(unit) if unit else None
        return Family("child", parent=fam.get("parent"), unit=unit, clone=clone if CLONE_RE.match(clone) else None,
                      team=team)
    return Family("parent", children=tuple(
        Child(str(c["key"]), c.get("unit") or None, str(c.get("state") or ""), c.get("owner"),
              team=lean_team(c["unit"]) if c.get("unit") else None)
        for c in fam.get("children") or [] if isinstance(c, Mapping) and c.get("key")))


def _team(e: Mapping[str, Any]) -> Optional[str]:
    a = e.get("actor")
    return a.get("team") if isinstance(a, Mapping) and isinstance(a.get("team"), str) and a.get("team") else None


def file_roles(evs: Sequence[Mapping[str, Any]], family: Optional[Family]) -> Dict[str, Tuple[FileRole, Optional[str]]]:
    """Each ledger file -> (its role, whose): a small requirement's (its name ends with -<lean_clone>.jsonl; without a
    clone known here, its records carry that unit's Lean team: brainkit.trace's rule); on a small requirement's board,
    each file that is not its own clone's is its big one's; any other is this requirement's own."""
    teams: Dict[str, set] = {}
    for e in evs:
        if e.get("_file"):
            teams.setdefault(str(e["_file"]), set()).update({_team(e)} - {None})

    def of(path: str, clone: Optional[str], team: Optional[str]) -> bool:
        return path.endswith("-%s.jsonl" % clone) if clone else bool(team and team in teams[path])

    out: Dict[str, Tuple[FileRole, Optional[str]]] = {}
    for path in teams:
        out[path] = ("own", None)
        if family is not None and family.role == "child" and (family.clone or family.team):
            if not of(path, family.clone, family.team):
                out[path] = ("parent", family.parent)
        elif family is not None and family.role == "parent":
            hit = next((c for c in family.children if of(path, c.clone, c.team)), None)
            if hit is not None:
                out[path] = ("child", hit.key)
    return out


def boundaries(f: Mapping[str, Any], t: Optional[Mapping[str, Any]]) -> List[float]:
    """The hand-over requests, oldest first: the trace's (each `brain handoff`), else run.json's handoff."""
    hands = ((t or {}).get("actions") or {}).get("handovers") or []
    out = sorted({at for at in (lean_view.epoch(h.get("at")) for h in hands
                                if isinstance(h, Mapping) and h.get("kind") == "requested") if at is not None})
    if not out:
        ho = (f.get("run") or {}).get("handoff") if isinstance(f.get("run"), Mapping) else None
        at = lean_view.epoch(ho.get("requested_at")) if isinstance(ho, Mapping) else None
        out = [at] if at is not None else []
    return out


@dataclass(frozen=True)
class Runner:
    """A run on the team runner: 运行机 第 n 次, the hand-over request it followed, its run id when known."""
    n: int
    key: RunKey
    after: float
    run_id: Optional[str]


@dataclass(frozen=True)
class Attribution:
    """run_of: who did a record (a ledger record by its file, a commit by who made it and when, a write-back: this
    machine's run). runners: the runner runs, oldest first."""
    roles: Mapping[str, Tuple[FileRole, Optional[str]]]
    bounds: Tuple[float, ...]
    local_files: FrozenSet[str]
    runners: Tuple[Runner, ...]

    def runner_at(self, at: Any) -> Optional[RunKey]:
        """The runner run at *at*: the one that followed the latest hand-over before it."""
        if not isinstance(at, (int, float)):
            return None
        hit = [r for r in self.runners if r.after <= at]
        return hit[-1].key if hit else None

    def run_of(self, x: Mapping[str, Any]) -> RunKey:
        if "_file" in x:
            role, whose = self.roles.get(str(x["_file"]), ("own", None))
            if role != "own":
                return "%s-%s" % (role, whose)
            return LOCAL if x["_file"] in self.local_files else self.runner_at(x.get("_at")) or LOCAL
        if "reflect" in x:              # brain reflect runs from this app's Brain page
            return LOCAL
        if x.get("by") == "bot" or x.get("after_handover"):
            return self.runner_at(x.get("at")) or LOCAL
        return LOCAL


def attribute(evs: Sequence[Mapping[str, Any]], roles: Mapping[str, Tuple[FileRole, Optional[str]]],
              bounds: Sequence[float], run_ids: Sequence[str] = (),
              commits: Sequence[Mapping[str, Any]] = ()) -> Attribution:
    """Who did each record. An own ledger file whose first record is before the first hand-over request is the local
    run's, all of it: the owner's session may still write after the hand-over. An own file that starts after one is
    the runner's, its records split at the later requests (a worktree used again). Runner runs are numbered over the
    requests that have runner records after them (its ledger records, or a bot's commits: *commits*, with at), so a
    hand-over the bridge refused takes no number. *run_ids* (this branch's RUN-<KEY>-<n>) name them in order when
    there are as many."""
    bounds = tuple(sorted(bounds))
    first: Dict[str, float] = {}
    for e in evs:
        f, at = e.get("_file"), e.get("_at")
        if f and roles.get(str(f), ("own", None))[0] == "own" and isinstance(at, (int, float)):
            first[str(f)] = min(first.get(str(f), at), at)
    local = frozenset(f for f, at in first.items() if not bounds or at < bounds[0])

    def after(at: Any) -> int:
        return bisect.bisect_right(bounds, at) - 1 if isinstance(at, (int, float)) else -1

    used = {after(e.get("_at")) for e in evs if str(e.get("_file")) in first and str(e.get("_file")) not in local}
    used |= {after(c.get("at")) for c in commits if c.get("by") == "bot"}
    used.discard(-1)
    ids = sorted({x for x in run_ids if isinstance(x, str) and RUN_ID_RE.match(x)},
                 key=lambda x: int(RUN_ID_RE.match(x).group(1)))
    named = len(ids) == len(used)
    return Attribution(dict(roles), bounds, local, tuple(
        Runner(k + 1, "runner-%d" % (k + 1), bounds[s], ids[k] if named else None) for k, s in enumerate(sorted(used))))


# ---------------------------------------------------------------- the runs, what each does now, the hand-overs

@dataclass(frozen=True)
class RunNow:
    """A run's state now (wire: RunNow): for the roster, and the command path to the node it is at. *do*: the step
    (Waits.do) its person takes while it waits on them, so the page names it under each waiting run."""
    status: str
    doing: str
    since: Optional[float]
    who: Optional[str] = None
    node: Optional[str] = None
    do: Optional[str] = None


@dataclass
class RunView:
    key: RunKey
    kind: Literal["local", "runner", "child", "parent"]
    label: str
    readable: Literal["here", "elsewhere", "none"]
    n: Optional[int] = None
    run_id: Optional[str] = None
    req: Optional[str] = None
    unit: Optional[str] = None
    now: Optional[RunNow] = None
    credits: Optional[float] = None
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    tail: Optional[Mapping[str, Any]] = None     # what its session here did last (sessions.tail): the busy one's, else the latest's


def _here(sessions: Sequence[Mapping[str, Any]]
          ) -> Tuple[List[Dict[str, Any]], Optional[float], Optional[Mapping[str, Any]]]:
    rows = [{"slot": s["slot"], "url": s["url"], "busy": bool(s.get("busy")), "title": str(s.get("title") or ""),
             "agent": str(s.get("agent") or "")} for s in sessions]
    last = next((s for s in sessions if s.get("busy")), sessions[-1] if sessions else None)
    return rows, (round(sum(s.get("credits") or 0 for s in sessions), 3) or None) if sessions else None, \
        (last or {}).get("tail")


def runs(nodes: Sequence[Node], who: Attribution, *, started: bool, family: Optional[Family],
         sessions: Sequence[Mapping[str, Any]], owner: Optional[str], mine: bool, team: bool) -> List[RunView]:
    """The runs, in the order of their first round, the local one first: the roster under the orchestrator box and
    who did each round. The local run is there once run.json is (*started*), round or not; a runner run once it has a
    record. Its sessions and credits are the ones on this machine: the local run's on a person's machine (the
    reader's own, *mine*), the runner's on the team runner (*team*); a small or big requirement's are elsewhere."""
    first: Dict[str, float] = {}
    for n in nodes:
        for r in n.rounds:
            first[r.run] = min(first.get(r.run, r.started), r.started)
    for x in who.runners:
        first.setdefault(x.key, x.after)
    if started:
        first.setdefault(LOCAL, float("-inf"))
    live = [s for s in sessions if not s.get("missing")]
    out: List[RunView] = []
    for key in sorted(first, key=lambda k: (k != LOCAL, first[k])):
        if key == LOCAL:
            mine_here = [s for s in live if s.get("kind", "local") == "local"] if not team else []
            here = not team and (mine or bool(mine_here))
            rows, credits, tail = _here(mine_here)
            out.append(RunView(LOCAL, "local", "本机会话" if here else _line(_name(owner or "负责人"), "的会话"),
                               "here" if here else "elsewhere", sessions=rows, credits=credits, tail=tail))
        elif key.startswith("runner-"):
            x = next(r for r in who.runners if r.key == key)
            theirs = [s for s in live if s.get("kind") == "runner" and (
                x.run_id in "%s %s" % (s.get("slot"), s.get("title")) if x.run_id else x is who.runners[-1])] if team else []
            rows, credits, tail = _here(theirs)
            out.append(RunView(key, "runner", "运行机 第 %d 次" % x.n, "here" if team else "elsewhere", n=x.n,
                               run_id=x.run_id, sessions=rows, credits=credits, tail=tail))
        else:
            kind, _, req = key.partition("-")
            unit = next((c.unit for c in (family.children if family else ()) if c.key == req), None)
            out.append(RunView(key, "child" if kind == "child" else "parent",
                               ("小需求 %s" if kind == "child" else "大需求 %s") % req, "elsewhere", req=req,
                               unit=unit if kind == "child" else None))
    return out


def node_now(key: RunKey, nodes: Sequence[Node]) -> Optional[str]:
    """The node a run is at: the one of its open round (the latest), else the next on the way after its last round (a
    stage it skipped is not on the way: CI 流水线 skipped after 构建测试 leaves 审查 next)."""
    open_ = [(r.started, n.id) for n in nodes for r in n.rounds if r.run == key and r.end.outcome is Outcome.OPEN]
    if open_:
        return max(open_)[1]
    done = [i for i, n in enumerate(nodes) if any(r.run == key and r.end.outcome is not Outcome.SKIPPED for r in n.rounds)]
    after = nodes[max(done) + 1:] if done else nodes
    return next((n.id for n in after if not n.rounds and n.child is None and n.waits.status == "waiting"), None)


def overlay(rs: Sequence[RunView], nodes: Sequence[Node], *, row: Mapping[str, Any],
            sessions: Sequence[Mapping[str, Any]], hand_at: Optional[float], last_at: Mapping[RunKey, float],
            over: Optional[Tuple[str, float]], you: str, approvals: Sequence[Mapping[str, Any]], now: float,
            live_slot: Optional[str] = None) -> None:
    """What each run is doing now, which is not an event: the local session waiting for a tool approval, busy,
    quiet (轮到你回话), missing; a run that stopped to ask; the runner's run by the row's state; the earlier ones over.
    The local session is the live dev session (*live_slot*: service._live's), never a review session beside it.
    Each state is stamped with the moment it began (the row's waits.since, else the record or session that says it),
    never this poll's time."""
    state, waits = row.get("state"), row.get("waits") or {}
    since = waits.get("since")
    who_you = "you" if you == "你" else "people"
    live = next((s for s in sessions if live_slot and s.get("slot") == live_slot), None)
    latest = max((r for r in rs if r.kind == "runner"), key=lambda r: r.n or 0, default=None)
    nodes_by = {n.id: n for n in nodes}

    def at_work(r: RunView, status: str, doing: str, at: Optional[float], who: Optional[str] = None,
                do: Optional[str] = None) -> RunNow:
        """A run at work, or waiting on a person, and the node it is at; one that waits on nobody is at none."""
        return RunNow(status, doing, at, who, node_now(r.key, nodes) if status != "waiting" else None, do)

    def doing_at(r: RunView) -> str:
        n = nodes_by.get(node_now(r.key, nodes) or "")
        return "在做：%s" % n.stage.label if n is not None else "在做"

    for r in rs:
        last = last_at.get(r.key)
        if r.kind == "child":
            r.now = RunNow("merged", "已合进来了", last)
        elif r.kind == "parent":
            r.now = RunNow("done", "做完了", last)
        elif over is not None:
            r.now = RunNow("skipped", "需求已关闭", over[1]) if over[0] == "closed" else \
                RunNow("merged", "已合进大需求" if state == "merged_into_parent" else "已合并", over[1])
        elif r.kind == "runner" and r is not latest:
            r.now = RunNow("done", "这一次结束了：之后又交接了一次", last)
        elif r.kind == "runner":
            if state == "parked":
                # once answered on the MR it waits on the bridge or on itself, not on a person (Waits.do none)
                r.now = at_work(r, "awaiting_approval", waits.get("text") or "停下等人回答", since or last,
                                waits.get("who") or "people", "answer" if waits.get("do") == "answer" else None)
            elif state == "acceptance":
                r.now = at_work(r, "awaiting_approval", waits.get("text") or "等产品验收", since or last,
                                waits.get("who") or "product", "accept")
            elif state == "to_merge":
                r.now = at_work(r, "awaiting_approval", waits.get("text") or "验收通过了：等人合并", since or last,
                                waits.get("who") or "people", "merge")
            elif state == "handed_over":
                r.now = at_work(r, "queued", waits.get("text") or "运行机在准备工作区", since or last)
            elif state == "rejected":
                r.now = RunNow("done", "这一次结束了", last)
            else:
                busy = any(s.get("busy") for s in sessions if s.get("kind") == "runner")
                r.now = at_work(r, "running", "AI 在做" if r.readable == "here" and busy else doing_at(r), last)
        elif hand_at is not None:
            if waits.get("do") == "push_handoff":
                r.now = RunNow("awaiting_approval", "交接没推上去：推上去以后再交接", since or hand_at, who_you,
                               do="push_handoff")
            elif state == "rejected":
                r.now = RunNow("awaiting_approval", "接手被拒：改好以后重新交接", since or hand_at, who_you, do="redo_handoff")
            elif state == "handed_over" and waits.get("who") == "you":
                r.now = RunNow("awaiting_approval", "推上去了，但 MR 上没有交接标签", since or hand_at, who_you,
                               do="label_handoff")
            else:
                r.now = RunNow("done", "做完 · 交出去了", hand_at)
        elif state == "claimed":
            r.now = RunNow("waiting", "认领了，还没开工", since)
        elif state == "parked":
            r.now = at_work(r, "awaiting_approval", waits.get("text") or "停下等你回答", since or last,
                            waits.get("who") or who_you, "answer")
        elif you != "你":
            r.now = at_work(r, "running", waits.get("text") or "在负责人自己的机器上做", last)
        else:
            s = live
            pend = next((a for a in approvals if a.get("state") == "pending" and a.get("line")), None)
            seen = (s or {}).get("last_activity")    # KiroCrew's: a number, or an ISO time
            seen = lean_view.epoch(seen) if isinstance(seen, str) else seen
            quiet = since if waits.get("do") in ("reply", "tool", "approve") and since is not None else max(
                [x for x in (seen, last) if isinstance(x, (int, float))], default=now)
            if s is not None and s.get("awaiting"):
                r.now = at_work(r, "awaiting_approval", "会话里有一个工具调用等你批准", quiet, "you", "tool")
            elif s is not None and s.get("busy"):
                r.now = at_work(r, "running", "AI 在做", last)
            elif pend is not None:
                r.now = at_work(r, "awaiting_approval", "等你批%s" % GATE_WORDS.get(str(pend.get("kind")), ""),
                                since or pend.get("at") or quiet, "you", "approve")
            elif waits.get("do") == "none" and waits.get("who") not in (None, "you", "agent"):
                r.now = at_work(r, "waiting", waits.get("text") or "没有等你的事", quiet)
            elif s is not None:
                r.now = at_work(r, "awaiting_approval", "会话空着：轮到你回话", quiet, "you", "reply")
            else:
                r.now = at_work(r, "stalled", "这台机器上还没有它的会话：开一个", quiet, do="reply")


def _step(key: str, title: str, status: str, at: Optional[float], text: str, todo: Optional[str] = None) -> Dict[str, Any]:
    return {"key": key, "title": title, "status": status, "at": at, "text": text, "todo": todo}


def hand_phase(row: Mapping[str, Any]) -> str:
    """Where the latest hand-over is, by the row's state (deliveries.classify, team_classify): unpushed (committed here,
    its push failed: nothing went out), unlabelled (pushed, no
    label on the MR), checking (the bridge checks it), refused, signing (the team runner's ledger has it: the signer
    signs), preparing (signed: the runner builds the worktree), started (the runner's run began), none."""
    state, w = row.get("state"), row.get("waits") or {}
    if w.get("do") == "push_handoff":
        return "unpushed"
    if state == "rejected":
        return "refused"
    if state in ("running", "parked", "acceptance", "to_merge", "merged"):
        return "started"
    if state != "handed_over":
        return "none"
    if w.get("who") == "you":
        return "unlabelled"
    if w.get("who") == "bridge":
        return "signing" if row.get("where") == "runner" else "checking"
    return "preparing"


def relays(bounds: Sequence[float], t: Optional[Mapping[str, Any]], row: Mapping[str, Any], who: Attribution,
           starts: Mapping[RunKey, float]) -> List[Dict[str, Any]]:
    """One per hand-over request, oldest first: 交接 → 桥核对 → 签名 → 运行机建工作区. A step is done when a record says
    so (the bridge's answer and the voucher, when this machine has them; the runner run's first record, *starts*, which
    also says the steps before it were done) and, for the latest request, when the row's state says so (hand_phase).
    A step that is stuck says what to do."""
    hands = sorted(((lean_view.epoch(h.get("at")), h) for h in ((t or {}).get("actions") or {}).get("handovers") or []
                    if isinstance(h, Mapping) and lean_view.epoch(h.get("at")) is not None), key=lambda x: x[0])
    waits = row.get("waits") or {}
    since = waits.get("since") if isinstance(waits.get("since"), (int, float)) else None
    out = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else float("inf")
        mine = [(at, h) for at, h in hands if b <= at < end]
        sha = next((str(h.get("commit") or "") for at, h in hands if at == b and h.get("kind") == "requested"), "")
        acc = next(((at, h) for at, h in mine if h.get("kind") == "bridge accepted"), None)
        rej = next(((at, h) for at, h in mine if h.get("kind") == "bridge rejected"), None)
        vou = next(((at, h) for at, h in mine if h.get("kind") == "voucher"), None)
        runner = next((x for x in who.runners if x.after == b), None)
        began = starts.get(runner.key) if runner is not None else None
        phase = hand_phase(row) if i == len(bounds) - 1 else "started" if runner is not None else \
            "refused" if rej is not None else "none"
        later = phase in ("signing", "preparing", "started")
        if phase == "unpushed":
            steps = [_step("handover", "交接", "failed", b, "交接提交了，但没推上去",
                           "推上去以后，在需求现状再点「交接给运行机」"),
                     _step("bridge", "桥核对", "waiting", None, "什么也没交出去：桥收不到"),
                     _step("signer", "签名", "waiting", None, "等桥核对完"),
                     _step("workspace", "运行机建工作区", "waiting", None, "等签名")]
            out.append({"to": None, "at": b, "sha": sha[:12] or None, "steps": steps})
            continue
        steps = [_step("handover", "交接", "stalled" if phase == "unlabelled" else "merged", b,
                       "推上去了，但 MR 上没有交接标签" if phase == "unlabelled" else
                       "交接了%s" % ("（提交 %s）" % sha[:8] if sha else ""),
                       "在 MR 上补上交接标签，或者在分支上再交接一次" if phase == "unlabelled" else None)]
        if acc is not None:
            iid = acc[1].get("mr_iid")
            steps.append(_step("bridge", "桥核对", "merged", acc[0], "桥接手了%s" % ("（MR !%s）" % iid if iid else "")))
        elif rej is not None or phase == "refused":
            raw = str(rej[1].get("reason") or "") if rej is not None else ""
            why = problems.reasons_text(raw.split("; "), 90)[0] if raw else \
                str(waits.get("text") or "").replace("接手被拒：", "")
            steps.append(_step("bridge", "桥核对", "failed", rej[0] if rej is not None else since,
                               "拒绝接手%s" % ("：%s" % why if why else ""),
                               (str(waits.get("todo") or "") or "改好以后在分支上重新交接") if i == len(bounds) - 1 else None))
        elif began is not None or later:
            steps.append(_step("bridge", "桥核对", "merged", began if began is not None else since, "桥接手了"))
        elif phase == "checking":
            steps.append(_step("bridge", "桥核对", "running", since or b, "在核对：标签、提交、材料"))
        else:
            steps.append(_step("bridge", "桥核对", "waiting", None,
                               "MR 上没有交接标签：桥不会接" if phase == "unlabelled" else "桥还没有核对"))
        if vou is not None:
            steps.append(_step("signer", "签名", "merged", vou[0], "签了接手合同"))
        elif began is not None or phase in ("preparing", "started"):
            steps.append(_step("signer", "签名", "merged", began if began is not None else since, "签了接手合同"))
        elif phase == "signing":
            steps.append(_step("signer", "签名", "running", since or b, "在签接手合同"))
        else:
            steps.append(_step("signer", "签名", "waiting", None, "等桥核对完"))
        if began is not None or phase == "started":
            steps.append(_step("workspace", "运行机建工作区", "merged", began if began is not None else since,
                               "运行机开工了"))
        elif phase == "preparing":
            stuck = waits.get("who") == "people"
            steps.append(_step("workspace", "运行机建工作区", "stalled" if stuck else "running", since or b,
                               str(waits.get("text") or "运行机在准备工作区"),
                               (str(waits.get("todo") or "") or "请管运行机的人看一下") if stuck else None))
        else:
            steps.append(_step("workspace", "运行机建工作区", "waiting", None, "等签好合同"))
        out.append({"to": runner.key if runner is not None else None, "at": b, "sha": sha[:12] or None, "steps": steps})
    return out
