"""brainkit.review._items: what the pages say, as items, and what changed between two versions. Pure functions over
bytes; no I/O, no git, no Lean. Private: import brainkit.review, never this module (a test fails the build on an
import of brainkit.review._* from outside the package).

The one extractor of ACs, assumptions and decisions: mrdesc and trace read requirements.md through it. It reads both
shapes in use: Lean's template (`| ID | Given / when | Then (expected) |`, `- A-1: ...`) and the one agents write in
practice (`| ID | 条件 → 结果 | 来源 |` under `###` subsections, an assumptions table `| ID | 假设 | 谁确认 |`).
Decisions are trace.decision_rows / trace.classify_who (the kit's one reading of decisions.md and its Who words).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from .. import trace as T
from .. import usage
from .types import ChangeKind, DocChange, Item, ItemChange, ItemKind, Kind, Page, Source

REQ_PAGE = "inception/requirements/requirements.md"
DESIGN_PAGE = "inception/application-design/application-design.md"
DECISIONS_PAGE = "decisions.md"
RENAME_RATIO = 0.85         # an id that vanished and a new id with this similar a text: one item renumbered
REWRITE_RATIO = 0.6         # below this, the fragment shown is the new text, not the changed spans
FRAGMENT_CHARS = 70
AC_RE = re.compile(r"^AC-[1-9][0-9]{0,3}$")
A_RE = re.compile(r"^A-[1-9][0-9]{0,3}$")
D_RE = re.compile(r"^D-[1-9][0-9]{0,3}$")
AC_BULLET_RE = re.compile(r"^\s*[-*]\s*(AC-[1-9][0-9]{0,3})\s*[:：.)]\s*(\S.*)$")
ID_RE = re.compile(r"(?<![A-Za-z0-9-])((?:AC|A|D)-[1-9][0-9]{0,3})(?![0-9])")
INFERRED_RE = re.compile(r"(?i)\binferred\b|推断|推测")
# a material segment (D01#S003 · <hash8>), an intent's AC (intent.md AC-2) or a decision id: an AC traced to these
SOURCE_RE = re.compile(r"D[0-9]{2}#S[0-9]{3}|intent\.md\s+AC-[0-9]+|(?<![A-Za-z0-9-])D-[1-9][0-9]{0,3}(?![0-9])")
SUPERSEDES_RE = re.compile(r"(?i)(?:supersedes|replaces|取代|代替|替代)\s*(D-[1-9][0-9]{0,3})")
SOURCE_HEADERS = ("来源", "source", "出处", "src")
WHO_HEADERS = ("谁确认", "who", "confirm", "确认", "谁")
HEADING_RE = re.compile(r"^(#{2,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEP_RE = re.compile(r"^:?-{2,}:?$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
ITEM_ROW_RE = re.compile(r"^\s*(?:\|\s*[`*]*(?:AC|A|D)-[1-9][0-9]{0,3}[`*]*\s*\||[-*]\s*(?:AC|A)-[1-9][0-9]{0,3}\s*[:：.)])")
BY_SOURCE = {"agent": Source.AGENT, "brain": Source.BRAIN, "check": Source.CHECK}


def norm(text: str) -> str:
    """The comparison form of a text: NFKC (full-width letters and digits as ASCII), whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


def page_kind(path: str) -> Optional[str]:
    """"requirements", "decisions" or None, from the page's path (project- or intent-relative)."""
    p = path.replace("\\", "/")
    if p == REQ_PAGE or p.endswith("/" + REQ_PAGE):
        return "requirements"
    if p == DECISIONS_PAGE or p.endswith("/" + DECISIONS_PAGE):
        return "decisions"
    return None


def _uncomment(text: str) -> str:
    """HTML comments removed, line numbers kept."""
    return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _bare(cell: str) -> str:
    return cell.strip().strip("`*").strip()


def _col(header: Sequence[str], words: Sequence[str]) -> Optional[int]:
    return next((i for i, h in enumerate(header) if i > 0 and any(w in h for w in words)), None)


