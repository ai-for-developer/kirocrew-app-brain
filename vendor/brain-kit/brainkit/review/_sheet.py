"""brainkit.review._sheet: choose what one person must decide for one round, fit it on one screen, account for the
rest, and render it so that nothing in it can approve, reject or record anything. Pure. Private: import
brainkit.review.

Where an item goes is one policy (place()), the same for every round; a round compared with an earlier one adds the
lines the person typed (PREVIOUS), what changed (CHANGED, new decisions) and, when that round carries, collapses what
did not change. Every text on the sheet is made by code from the pages' own cells and the records; a reviewer's
problem line is the only model-written text, shown as its one line.
"""
from __future__ import annotations

import difflib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .. import mrdesc
from ..intake import checks
from . import _items, _rounds
from ._rounds import Line
from .types import (ChangeKind, DocChange, Effect, Finding, Gate, Item, ItemChange, ItemKind, Kind, Resolution,
                    ReviewerRun, Round, Row, SCHEMA, Section, SectionKey, Sheet, Slot, Source, LINE_BUDGET)

CAPS = {                    # rows per section before it caps (the rest is counted under COLLAPSED, never dropped)
    SectionKey.PREVIOUS: 12, SectionKey.BOTH: 8, SectionKey.ONE: 6, SectionKey.CHANGED: 10,
    SectionKey.DECISIONS: 12, SectionKey.ASSUMPTIONS: 20, SectionKey.INFERRED: 8,
}
CELL_CHARS = 60             # display columns of one item's text (a cell of a two-up table)
PROBLEM_CHARS = 90          # characters of one finding's problem text
LINE_CHARS = 30             # characters of a line the person typed, quoted back
NOW_CHARS = 160             # characters of what became of it
MODEL_NAMES = {"claude-opus-5.5": "Opus", "kimi-k3": "K3"}     # display only; any other id is shown as it is
ORDER = (SectionKey.PREVIOUS, SectionKey.BOTH, SectionKey.ONE, SectionKey.CHANGED, SectionKey.DECISIONS,
         SectionKey.ASSUMPTIONS, SectionKey.INFERRED)
FIT_ORDER = (SectionKey.ASSUMPTIONS, SectionKey.INFERRED, SectionKey.DECISIONS, SectionKey.ONE, SectionKey.CHANGED)
TWO_UP = (SectionKey.DECISIONS, SectionKey.ASSUMPTIONS, SectionKey.INFERRED)
HEADERS = {
    SectionKey.PREVIOUS: ("你上轮写的", "现在"), SectionKey.BOTH: ("", "涉及", "问题"),
    SectionKey.ONE: ("", "涉及", "问题", "谁"), SectionKey.CHANGED: ("", "改了什么"),
    SectionKey.DECISIONS: ("", "决定"), SectionKey.ASSUMPTIONS: ("", "假设"), SectionKey.INFERRED: ("", "AC"),
}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
ROUND_FINDINGS = "这一轮的评审"     # round >= 2: BOTH and ONE under one heading, one table, both reviewers' rows first
STATUS = {"failed": "运行失败", "timeout": "超时", "bad_output": "回答读不出来", "skipped": "没跑"}

# Sheet.collapsed reasons (sheet.json keys); the counts line says each in Chinese
TRACED = "traced, nobody flagged"
OWNER = "answered by you"
VOUCHER = "answered on the MR"
BRAIN = "from the Brain"
CHECKED = "decided by a check"
WHO_UNKNOWN = "who not in the vocabulary"
REPLACED = "replaced by a later row"
UNCHANGED = "unchanged, not rejected last round"
OVER = "over the screen budget"
REASON_ORDER = (TRACED, OWNER, VOUCHER, BRAIN, CHECKED, WHO_UNKNOWN, REPLACED, UNCHANGED, OVER)
BY_SOURCE = {Source.OWNER: OWNER, Source.VOUCHER: VOUCHER, Source.BRAIN: BRAIN, Source.CHECK: CHECKED,
             Source.UNKNOWN: WHO_UNKNOWN}
INFERRED_SOURCES = (Source.INFERRED, Source.PARTLY_INFERRED, Source.UNKNOWN)
ASIDE_RE = re.compile(r"（[^（）]*）")
RECOMMENDED_RE = re.compile(r"(?i)按推荐|as recommended")
SEP_ROW_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
ID_NUM_RE = re.compile(r"^(AC|A|D|F)-([0-9]+)$")


@dataclass(frozen=True)
class Before:
    """What the person saw last time, for a round compared with an earlier one."""
    round: Round                            # the round compared with (_rounds.previous_seen)
    label: str                              # how the person knows it: "第 1 轮", "INCEPTION 第 1 轮"
    items: Dict[str, Item]                  # its items the person was asked about, read the way this gate reads them
    changes: Tuple[ItemChange, ...]         # _items.diff(items, this round's items)
    lines: Tuple[Line, ...]                 # what the person typed against it that still stands
    findings: Tuple[Finding, ...] = ()      # the findings its sheet showed (none when no sheet was pinned)
    page_changes: Tuple[DocChange, ...] = ()
    carries: bool = False                   # answered item by item, or approved (_rounds.carries)
    unseen: Tuple[Finding, ...] = ()        # its sheet's findings that the screen could not hold


def before(prev: Round, kind: Kind, old_pages: Dict[str, bytes], old_decisions: Optional[Tuple[str, bytes]],
           prev_sheet: Optional[Sheet], items_now: Dict[str, Item], pages_now: Dict[str, bytes],
           bot_users: Sequence[str] = ()) -> Before:
    """What the person saw in `prev`, read the way this gate reads it: the items prev's own gate asked about (an item
    the person was not asked about there is placed as in round 1), what changed since, what they typed, the findings
    its sheet showed and the ones it could not, and the page changes outside the item rows. `old_pages`,
    `old_decisions`: prev's pinned bytes; `prev_sheet`: its pinned sheet.json, read back (None when none was)."""
    asked = _items.gate_items(prev.gate.kind.value, old_pages, old_decisions, bot_users)
    as_now = _items.gate_items(kind.value, old_pages, old_decisions, bot_users)
    seen = {i: it for i, it in as_now.items() if i in asked}
    changes = tuple(c for c in _items.diff(seen, items_now) if not (c.change is ChangeKind.ADDED and c.id in as_now))
    pchanges = tuple(c for p in pages_now if p in old_pages for c in _items.page_changes(p, old_pages[p], pages_now[p]))
    label = "第 %d 轮" % prev.n if prev.gate.kind is kind else "%s 第 %d 轮" % (prev.gate.kind.value, prev.n)
    found = prev_sheet.findings if prev_sheet is not None else ()
    shown = set(prev_sheet.shown_findings) if prev_sheet is not None else set()
    return Before(prev, label, seen, changes, tuple(_rounds.lines(prev)),
                  findings=tuple(f for f in found if f.fid in shown), page_changes=pchanges,
                  carries=_rounds.carries(prev), unseen=tuple(f for f in found if f.fid not in shown))


