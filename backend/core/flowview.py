"""How a requirement's run goes, from brain-kit's flows (brainkit/flows.py): the app's one door to it.

brain-kit decides every rule that differs by flow (lean: a Lean intent carries the run; fast, 快速: none, and it ends
with an MR a person merges): what a person may do with a run, which ledger records are its own, where its
decisions.md is, whether a repo offers fast runs. The app reads run.json from git, so read() is the boundary: every
requirement's facts carry its flow from here, and the views branch on that one value.
"""

from __future__ import annotations

from typing import Any, Callable

from . import bk

BROKEN = "run.json 的 mode 这个 Brain 不认识（%s）：用新版的 Brain app；是改坏了的话，在会话里让 AI 改回来"
LOCAL_ONLY = "这个仓库只在本机做（没有远端，或没写 GitLab 项目）：快速需求做完要开 MR，这里开不了"


def read(run: Any) -> dict[str, Any]:
    """The facts' flow and flow_error for a run.json (None: no run yet). A mode brain-kit does not know is said, never
    taken for another: the row says flow_error and the actions refuse; flow is then the default, so the page shows
    what it can."""
    fl = bk.m("flows")
    if run is None:
        return {"flow": fl.DEFAULT, "flow_error": ""}
    try:
        return {"flow": fl.of(run), "flow_error": ""}
    except bk.brain_error() as exc:
        return {"flow": fl.DEFAULT, "flow_error": BROKEN % str(exc)[:200]}


def wire(flow: Any) -> dict[str, Any] | None:
    """Requirement.flow (ui/src/types.ts FlowView); None for facts made without one."""
    if flow is None:
        return None
    return {"id": flow.value, "label": flow.label, "intent": flow.intent,
            "actions": sorted(a.value for a in flow.actions)}


def belongs(flow: Any, run: dict[str, Any]) -> Callable[[dict[str, Any]], bool]:
    """Which ledger records are the run's own (lean_view.events_where's filter)."""
    fl = bk.m("flows")
    return lambda rec: fl.belongs(flow, run, rec)


def decisions_path(flow: Any, run: dict[str, Any], runs_dir: str) -> str | None:
    """Where the run keeps its decisions.md; None when run.json names no path brain-kit takes."""
    try:
        return bk.m("flows").decisions_path(flow, run, runs_dir)
    except bk.brain_error():
        return None


def action_problem(flow: Any, action: str) -> str:
    """'' when a person may do *action* (a flows.Action value) with a run of *flow*, else brain-kit's why not."""
    fl = bk.m("flows")
    return fl.action_problem(flow, fl.Action(action))


def reads_own_mr(flow: Any) -> bool:
    """The run ends with its own MR (开 MR), so its row reads that MR before any hand-over."""
    return bk.m("flows").Action.MR in flow.actions


def offer(path: str, ref: str | None, *, local_only: bool) -> dict[str, Any]:
    """IntakeNext.fast: whether 开需求 offers 「快速：直接写代码」 for the repo at *path*, by its default branch at *ref*
    (origin's when fetched, else the local one, as a start branches from it), and why not in brain-kit's words."""
    fl = bk.m("flows")
    if local_only:
        return {"ok": False, "why": LOCAL_ONLY}
    try:
        why = fl.refusals(fl.Flow.FAST, lambda: fl.policy_at(path, ref))
    except bk.brain_error() as exc:
        return {"ok": False, "why": "默认分支上的 .brain/kit.json 读不了：%s" % str(exc)[:200]}
    return {"ok": not why, "why": why[0].zh if why else ""}
