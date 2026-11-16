"""产出's session tabs (它的回复 · 接到的任务 · 做了什么): the session turns a round was done in, for the 需求看板's drawer.

``GET /outputs/{repo}/{key}/turns?node&round`` (turn_views). Lean records each message its hook sees arrive in a
session (HUMAN_TURN: a person's or the app's, by the sha256 of its text, aidlc_lean.py hook_prompt). The same text is
a user row of the session's transcript, so the sha256 finds the row, and the rows after it, up to the next message, are
the turn: the agent's replies and its tool calls. A round was done in its run's turns from the one it started in to
the one its agent last worked on it in; a person's decision on it is typed in a turn of its own, the next round's.

What a turn's message shows (task), default deny: a row a person typed (meta.human) only its length and time, the owner
included, with a link to read it in the session; a message the app or the bridge sent, what SessionInfo.shown says
(written when it was sent, never read back from the transcript); anything else nothing. A round done where this
machine cannot read its session (the team runner, someone else's machine, a small requirement's own board, brain
reflect) gets a card instead (why_not): where its session is and what is here. Pure: the service reads the
transcripts (SessionManager.transcript) and passes them in.
"""

from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import sessions, stages
from .outputs import Fold, NodeQuery, pseudonymize, round_of, shown_text
from .runs import RunView

MATCH_S = 900.0        # a turn's row: the one with its sha256 nearest Lean's record in time, at most 15 minutes off
REPLY_MAX = 20_000     # characters of a reply sent to the page
CALLS_MAX = 400        # calls of a turn sent to the page
AGENT_ENDS = (stages.Outcome.DONE, stages.Outcome.SKIPPED, stages.Outcome.PASSED, stages.Outcome.FAILED)

HIDDEN = "这条消息没有在这里显示：它不是 app 发的，说不清是谁写的"
NOT_FOUND = "找不到这一回合的消息（会话可能被删了）：到 KiroCrew 里看"
NO_TURNS = "Lean 没记下这一轮是在哪个回合里做的：到会话里看"
NOT_STARTED = "这一步还没开始：开始以后，这里有它每一回合的回复、接到的消息和工具调用"
LONG = "\n\n（太长了：只显示了前 2 万个字，全文到会话里看）"


@dataclass(frozen=True)
class Slot:
    """One session of the round's run on this machine: its key and link, its project directory (a call's path is said
    relative to it), whether it is busy, its transcript (oldest first; *whole*: none of its rows was left out) and what
    the app showed of each message it sent there, by sendId (SessionInfo.shown; None: nothing)."""
    key: str
    url: str
    cwd: str
    busy: bool
    rows: Sequence[Mapping[str, Any]]
    whole: bool
    sent: Mapping[str, Optional[Mapping[str, str]]]


