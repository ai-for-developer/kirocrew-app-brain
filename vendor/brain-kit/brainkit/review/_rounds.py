"""brainkit.review._rounds: rounds, verdicts and item states, derived. Pure over three inputs that other code wrote:
Lean's events (aidlc-docs/audit/*.jsonl, read as mrdesc._events reads them), the review log (review_turn.read_log)
and the snapshot store (review_turn.read_blob, reached through the callables the caller passes). Nothing here writes.
Private: import brainkit.review.

Lean keeps no round counter and no text (grounding: only distinct shas across APPROVAL_REQUESTED / REJECTED /
APPROVED; feedback typed in plain chat leaves no record). The two facts Lean cannot give are what the prompt hook
pinned at the person's turn: that a person replied while a version was open (so it is a round FOR THEM), and the bytes
of that version (so round 2 can be compared with it after the agent rewrote the files).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .types import (Effect, Gate, Item, ItemKind, ItemRejection, ItemState, Outcome, Round, Sheet, Verdict, Via)

Items = Optional[Dict[str, Item]]
LEAN_ANSWERS = ("APPROVED", "REJECTED")


@dataclass(frozen=True)
class Line:
    """One line the person typed against a round that still stands (a later line on the same ids did not take it
    back), its verdicts grouped back together: what the round-2 sheet shows one row for."""
    ids: Tuple[str, ...]            # as typed: F-1, or A-5, A-7
    effect: Effect
    words: str
    at: str
    ref: str
    about: Tuple[str, ...]          # the items it is about: an F-n's targets, an item's own id


def _d(e) -> dict:
    d = e.get("data")
    return d if isinstance(d, dict) else {}


def files_of(e) -> Dict[str, str]:
    """path -> sha256 of the pages a Lean record covers (INCEPTION: `files`; one page: `path` and `sha256`)."""
    d = _d(e)
    if isinstance(d.get("files"), dict):
        return {str(p): str(h) for p, h in d["files"].items()}
    return {str(d["path"]): str(d.get("sha256"))} if d.get("path") else {}


def epoch(ts) -> Optional[float]:
    """Seconds of an ISO time stamp as Lean writes it ('2026-11-08T14:57:52.123Z'), or None."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _outcome(events: Sequence[dict], start: int, kind: str, sha: str, files: Dict[str, str]):
    """(outcome, Lean's words) of the version requested at events[start], walking what Lean recorded after it. An
    APPROVED or REJECTED answers the version when it covers all of its pages with the same per-page sha (so an
    INCEPTION approval answers a REQUIREMENTS request on the same file, as Lean's pending_request reads it); a request
    of another sha on one of its pages supersedes it while it is open; a request of the same bytes again reopens it."""
    state, words = Outcome.OPEN, None
    for e in events[start + 1:]:
        ev, d = e.get("event"), _d(e)
        if ev not in LEAN_ANSWERS and ev != "APPROVAL_REQUESTED":
            continue
        f = files_of(e)
        if ev == "APPROVAL_REQUESTED":
            if d.get("kind") == kind and d.get("sha256") == sha:
                if state is not Outcome.APPROVED:
                    state, words = Outcome.OPEN, None
            elif state is Outcome.OPEN and any(p in f and f[p] != h for p, h in files.items()):
                state = Outcome.SUPERSEDED
        elif files and all(f.get(p) == h for p, h in files.items()):
            state = Outcome.APPROVED if ev == "APPROVED" else Outcome.REJECTED
            words = d.get("words") if ev == "REJECTED" else None
    return state, words


def knows(sheet: Sheet, item_id: str) -> bool:
    """The sheet accounts for this item id (shown or collapsed) or has this finding."""
    if item_id.startswith("F-"):
        return any(f.fid == item_id for f in sheet.findings)
    return item_id in sheet.shown or any(item_id in ids for ids in sheet.collapsed.values())


def _bind(turn, kind: str, sha: str, sheet_of: Callable[[str], Optional[Sheet]]) -> List[Verdict]:
    """The verdicts of one counted TURN that belong to the round (kind, sha). An F-n binds only to the round whose
    pinned sheet has it (F ids never repeat within an intent; an F-n of a round that is gone binds nowhere). With one
    round open an item binds to it; with several (REQUIREMENTS and DESIGN requested apart) to the rounds whose sheet
    accounts for it, or to all of them when none does. Questions and unclear lines are no verdicts."""
    sheets = {o.sha256: (sheet_of(o.sheet) if o.sheet else None) for o in turn.open}
    own = sheets.get(sha)
    out = []
    for line in turn.lines:
        effect = Effect(line.effect.value)
        if effect not in (Effect.CHANGE, Effect.KEEP):
            continue
        for i in line.ids:
            if i.startswith("F-"):
                f = next((f for f in own.findings if f.fid == i), None) if own else None
                if f is None:
                    continue
                about = f.targets
            else:
                knowing = [s for s, sh in sheets.items() if sh is not None and knows(sh, i)]
                if len(turn.open) > 1 and knowing and sha not in knowing:
                    continue
                about = (i,)
            out.append(Verdict(i, effect, line.words, turn.ts, Via.CHAT, turn.ref, about, line.n))
    return out