@dataclass(frozen=True)
class Previous:
    """One PREVIOUS row: a line the person typed and what became of it, computed from bytes."""
    line: Line
    now: str
    ids: Tuple[str, ...]                    # what the row shows: the line's items, the ones that followed, its rows
    moved: bool = False                     # an item it is about changed, was replaced or removed since


# ---------- text ----------

def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _clip(text: str, limit: int) -> str:
    """Cut to `limit` display columns, at the last clause mark when that keeps at least half, with …"""
    if width(text) <= limit:
        return text
    out, used = "", 0
    for c in text:
        used += width(c)
        if used > limit - 1:
            break
        out += c
    mark = max(out.rfind(c) for c in "；;。，,、")
    return (out[:mark] if mark >= len(out) // 2 else out).rstrip(" ；;。，,、") + "…"


def display(text: str, limit: int = CELL_CHARS) -> str:
    """An item's text for the sheet, made from its own cell: asides in （…） and code marks dropped, one line, inert,
    cut at a clause mark."""
    s = " ".join(ASIDE_RE.sub("", text or "").replace("`", "").split()).strip(" ；;。")
    s = s or " ".join((text or "").replace("`", "").split())
    return mrdesc._one(_clip(s, limit))


def _plain(text: str, limit: int) -> str:
    return mrdesc._one(" ".join(str(text or "").replace("`", "'").split()), limit)


def name(model: str) -> str:
    return MODEL_NAMES.get(model, model)


def _num(i: str) -> Tuple[str, int]:
    m = ID_NUM_RE.match(i)
    return (m.group(1), int(m.group(2))) if m else (i, 0)


def ids_text(ids: Sequence[str], sep: str = "、") -> str:
    """D-1…D-5、D-7, AC-2: runs of three or more consecutive ids of one kind as a range, in id order."""
    by: Dict[str, List[int]] = {}
    other = []
    for i in dict.fromkeys(ids):
        p, k = _num(i)
        (by.setdefault(p, []).append(k) if k else other.append(i))
    out = []
    for p in ("AC", "A", "D", "F"):
        ks = sorted(by.get(p, ()))
        start = 0
        for j in range(1, len(ks) + 1):
            if j == len(ks) or ks[j] != ks[j - 1] + 1:
                run = ks[start:j]
                out += ["%s-%d…%s-%d" % (p, run[0], p, run[-1])] if len(run) >= 3 else ["%s-%d" % (p, k) for k in run]
                start = j
    return sep.join(out + other)


def _and(parts: Sequence[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return "".join(parts)
    head = "、".join(parts[:-1])
    return head + (" 和" if head[-1:].isascii() else "和") + (" " if parts[-1][:1].isascii() else "") + parts[-1]


def counts_text(ids: Sequence[str], items: Dict[str, Item]) -> str:
    """'3 条 AC、2 条假设、4 个 agent 决定和 D-1…D-5': how many of each, decisions not the agent's by id."""
    its = [items[i] for i in ids if i in items]
    ac = sum(1 for it in its if it.kind is ItemKind.AC)
    a = sum(1 for it in its if it.kind is ItemKind.ASSUMPTION)
    agent = sum(1 for it in its if it.kind is ItemKind.DECISION and it.source is Source.AGENT)
    rest = [it.id for it in its if it.kind is ItemKind.DECISION and it.source is not Source.AGENT]
    return _and(["%d 条 AC" % ac if ac else "", "%d 条假设" % a if a else "", "%d 个 agent 决定" % agent if agent
                 else "", ids_text(rest)])


def who_text(it: Item) -> str:
    """Who decided a decision, for a sheet cell. A Who outside the vocabulary is not shown (it may be a real name)."""
    if it.source is Source.AGENT:
        return "agent"
    if it.source is Source.OWNER:
        return "你"
    if it.source is Source.BRAIN:
        return "Brain " + "、".join(c for c in it.cites if not ID_NUM_RE.match(c))
    if it.source is Source.CHECK:
        return "检查"
    if it.source is Source.VOUCHER:
        return mrdesc._one((it.who or "").split()[0] if it.who else "MR")
    return "Who 不明"


def _confirm(who: Optional[str]) -> str:
    w = " ".join((who or "").split())
    return "你" if w.lower() in ("run owner", "owner", "你") else mrdesc._one(w)


def seconds_text(s: Optional[float]) -> str:
    if s is None:
        return "?"
    s = int(round(s))
    return "%d 秒" % s if s < 60 else "%d 分 %02d 秒" % (s // 60, s % 60)


# ---------- what became of each line the person typed ----------

def _what(c: ItemChange) -> str:
    """The change of the statement itself (an AC's condition and result, an assumption, a decision); a source or Who
    cell that changed with it is left out. Text only added reads as added however much it is; the whole row's
    change when only those other cells changed."""
    b, a = c.before, c.after
    if b.main == a.main:
        return c.fragment
    ops = [o for o in difflib.SequenceMatcher(None, b.main, a.main, autojunk=False).get_opcodes() if o[0] != "equal"]
    if all(o[0] == "insert" for o in ops):
        return _plain("；".join("加「%s」" % a.main[j1:j2].strip() for _, _, _, j1, j2 in ops[:2]),
                      _items.FRAGMENT_CHARS)
    frag = _items.fragment(replace(b, cells=(b.main,)), replace(a, cells=(a.main,)))
    return "改成" + frag if frag.startswith("「") else frag


def _became(t: str, c: Optional[ItemChange], replaced_by: Dict[str, str]) -> Tuple[str, Optional[bool]]:
    """(what became of one item a line was about, whether it moved): True changed, replaced or removed; False the
    same; None not in the pages."""
    if t in replaced_by:
        return "%s 由 %s 取代" % (t, replaced_by[t]), True
    if c is None:
        return "%s 文件里没有" % t, None
    if c.change is ChangeKind.UNCHANGED:
        return "%s 没改" % t, False
    if c.change is ChangeKind.REMOVED:
        return "%s 删了" % t, True
    if c.change is ChangeKind.RENAMED:
        return "%s 现在是 %s%s" % (t, c.id, "：" + _what(c) if c.fragment else ""), True
    return "%s %s" % (t, _what(c)), True


def previous(before: Before, items: Dict[str, Item]) -> List[Previous]:
    """One Previous per line that still stands, in typing order: per item it is about, changed (the statement's
    change) / not changed / replaced by a new row / removed; the changed items that cite it (跟着改了 AC-13); the new
    decision rows that name it (取代, 保留 D-11…) or that its new text cites: a row in the person's own words
    (记了你的话), the agent's row behind a change, or the agent's reason for not changing it. Also what the round-2
    brief tells the reviewers (_prereview.SentBack.now)."""
    by_id = {c.id: c for c in before.changes}
    renamed = {c.renamed_from: c for c in before.changes if c.change is ChangeKind.RENAMED}
    new_rows = [c.after for c in before.changes if c.change is ChangeKind.ADDED and c.after.kind is ItemKind.DECISION]
    replaced_by = {old: d.id for d in new_rows for old in d.supersedes}
    about_all = {i for ln in before.lines for i in ln.about}
    taken, out = set(), []
    for ln in before.lines:
        named = {replaced_by[t] for t in ln.about if t in replaced_by}
        parts, refs, ids = [], [], [i for i in ln.about if i in items]
        for t in ln.about:
            c = by_id.get(t) or renamed.get(t)
            parts.append(_became(t, c, replaced_by))
            if c is not None and c.change is ChangeKind.RENAMED:
                ids.append(c.id)
            after = items.get(c.id) if c is not None else None
            refs += [d for d in new_rows if d.id not in named and d not in refs and
                     (t in d.supersedes or t in d.cites or (after is not None and d.id in after.cites))]
        followers = [c.id for c in before.changes
                     if c.change in (ChangeKind.CHANGED, ChangeKind.ADDED, ChangeKind.RENAMED)
                     and c.after.kind is not ItemKind.DECISION and c.id not in about_all and c.id not in taken
                     and any(t in c.after.cites for t in ln.about)]
        taken.update(followers)
        ids += followers + [d.id for d in refs if d.id in items]
        moved = [m for _, m in parts]
        if not any(moved):
            text = "没改，照你说的" if ln.effect is Effect.KEEP else "**没改**"
            text += "".join("；" + p for p, m in parts if m is None)
        else:
            text = ("改了。" if all(moved) else "") + "；".join(p for p, _ in parts)
        if followers:
            text += "；跟着改了 %s" % ids_text(followers)
        mine = [d for d in refs if d.source is Source.OWNER]
        theirs = [d for d in refs if d.source is not Source.OWNER]
        text += "".join("；%s 记了你的话" % d.id for d in mine)
        if theirs and any(moved):
            text += "（%s）" % "、".join(d.id for d in theirs)
        elif theirs:
            text += "。理由在 %s" % "；".join("%s：「%s」" % (d.id, display(d.main, 70)) for d in theirs)
        if ln.effect is Effect.CHANGE and not any(moved):
            text += "。你再定" if theirs else "，也没有说明为什么的决定行。你再定"
        out.append(Previous(ln, _plain(text, NOW_CHARS), tuple(dict.fromkeys(ids)), any(moved)))
    return out


# ---------- where each item goes ----------

def _round1(items: Dict[str, Item], placed: Dict[str, object], findings: Sequence[Finding]) -> Dict[str, object]:
    """The round-1 policy for the items not placed yet: an agent's decision is shown, a person's / the Brain's / a
    check's is counted, a decision a later row replaces is counted; every assumption is shown with the inferred ACs
    that rest on it; an AC with a source nobody flagged is counted, an inferred one is shown."""
    out = {}
    replaced = {s for it in items.values() for s in it.supersedes}
    for i, it in items.items():
        if i in placed:
            continue
        if it.kind is ItemKind.DECISION:
            out[i] = REPLACED if i in replaced else SectionKey.DECISIONS if it.source is Source.AGENT else \
                BY_SOURCE.get(it.source, WHO_UNKNOWN)
        elif it.kind is ItemKind.ASSUMPTION:
            out[i] = SectionKey.ASSUMPTIONS
    shown_a = {i for i, p in out.items() if p is SectionKey.ASSUMPTIONS}
    for i, it in items.items():
        if i in placed or it.kind is not ItemKind.AC:
            continue
        if it.source not in INFERRED_SOURCES:
            out[i] = TRACED
        else:
            a = next((c for c in it.cites if c in shown_a), None)
            out[i] = ("pair", a) if a else SectionKey.INFERRED
    return out


def place(items: Dict[str, Item], findings: Sequence[Finding], before: Optional[Before],
          prev_rows: Sequence[Previous]) -> Dict[str, object]:
    """id -> SectionKey (shown there), ("pair", A-n) (shown in that assumption's row), or a collapsed reason."""
    placed: Dict[str, object] = {}
    for p in prev_rows:
        for i in p.ids:
            if i in items and not (items[i].kind is ItemKind.DECISION and items[i].source is Source.AGENT and
                                   i not in p.line.about):
                placed.setdefault(i, SectionKey.PREVIOUS)
    for f in findings:
        for t in f.targets:
            if t in items:
                placed.setdefault(t, SectionKey.BOTH if f.both else SectionKey.ONE)
    if before is not None:
        new_rows = [c.after for c in before.changes if c.change is ChangeKind.ADDED and
                    c.after.kind is ItemKind.DECISION]
        replaced = {old for d in new_rows for old in d.supersedes}
        for c in before.changes:
            if c.id in placed or c.after is None:
                continue
            if c.change is ChangeKind.UNCHANGED:
                if c.id in replaced:
                    placed[c.id] = SectionKey.CHANGED
                elif before.carries:
                    placed[c.id] = UNCHANGED
            elif c.change is ChangeKind.ADDED and c.after.kind is ItemKind.DECISION:
                if before.carries:
                    placed[c.id] = SectionKey.DECISIONS
            else:
                placed[c.id] = SectionKey.CHANGED
    placed.update(_round1(items, placed, findings))
    return placed


# ---------- rows ----------

def _problem(f: Finding, reviewers: Dict[Slot, ReviewerRun], left_off: str = "") -> str:
    """The finding's one line and its marks; `left_off`: the round whose screen could not hold it ("第 1 轮")."""
    slots = [s for s in (Slot.A, Slot.B, Slot.CHECK) if s in f.by]
    text = next((f.notes[s.value] for s in slots if f.notes.get(s.value)), "")
    marks = []
    off = [s for s in slots if f.quote_ok.get(s.value) is False]
    if off:
        marks.append("%s引文对不上" % ("" if len(slots) == 1 else "、".join(name(reviewers[s].model) if s in reviewers
                                                                              else s.value for s in off) + " "))
    if f.unknown_targets:
        marks.append("%s 文件里没有" % "、".join(f.unknown_targets))
    if left_off:
        marks.append("%s一屏没放下" % left_off)
    return _plain(text, PROBLEM_CHARS) + "".join("（%s）" % m for m in marks)


def _who(f: Finding, reviewers: Dict[Slot, ReviewerRun]) -> str:
    out = []
    for s in (Slot.A, Slot.B):
        if s in f.by:
            r = reviewers.get(s)
            label = name(r.model) if r else s.value
            out.append(label + ("（模型没核实）" if r and r.model_check not in ("", "match") else ""))
    if Slot.CHECK in f.by:
        out.append("检查")
    return " · ".join(out)


def _finding_rows(findings: Sequence[Finding], items: Dict[str, Item], reviewers: Dict[Slot, ReviewerRun],
                  both: bool, who: bool, left_off: Dict[str, str]) -> Tuple[Row, ...]:
    rows = []
    for f in findings:
        if f.both != both:
            continue
        cells = [f.fid, ids_text(f.targets, " · ") or "整份", _problem(f, reviewers, left_off.get(f.fid, ""))]
        rows.append(Row(tuple(t for t in f.targets if t in items), tuple(cells + [_who(f, reviewers)] if who else cells),
                        f.fid))
    return tuple(rows)


def _change_cell(c: ItemChange, replaced_by: Dict[str, str]) -> str:
    if c.id in replaced_by:
        return "由 %s 取代" % replaced_by[c.id]
    if c.change is ChangeKind.ADDED:
        return "新加：" + display(c.after.main)
    if c.change is ChangeKind.RENAMED:
        return "原来是 %s%s" % (c.renamed_from, "：" + _what(c) if c.fragment else "")
    if c.after.kind is ItemKind.DECISION:
        return _plain(_what(c), CELL_CHARS * 2) + "（改了旧的决定行：该加一行取代它）"
    return _plain(_what(c), CELL_CHARS * 2)


def _page(path: str) -> str:
    kind = _page_kind(path)
    return {"requirements": "需求", "design": "设计"}.get(kind, path.rsplit("/", 1)[-1])


def _page_kind(path: str) -> Optional[str]:
    if path.endswith("requirements.md"):
        return "requirements"
    return "design" if path.endswith("application-design.md") else None


def _sections(gate: Gate, items: Dict[str, Item], findings: Sequence[Finding], reviewers: Sequence[ReviewerRun],
              before: Optional[Before], prev_rows: Sequence[Previous], placed: Dict[str, object],
              resolutions: Sequence[Resolution], left_off: Dict[str, str]) -> List[Section]:
    """`left_off`: F-n -> the earlier round whose screen could not hold it, for the findings listed again here."""
    revs = {r.slot: r for r in reviewers}
    carrying = before is not None and before.carries
    out = []
    if prev_rows:
        res = {}
        for r in resolutions:
            res.setdefault(r.target, []).append(r)
        rows = []
        for p in prev_rows:
            ln = p.line
            said = "`%s`" % _plain("%s %s" % ("、".join(ln.ids), ln.words), LINE_CHARS).strip()
            if any(i.startswith("F-") for i in ln.ids) and ln.about:
                said += "（%s）" % "、".join(ln.about)
            cells = [said, p.now]
            if resolutions:
                cells.append(" · ".join("%s：%s" % (name(revs[r.slot].model) if r.slot in revs else r.slot.value,
                                                   "改到了" if r.resolved else "没改完" if r.resolved is False
                                                   else "没说") + ("：" + _plain(r.note, 40) if r.note and
                                                                  r.resolved is False else "")
                                    for r in res.get(ln.ids[0], ())) or "—")
            rows.append(Row(p.ids, tuple(cells)))
        title = "你上轮退回的" if all(p.line.effect is Effect.CHANGE for p in prev_rows) else "你上轮写的"
        out.append(Section(SectionKey.PREVIOUS, title, tuple(rows)))
    if carrying:
        out += [Section(SectionKey.BOTH, ROUND_FINDINGS, _finding_rows(findings, items, revs, True, True, left_off),
                        "只看改动"),
                Section(SectionKey.ONE, ROUND_FINDINGS, _finding_rows(findings, items, revs, False, True, left_off),
                        "只看改动")]
    else:
        out.append(Section(SectionKey.BOTH, "两个模型都提了", _finding_rows(findings, items, revs, True, False, left_off)))
        one = _finding_rows(findings, items, revs, False, True, left_off)
        checks_ = sum(1 for r in one if r.cells[-1] == "检查")
        title = "代码检查提了" if one and checks_ == len(one) else "一个模型或代码检查提了" if checks_ else "一个模型提了"
        out.append(Section(SectionKey.ONE, title, one))
    if before is not None:
        new_rows = [c.after for c in before.changes if c.change is ChangeKind.ADDED and
                    c.after.kind is ItemKind.DECISION]
        replaced_by = {old: d.id for d in new_rows for old in d.supersedes}
        told = {i for p in prev_rows for i in p.ids}
        rows = [Row((c.id,), (c.id, _change_cell(c, replaced_by))) for c in before.changes
                if c.after is not None and c.id not in told and
                (placed.get(c.id) is SectionKey.CHANGED or c.change in (ChangeKind.CHANGED, ChangeKind.RENAMED) or
                 (c.change is ChangeKind.ADDED and c.after.kind is not ItemKind.DECISION))]
        rows += [Row((), (c.id, "删了：" + display(c.before.main))) for c in before.changes
                 if c.change is ChangeKind.REMOVED and c.id not in {i for p in prev_rows for i in p.line.about}]
        rows += [Row((), ("%s · %s" % (_page(d.path), mrdesc._one(d.heading, 40)),
                          "、".join(x for x in ("加 %d 行" % d.added if d.added else "",
                                                "删 %d 行" % d.removed if d.removed else "") if x)))
                 for d in before.page_changes]
        out.append(Section(SectionKey.CHANGED, "其他改动", tuple(rows)))
    shown_up = [i for i, p in placed.items() if p in (SectionKey.BOTH, SectionKey.ONE, SectionKey.PREVIOUS)]
    dec = [it for i, it in items.items() if placed.get(i) is SectionKey.DECISIONS]
    note = []
    above = [i for i in shown_up if items[i].kind is ItemKind.DECISION and items[i].source is Source.AGENT
             and placed[i] is not SectionKey.PREVIOUS]
    if above:
        note.append("另 %d 条在上面：%s" % (len(above), ids_text(above)))
    yours = [i for i, p in placed.items() if p is SectionKey.PREVIOUS and items[i].kind is ItemKind.DECISION and
             items[i].source is Source.OWNER and before is not None and i not in before.items]
    if yours:
        note.append("%s 是你的话，在 ①" % ids_text(yours))
    if carrying:
        added = {c.id for c in before.changes if c.change is ChangeKind.ADDED}
        title = "新的决定" if all(it.id in added for it in dec) else "要你看的决定"
        rows = tuple(Row((it.id,), (it.id, display(it.main), who_text(it))) for it in dec)
    else:
        title = "agent 自己定的"
        rows = tuple(Row((it.id,), (it.id, display(it.main))) for it in dec)
    out.append(Section(SectionKey.DECISIONS, title, rows, "；".join(note)))
    assumptions = [it for it in items.values() if it.kind is ItemKind.ASSUMPTION]
    common = Counter(_confirm(it.who) for it in assumptions).most_common(1)
    common = common[0][0] if common else ""
    pairs: Dict[str, List[str]] = {}
    for i, p in placed.items():
        if isinstance(p, tuple):
            pairs.setdefault(p[1], []).append(i)
    rows = []
    for it in assumptions:
        if placed.get(it.id) is not SectionKey.ASSUMPTIONS:
            continue
        mark = "" if _confirm(it.who) == common else "（%s）" % (
            "你确认" if _confirm(it.who) == "你" else _confirm(it.who) or "没写谁确认")
        rows.append(Row((it.id,) + tuple(pairs.get(it.id, ())), (" · ".join((it.id,) + tuple(pairs.get(it.id, ()))),
                                                                  display(it.main) + mark)))
    note = []
    if common:
        differs = any(_confirm(it.who) != common for it in assumptions)
        note.append(("除标了的，都要%s确认" if differs else "都要%s确认") % common)
    elsewhere = [it for it in assumptions if placed.get(it.id) in (SectionKey.BOTH, SectionKey.ONE)]
    if elsewhere:
        where = {t: f.fid for f in reversed(findings) for t in f.targets}
        note.append("、".join("%s 在 %s" % (it.id, where.get(it.id, "上面")) for it in elsewhere))
    out.append(Section(SectionKey.ASSUMPTIONS, "假设", tuple(rows), "；".join(note)))
    inferred = [it for it in items.values() if placed.get(it.id) is SectionKey.INFERRED]
    others = [it for it in items.values() if it.kind is ItemKind.AC and it.source in INFERRED_SOURCES and
              placed.get(it.id) is not SectionKey.INFERRED and placed.get(it.id) is not UNCHANGED]
    out.append(Section(SectionKey.INFERRED, "AC 里 agent 推断、没有出处的",
                       tuple(Row((it.id,), (it.id, display(it.main))) for it in inferred),
                       "其余在上面" if others and inferred else ""))
    return [s for s in out if s.rows]


# ---------- the screen budget ----------

def two_up(s: Section) -> bool:
    return s.key in TWO_UP and all(len(r.cells) == 2 for r in s.rows)


def _blocks(sections: Sequence[Section]) -> List[List[Section]]:
    """The sections as the person reads them: one heading and one table each, except this round's findings in round
    >= 2 (BOTH then ONE titled ROUND_FINDINGS), which share them. The COLLAPSED line and empty sections are no block."""
    out: List[List[Section]] = []
    for s in sections:
        if s.key is SectionKey.COLLAPSED or not s.rows:
            continue
        if out and s.key is SectionKey.ONE and out[-1][-1].key is SectionKey.BOTH and out[-1][-1].title == s.title:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _height(sections: Sequence[Section]) -> int:
    """Rendered lines of these sections: heading, header, rows (two items per row when two-up)."""
    return sum(2 + sum(math.ceil(len(s.rows) / 2) if two_up(s) else len(s.rows) for s in b) for b in _blocks(sections))


def _cut(rows: Sequence[Row], n: int, reserved: FrozenSet[str]) -> Tuple[Row, ...]:
    """`rows` without the last `n` of them whose finding is not in `reserved`."""
    gone = [k for k in range(len(rows) - 1, -1, -1) if rows[k].finding not in reserved][:n]
    return tuple(r for k, r in enumerate(rows) if k not in gone)


def fit(sections: Sequence[Section], budget: int = LINE_BUDGET, reserved: FrozenSet[str] = frozenset()
        ) -> Tuple[Tuple[Section, ...], Dict[str, Tuple[str, ...]]]:
    """Cap sections to CAPS, then, while their rendered height is over `budget`, cap the lowest-priority section
    further (ASSUMPTIONS, then INFERRED, DECISIONS, ONE, then CHANGED; never BOTH or PREVIOUS). A section is cut
    from its end, except for the rows of the findings in `reserved`: they stay, and the rows before them go. Returns
    the kept sections and the ids of the rows left out, under OVER; a capped section's note says how many more there
    are and where. A section left without rows is dropped."""
    kept = list(sections)
    for k, s in enumerate(kept):
        cap = CAPS.get(s.key)
        if cap is not None and len(s.rows) > cap:
            kept[k] = replace(s, rows=_cut(s.rows, len(s.rows) - cap, reserved))
    for key in FIT_ORDER:
        while _height(kept) > budget:
            k = next((k for k, s in enumerate(kept) if s.key is key and
                      any(r.finding not in reserved for r in s.rows)), None)
            if k is None:
                break
            s = kept[k]
            kept[k] = replace(s, rows=_cut(s.rows, 2 - len(s.rows) % 2 if two_up(s) else 1, reserved))
    over = []
    for k, s in enumerate(kept):
        still = {id(r) for r in s.rows}
        left = [r for r in sections[k].rows if id(r) not in still]
        if left:
            over += [i for r in left for i in r.ids]
            more = "另 %d 条一屏放不下，在 sheet.json 和 `brain review --json` 里" % len(left)
            kept[k] = replace(s, note="；".join(x for x in (s.note, more) if x))
    return tuple(s for s in kept if s.rows), ({OVER: tuple(dict.fromkeys(over))} if over else {})


# ---------- the sheet ----------

def approve_hint(kind: str, sha8: str, n: int) -> str:
    """The one sentence naming Lean's line, inside a sentence and never on a line of its own."""
    line = "都接受：自己打 Lean 给的那一行 `approve %s %s`。" % (kind, sha8)
    if n <= 1:
        return line + "要改：写上面那种行，agent 改完出第 2 轮，只列改了的。整份退回用 Lean 的 reject 行。"
    return line + "还要改：写上面那种行，agent 改完出第 %d 轮。" % (n + 1)


def _lead(files: Dict[str, str], title: str, page_lines: Optional[Dict[str, int]], before: Optional[Before],
          items: Dict[str, Item]) -> str:
    if before is None:
        size = " + ".join("%s %d 行" % (p.rsplit("/", 1)[-1], page_lines[p]) for p in files
                          if page_lines and p in page_lines)
        return "%s原文 %s；这里只列要你定的。" % ("「%s」，" % mrdesc._one(title, 60) if title else "", size or "（没读到）")
    r = before.round
    changed = [c for c in before.changes if c.after is not None and c.change is not ChangeKind.UNCHANGED and
               not (c.change is ChangeKind.ADDED and c.after.kind is ItemKind.DECISION)]
    added = [c for c in before.changes if c.change is ChangeKind.ADDED and c.after.kind is ItemKind.DECISION]
    pages = Counter(_page(d.path) for d in before.page_changes)
    facts = ["%d 条改了" % len(changed) if changed else "", "加了 %d 个决定" % len(added) if added else ""]
    facts += ["%s里 %d 段有改动" % (p, k) for p, k in pages.items()]
    facts = [f for f in facts if f]
    head = "和%s（%s）比：%s。" % (before.label, r.sha256[:8], "，".join(facts) if facts else "条目和页面都没变")
    if before.carries:
        return head + "没变、上轮也没退回的，都折叠在最后一行。"
    if r.lean_words:
        why = "上轮整份退回了（Lean 记下的原话：「%s」）" % _plain(r.lean_words, 100)
    else:
        why = "上轮没有逐条回"
    return head + why + "，这一轮整份重列，改了的在「其他改动」里。"


def _counts_line(collapsed: Dict[str, Tuple[str, ...]], items: Dict[str, Item], carrying: bool,
                 brain_empty: bool, unanswered: Sequence[str]) -> str:
    clauses = []
    for reason in REASON_ORDER:
        ids = collapsed.get(reason, ())
        if not ids:
            continue
        if reason == TRACED:
            clauses.append("%d 条 AC 有材料或问答出处、评审没提" % len(ids))
        elif reason == OWNER:
            k = sum(1 for i in ids if RECOMMENDED_RE.search(items[i].text))
            clauses.append("%s 是你在问答里定的%s" % (ids_text(ids), "（其中 %d 条是一句「都按推荐」）" % k if k else ""))
        elif reason == VOUCHER:
            clauses.append("%s 是在 MR 里答的" % ids_text(ids))
        elif reason == BRAIN:
            clauses.append("%s 按团队 Brain 定的" % ids_text(ids))
        elif reason == CHECKED:
            clauses.append("%s 是检查定的" % ids_text(ids))
        elif reason == WHO_UNKNOWN:
            clauses.append("%s 的 Who 不在词表里" % ids_text(ids))
        elif reason == REPLACED:
            clauses.append("%s 已被后面的行取代" % ids_text(ids))
        elif reason == UNCHANGED:
            clauses.append("%s都没变，上轮也没退回" % (counts_text(ids, items) + ("" if len(ids) == 1 else " ")))
        elif reason == OVER:
            clauses.append("另 %s 一屏放不下，没列，在 sheet.json 里" % counts_text(ids, items))
    if brain_empty:
        clauses.append("团队 Brain 还没有条目，没有可比的冲突")
    if unanswered:
        clauses.append("上轮没理的 %s，涉及的条目没变，不再列" % ids_text(unanswered))
    if not clauses:
        return ""
    return ("不用再看：" if carrying else "不用逐条看：") + "；".join(clauses) + "。"


def build(gate: Gate, sha256: str, n: int, files: Dict[str, str], items: Dict[str, Item],
          findings: Sequence[Finding], reviewers: Sequence[ReviewerRun], before: Optional[Before] = None, *,
          title: str = "", page_lines: Optional[Dict[str, int]] = None, decisions_sha256: Optional[str] = None,
          resolutions: Sequence[Resolution] = (), brain_empty: bool = False, warnings: Sequence[str] = (),
          built_at: str = "") -> Sheet:
    """The sheet of one round. With no round to compare with (`before` None):
         BOTH, ONE        findings, both reviewers first; a code check's row in ONE, 谁 = 检查, and the first code
                          check keeps its row whatever else the screen budget cuts
         DECISIONS        every agent decision of the gate that is not a finding's target
         ASSUMPTIONS      every assumption not a finding's target, with the inferred AC that rests on it (A-4 · AC-4)
         INFERRED         ACs whose source is inferred or partly inferred, not tied to an assumption or a finding
         COLLAPSED        one line: traced ACs nobody flagged, decisions a person / the Brain / a check made (with how
                          many were one 都按推荐), decisions a later row replaces, the empty-Brain note
       Compared with an earlier round (`before`):
         PREVIOUS         one row per line the person typed there that still stands: their words, and what became of
                          each item it is about (changed + fragment / 没改 + the agent's row / replaced / removed),
                          the items that followed it, each reviewer's resolution
         BOTH, ONE        the earlier round's findings its screen could not hold and the person did not answer
                          (first, marked: `carried`), then this round's findings
         CHANGED          items that changed without being sent back; page changes per heading
         DECISIONS        new decision rows (who decided each) when the round carries
         COLLAPSED        also: unchanged and not rejected (only when the earlier round carries), last round's
                          findings shown and left unanswered whose targets did not change
       What `before` did not ask about is placed as in round 1. Every item id of the round lands in exactly one of
       shown / collapsed, the rendered height is within LINE_BUDGET, no rendered line reads as an approval, an answer
       or a verdict line: each is checked here and raises AssertionError when broken."""
    prev_rows = previous(before, items) if before is not None and before.lines else []
    answered = {i for ln in before.lines for i in ln.ids} if before is not None else set()
    carried = tuple(f for f in before.unseen if f.fid not in answered) if before is not None else ()
    findings = carried + tuple(findings)
    placed = place(items, findings, before, prev_rows)
    sections = _sections(gate, items, findings, reviewers, before, prev_rows, placed, resolutions,
                         {f.fid: before.label for f in carried})
    unanswered = ()
    if before is not None and before.carries:
        unchanged = {c.id for c in before.changes if c.change is ChangeKind.UNCHANGED}
        unanswered = tuple(f.fid for f in before.findings if f.fid not in answered and f.targets and
                           all(t in unchanged for t in f.targets))
    lead = _lead(files, title, page_lines, before, items)
    probe = Sheet(SCHEMA, gate, sha256, n, before.round.sha256 if before else None, dict(files), tuple(reviewers),
                  findings, (), frozenset(), {}, approve_hint(gate.kind.value, sha256[:8], n), built_at,
                  tuple(warnings), tuple(resolutions), decisions_sha256, title, page_lines, lead,
                  carried=tuple(f.fid for f in carried))
    overhead = height(render(replace(probe, sections=(Section(SectionKey.COLLAPSED, "", (), "x"),))))
    code_checks = [f.fid for f in findings if Slot.CHECK in f.by]
    kept, over = fit(sections, LINE_BUDGET - overhead, frozenset(code_checks[:1]))
    shown = frozenset(i for s in kept for r in s.rows for i in r.ids if i in items)
    collapsed: Dict[str, List[str]] = {}
    for i in items:
        if i not in shown:
            p = placed.get(i)
            collapsed.setdefault(p if isinstance(p, str) and not isinstance(p, SectionKey) else OVER, []).append(i)
    collapsed_t = {k: tuple(v) for k, v in collapsed.items()}
    line = _counts_line(collapsed_t, items, before is not None and before.carries, brain_empty, unanswered)
    final = kept + ((Section(SectionKey.COLLAPSED, "", (), line),) if line else ())
    sheet = replace(probe, sections=final, shown=shown, collapsed=collapsed_t, examples=_examples(kept, prev_rows))
    _check(sheet, items)
    return sheet


def _check(sheet: Sheet, items: Dict[str, Item]) -> None:
    from . import review_turn
    every = [i for ids in sheet.collapsed.values() for i in ids]
    if len(every) != len(set(every)) or set(every) & sheet.shown or set(every) | sheet.shown != set(items):
        raise AssertionError("shown and collapsed do not partition the round's items")
    text = render(sheet)
    if height(text) > LINE_BUDGET:
        raise AssertionError("the sheet is %d lines, over %d" % (height(text), LINE_BUDGET))
    verdict = review_turn().VERDICT_LINE_RE
    for line in text.split("\n"):
        if verdict.match(line) or checks.APPROVAL_LINE_RE.match(line) or mrdesc.ANSWER_LINE_RE.match(line):
            raise AssertionError("a sheet line reads as a verdict, an approval or an answer: %r" % line)


# ---------- rendering ----------

def height(text: str) -> int:
    """The lines a person reads: blank lines and table separator rows not counted."""
    return sum(1 for line in text.split("\n") if line.strip() and not SEP_ROW_RE.match(line))


def carrying(sheet: Sheet) -> bool:
    return UNCHANGED in sheet.collapsed or any(s.key is SectionKey.PREVIOUS for s in sheet.sections)


def _examples(sections: Sequence[Section], prev_rows: Sequence[Previous]) -> Tuple[str, ...]:
    """The answer lines the sheet shows as examples, in the forms the grammar records (review_turn.compose's), made
    from the ids it displays: an item to change (`A-1 改：原因`); in round 1 a decision to change too, a finding upheld
    (`F-1 对`) and another dismissed (`F-2 不用`); from round 2 a finding upheld and the decision a sent-back line left
    unchanged, taken back (`D-11 可以`, the one waiting for the person)."""
    sec = {s.key: s for s in sections}

    def first(key, kind):
        return next((i for r in getattr(sec.get(key), "rows", ()) for i in r.ids if i.startswith(kind + "-")), None)
    fids = [r.finding for k in (SectionKey.BOTH, SectionKey.ONE) for r in getattr(sec.get(k), "rows", ()) if r.finding]
    if prev_rows:
        waiting = next((i for p in prev_rows if p.line.effect is Effect.CHANGE and not p.moved
                        for i in p.line.about if i.startswith("D-")), None)
        a = first(SectionKey.PREVIOUS, "A") or first(SectionKey.ASSUMPTIONS, "A") or first(SectionKey.CHANGED, "A")
        d = None if waiting or a else first(SectionKey.DECISIONS, "D")
        out = ["%s 改：原因" % x for x in (a, d) if x] + (["%s 对" % fids[0]] if fids else []) + \
              (["%s 可以" % waiting] if waiting else [])
    else:
        one = [r.finding for r in getattr(sec.get(SectionKey.ONE), "rows", ()) if r.finding]
        other = next((f for f in one + fids if f != fids[0]), None) if fids else None
        a, d = first(SectionKey.ASSUMPTIONS, "A"), first(SectionKey.DECISIONS, "D")
        out = ["%s 改：原因" % x for x in (a, d) if x] + (["%s 对" % fids[0]] if fids else []) + \
              (["%s 不用" % other] if other else [])
    if not any(x.endswith(" 改：原因") for x in out):
        ac = next((i for s in sections for r in s.rows for i in r.ids if i.startswith("AC-")), None)
        out.insert(0, "%s 改：原因" % (ac or "AC-1"))
    return tuple(out)


def _table(block: Sequence[Section], revs_cols: bool) -> List[str]:
    s = block[-1]
    head = list(HEADERS[s.key])
    if s.key is SectionKey.PREVIOUS and revs_cols:
        head.append("预审")
    rows = [list(r.cells) for x in block for r in x.rows]
    if s.key is SectionKey.DECISIONS and not two_up(s):
        head.append("谁")
    if len(block) == 1 and two_up(s):
        head = head * 2
        rows = [rows[k] + (rows[k + 1] if k + 1 < len(rows) else ["", ""]) for k in range(0, len(rows), 2)]
    out = [_row(head), "|" + "---|" * len(head)]
    return out + [_row(r) for r in rows]


def _row(cells: Sequence[str]) -> str:
    return "|" + "|".join(" %s " % c.replace("|", "\\|") if c else " " for c in cells) + "|"


def _prereview_line(sheet: Sheet) -> str:
    runs = [r for r in sheet.reviewers if r.slot is not Slot.CHECK]
    ok = [r for r in runs if r.status == "ok"]
    bad = [r for r in runs if r.status != "ok"]
    mine = [f for f in sheet.findings if f.fid not in sheet.carried]
    checks_ = sum(1 for f in mine if Slot.CHECK in f.by)
    if not ok:
        why = next((r.note for r in runs if r.note), "")
        line = "没有预审%s。" % ("：" + _plain(why, 120) if why else "")
    else:
        names = "、".join(name(r.model) for r in ok)
        parts = ["%s %s一份副本（%s）" % (names, "各读" if len(ok) > 1 else "读了",
                                       " / ".join(seconds_text(r.seconds) for r in ok))]
        counts = [r.findings for r in ok]
        parts.append("各提 %d 条" % counts[0] if len(ok) > 1 and len(set(counts)) == 1 else
                     "、".join("%s 提 %d 条" % (name(r.model), r.findings) for r in ok))
        if len(ok) > 1:
            both = sum(1 for f in mine if f.both)
            parts.append("%d 处两边都提，排在最前" % both if both else "没有两边都提的")
        line = "预审%s：%s。" % ("只看改动" if carrying(sheet) else "", "，".join(parts))
        for r in bad:
            line += "%s 没交：%s。" % (name(r.model), _plain(r.note, 80) if r.note else STATUS.get(r.status, r.status))
        unsure = [name(r.model) for r in ok if r.model_check not in ("", "match")]
        if unsure:
            line += "%s 模型没核实。" % "、".join(unsure)
    if checks_:
        line += "代码检查另提 %d 条。" % checks_
    return line


# What the sheet says an example line records, after it (the forms are review_turn.compose's).
GLOSSES = {"对": "（评审说得对，要改）", "不用": "（这条不用改）", "可以": "（收回上轮的退回）"}


def render(sheet: Sheet) -> str:
    """Markdown, Chinese. The title (gate, short sha, round), the lead sentence (the pages and their length, or what
    changed since the round compared with), the reviewers and what they gave, one sentence on how to answer with
    the sheet's own ids, the sections as tables, the counts line, the approve sentence. Every item row is a table row
    (starts with '|'), every other line starts with fixed text: no line starts with an id, a list marker or a verb.
    The whole text goes through mrdesc.inert() last."""
    out = ["### 审批单 · %s %s · 第 %d 轮" % (sheet.gate.kind.value, sheet.sha256[:8], sheet.n)]
    if sheet.lead:
        out.append(sheet.lead)
    out.append(_prereview_line(sheet))
    if sheet.warnings:
        out.append("注意：%s。" % "；".join(_plain(w, 120) for w in sheet.warnings))
    out += ["", "要改才写一行，条目号放行首，例如 %s。原因只写在「改：」后面。不写就是接受。" % "、".join(
        "`%s`%s" % (e, GLOSSES.get(e.split(" ", 1)[1], "") if e.startswith("F-") or e.endswith(" 可以") else "")
        for e in sheet.examples)]
    cols = any(sheet.resolutions)
    for k, block in enumerate(_blocks(sheet.sections)):
        notes = "；".join(dict.fromkeys(c for s in block if s.note for c in s.note.split("；")))
        out += ["", "**%s %s%s**" % (CIRCLED[k] if k < len(CIRCLED) else "·", block[0].title,
                                     "（%s）" % notes if notes else ""), ""] + _table(block, cols)
    line = next((s.note for s in sheet.sections if s.key is SectionKey.COLLAPSED and s.note), "")
    if line:
        out += ["", line]
    out += ["", sheet.approve_hint]
    return mrdesc.inert("\n".join(out)) + "\n"


# ---------- sheet.json ----------

def to_json(sheet: Sheet) -> dict:
    """sheet.json (schema 1): everything in Sheet, findings with all notes and evidence, rows with their ids, and
    shown_findings (the F-n with a row). This is what the hook pins and what `F-n` resolves against (findings[].fid ->
    targets); the receipt takes only shown_findings as findings the person could leave unmentioned."""
    return {
        "schema": sheet.schema, "gate": {"intent": sheet.gate.intent, "kind": sheet.gate.kind.value},
        "sha256": sheet.sha256, "n": sheet.n, "previous": sheet.previous, "files": dict(sheet.files),
        "reviewers": [{"slot": r.slot.value, "model": r.model, "status": r.status, "seconds": r.seconds,
                       "findings": r.findings, "note": r.note, "model_check": r.model_check, "session": r.session}
                      for r in sheet.reviewers],
        "findings": [{"fid": f.fid, "targets": list(f.targets), "type": f.type.value,
                      "by": [s.value for s in Slot if s in f.by], "notes": dict(f.notes),
                      "evidence": dict(f.evidence), "quote_ok": dict(f.quote_ok),
                      "unknown_targets": list(f.unknown_targets)} for f in sheet.findings],
        "sections": [{"key": s.key.value, "title": s.title, "note": s.note,
                      "rows": [{"ids": list(r.ids), "cells": list(r.cells), "finding": r.finding} for r in s.rows]}
                     for s in sheet.sections],
        "shown": sorted(sheet.shown), "collapsed": {k: list(v) for k, v in sheet.collapsed.items()},
        "shown_findings": list(sheet.shown_findings), "carried": list(sheet.carried),
        "approve_hint": sheet.approve_hint, "built_at": sheet.built_at, "warnings": list(sheet.warnings),
        "resolutions": [{"target": r.target, "slot": r.slot.value, "resolved": r.resolved, "note": r.note}
                        for r in sheet.resolutions],
        "decisions_sha256": sheet.decisions_sha256, "title": sheet.title,
        "page_lines": dict(sheet.page_lines) if sheet.page_lines is not None else None, "lead": sheet.lead,
        "examples": list(sheet.examples),
    }


def from_json(data: dict) -> Sheet:
    """The Sheet of a sheet.json; ValueError when it is not one (another schema, or a shape this code did not
    write). shown_findings is not read: it follows from the rows. A sheet.json from before `carried` carried none."""
    from .types import FindingType
    try:
        if data.get("schema") != SCHEMA:
            raise ValueError("sheet.json schema %r, not %d" % (data.get("schema"), SCHEMA))
        return Sheet(
            data["schema"], Gate(data["gate"]["intent"], Kind(data["gate"]["kind"])), data["sha256"], data["n"],
            data["previous"], dict(data["files"]),
            tuple(ReviewerRun(Slot(r["slot"]), r["model"], r["status"], r["seconds"], r["findings"], r["note"],
                              r["model_check"], r["session"]) for r in data["reviewers"]),
            tuple(Finding(f["fid"], tuple(f["targets"]), FindingType(f["type"]), frozenset(Slot(s) for s in f["by"]),
                          dict(f["notes"]), dict(f["evidence"]), dict(f["quote_ok"]), tuple(f["unknown_targets"]))
                  for f in data["findings"]),
            tuple(Section(SectionKey(s["key"]), s["title"],
                          tuple(Row(tuple(r["ids"]), tuple(r["cells"]), r["finding"]) for r in s["rows"]), s["note"])
                  for s in data["sections"]),
            frozenset(data["shown"]), {k: tuple(v) for k, v in data["collapsed"].items()}, data["approve_hint"],
            data["built_at"], tuple(data["warnings"]),
            tuple(Resolution(r["target"], Slot(r["slot"]), r["resolved"], r["note"]) for r in data["resolutions"]),
            data["decisions_sha256"], data["title"],
            dict(data["page_lines"]) if data["page_lines"] is not None else None, data["lead"],
            tuple(data["examples"]), tuple(data.get("carried", ())))
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError("sheet.json is not a review sheet: %s" % e)