def _epoch(stamp: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", "surrogatepass")).hexdigest()


def _work_end(r: stages.Round) -> Optional[float]:
    """The last moment its agent worked on round *r*: its request, or an end the agent made (done, skipped, its
    checks); None while it is at work."""
    ends = ([r.ask.at] if r.ask is not None else []) + \
        ([r.ended] if r.end.outcome in AGENT_ENDS and r.ended is not None else [])
    return max(ends) if ends else r.ended


def round_turns(turns: Sequence[stages.Turn], r: stages.Round) -> List[stages.Turn]:
    """The turns round *r* was done in: its run's, from the one it started in to the one its agent last worked in."""
    mine = [t for t in turns if t.run == r.run]
    at = [t.at for t in mine]
    end = _work_end(r)
    return mine[max(0, bisect.bisect_right(at, r.started) - 1):len(mine) if end is None else bisect.bisect_right(at, end)]


def sessions_to_read(fold: Fold, q: NodeQuery) -> List[Mapping[str, Any]]:
    """The sessions (slot, url, busy) whose transcripts the round's turns are in: its run's on this machine, when it has
    turns to find; [] otherwise."""
    n, r = round_of(fold, q)
    if r is None or n.stage.rounds == "reflect" or not round_turns(fold.turns, r):
        return []
    run = next((x for x in fold.runs if x.key == r.run), None)
    return list(run.sessions) if run is not None and run.readable == "here" else []


def _task(m: Mapping[str, Any], s: Slot) -> Dict[str, Any]:
    meta = m["meta"] if isinstance(m.get("meta"), dict) else {}
    if meta.get("human"):
        return {"kind": "person", "chars": len(str(m.get("content") or "")), "url": s.url}
    shown = s.sent.get(str(meta.get("sendId") or ""))
    if shown:
        return {"kind": "bridge" if shown.get("by") == "bridge" else "app", "cause": shown.get("cause") or "",
                "text": shown_text(shown.get("text") or "", REPLY_MAX)[0]}
    return {"kind": "hidden", "why": HIDDEN}


def _call(m: Mapping[str, Any], s: Slot, lo: float, hi: float, scrub: Callable[[str], str]) -> Dict[str, Any]:
    """A call, its detail said relative to the worktree and scrubbed before tool_call cuts it to 120 characters."""
    c = sessions.tool_call(dict(m), lambda x: scrub(pseudonymize(x.replace(s.cwd.rstrip("/") + "/", "") if s.cwd else x)))
    at = c["at"] or None
    return {"at": at, "tool": sessions.call_name(c), "detail": c["detail"], "in_round": at is not None and lo <= at <= hi}


def _found(t: stages.Turn, s: Slot, i: int, n: Optional[int], lo: float, hi: float, also: List[str],
           scrub: Callable[[str], str]) -> Dict[str, Any]:
    """The turn whose message is row *i* of *s*: the rows after it, up to the next message. Its reply is cut at a line
    end and its emails are pseudonyms (outputs.shown_text), as the drawer's files are."""
    j = next((k for k in range(i + 1, len(s.rows)) if s.rows[k].get("role") == "user"), len(s.rows))
    body = s.rows[i + 1:j]
    said = [str(m.get("content")) for m in body if m.get("role") == "assistant" and str(m.get("content") or "").strip()]
    reply, cut = shown_text(said[-1], REPLY_MAX) if said else (None, False)
    calls = [_call(m, s, lo, hi, scrub) for m in body if m.get("role") == "tool"]
    stamps = [x for x in (_epoch(m.get("ts")) for m in body) if x is not None]
    return {"n": n, "at": _epoch(s.rows[i].get("ts")) or t.at, "until": max(stamps) if stamps else None, "slot": s.key,
            "url": s.url, "task": _task(s.rows[i], s),
            "reply": reply + LONG if cut else reply,
            "live": s.busy and j == len(s.rows), "calls": calls[:CALLS_MAX], "more_calls": max(0, len(calls) - CALLS_MAX),
            "also": also}


def _missing(t: stages.Turn, also: List[str]) -> Dict[str, Any]:
    return {"n": None, "at": t.at, "until": None, "slot": None, "url": None, "task": {"kind": "hidden", "why": NOT_FOUND},
            "reply": None, "live": False, "calls": [], "more_calls": 0, "also": also}


def _views(fold: Fold, n: stages.Node, r: stages.Round, slots: Sequence[Slot], lo: float, hi: float,
           scrub: Callable[[str], str]) -> List[Dict[str, Any]]:
    own = round_turns(fold.turns, r)
    others: Dict[stages.Turn, List[str]] = {}     # the other nodes a turn also worked on (也做了); a skip is no work
    for x in fold.nodes:
        work = [xr for xr in x.rounds if xr.run == r.run and xr.end.outcome is not stages.Outcome.SKIPPED]
        for xr in work if x.id != n.id else []:
            for t in round_turns(fold.turns, xr):
                others.setdefault(t, []).append(x.stage.label + (" · " + x.unit if x.unit else ""))
    rows: Dict[str, List[Tuple[float, Slot, int, int]]] = {}
    for s in slots:
        k = 0
        for i, m in enumerate(s.rows):
            if m.get("role") == "user":
                k += 1
                rows.setdefault(_sha(m.get("content")), []).append((_epoch(m.get("ts")) or 0.0, s, i, k))
    used: set = set()
    out = []
    for t in own:
        hit = min((c for c in rows.get(t.sha256, ()) if (c[1].key, c[2]) not in used and abs(c[0] - t.at) <= MATCH_S),
                  key=lambda c: abs(c[0] - t.at), default=None)
        also = list(dict.fromkeys(others.get(t, [])))
        if hit is None:
            out.append(_missing(t, also))
            continue
        _, s, i, k = hit
        used.add((s.key, i))
        out.append(_found(t, s, i, k if s.whole else None, lo, hi, also, scrub))
    return out


def why_not(n: stages.Node, r: stages.Round, run: Optional[RunView], key: str) -> Optional[Dict[str, Any]]:
    """A round whose session this machine cannot read: where it is, what is here instead, what to do, and when it asked
    for approval (*asked*); None when it is here. The page words Lean's record of the round from its times."""
    if n.stage.rounds == "reflect":
        return {"title": "这一步是 brain reflect 程序做的，没有 AI 会话", "missing": None,
                "here": ["它写进 Brain 的条目：在「产出文件」里"], "do": None, "asked": None}
    if run is not None and run.readable == "here":
        return None
    decided = next((e.decision for e in (r.decided, r.end) if e is not None and e.decision), None)
    here = (["它的 %d 次提交（「产出文件」里都能看）" % len(r.commits)] if r.commits else []) + \
        (["它自己写的说明（在上面）"] if r.note else []) + (["它在 MR 上问的 %s" % decided] if decided else [])
    asked = r.ask.at if r.ask is not None else None
    if run is None or run.kind in ("child", "parent"):
        who = run.label if run is not None else "别的需求"
        return {"title": "这一轮是%s做的" % who, "missing": "它的回复、接到的消息和工具调用在它自己的需求看板上。",
                "here": here, "do": "在%s的需求看板上打开这一步。" % who, "asked": asked}
    if run.kind == "runner":
        return {"title": "这一轮是运行机做的（%s）" % (run.run_id or run.label),
                "missing": "它的回复、接到的消息和工具调用留在运行机上，这台机器读不到。", "here": here,
                "do": "要看它的回复和工具调用：请管运行机的人在运行机上的 Brain 交付里打开 %s。" % key, "asked": asked}
    return {"title": "这一轮在%s里做的" % run.label, "missing": "它的回复、接到的消息和工具调用在那台机器上，这台机器读不到。",
            "here": here, "do": "要看：在做这一轮的那台机器上的 Brain 交付里打开 %s。" % key, "asked": asked}


def turn_views(fold: Fold, q: NodeQuery, slots: Sequence[Slot], key: str, now: float,
               scrub: Callable[[str], str]) -> Dict[str, Any]:
    """One round of one node: the turns it was done in, each with its message, reply and calls; or why_not. *scrub*:
    the app's scrubber, run on a call's detail before it is cut (the route's scrub_tree runs on the rest after)."""
    n, r = round_of(fold, q)
    if r is None:
        return {"node": n.id, "round": None, "run": None, "turns": [], "why_not": None, "why": NOT_STARTED, "lit": None,
                "meta": {"took": None, "turns": None, "calls": None}}
    run = next((x for x in fold.runs if x.key == r.run), None)
    card = why_not(n, r, run, key)
    took = (r.ended or now) - r.started
    if card is not None:
        return {"node": n.id, "round": r.n, "run": r.run, "turns": [], "why_not": card, "why": None, "lit": None,
                "meta": {"took": took, "turns": None, "calls": None}}
    lo, hi = r.started, _work_end(r) or now       # a call is lit from the round's start to its agent's last work on it
    views = _views(fold, n, r, slots, lo, hi, scrub)
    return {"node": n.id, "round": r.n, "run": r.run, "turns": views, "why_not": None,
            "why": None if views else NO_TURNS, "lit": {"from": lo, "to": hi},
            "meta": {"took": took, "turns": len(views), "calls": sum(len(v["calls"]) + v["more_calls"] for v in views)}}