def rounds(gate: Gate, events: Sequence[dict], turns: Sequence, sheet_of: Callable[[str], Optional[Sheet]]
           ) -> List[Round]:
    """Every requested version of the gate's pages, in order of first request (one Round per distinct sha256: Lean
    does not de-duplicate requests), with outcome, number and verdicts. `events`: the intent's Lean records, oldest
    first; `turns`: the intent's counted() TURNs, oldest first; `sheet_of(blob sha256)`: the pinned sheet.json read
    back, used to resolve F-n. A round is seen from the first counted TURN that had it open: n counts seen rounds only,
    its sheet and decisions.md are the first ones pinned for it. STALE is not decided here: it needs the pages as they
    are now (the caller marks an OPEN round whose pages no longer hash as requested)."""
    kind = gate.kind.value
    first: Dict[str, int] = {}
    for k, e in enumerate(events):
        d = _d(e)
        if e.get("event") == "APPROVAL_REQUESTED" and d.get("kind") == kind and d.get("sha256"):
            first.setdefault(d["sha256"], k)
    out, n = [], 0
    for sha, k in first.items():
        files = files_of(events[k])
        outcome, words = _outcome(events, k, kind, sha, files)
        seen = [t for t in turns if any(o.kind == kind and o.sha256 == sha for o in t.open)]
        pins = [o for t in seen for o in t.open if o.kind == kind and o.sha256 == sha]
        verdicts = tuple(v for t in seen for v in _bind(t, kind, sha, sheet_of))
        if seen:
            n += 1
        out.append(Round(gate, sha, files, str(events[k].get("ts") or ""), outcome, n if seen else None,
                         seen[0].ts if seen else None, next((o.sheet for o in pins if o.sheet), None),
                         next((o.decisions for o in pins if o.decisions), None), words, verdicts,
                         any(v.effect in (Effect.CHANGE, Effect.KEEP) for v in verdicts)))
    return out


def previous_seen(rounds_of_any_gate: Sequence[Round], round_: Round,
                  readable: Callable[[Round], bool] = lambda r: True) -> Optional[Round]:
    """The round this one is compared with: the latest SEEN round, of any gate, requested before it, that covers at
    least one of its pages and whose pinned pages read back (`readable`). Of another gate it gives the carry-over
    across gates by wording: INCEPTION round 1, then REQUIREMENTS requested on its own, compares requirements.md with
    what the person saw in INCEPTION. None: round 1 for the person (the full sheet)."""
    pages = set(round_.files)
    cands = [r for r in rounds_of_any_gate
             if r.n is not None and (r.gate.kind, r.sha256) != (round_.gate.kind, round_.sha256)
             and r.requested_at <= round_.requested_at and pages & set(r.files) and readable(r)]
    return max(cands, key=lambda r: r.requested_at) if cands else None


def carries(prev: Round) -> bool:
    """May items of `prev` that were not rejected there and are unchanged since collapse in the next sheet? Only when
    the person answered `prev` item by item (replied_itemwise) or approved it. A round answered only in prose, or
    rejected whole with Lean's reject line, carries nothing: the next sheet is a full sheet again, changes marked."""
    return prev.outcome is not Outcome.REJECTED and (prev.replied_itemwise or prev.outcome is Outcome.APPROVED)


def last_verdicts(r: Round) -> Dict[str, Verdict]:
    """The verdict that stands per id in one round: the last one typed (`A-3 可以` after `A-3 不对` takes it back),
    and an upheld F-n spread onto each of its targets as that F-n's own Verdict (its target says which finding); a
    dismissed F-n says nothing about its targets. An item's own later line wins over an earlier F-n, and the reverse."""
    last: Dict[str, Tuple[int, Verdict]] = {}
    for k, v in enumerate(r.verdicts):
        last[v.target] = (k, v)
    spread: Dict[str, Tuple[int, Verdict]] = {}
    for t, (k, v) in last.items():
        if t.startswith("F-") and v.effect is Effect.CHANGE:
            for i in v.resolves_to:
                if (i not in last or last[i][0] < k) and (i not in spread or spread[i][0] < k):
                    spread[i] = (k, v)
    out = {t: v for t, (_, v) in last.items()}
    out.update({i: v for i, (_, v) in spread.items()})
    return out


def _stands(v: Verdict, stand: Dict[str, Verdict]) -> bool:
    """Not taken back: no later line on the same id, and an upheld F-n still holds for one of its targets (each
    target retyped later, `AC-8 可以`, takes the whole line back)."""
    if stand.get(v.target) is not v:
        return False
    if v.target.startswith("F-") and v.effect is Effect.CHANGE and v.resolves_to:
        return any(stand.get(i) is v for i in v.resolves_to)
    return True