def _cites(own: str, cells: Sequence[str]) -> Tuple[str, ...]:
    whole = " ".join(cells)
    brain = list(dict.fromkeys(usage.CITE_RE.findall(whole)))
    ids = [i for i in dict.fromkeys(ID_RE.findall(whole)) if i != own]
    return tuple(brain + ids)


def ac_source(cell: str) -> Source:
    """TRACED: names a material segment, an intent AC or a decision; PARTLY_INFERRED: that and an inferred detail;
    INFERRED: only `inferred (...)`, or only an assumption id (it rests on that assumption); UNKNOWN: nothing."""
    traced, inferred = bool(SOURCE_RE.search(cell)), bool(INFERRED_RE.search(cell))
    if traced:
        return Source.PARTLY_INFERRED if inferred else Source.TRACED
    if inferred or re.search(r"(?<![A-Za-z0-9-])A-[1-9][0-9]{0,3}(?![0-9])", cell):
        return Source.INFERRED
    return Source.UNKNOWN


def _requirements(path: str, text: str) -> Tuple[List[Item], List[Tuple[str, int]]]:
    out, dups, seen = [], [], set()
    section = sub = ""
    zone, header, fence = None, None, False

    def add(item):
        if item.id in seen:
            dups.append((item.id, item.line))
        else:
            seen.add(item.id)
            out.append(item)

    for n, line in enumerate(_uncomment(text).split("\n"), 1):
        if FENCE_RE.match(line):
            fence, header = not fence, None
            continue
        if fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            title = m.group(2).strip()
            if len(m.group(1)) == 2:
                section, sub = title, ""
                low = title.lower()
                zone = "ac" if low.startswith("acceptance") or title.startswith("验收") else \
                    "a" if low.startswith("assumption") or title.startswith("假设") else None
            else:
                sub = title
            header = None
            continue
        if zone is None:
            continue
        s = line.strip()
        if not s.startswith("|"):
            header = None
            bm = AC_BULLET_RE.match(line) if zone == "ac" else T.ASSUMPTION_RE.match(line) if zone == "a" else None
            if bm and bm.group(2).strip():
                body = bm.group(2).strip()
                kind = ItemKind.AC if zone == "ac" else ItemKind.ASSUMPTION
                add(Item(bm.group(1), kind, norm(body), (body,), body, path, n, sub or section,
                         ac_source(body) if zone == "ac" else Source.ASSUMED, cites=_cites(bm.group(1), [body])))
            continue
        cells = T._cells(s)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(SEP_RE.match(c) for c in cells if c):
            continue
        iid = _bare(cells[0]) if cells else ""
        rest = list(cells[1:])
        if zone == "ac" and AC_RE.match(iid):
            src = _col(header, SOURCE_HEADERS)
            texts = [c for i, c in enumerate(rest, 1) if i != src]
            main = " → ".join(c.strip() for c in texts if c.strip())
            if not main:
                continue                        # the template's empty `| AC-1 |  |  |`
            scell = cells[src] if src is not None and src < len(cells) else ""
            add(Item(iid, ItemKind.AC, norm(" | ".join(rest)), tuple(rest), main, path, n, sub or section,
                     ac_source(scell if src is not None else main), cites=_cites(iid, rest)))
        elif zone == "a" and A_RE.match(iid):
            who = _col(header, WHO_HEADERS)
            texts = [c for i, c in enumerate(rest, 1) if i != who]
            main = " · ".join(c.strip() for c in texts if c.strip())
            if not main:
                continue
            wcell = cells[who].strip() if who is not None and who < len(cells) else ""
            add(Item(iid, ItemKind.ASSUMPTION, norm(" | ".join(rest)), tuple(rest), main, path, n, sub or section,
                     Source.ASSUMED, who=wcell or None, cites=_cites(iid, rest)))
    return out, dups


