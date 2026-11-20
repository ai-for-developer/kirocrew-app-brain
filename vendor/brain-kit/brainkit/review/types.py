"""brainkit.review.types: the public domain types of the review sheet (审批单). Python 3.9, stdlib only.

Every value here is DERIVED. Nothing in this file is stored as such: the stored facts are
  - Lean's ledger (aidlc-docs/audit/<clone>.jsonl): which version was requested, approved, rejected; HUMAN_TURN hashes
  - the review log the prompt hook writes (aidlc-docs/audit/brain/<clone>.review.ndjson, kit/.kiro/brain/
    review_turn.py): what a person typed against an open round, and which bytes were in front of them at that moment
  - snapshots (aidlc-docs/audit/brain/blobs/<sha256>): those bytes, content-addressed, verified on every read
  - the round's work folder runs/<KEY>/review/<KIND>-<sha12>/: brief, each reviewer's answer, sheet.json (write-once)
Everything below is recomputed from those on each call: same records in, same values out.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Dict, FrozenSet, NamedTuple, Optional, Tuple

SCHEMA = 1          # of view() and sheet.json; bump on an incompatible change (the app checks it)
LINE_BUDGET = 45    # rendered lines of a sheet, blank lines and table separator rows not counted: one screen


class Kind(str, enum.Enum):
    """The Lean approvals that get a sheet. PLAN, RELEASE, IDEATION and CR-* do not."""
    INCEPTION = "INCEPTION"         # requirements.md + application-design.md (one request, one sha)
    REQUIREMENTS = "REQUIREMENTS"
    DESIGN = "DESIGN"


class Gate(NamedTuple):
    """What a sheet is keyed by (and the app's drawer tab): the intent and the approval kind. INCEPTION spans two
    documents, so the key is never a document or a board node."""
    intent: str
    kind: Kind


# ---------- items: what a person can accept or reject one by one ----------

class ItemKind(str, enum.Enum):
    """The values plus "F" are review_turn.ITEM_PREFIXES. The grammar file imports nothing from brainkit, so this
    list cannot be derived from it: tests/test_review_boundaries.py fails when the two disagree."""
    AC = "AC"                   # acceptance criterion row, requirements.md
    ASSUMPTION = "A"            # A-n, requirements.md (a table row or a `- A-n:` line)
    DECISION = "D"              # D-n row of decisions.md


class Source(str, enum.Enum):
    """Why an item is (or is not) on the sheet, read from its own cells: an AC's source, a decision's Who."""
    TRACED = "traced"           # AC cites a material segment (D01#S003 · <hash8>), intent.md AC-n or a decision id
    PARTLY_INFERRED = "partly"  # AC cites sources AND names an inferred detail ("…; 503 inferred (product to confirm)")
    INFERRED = "inferred"       # AC source is only `inferred (product to confirm)` (or only an assumption id)
    AGENT = "agent"             # decision Who = agent (or a kit.json bot user): the agent decided on its own
    OWNER = "owner"             # decision Who = owner: the person at this machine answered in chat
    BRAIN = "brain"             # decision Who = brain <entry id>
    CHECK = "check"             # decision Who = check
    VOUCHER = "voucher"         # decision Who = <pseudonym> <voucher id>: answered through the bridge
    ASSUMED = "assumed"         # every assumption A-n: someone named in 谁确认 has to confirm it
    UNKNOWN = "unknown"         # AC without a source, decision whose Who is empty or outside the vocabulary


@dataclass(frozen=True)
class Item:
    """One row a person may reject on its own. Identity across rounds is `id` (AC-36 stays AC-36); a row renumbered
    to another id is caught by text (see _items.diff). `text` is the comparison key (NFKC, whitespace collapsed, every
    cell but the id joined); `cells` are the cells as written, the id left out; `main` is the statement as written
    (an AC's condition and result without its source, an assumption's text, a decision's Decision cell)."""
    id: str
    kind: ItemKind
    text: str
    cells: Tuple[str, ...]
    main: str
    path: str                   # project-relative page, e.g. aidlc-docs/<intent>/inception/requirements/requirements.md
    line: int                   # 1-based line of the row in that page
    section: str                # the heading it sits under ("登录与权限", "Assumptions", the decision's Stage)
    source: Source
    who: Optional[str] = None           # assumption 谁确认 / decision Who, as written
    stage: Optional[str] = None         # decision Stage (Requirements | Application design | ...)
    cites: Tuple[str, ...] = ()         # Brain entry ids, then AC-n, A-n and D-n ids named in its cells (not its own)
    supersedes: Tuple[str, ...] = ()    # decision ids this row says it replaces ("取代 D-24", "supersedes D-24")


@dataclass(frozen=True)
class Page:
    """One page read as items. `duplicates`: (id, line) of each later row whose id an earlier row already has (the
    first row wins; brain review shows it as a code check, it never guesses which one is meant)."""
    path: str
    items: Tuple[Item, ...]
    duplicates: Tuple[Tuple[str, int], ...]
    lines: int                  # the page's line count, for the sheet's header


class ChangeKind(str, enum.Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    RENAMED = "renamed"         # the same text (difflib ratio >= RENAME_RATIO) under another id


@dataclass(frozen=True)
class ItemChange:
    id: str
    change: ChangeKind
    before: Optional[Item]
    after: Optional[Item]
    fragment: str = ""          # the changed span(s) for one screen: +「…」 −「…」, or the new text when mostly rewritten
    renamed_from: Optional[str] = None


@dataclass(frozen=True)
class DocChange:
    """A change outside the item rows (Goal, NFR table, Components, Data model...): a count per `## ` heading."""
    path: str
    heading: str
    added: int
    removed: int


# ---------- findings: what the two reviewers (or a code check) flagged ----------

class FindingType(str, enum.Enum):
    CONTRADICTION = "contradiction"     # 矛盾
    UNTESTABLE = "untestable"           # 测不了
    MISSING = "missing"                 # 漏了 (an edge case nobody wrote down)
    BRAIN = "brain"                     # conflicts with a Brain entry (its id must exist: checked)
    CHECK = "check"                     # found by code, not a model: Who vocabulary, Brain ids, duplicate ids, ...
    OTHER = "other"


class Slot(str, enum.Enum):
    """The two reviewers. Their models come from kit.json at run time (a = `model`, b = `review_model`); no agent spec
    repeats them. CHECK is code: never counted as one of the two models."""
    A = "a"
    B = "b"
    CHECK = "check"


@dataclass(frozen=True)
class ReviewerRun:
    slot: Slot
    model: str
    status: str                 # ok | failed | timeout | skipped (no kiro-cli, or --no-prereview) | bad_output
    seconds: Optional[float]
    findings: int
    note: str = ""              # why it failed, one line, shown in the sheet header
    model_check: str = ""       # match | mismatch | inconclusive (kiro-cli's own session record), "" when not run
    session: Optional[str] = None       # the kiro-cli session id of the run


@dataclass(frozen=True)
class Finding:
    """One sheet line F-n: raw findings that flag the same item(s). `both` is True only when slot a and slot b each
    flagged at least one shared target (the merge is by target item, done by code). F ids never repeat within an
    intent: round 2 goes on from F-8 when round 1 ended at F-7, so `F-1 对` names one finding forever and resolves
    through the sheet.json that has it, pinned by the hook when the person first saw it."""
    fid: str
    targets: Tuple[str, ...]            # item ids in page order (may be empty: about the pages as a whole)
    type: FindingType
    by: FrozenSet[Slot]
    notes: Dict[str, str]               # slot value -> that reviewer's one-line problem, as written (made inert)
    evidence: Dict[str, str]            # slot value -> the quote it gave from the pages (sheet.json, not the screen)
    quote_ok: Dict[str, bool]           # slot value -> the quote is in the requested bytes (False: 引文对不上)
    unknown_targets: Tuple[str, ...] = ()   # ids it named that the pages do not have

    @property
    def both(self) -> bool:
        return Slot.A in self.by and Slot.B in self.by


@dataclass(frozen=True)
class Resolution:
    """Round >= 2: one reviewer's judgment of a line the person sent back last round (the brief lists each with the
    person's words and what changed): did the pages now do what the person asked? Changed / not changed is computed
    from bytes; this adds whether the change answers the person."""
    target: str                 # the line's first id as the person typed it: F-1, A-3, D-19
    slot: Slot
    resolved: Optional[bool]    # True 改到了, False 没改完, None the reviewer did not say
    note: str = ""


# ---------- rounds and verdicts ----------

class Effect(str, enum.Enum):
    """What one verdict line says about the DOCUMENT (the same values as review_turn.Effect)."""
    CHANGE = "change"           # item rejected / finding upheld: the pages must change here
    KEEP = "keep"               # item accepted explicitly / finding dismissed explicitly
    QUESTION = "question"       # a question: answered in chat, not a verdict
    UNCLEAR = "unclear"         # starts with an id, no word the grammar knows (finding lines only): not a verdict


class Via(str, enum.Enum):
    CHAT = "chat"               # typed in a local chat, recorded by the prompt hook, joined to Lean's HUMAN_TURN
    VOUCHER = "voucher"         # Phase 3: an MR answer's signed voucher (rejected_items)


@dataclass(frozen=True)
class Verdict:
    target: str                 # AC-n | A-n | D-n | F-n as typed (upper case)
    effect: Effect
    words: str                  # the line after the ids, masked like Lean masks rejection words, <= 300 chars
    at: str
    via: Via
    ref: str                    # "aidlc-docs/audit/brain/<clone>.review.ndjson#<seq>" or "V-<voucher id>"
    resolves_to: Tuple[str, ...] = ()   # F-n: its targets (from the round's pinned sheet); an item: (target,)
    line: int = 0               # the prompt line it was typed on (1-based; 0 for a voucher): `F-1 对` and `F-2 对`
                                # in one message are two lines, and round 2 shows a row for each


class Outcome(str, enum.Enum):
    OPEN = "open"               # requested, pages unchanged, no Lean decision yet
    APPROVED = "approved"       # Lean APPROVED this version (also by a kind covering these files)
    REJECTED = "rejected"       # Lean REJECTED while these bytes were current (`reject INCEPTION …`): the whole round
    SUPERSEDED = "superseded"   # a later request covers the same page(s) with another sha
    STALE = "stale"             # the pages changed after the request and nothing was requested since


@dataclass(frozen=True)
class Round:
    """One requested version of a gate's pages. Identity: the Lean request sha256 (a re-request of identical bytes
    is the same round). `n` counts only rounds a person saw (a counted TURN with this round open): a version the agent
    requested and replaced before anyone replied is not a round for the person (n None)."""
    gate: Gate
    sha256: str
    files: Dict[str, str]               # path -> sha256, as Lean recorded them in APPROVAL_REQUESTED
    requested_at: str
    outcome: Outcome
    n: Optional[int]                    # 1, 2, ... among seen rounds of the gate; None while unseen
    seen_at: Optional[str]
    sheet_sha256: Optional[str]         # the sheet.json blob pinned at the person's first turn (None: no sheet out)
    decisions_sha256: Optional[str]     # decisions.md blob pinned at the same turn (Lean's sha does not cover it)
    lean_words: Optional[str] = None    # Lean's REJECTED words for a whole-round rejection (already masked by Lean)
    verdicts: Tuple[Verdict, ...] = ()  # in time order; the last one per target stands
    replied_itemwise: bool = False      # at least one CHANGE or KEEP line was recorded against this round


class ItemState(str, enum.Enum):
    """Per item, once a gate is approved (reflect, eval and the app read this)."""
    ACCEPTED = "accepted"       # shown in a seen round with exactly the approved text, and no CHANGE stands on it
    SUPERSEDED = "superseded"   # a decision row a later row replaces (`取代 D-24`): it stays in decisions.md, it no longer holds
    PASSED = "passed"           # approved with the page, never shown on its own (collapsed, over the budget, no sheet)
    REJECTED = "rejected"       # a CHANGE stands on the approved text (the person approved over their own objection)
    OPEN = "open"               # the gate is not approved yet


@dataclass(frozen=True)
class ItemRejection:
    """A rejection that stands (the item changed after it, a later row replaced it, or it is still REJECTED): reflect
    wants a correction proposal whose src names `item`. A rejection withdrawn later (`D-19 可以` on unchanged text),
    or an item left unchanged and accepted when shown again, is not one."""
    gate: Gate
    item: str
    round_sha8: str
    words: str
    via_finding: Optional[str]          # "F-1" when it came from upholding a finding
    ref: str
    at: str = ""                        # when the line was typed (the TURN's ts)


# ---------- the sheet ----------

class SectionKey(str, enum.Enum):
    PREVIOUS = "previous"           # round >= 2: what you sent back last round, and what became of it
    BOTH = "both"                   # findings both reviewers raised
    ONE = "one"                     # findings one reviewer, or a code check, raised
    CHANGED = "changed"             # round >= 2: items that changed without being sent back; page changes per heading
    DECISIONS = "decisions"         # agent decisions (round 1: all of the gate's; round >= 2: the new ones)
    ASSUMPTIONS = "assumptions"     # round 1: every assumption, with the inferred AC that rests on it
    INFERRED = "inferred"           # round 1: ACs the agent inferred, not tied to an assumption or a finding
    COLLAPSED = "collapsed"         # one line of counts: what you do not need to read, and why


@dataclass(frozen=True)
class Row:
    ids: Tuple[str, ...]            # the item ids this row shows (they count as shown, see Sheet.shown)
    cells: Tuple[str, ...]          # already one line and inert
    finding: Optional[str] = None   # F-n when the row is a finding


@dataclass(frozen=True)
class Section:
    key: SectionKey
    title: str
    rows: Tuple[Row, ...]
    note: str = ""                  # e.g. "另 2 条在上面：D-20、D-24"


@dataclass(frozen=True)
class Sheet:
    """What one person sees for one round, and the exact accounting of it. Written once per round to
    runs/<KEY>/review/<KIND>-<sha12>/sheet.json (never rewritten: the person's `F-1 对` refers to this F-1) and
    pinned by the prompt hook at the person's first turn. Invariants (tested):
      - `shown` and the ids under `collapsed` partition the item ids of the round (nothing silently disappears)
      - no line of render(sheet) matches Lean's APPROVE_RE / REJECT_RE / NEAR_APPROVAL_RE, brain-kit's
        intake APPROVAL_LINE_RE, the bridge's ANSWER_RE or review_turn.VERDICT_LINE_RE (pasting it back records
        nothing)
      - render(sheet) fits LINE_BUDGET; over it, lower sections cap and their rest goes to `collapsed`
      - a finding without a row (not in shown_findings) was not seen: silence accepts nothing about it, and the next
        round's sheet lists it again (in that sheet's `carried`)"""
    schema: int
    gate: Gate
    sha256: str
    n: int                                  # the round number the person will see (seen rounds before it + 1)
    previous: Optional[str]                 # sha256 of the round it is compared with (the last one seen), or None
    files: Dict[str, str]                   # path -> sha256 of the requested pages
    reviewers: Tuple[ReviewerRun, ...]
    findings: Tuple[Finding, ...]
    sections: Tuple[Section, ...]
    shown: FrozenSet[str]                   # item ids displayed with their text or their change
    collapsed: Dict[str, Tuple[str, ...]]   # reason -> item ids not displayed one by one
    approve_hint: str                       # the sentence naming Lean's line (never a line of its own)
    built_at: str = ""
    warnings: Tuple[str, ...] = ()          # "K3 超时没交", "没有 kiro-cli：没有预审", "decisions.md 还是空的"
    resolutions: Tuple[Resolution, ...] = ()
    decisions_sha256: Optional[str] = None  # sha256 of the decisions.md the sheet was built from
    title: str = ""                         # the requirement's title, for the header
    page_lines: Optional[Dict[str, int]] = None     # path -> line count of each requested page, for the header
    lead: str = ""                          # the sentence under the title: the pages' size, or what changed since
                                            # the round compared with (its number is not in this round's data)
    examples: Tuple[str, ...] = ()          # the answer lines the sheet shows, from its own ids (`F-1 对`, `D-11 可以`)
    carried: Tuple[str, ...] = ()           # F-n of the round compared with that its screen could not hold, listed
                                            # again here: first in their table, marked, not this round's review

    @property
    def shown_findings(self) -> Tuple[str, ...]:
        """The F-n with a row in a findings table, in sheet order: the findings the person saw. sheet.json records
        them; the prompt hook's receipt reads that list (only these can go unmentioned)."""
        return tuple(r.finding for s in self.sections if s.key in (SectionKey.BOTH, SectionKey.ONE)
                     for r in s.rows if r.finding)