def lines(r: Round) -> List[Line]:
    """The lines typed against the round that still stand for at least one of their ids, one per prompt line (a line
    may name several ids: `A-5、A-7 不对`), in typing order."""
    stand = last_verdicts(r)
    groups: Dict[Tuple[str, int], List[Verdict]] = {}
    for v in r.verdicts:
        groups.setdefault((v.ref, v.line), []).append(v)
    out = []
    for g in groups.values():
        if any(_stands(v, stand) for v in g):
            about = tuple(dict.fromkeys(i for v in g for i in v.resolves_to))
            out.append(Line(tuple(v.target for v in g), g[0].effect, g[0].words, g[0].at, g[0].ref, about))
    return out


def _history(rounds_: Sequence[Round], a: Round) -> List[Round]:
    """The seen rounds, of any gate, up to and including `a`, that cover one of its pages: what the person was shown
    of these pages before (or when) `a` was approved."""
    pages = set(a.files)
    return [r for r in rounds_ if r.n is not None and r.requested_at <= a.requested_at and pages & set(r.files)]


def item_states(gate: Gate, rounds_: Sequence[Round], items_of: Callable[[Round], Items],
                shown_in: Callable[[Round], frozenset]) -> Dict[str, ItemState]:
    """Per item of the gate's approved version (the last APPROVED round of the gate). SUPERSEDED: a decision a row of
    that version replaces. Otherwise the person's last word on exactly the approved text, walking the seen rounds in
    order (`rounds_`: rounds of every gate of the intent, so a round of another gate that showed the same wording
    counts): a CHANGE standing in a round makes it REJECTED, a KEEP or being shown there without objection makes it
    ACCEPTED (an item rejected, then shown again unchanged and not objected to, is accepted), and a round where the
    item had another text forgets both (a fix nobody was shown is PASSED). Never shown, never answered: PASSED,
    approved with the page. Every item OPEN while the gate has no APPROVED round. `items_of(round)`: its items from
    its pinned bytes (None: unreadable); `shown_in(round)`: its sheet's shown ids."""
    mine = [r for r in rounds_ if r.gate == gate]
    approved = [r for r in mine if r.outcome is Outcome.APPROVED]
    if not approved:
        return {i: ItemState.OPEN for i in ((items_of(mine[-1]) or {}) if mine else {})}
    a = approved[-1]
    final = items_of(a) or {}
    replaced = {s for it in final.values() for s in it.supersedes}
    hist = [(items_of(r) or {}, shown_in(r), last_verdicts(r)) for r in _history(rounds_, a)]
    out = {}
    for i, it in final.items():
        if it.kind is ItemKind.DECISION and i in replaced:
            out[i] = ItemState.SUPERSEDED
            continue
        state = ItemState.PASSED
        for texts, shown, stand in hist:
            then = texts.get(i)
            if then is None:
                continue
            if then.text != it.text:
                state = ItemState.PASSED
            elif i in stand:
                state = ItemState.REJECTED if stand[i].effect is Effect.CHANGE else ItemState.ACCEPTED
            elif i in shown:
                state = ItemState.ACCEPTED
        out[i] = state
    return out


def _about(v: Verdict) -> Iterable[str]:
    return v.resolves_to if v.target.startswith("F-") else (v.target,)


def rejections(gate: Gate, rounds_: Sequence[Round], items_of: Callable[[Round], Items],
               shown_in: Callable[[Round], frozenset]) -> List[ItemRejection]:
    """The rejections that stand, for reflect and eval, one per (round, item) in round and typing order: a CHANGE on
    an item whose text then changed in a later version (or that went away), or on a decision a later row replaced
    (`取代 D-24`): the agent fixed something, a lesson; or an item item_states still calls REJECTED. A CHANGE later
    taken back on unchanged text (`D-19 改` in round 1, `D-19 可以` in round 2), or left unchanged and then accepted
    when shown again (AC-39 under an upheld F-2), is no lesson and is not returned. The invented OMS run gives
    AC-36, D-24, AC-38, AC-15, A-3."""
    mine = [r for r in rounds_ if r.gate == gate]
    states = item_states(gate, rounds_, items_of, shown_in)
    out = []
    for k, r in enumerate(mine):
        if r.n is None:
            continue
        stand, before = last_verdicts(r), items_of(r) or {}
        later = [x for x in (items_of(x) for x in mine[k + 1:]) if x is not None]
        done = set()
        for v in r.verdicts:
            for i in _about(v):
                w = stand.get(i)
                if i in done or w is None or w.effect is not Effect.CHANGE:
                    continue
                done.add(i)
                old = before.get(i)
                changed = old is not None and any(i not in x or x[i].text != old.text for x in later)
                replaced = any(i in it.supersedes for x in later for it in x.values())
                if changed or replaced or states.get(i) is ItemState.REJECTED:
                    out.append(ItemRejection(gate, i, r.sha256[:8], w.words,
                                             w.target if w.target.startswith("F-") else None, w.ref, w.at))
    return out


def voucher_verdicts(vouchers_dir: Optional[str], key: str, gate: Gate) -> List[Tuple[str, Verdict]]:
    """Phase 3: (round sha8, Verdict via VOUCHER) from answer vouchers whose lean_request names this gate: one CHANGE
    per rejected_items entry; `approve` adds nothing (the Lean APPROVED record already closes the round). Signed by
    the bridge, read the way reflect.rejected_without_correction reads them. Phase 1: none."""
    return []