def _decisions(path: str, text: str, bot_users: Sequence[str]) -> Tuple[List[Item], List[Tuple[str, int]]]:
    cut = re.search(r"(?m)^##[ \t]+Brain proposals", text)
    out, dups, seen = [], [], set()
    for n, row in T.decision_rows(text[:cut.start()] if cut else text):
        did, dec = _bare(row.get("id", "")), row.get("decision", "")
        if not D_RE.match(did) or not dec.strip():
            continue
        by, shown = T.classify_who(row.get("who"), bot_users)
        source = BY_SOURCE.get(by) or (Source.OWNER if by == "person" and shown == "owner" else
                                       Source.VOUCHER if by == "person" else Source.UNKNOWN)
        cells = tuple(v for k, v in row.items() if k != "id")
        item = Item(did, ItemKind.DECISION, norm(" | ".join(cells)), cells, dec.strip(), path, n,
                    (row.get("stage") or "").strip(), source, who=(row.get("who") or "").strip() or None,
                    stage=(row.get("stage") or "").strip() or None, cites=_cites(did, cells),
                    supersedes=tuple(i for i in dict.fromkeys(SUPERSEDES_RE.findall(dec + " " + row.get("why", "")))
                                     if i != did))
        if did in seen:
            dups.append((did, n))
        else:
            seen.add(did)
            out.append(item)
    return out, dups


def read_page(path: str, data: Optional[bytes], bot_users: Sequence[str] = ()) -> Page:
    """The items of one page, in page order. requirements.md: AC-n (table rows whose first cell is AC-n, or `- AC-n:`
    bullets, anywhere under `## Acceptance criteria`; the column headed 来源/source/出处 is the source) and A-n (table
    rows or `- A-n:` lines under `## Assumptions`; the column headed 谁确认/who is who confirms). decisions.md: D-n rows
    above `## Brain proposals`. Any other page has no items. Never raises on odd Markdown: a row it cannot read is
    skipped; a row with an id an earlier row has is a duplicate (the first wins)."""
    text = (data or b"").decode("utf-8", "replace").replace("\r\n", "\n")
    kind = page_kind(path)
    items, dups = (_requirements(path, text) if kind == "requirements" else
                   _decisions(path, text, bot_users) if kind == "decisions" else ([], []))
    return Page(path, tuple(items), tuple(dups), text.count("\n") + (0 if text.endswith("\n") or not text else 1))


def items(path: str, data: Optional[bytes], bot_users: Sequence[str] = ()) -> List[Item]:
    return list(read_page(path, data, bot_users).items)


def design_stage(stage: Optional[str]) -> bool:
    """A decision taken in Application design or Units (what a DESIGN approval covers), from its Stage cell."""
    s = (stage or "").lower()
    return any(w in s for w in ("design", "设计", "unit", "单元"))


def gate_items(kind: str, pages: Dict[str, bytes], decisions: Optional[Tuple[str, bytes]],
               bot_users: Sequence[str] = ()) -> Dict[str, Item]:
    """{id: item} a gate asks about, in order: REQUIREMENTS -> the requirements page's ACs and assumptions and the
    decisions not taken in a design stage; DESIGN -> the decisions taken in a design stage; INCEPTION -> all of them.
    `pages`: the requested pages (path -> bytes); `decisions`: (path, bytes) of the intent's decisions.md, or None."""
    k = Kind(kind)
    out = {}
    if k in (Kind.INCEPTION, Kind.REQUIREMENTS):
        for path, data in pages.items():
            if page_kind(path) == "requirements":
                for it in items(path, data, bot_users):
                    out.setdefault(it.id, it)
    if decisions is not None:
        for it in items(decisions[0], decisions[1], bot_users):
            if k is Kind.INCEPTION or design_stage(it.stage) == (k is Kind.DESIGN):
                out.setdefault(it.id, it)
    return out


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def diff(before: Dict[str, Item], after: Dict[str, Item]) -> List[ItemChange]:
    """Item identity across two versions. Same id and same text: UNCHANGED; same id, other text: CHANGED with a
    fragment; an id only in `after` whose text matches an id only in `before` (ratio >= RENAME_RATIO, same kind, best
    pair first): RENAMED; the rest ADDED / REMOVED. Order: `after`'s order, then the removed ones in `before`'s."""
    gone = [b for i, b in before.items() if i not in after]
    new = [a for i, a in after.items() if i not in before]
    pairs = sorted(((_ratio(b.text, a.text), k, j) for k, b in enumerate(gone) for j, a in enumerate(new)
                    if a.kind == b.kind), reverse=True)
    renamed, used_b, used_a = {}, set(), set()
    for r, k, j in pairs:
        if r < RENAME_RATIO:
            break
        if k in used_b or j in used_a:
            continue
        used_b.add(k)
        used_a.add(j)
        renamed[new[j].id] = gone[k]
    out = []
    for i, a in after.items():
        b = before.get(i)
        if b is not None:
            same = b.text == a.text
            out.append(ItemChange(i, ChangeKind.UNCHANGED if same else ChangeKind.CHANGED, b, a,
                                  "" if same else fragment(b, a)))
        elif i in renamed:
            b = renamed[i]
            out.append(ItemChange(i, ChangeKind.RENAMED, b, a, "" if b.text == a.text else fragment(b, a), b.id))
        else:
            out.append(ItemChange(i, ChangeKind.ADDED, None, a, _cut(a.main, FRAGMENT_CHARS)))
    for k, b in enumerate(gone):
        if k not in used_b:
            out.append(ItemChange(b.id, ChangeKind.REMOVED, b, None))
    return out


