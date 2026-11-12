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
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Tuple

from . import lean_view
from .stages import RunKey

LOCAL: RunKey = "local"
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