def _cut(text: str, limit: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def fragment(before: Item, after: Item, limit: int = FRAGMENT_CHARS) -> str:
    """The change of one item for one screen, from the cells as written: the longest changed span (and the second,
    when there is room) as 加「…」 / 删「…」 / 「…」改成「…」; when the item was mostly rewritten (ratio < REWRITE_RATIO),
    「new text」. Cut to `limit` characters with …"""
    a, b = " ".join(" | ".join(before.cells).split()), " ".join(" | ".join(after.cells).split())
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.ratio() < REWRITE_RATIO:
        return "「%s」" % _cut(after.main, limit - 2)
    spans = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "insert":
            spans.append((j2 - j1, "加「%s」" % b[j1:j2].strip()))
        elif op == "delete":
            spans.append((i2 - i1, "删「%s」" % a[i1:i2].strip()))
        elif op == "replace":
            spans.append((max(i2 - i1, j2 - j1), "「%s」改成「%s」" % (a[i1:i2].strip(), b[j1:j2].strip())))
    spans = [s for s in spans if s[1] not in ("加「」", "删「」")]
    spans.sort(key=lambda s: -s[0])
    text = "；".join(s for _, s in spans[:2])
    if len(spans) > 1 and len(text) > limit:
        text = spans[0][1]
    return _cut(text, limit)


def _sections(text: str) -> Dict[str, List[str]]:
    """{`## ` heading: its lines that are not item rows}, in page order; lines before the first heading under ''."""
    out, cur = {"": []}, ""
    for line in _uncomment(text).split("\n"):
        m = re.match(r"^##[ \t]+(.+?)[ \t]*$", line)
        if m:
            cur = m.group(1).strip()
            out.setdefault(cur, [])
        elif line.strip() and not ITEM_ROW_RE.match(line):
            out[cur].append(line.strip())
    return out


def page_changes(path: str, before: Optional[bytes], after: Optional[bytes]) -> List[DocChange]:
    """Changes outside the item rows: per `## ` heading, the lines added and removed (difflib over the lines that
    are not AC / A / D rows). Headings in the order of `after`, then those only `before` has."""
    b = _sections((before or b"").decode("utf-8", "replace"))
    a = _sections((after or b"").decode("utf-8", "replace"))
    out = []
    for h in list(a) + [h for h in b if h not in a]:
        added = removed = 0
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b.get(h, []), a.get(h, []),
                                                          autojunk=False).get_opcodes():
            if op in ("replace", "delete"):
                removed += i2 - i1
            if op in ("replace", "insert"):
                added += j2 - j1
        if added or removed:
            out.append(DocChange(path, h or "(开头)", added, removed))
    return out


def explanations(target: str, new_decisions: Sequence[Item]) -> List[Item]:
    """Decision rows added since the last round that name `target` (in their Decision or Why, or by `取代`): how the
    agent answered a line it did not carry out ("D-32 保留 D-19…"), or what replaced it ("D-28 … 取代 D-24")."""
    return [d for d in new_decisions if target in d.supersedes or target in d.cites]
