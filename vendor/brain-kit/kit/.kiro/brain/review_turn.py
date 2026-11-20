"""brain-kit: the record of a person's turn while a review sheet (审批单) is out. Installed by brain-kit as
.kiro/brain/review_turn.py; AI-DLC Lean is unchanged.

Stdlib only, and no brainkit import: the hook adapter (.kiro/brain/lean-hook.py) runs this file from the bytes its
kit check just compared with the reference commit (it is under .kiro/brain/, a kit path), and brainkit loads the same
file from its own kit/ to read what it wrote. This one file owns everything about the record:
  - the verdict line grammar (VERDICT_LINE_RE, LEADS, verdict_lines, effect_of, hints, compose): the only copy anywhere
  - the log format, written AND read here (append, read_log)
  - the snapshot store, written AND read here (put_blob, read_blob)
  - where a round's work folder is (review_dir), so the hook and `brain review` agree on it
  - the receipt the session gets back (receipt)

  aidlc-docs/audit/brain/<clone-id>.review.ndjson     one TURN record per person's turn that matters, hash-chained
  aidlc-docs/audit/brain/blobs/<sha256>               bytes of a requested page / decisions.md / sheet.json, write-once

Why there: Lean refuses the agent's file tools and its shell commands anything under aidlc-docs/audit/ (guard_write,
RECORDS_RE), and that folder is committed with Lean's ledger. Lean reads only aidlc-docs/audit/*.jsonl at the top
(Project._read_events, chain_check): a subfolder is invisible to it. The suffix is .review.ndjson, not .jsonl, because
brain-kit's trace and mrdesc read every *.jsonl below aidlc-docs/audit/ as Lean's.

Who writes: only the UserPromptSubmit hook. No brain command writes here (an agent can run brain commands).
What counts: a TURN counts only when Lean logged the same prompt as HUMAN_TURN (sha256 and session): brainkit joins the
two at read time, so a subagent's prompt or a host prompt (Lean logs neither as HUMAN_TURN) does not count. Verdicts
are exactly as trustworthy as Lean's own approval records, no more: a payload piped into the hook by hand runs the
same hook and gets the same HUMAN_TURN, as an approval line in it gets Lean's APPROVED: a guardrail, not a security
boundary (Lean's docs say the same of its gates).
"""
from __future__ import annotations

import copy
import enum
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:
    fcntl = None

SCHEMA = 1
LOG_DIR = "aidlc-docs/audit/brain"
LOG_SUFFIX = ".review.ndjson"
BLOB_DIR = LOG_DIR + "/blobs"
SHEET_FILE = "sheet.json"
GATES = ("INCEPTION", "REQUIREMENTS", "DESIGN")
MAX_LINES = 40              # verdict lines kept per turn (a pasted page is not a review)
MAX_WORDS = 300             # characters kept per line, after masking
MAX_BLOB = 4 * 1024 * 1024
JOIN_SECONDS = 120          # a TURN and Lean's HUMAN_TURN of one submit are written within this of each other
MAX_QUOTED = 3              # lines a receipt quotes as not recorded; the rest are counted

# ---------- the grammar: one line = one or more item ids first, then what to do ----------
# Disjoint from Lean's prompt grammar BY CONSTRUCTION: every Lean pattern (APPROVE_RE, REJECT_RE, NEAR_APPROVAL_RE,
# ALLOW_PROTECTED_RE) and brain-kit's intake APPROVAL_LINE_RE start a line with a verb; this starts it with an id, so
# no line can be both (tests run Lean's own compiled regexes, loaded from the vendored aidlc_lean.py, on a corpus).
ITEM_PREFIXES = ("AC", "A", "D", "F")      # brainkit's ItemKind values + "F" must equal this (a test fails if not)
ITEM = r"(?:%s)-[1-9][0-9]{0,3}" % "|".join(ITEM_PREFIXES)
ITEM_ID_RE = re.compile(r"^%s$" % ITEM)
HYPHENS = "-－‐‑‒–−"      # an id as typed: A－3 (a full-width or Unicode hyphen) is A-3
ITEM_TYPED = r"(?:%s)[%s][1-9][0-9]{0,3}" % ("|".join(ITEM_PREFIXES), HYPHENS)
HYPHEN_TO_ASCII = str.maketrans({h: "-" for h in HYPHENS[1:]})
AFTER_IDS = r"[ \t:：,，、;；\-—–]*"
VERDICT_LINE_RE = re.compile(
    r"(?i)^[ \t]*(?:[-*+][ \t]+|[0-9]{1,3}[.)、）．][ \t]*)?"   # a list marker is fine; '>' (a quote) and '|' are not
    r"(?P<ids>" + ITEM_TYPED + r"(?:[ \t]*[,，、/][ \t]*" + ITEM_TYPED + r")*)"
    r"(?![0-9A-Za-z_-])" + AFTER_IDS + r"(?P<words>.*)$")
ID_SPLIT_RE = re.compile(r"[ \t]*[,，、/][ \t]*")
QUESTION_RE = re.compile(r"(?:[?？]|[吗呢么嘛][。.!！]?)\s*$")
# Lines a person may mean as a verdict that are not one: a verb first, then an item id (Lean ignores `驳回 A-3`; it
# takes `驳回 DESIGN 的 D-18` as rejecting all of DESIGN). Used only to explain, in the receipt, why nothing was
# recorded.
KEEP_VERBS = ("批准", "同意", "approve", "approved")
CHANGE_VERBS = ("驳回", "拒绝", "reject", "退回")
VERB_FIRST_ITEM_RE = re.compile(r"(?i)^[ \t]*(?:[-*+][ \t]+)?(?P<verb>%s)" % "|".join(KEEP_VERBS + CHANGE_VERBS) +
                                r"[ \t]*[:：]?[ \t]*(?:(?:IDEATION|REQUIREMENTS|DESIGN|INCEPTION)\b[^\n]*?)?"
                                + ITEM_TYPED)
QUOTED_ITEM_RE = re.compile(r"(?i)^[ \t]*>[ \t>]*" + ITEM_TYPED)
ITEM_IN_TEXT_RE = re.compile(r"(?i)(?<![0-9A-Za-z_-])(" + ITEM_TYPED + r")(?![0-9A-Za-z_-])")
ITEM_END_RE = re.compile(r"(?i)(?:" + ITEM_TYPED + r")$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CLONE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Effect(str, enum.Enum):
    CHANGE = "change"       # item rejected / finding upheld: the document must change here
    KEEP = "keep"           # item accepted / finding dismissed: it stays as written
    QUESTION = "question"   # a question about the item: answered in chat, not a verdict
    UNCLEAR = "unclear"     # words too open to read, a finding line with none we know, F and item ids on one line


C, K, U = Effect.CHANGE, Effect.KEEP, Effect.UNCLEAR
# A line records a verdict only when the words after its ids are leads and nothing else (a whole phrase of LEADS,
# after FILLERS if any: A-3 可以, F-1 对, A-3、A-4 都不用改, F-3 说得对，要改, F-1 有道理，但这版先不改), or a change
# and what it is (A-3 改：每天取). Any other words decide nothing for sure: a table of free text is never complete, and
# words after a lead may carry the real decision (F-3 对，二期再做; A-11 可以，上限调到 30 MB; F-18 不用：超过 1 万行回
# 422), so such a line is UNCLEAR and the receipt says how to write it. Effects are about the DOCUMENT: CHANGE = the
# row must change / the reviewer is right; KEEP = the row stays / the finding is dismissed. Per lead: (on an item, on
# a finding); U: too open to record on that kind (F-1 不对, F-1 可以 are read both ways); None: no verdict on that
# kind. The words judge the ROW: 对 keeps an item and upholds a finding.
LEADS = {
    # the row is right
    "对": (K, C), "对的": (K, C), "没错": (K, C), "同意": (K, C), "赞同": (K, C), "认同": (K, C), "有道理": (K, C),
    "说得对": (K, C), "说的对": (K, C), "合理": (K, C), "接受": (K, U), "认可": (K, C), "确认": (K, U),
    "正确": (K, C), "agree": (K, C), "agreed": (K, C), "confirmed": (K, C), "makes sense": (K, C), "correct": (K, C),
    "right": (K, C), "valid": (K, C), "yes": (K, C), "yep": (K, C), "+1": (K, C), "good point": (K, C),
    "good catch": (None, C), "nice catch": (None, C),
    "✓": (K, U), "✔": (K, U), "√": (K, U), "[x]": (K, U),           # KiroCrew folds ✓ to [x] and ✗ to [ ]; a mark on
    "✗": (C, U), "×": (C, U), "❌": (C, U), "[ ]": (C, U), "x": (C, U),   # a finding is read both ways
    # the row is wrong
    "不对": (C, U), "不合理": (C, U), "不行": (C, U), "不可以": (C, U), "错": (C, U), "错了": (C, U), "不正确": (C, U),
    "不对劲": (C, U), "wrong": (C, U), "incorrect": (C, U), "不认": (C, K), "不同意": (C, K), "反对": (C, K),
    "disagree": (C, K), "no": (C, K),
    "不要": (C, None), "不要了": (C, None),
    # fine
    "可以": (K, U), "可以接受": (K, U), "行": (K, U), "好": (K, U), "好的": (K, U), "没问题": (K, U), "没意见": (K, U),
    "没异议": (K, U), "无异议": (K, U), "不错": (K, U), "挺好": (K, U), "很好": (K, U), "没毛病": (K, U),
    "按推荐": (K, U), "按推荐来": (K, U), "按推荐的来": (K, U), "改到了": (K, U), "改好了": (K, U),
    "ok": (K, U), "okay": (K, U), "fine": (K, U), "good": (K, U), "looks good": (K, U), "looks good to me": (K, U),
    "lgtm": (K, U), "sounds good": (K, U), "no problem": (K, U), "no problems": (K, U), "no objection": (K, U),
    "no objections": (K, U), "no concerns": (K, U), "it's fine": (K, U), "keep": (K, U), "fixed": (K, U),
    # leave it as it is
    "不用改": (K, K), "不需要改": (K, K), "无需改": (K, K), "不必改": (K, K), "不改": (K, K), "不要改": (K, K),
    "别改": (K, K), "可以不改": (K, K), "不用动": (K, K), "不要动": (K, K), "别动": (K, K), "不动": (K, K),
    "先不改": (K, K), "暂不改": (K, K), "暂时不改": (K, K), "保持不变": (K, K), "保持原样": (K, K),
    "维持原样": (K, K), "维持原方案": (K, K), "维持现状": (K, K), "按原方案": (K, K), "不要紧": (K, K),
    "没关系": (K, K), "不是问题": (K, K), "下一版再说": (K, K), "下版再说": (K, K), "以后再说": (K, K),
    "后面再说": (K, K), "回头再说": (K, K), "保留": (K, U), "维持": (K, U), "保持": (K, U), "照旧": (K, U),
    "先这样": (K, U), "就这样": (K, U), "不用": (U, K), "不需要": (U, K), "不用管": (U, K), "算了": (U, K),
    "忽略": (U, K), "no change": (K, K), "no changes": (K, K), "no change needed": (K, K), "no need": (K, K),
    "no need to change": (K, K), "no issue": (K, K), "no issues": (K, K), "not an issue": (K, K),
    "not a real issue": (K, K), "keep as is": (K, K), "leave it": (K, K), "leave as is": (K, K), "as is": (K, K),
    "fine as is": (K, K), "won't fix": (U, K), "won’t fix": (U, K), "wont fix": (U, K), "not needed": (U, K),
    "skip": (U, K),
    "ignore": (U, K), "out of scope": (U, K),
    # change it
    "改": (C, C), "要改": (C, C), "得改": (C, C), "需要改": (C, C), "必须改": (C, C), "改掉": (C, C), "修改": (C, C),
    "修正": (C, C), "调整": (C, C), "不改不行": (C, C), "非改不可": (C, C), "同意评审": (C, C), "同意预审": (C, C),
    "评审说得对": (C, C), "不是小问题": (C, C), "没改完": (C, C), "没改好": (C, C), "没改到": (C, C), "还没改": (C, C),
    "没改对": (C, C), "fix": (C, C), "fix it": (C, C), "please fix": (C, C), "needs fix": (C, C),
    "needs a fix": (C, C), "must fix": (C, C), "should fix": (C, C), "will fix": (C, C), "change": (C, C),
    "change it": (C, C), "needs change": (C, C), "needs changes": (C, C), "needs work": (C, C),
    "删": (C, None), "删掉": (C, None), "去掉": (C, None), "删除": (C, None), "退回": (C, None), "重写": (C, None),
    "remove": (C, None), "delete": (C, None),           # F-1 删掉 may drop the finding itself
}
# Words that may stand before a lead and change nothing: A-3、A-4 都可以; A-3 这个不用改; F-2 不用说，肯定要改. No hedge
# (可能, 也许, 应该): a person who is not sure has given no verdict.
FILLERS = ("都", "也", "也都", "全", "全都", "全部", "均", "就", "那就", "还是", "其实", "确实", "的确", "肯定", "当然",
           "真的", "我觉得", "我认为", "感觉", "个人觉得", "好像", "建议", "最好", "先", "这个", "这条", "这一条", "这里",
           "这块", "这点", "这项", "这些", "这几条", "这两条", "这版", "这一版", "本版", "这次", "这轮", "不用说", "i think",
           "imo", "imho", "all", "both", "also", "totally", "definitely")
# Right after a lead, a contrast hands the line to the clause after it, which must be a lead too (F-1 对，但不用改).
CONTRASTS = ("但是", "但", "不过", "可是", "然而", "只是", "就是", "but", "however", "though", "although", "yet",
             "except")
# The leads a reason may follow, after a colon: a change (A-3 改：每天取; on an item also the taught A-3 不对：原因).
CHANGE_FORMS = frozenset(("改", "要改", "得改", "需要改", "必须改", "改掉", "修改", "修正", "调整", "删", "删掉", "去掉",
                          "删除", "退回", "重写", "fix", "please fix", "change", "不对", "不合理", "不行", "不可以", "错",
                          "错了", "不正确", "wrong", "incorrect", "不同意", "反对", "不认", "no"))
# What a change's reason may not say: a way of not changing (A-3 改：每天取，不过这期不做), or that the person is not
# sure (F-24 要改：我也说不太准). The receipt then asks again; it never records the change.
KEEP_CUES = ("不改", "不用改", "不要改", "别改", "不需要改", "不必改", "无需改", "先不", "暂不", "暂时不", "不动", "不用动",
             "别动", "保持现状", "保持原", "保持不变", "维持现状", "维持原", "保留现状", "保留原", "保留吧", "先保留", "还是保留",
             "照旧", "原样", "算了", "二期", "下期", "这期", "本期", "下版", "下一版", "下个版本", "下个迭代", "下一个迭代",
             "后续", "以后", "之后再", "回头再", "后面再", "上线后", "到时候", "遇到再", "先这样", "就这样", "不急", "先放着",
             "先记着", "先凑合", "先用着", "能用就行", "不处理", "不用处理", "不管了", "先不管", "不用管", "这版不做",
             "这期不做", "先不做", "暂不做", "不用了", "没关系", "不要紧", "无所谓", "没必要", "不必了", "不值得", "不划算",
             "不现实", "意义不大", "影响不大", "问题不大", "不影响", "概率很低", "风险可控", "keep it", "keep as is",
             "keep the current", "leave it", "leave as is", "as is", "as-is", "later", "for now", "next version",
             "next release", "next sprint", "phase 2", "backlog", "follow-up", "follow up", "separate ticket", "go-live",
             "not now", "won't fix", "won’t fix", "wont fix", "out of scope", "no change", "no need", "not needed",
             "not necessary", "not worth", "nice to have", "not urgent", "don't bother", "by design",
             "works as intended", "expected behavior", "risk accepted", "never mind", "nvm", "skip it", "ignore it",
             "defer", "postpone")
# A reason that says the person is not sure gives no verdict (F-24 应该要改吧，我不太确定).
UNSURE_CUES = ("的话", "也行", "也可以", "也好", "都行", "要不要", "是否", "看开发", "看你们", "代价", "成本", "太高",
               "太大", "可能", "大概", "没把握", "不太准", "我想想", "考虑", "看看", "i guess", "possibly", "might",
               "不确定", "不太确定", "不知道", "不清楚", "拿不准", "说不好", "说不准", "不好说", "再想想", "想一想", "待定",
               "也许", "或许", "not sure", "unsure", "maybe", "perhaps", "probably", "tbd", "idk", "no idea")
TAILS = "的"                         # a particle that ends a lead: 对的, 不对的
APPROVALS = frozenset(("可以", "行", "好", "好的", "没问题", "没意见", "没异议", "不错", "挺好", "很好", "没毛病"))
APPROVAL_TAILS = "的吧啊呀哈"           # 可以啊, 好吧: a sure yes; not 对吧 (a question) or 不改啊 (a complaint)
LE_LEADS = ("可以", "好", "行", "没问题", "不用改", "不需要改", "不改", "不用动", "不用", "不需要", "不对", "不行")  # not 对了
ACTION_TAILS = "吧"                  # 改吧; 改了 and 改啦 say it was changed, not that it should be
SEPARATORS = " \t:：,，、;；.。!！~～…—–-"
REASON_COLON_RE = re.compile(r"(?<![0-9])[:：]|[:：](?![0-9])")       # 09:00 and 9：00 are times, not a reason
QUESTION_TAIL_RE = re.compile(r"^[ \t]*(?:[吗呢么嘛吧][ \t]*[?？]+|[吗呢么嘛][ \t]*[。.!！]*|[?？]+)[ \t]*$")


@dataclass(frozen=True)
class LineVerdict:
    n: int                  # line number in the prompt (1-based)
    ids: Tuple[str, ...]    # upper case, in the order typed, each once
    effect: Effect
    words: str              # the rest of the line as typed (masked, <= MAX_WORDS), e.g. "不对：汇率不能写死"


@dataclass(frozen=True)
class OpenRound:
    """A Lean request still current at this turn, and the bytes in front of the person: the pages (the hashes Lean
    recorded), decisions.md and the round's sheet.json (hashes of the blobs stored for them now)."""
    kind: str
    sha256: str
    files: Dict[str, str]
    decisions: Optional[str]
    sheet: Optional[str]


@dataclass(frozen=True)
class Turn:
    seq: int
    ts: str
    intent: Optional[str]
    session: Optional[str]
    prompt_sha256: str       # == Lean's HUMAN_TURN.data.sha256 of this prompt: the join key
    open: Tuple[OpenRound, ...]
    lines: Tuple[LineVerdict, ...]
    hints: Tuple[str, ...]   # lines that look meant but were not recorded (verb first, a quote), for the log
    file: str                # the log file it came from (one per clone), repo-relative

    @property
    def ref(self) -> str:
        return "%s#%d" % (self.file, self.seq)


# ---------- the grammar ----------

def _longest_first(words) -> Tuple[str, ...]:
    return tuple(sorted(words, key=len, reverse=True))


_LEADS, _FILLERS, _CONTRASTS = (_longest_first(w) for w in (LEADS, FILLERS, CONTRASTS))
# Leads that ask for an action: 改了 says it was changed, 删了 that it was deleted (judgments take 了: 不对了).
ACTIONS = frozenset(w for w, (e, _) in LEADS.items() if e is C and any(v in w for v in ("改", "删", "去掉", "退回",
                                                                                         "重写", "调整", "修")))


def _starts(text: str, words: Sequence[str]) -> Optional[Tuple[str, str]]:
    """(word, rest) for the first of `words` (longest first) that `text` starts with, ASCII without regard to case, or
    None. An ASCII word may not run on into a letter or a digit: "no" is not "nothing"."""
    low = text.lower()
    for word in words:
        if low.startswith(word.lower()):
            rest = text[len(word):]
            if not (word[-1:].isascii() and word[-1:].isalnum() and rest[:1].isascii() and rest[:1].isalnum()):
                return word, rest
    return None


def _clause(text: str, side: int) -> Optional[Tuple[Optional[Effect], str, str]]:
    """(effect, rest, lead) when `text` starts with a whole lead (after FILLERS), else None. side 0: an item, 1: a
    finding. A lead is whole when the end, a separator or a particle (可以的) follows it: one that runs on is none
    (不改|不行, 对|于, no| idea, 对| App 来说)."""
    t = text.lstrip(" \t")
    while True:
        hit = _starts(t, _LEADS)
        if hit is not None:
            ended = _ended(*hit)
            if ended is not None:
                return LEADS[hit[0]][side], ended, hit[0]
        filler = _starts(t, _FILLERS)
        if filler is None:
            return None
        t = filler[1].lstrip(" \t，,")


def _ended(word: str, rest: str) -> Optional[str]:
    """What follows the lead `word` when the lead ends there, else None."""
    if not rest or rest[0] in SEPARATORS or QUESTION_TAIL_RE.match(rest):
        nxt = rest.lstrip(" ")
        wordy = any(c.isalnum() for c in word)          # not a mark such as ✓ or ×
        if rest[:1] == " " and wordy and nxt and nxt[0] not in SEPARATORS and not (
                _starts(nxt, _CONTRASTS) or _clause(nxt, 0) or _clause(nxt, 1)):
            return None                 # words run on after a space: "no idea", "ok to remove", 对 App 团队来说
        return rest
    tails = ACTION_TAILS if word in ACTIONS else (APPROVAL_TAILS if word in APPROVALS else TAILS) + \
        ("了" if word in LE_LEADS else "")
    if rest[0] in tails and (len(rest) == 1 or rest[1] in SEPARATORS or QUESTION_TAIL_RE.match(rest[1:])):
        return rest[1:]
    return None


def effect_of(item_id: str, words: str) -> Effect:
    """What a verdict line does: CHANGE or KEEP only when the words after the ids are leads and nothing else (agreeing
    clauses, or a contrast handing the line to the lead after it), or a change and its reason after a colon; a line
    that ends as a question is a QUESTION, unless a colon puts the question in the reason (A-3 改：能不能每天取？);
    anything else, and a bare id, is UNCLEAR."""
    w = words.strip()
    if not w:
        return Effect.UNCLEAR
    side = 1 if item_id.upper().startswith("F-") else 0
    if QUESTION_RE.search(w) and not REASON_COLON_RE.search(w):
        return Effect.QUESTION
    got = _clause(w, side)
    if got is None:
        return Effect.UNCLEAR
    effect, rest, lead = got
    if QUESTION_TAIL_RE.match(rest):
        return Effect.QUESTION
    return _after(effect, rest, side, lead)


def _after(effect: Optional[Effect], rest: str, side: int, lead: str, depth: int = 0) -> Effect:
    """The line's effect, given the lead's `effect`, what follows it, and the lead itself."""
    effect = Effect.UNCLEAR if effect is None else effect
    body = rest.lstrip(SEPARATORS)
    if not body:
        return effect
    if depth > 4:
        return Effect.UNCLEAR
    if REASON_COLON_RE.search(rest[:len(rest) - len(body)]):
        return _reason(body) if effect is Effect.CHANGE and lead in CHANGE_FORMS else Effect.UNCLEAR
    but = _starts(body, _CONTRASTS)
    if but is not None:
        got = _clause(but[1].lstrip(SEPARATORS), side)
        if got is None or got[0] not in (Effect.CHANGE, Effect.KEEP):
            return Effect.UNCLEAR
        return _after(got[0], got[1], side, got[2], depth + 1)
    got = _clause(body, side)
    if got is None:
        return Effect.UNCLEAR           # words that are no lead: they may decide (F-3 对，二期再做)
    other = Effect.UNCLEAR if got[0] is None else got[0]
    if effect is Effect.UNCLEAR:
        effect = other
    elif other is not effect:
        return Effect.UNCLEAR           # A-3 不对，不用改; F-8 对，改到了
    return _after(effect, got[1], side, got[2], depth + 1)


def _reason(text: str) -> Effect:
    """A change's reason: anything but a way of not changing, or that the person is not sure."""
    low = text.lower()
    return Effect.UNCLEAR if any(c in low for c in KEEP_CUES + UNSURE_CUES) else Effect.CHANGE


def _id(typed: str) -> str:
    return typed.upper().translate(HYPHEN_TO_ASCII)


def _pasted(line: str) -> bool:
    """A row copied from the rendered sheet: its cells arrive tab-separated (nobody types a tab in a chat)."""
    return "\t" in line


def _lines(prompt: str, wide_space_re):
    """(line number, line) of the prompt outside fenced blocks (``` or ~~~: quoted text), read as Lean reads approval
    lines (WIDE_SPACE_RE spaces as plain spaces)."""
    fence = False
    for n, line in enumerate(wide_space_re.sub(" ", prompt).split("\n"), 1):
        line = line.rstrip("\r")
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if not fence:
            yield n, line


def verdict_lines(prompt: str, wide_space_re, mask: Callable[[str], str]) -> List[LineVerdict]:
    """Every verdict line of a prompt, in order, at most MAX_LINES. `wide_space_re` and `mask` are Lean's own
    (WIDE_SPACE_RE, mask): lines are read the way Lean reads approval lines, and kept the way Lean keeps rejection
    words. A line with F ids and item ids together is UNCLEAR: the same word means opposite things on the two."""
    out = []
    for n, line in _lines(prompt, wide_space_re):
        m = VERDICT_LINE_RE.match(line)
        if not m or _pasted(line):
            continue
        ids = tuple(dict.fromkeys(_id(x) for x in ID_SPLIT_RE.split(m.group("ids").strip())))
        words = mask(m.group("words").strip())[:MAX_WORDS]
        mixed = len({i.startswith("F-") for i in ids}) > 1
        out.append(LineVerdict(n, ids, Effect.UNCLEAR if mixed else effect_of(ids[0], words), words))
        if len(out) >= MAX_LINES:
            break
    return out


def hints(prompt: str, wide_space_re, reject_re, mask: Callable[[str], str]) -> List[str]:
    """Lines that look like a verdict but are none, each with why, in Chinese (for the receipt): `驳回 A-3 …` (a verb
    first: Lean ignores it and so does this grammar), `驳回 DESIGN 的 D-18` (Lean's own line: it rejects ALL of
    DESIGN, not one item), `> A-3 不对` (a quote). `reject_re`: Lean's REJECT_RE; `mask`: Lean's mask(), applied
    before a line is quoted (the hints are stored with the turn)."""
    out = []
    for _, raw in _lines(prompt, wide_space_re):
        row = VERDICT_LINE_RE.match(raw)
        if row and not _pasted(raw):
            continue
        line = mask(raw)
        said = _cut(line.strip(), 20)
        lean = reject_re.match(line)
        ids = list(dict.fromkeys(_id(m.group(1)) for m in ITEM_IN_TEXT_RE.finditer(lean.group(3)))) if lean else []
        verb = VERB_FIRST_ITEM_RE.match(line)
        if row:
            first = _id(ID_SPLIT_RE.split(row.group("ids").strip())[0])
            out.append("「%s」是从表格里复制的一行，没记：%s" % (said, (
                "同意评审写「%s」，不同意写「%s」" % (compose([(first, "change", "")]), compose([(first, "keep", "")])))
                if first.startswith("F-") else "要改写「%s」" % compose([(first, "change", "原因")])))
        elif ids:
            out.append("「%s」是 Lean 的驳回行：退回的是整个 %s，不是 %s 一条" % (said, lean.group(2).upper(), "、".join(ids)))
        elif verb:
            item = _id(ITEM_END_RE.search(verb.group(0)).group(0))
            keep = verb.group("verb").lower() in KEEP_VERBS
            fix = compose([(item, "keep", "")] if keep else
                          [(item, "change", _cut(line[verb.end():].strip(" \t:：,，、"), 12))])
            out.append("「%s」没记：条目号要放在行首，写成「%s」" % (said, fix))
        elif QUOTED_ITEM_RE.match(line):
            out.append("「%s」是引用，没记" % said)
        if len(out) >= MAX_LINES:
            break
    return out


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def compose(choices: Sequence[Tuple[str, str, str]]) -> str:
    """The lines the sheet teaches, for the receipts and the app's per-item clicks (Phase 2): [(id, "change"|"keep",
    reason)] -> lines to paste into the chat. CHANGE -> "A-3 改[：<reason>]", a finding with no reason "F-1 对";
    KEEP -> "A-3 可以", "F-1 不用" (a keep takes no reason: words after it may be a change in disguise, so the grammar
    records a keep only alone). verdict_lines(compose(x)) gives back each id and effect, except that a change's reason
    that says not to change, or that the person is not sure, reads as UNCLEAR. ValueError on an id the grammar does
    not take, another effect, a keep with a reason, or a reason with a line break."""
    out = []
    for item, effect, reason in choices:
        i = str(item).strip().upper()
        if not ITEM_ID_RE.match(i):
            raise ValueError("%r is not an item id the sheet takes (AC-n, A-n, D-n, F-n)" % (item,))
        e = Effect(effect)
        if e not in (Effect.CHANGE, Effect.KEEP):
            raise ValueError("a sheet line says change or keep, not %s" % e.value)
        r = str(reason or "")
        if "\n" in r or "\r" in r:
            raise ValueError("a reason is one line")
        r = r.strip()
        if e is Effect.KEEP and r:
            raise ValueError("a keep takes no reason")
        if e is Effect.CHANGE:
            word = "对" if i.startswith("F-") and not r else "改"
        else:
            word = "不用" if i.startswith("F-") else "可以"
        out.append("%s %s%s" % (i, word, "：" + r if r else ""))
    return "\n".join(out)


def review_dir(runs_dir: str, key: str, kind: str, sha256: str) -> str:
    """runs/<KEY>/review/<KIND>-<sha12>: the work folder of one round (brief.md, each reviewer's answer, sheet.json),
    repo-relative with '/'. `brain review` writes it; the hook only reads its sheet.json, to pin it."""
    return "%s/%s/review/%s-%s" % (runs_dir.strip("/"), key, kind, sha256[:12])


# ---------- what is open now (the hook) ----------

PROMPT_RECORDS = ("HUMAN_TURN", "SUBAGENT_PROMPT", "HOST_TURN")     # what Lean's prompt hook records for a prompt


def pin_open_rounds(root: str, pj, lean: dict, intent: str, session: Optional[str], prompt_sha256: str,
                sheet_path: Callable[[str, str], Optional[str]]) -> List[OpenRound]:
    """When Lean's hook has already run for the prompt (Kiro CLI may run Lean's workspace hook first), what it
    recorded for it (its HUMAN_TURN of this session and sha256, and after that the APPROVED or REJECTED of an approval
    line in the same message) is left out: a request this very message answers is still open for its other lines, in
    either hook order."""
    pages = [pj.doc(intent, d) for d in (lean["REQ_DOC"], lean["DESIGN_DOC"])]
    gate_pages = {lean["rel"](root, p) for p in pages}
    before = _before(pj, lean, prompt_sha256, session)
    view = copy.copy(pj)
    view.events = lambda: before
    asked = {}
    for page in pages:
        e = view.pending_request(page)
        d = (e or {}).get("data") or {}
        files = _files_of(d)
        if e is not None and d.get("kind") in GATES and _is_sha(d.get("sha256")) and set(files) <= gate_pages:
            asked.setdefault((d["kind"], d["sha256"]), files)
    out = []
    for (kind, sha256), files in asked.items():
        r = _pin_unless_changed(root, pj, lean, intent, kind, sha256, files, sheet_path(kind, sha256))
        if r is not None:
            out.append(r)
    return out


def _pin_unless_changed(root, pj, lean, intent, kind, sha256, files, sheet_rel) -> Optional[OpenRound]:
    pages = [_read_nofollow(root, p) for p in files]
    if any(b is None or hashlib.sha256(b).hexdigest() != files[p] for p, b in zip(files, pages)):
        return None
    for b in pages:
        put_blob(root, b)
    decisions = _read_nofollow(root, lean["rel"](root, pj.doc(intent, lean["DECISIONS_DOC"])))
    sheet = _read_nofollow(root, sheet_rel) if sheet_rel else None
    return OpenRound(kind, sha256, dict(files), None if decisions is None else put_blob(root, decisions),
                     None if _sheet(sheet, sha256) is None else put_blob(root, sheet))


def _files_of(d: dict) -> Dict[str, str]:
    files = d.get("files")
    return dict(files) if isinstance(files, dict) else {d.get("path"): d.get("sha256")}


def _sheet(data: Optional[bytes], sha256: str) -> Optional[dict]:
    if data is None:
        return None
    try:
        doc = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and doc.get("sha256") == sha256 else None


def _before(pj, lean: dict, prompt_sha256: str, session: Optional[str]) -> list:
    """Lean's records as they were when this prompt came: without what Lean's hook wrote for it, if it ran first."""
    events = pj.events()
    k = _index_of_lean_record(events, lean, prompt_sha256, session)
    return events if k is None else events[:k]


def _index_of_lean_record(events: list, lean: dict, prompt_sha256: str, session: Optional[str]) -> Optional[int]:
    for k in range(len(events) - 1, -1, -1):
        e = events[k]
        d = e.get("data") or {}
        if e.get("event") in PROMPT_RECORDS and d.get("sha256") == prompt_sha256 and d.get("session") == session \
                and _recent(lean, e.get("ts")):
            return k
    return None


def _recent(lean: dict, ts) -> bool:
    at = lean["ts_epoch"](ts)
    return at is not None and abs(time.time() - at) <= JOIN_SECONDS


def _is_subagent_prompt(pj, lean: dict, prompt: str) -> bool:
    """Lean's own take_dispatch, run on a throwaway copy of its pending dispatches: the real list stays as it is for
    Lean's hook."""
    src = os.path.join(pj.local, lean["DISPATCH_FILE"])
    if not os.path.isfile(src):
        return False
    with tempfile.TemporaryDirectory(prefix="brain-review-") as tmp:
        shutil.copyfile(src, os.path.join(tmp, lean["DISPATCH_FILE"]))
        view = copy.copy(pj)
        view.local = tmp
        return lean["take_dispatch"](view, prompt) is not None


CHANGED_AFTER_REQUEST = "文件在请求之后改过"
UNSURE_NOTE = "这条消息里有 KiroCrew 加的%s，分不清哪几行是你打的，条目行都没记；是你要记的，请单独再发一次"


def _why_closed(lean: dict, intent: Optional[str], events: list) -> str:
    if not intent:
        return "没有选中的 intent"
    asked = [k for k, e in enumerate(events) if e.get("event") == "APPROVAL_REQUESTED" and e.get("intent") == intent
             and (e.get("data") or {}).get("kind") in GATES]
    if not asked:
        return "还没有请求过"
    pages = _files_of(events[asked[-1]].get("data") or {})
    answer = next((e["event"] for e in reversed(events[asked[-1] + 1:]) if e.get("event") in ("APPROVED", "REJECTED")
                   and any(lean["covers"](e, p) for p in pages)), None)
    return {"APPROVED": "上一版已经批准了", "REJECTED": "上一版已经驳回了"}.get(answer, CHANGED_AFTER_REQUEST)


# ---------- the hook entry ----------

def observe(root: str, prompt: str, session: Optional[str], lean: dict, run_key: Optional[str],
            runs_dir: str = "runs", said: Optional[str] = None, unsure: str = "") -> str:
    """The UserPromptSubmit work: called by lean-hook.py for a local session's prompt (KiroCrew: in-process, before
    Lean; Kiro CLI: `lean-hook.py --observe`, next to Lean's own workspace hook). Returns the receipt for the session
    ('' when there is nothing to say). Raises nothing the caller must handle specially: the caller catches every
    exception, because a failure here must never stop or change what Lean records.

    `prompt` is the prompt exactly as Lean gets it (KiroCrew: after the adapter's host-mark escaping; Kiro CLI: as
    typed), so Lean's text_sha(prompt) is the sha256 Lean puts in this turn's HUMAN_TURN. `said`: what the person typed
    in it (KiroCrew's prompt also holds a replay of the conversation so far; the adapter's person_text), the only text
    read for verdict lines, hints and Lean lines; None: the whole prompt. `unsure`: what KiroCrew put in `said` that
    cannot be told from the person's words (person_text's why, e.g. 子代理通知): no line is recorded, and when `said`
    holds verdict lines the receipt says why. `lean`: Lean's module
    namespace (vars() of aidlc_lean executed from the checked bytes): Project, text_sha, mask, WIDE_SPACE_RE,
    HOST_PROMPT_RE, REJECT_RE, APPROVE_RE, REQ_DOC, DESIGN_DOC, take_dispatch, ... Nothing of Lean's is copied here.

    Writes one TURN only while a round is open, and only when it carries something: verdict lines, or bytes of the
    open round (pages, decisions.md, sheet.json) no counted TURN of this clone's log has pinned yet. With no round
    open, an id-first line is ordinary chat (before a request, after its answer): nothing is written, and the session
    is told only when the request still stands but its pages changed after it. The same prompt (sha256) in the same session within JOIN_SECONDS
    of the last TURN is one turn (a second hook path for the same submit writes nothing and says nothing).
    A host prompt (Lean's HOST_PROMPT_RE) and a subagent's prompt (it matches a pending dispatch, checked with Lean's
    own take_dispatch on a throwaway copy of .aidlc-lean/subagents.json, so the real one is never popped; or Lean's
    hook, run first, recorded it as SUBAGENT_PROMPT or HOST_TURN) are ignored; so is a prompt whose `said` matches a
    pending dispatch (a sub-agent KiroCrew starts under an agent name gets its task right after the header, which
    Lean's own match does not look past)."""
    pj = lean["Project"](root)
    if not pj.initialized or lean["HOST_PROMPT_RE"].search(prompt) or _is_subagent_prompt(pj, lean, prompt) or \
            (said is not None and said != prompt and _is_subagent_prompt(pj, lean, said)):
        return ""
    session = session if isinstance(session, str) else None
    sha = lean["text_sha"](prompt)
    events = pj.events()
    k = _index_of_lean_record(events, lean, sha, session)
    if k is not None and events[k].get("event") != "HUMAN_TURN":
        return ""
    clone = pj.clone_id()
    part, rel, log = _open_part(root, clone)
    mine = log.turns
    if mine and _same(mine[-1], sha, session, lean):
        return ""
    text = (prompt if said is None else said).encode("utf-8", "replace").decode("utf-8")
    lines = verdict_lines(text, lean["WIDE_SPACE_RE"], lean["mask"])
    notes = hints(text, lean["WIDE_SPACE_RE"], lean["REJECT_RE"], lean["mask"])
    if unsure:
        notes = [UNSURE_NOTE % unsure] if lines else []
        lines = []
    intent = pj.active()

    def sheet_path(kind, sha256):
        return review_dir(runs_dir, run_key, kind, sha256) + "/" + SHEET_FILE if run_key else None

    rounds = pin_open_rounds(root, pj, lean, intent, session, sha, sheet_path) if intent else []
    turn = Turn(0, "", intent, session, sha, tuple(rounds), tuple(lines), tuple(notes), rel)
    if not rounds:
        why = _why_closed(lean, intent, _before(pj, lean, sha, session)) if lines else ""
        return receipt(turn, {}, (), why) if why == CHANGED_AFTER_REQUEST else ""
    pinned = {_pin_key(o) for t in counted(mine, events, lean["ts_epoch"]) for o in t.open}
    if lines or any(_pin_key(o) not in pinned for o in rounds):
        rec = _append(root, clone, turn, unless_last=lambda last: _same(last, sha, session, lean), part=part)
        if rec is None:
            return ""
        turn = _turn(rec, rel)
    if not lines and not notes:
        return ""
    as_lean_reads = lean["WIDE_SPACE_RE"].sub(" ", text)
    kinds = tuple(name for name, rx in (("approve", lean["APPROVE_RE"]), ("reject", lean["REJECT_RE"]))
                  if not unsure and rx.search(as_lean_reads))
    sheets = _sheets_of(root, turn.open)
    if any(i.startswith("F-") for x in turn.lines for i in x.ids):
        sheets = {**_sheets_of(root, _logged_rounds(root, intent)), **sheets}
    return receipt(turn, sheets, kinds)


def counted(turns: Sequence[Turn], events: Sequence[dict], epoch: Callable[[str], Optional[float]]) -> List[Turn]:
    """The turns that count: Lean logged a HUMAN_TURN with the same session and prompt sha256 within JOIN_SECONDS, in
    either order (Kiro CLI runs the two hooks side by side, KiroCrew runs observe first and Lean right after). Lean
    already told a subagent's prompt or a host's from a person's (SUBAGENT_PROMPT, HOST_TURN: no partner), and an
    --observe call Lean never saw has none either. A partner proves what Lean's own approval records prove, no more
    (see the top). Lean keeps the payload's session id as given: one that is no string reads as none, as observe()
    records it. `epoch`: an ISO time stamp -> seconds, or None. The hook and brainkit both count with this."""
    humans: Dict[Tuple[Optional[str], str], List[float]] = {}
    for e in events:
        d, at = e.get("data") if isinstance(e.get("data"), dict) else {}, epoch(e.get("ts"))
        if e.get("event") == "HUMAN_TURN" and isinstance(d.get("sha256"), str) and at is not None:
            s = d.get("session")
            humans.setdefault((s if isinstance(s, str) else None, d["sha256"]), []).append(at)
    out = []
    for t in turns:
        at = epoch(t.ts)
        if at is not None and any(abs(at - h) <= JOIN_SECONDS for h in humans.get((t.session, t.prompt_sha256), ())):
            out.append(t)
    return out


def _same(t: Turn, prompt_sha256: str, session: Optional[str], lean: dict) -> bool:
    return t.prompt_sha256 == prompt_sha256 and t.session == session and _recent(lean, t.ts)


def _pin_key(o: OpenRound) -> tuple:
    return o.kind, o.sha256, tuple(sorted(o.files.items())), o.decisions, o.sheet


def _sheets_of(root: str, rounds: Sequence[OpenRound]) -> Dict[str, dict]:
    out = {}
    for o in rounds:
        doc = _sheet(read_blob(lambda p: _read_nofollow(root, p), o.sheet), o.sha256) if o.sheet else None
        if doc is not None:
            out.setdefault(o.sha256, doc)
    return out


def _logged_rounds(root: str, intent: Optional[str]) -> List[OpenRound]:
    d = os.path.join(root, *LOG_DIR.split("/"))
    names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    turns = read_log(lambda _: ["%s/%s" % (LOG_DIR, n) for n in names], lambda p: _read_nofollow(root, p))[0]
    return [o for t in turns if t.intent == intent for o in t.open]


def receipt(turn: Turn, sheets: Dict[str, dict], lean_lines: Sequence[str] = (), why_closed: str = "") -> str:
    """What the session is told (stdout of UserPromptSubmit reaches the model): at most two lines, Chinese, each
    starting "[brain-kit] 审批单"; the steering has the agent repeat it at the start of its reply, so the person sees
    what was recorded. The first line says what was recorded against the open rounds, the second what was not and why.
    `sheets`: sha256 of a round -> its pinned sheet.json, of which only n, sha256, the findings' fid and targets
    (item ids only) and shown_findings are read: the open rounds' sheets, and earlier ones, so that an F-n of an
    earlier round is named by its round. Only a finding in shown_findings can be 没提: one the screen could not
    hold was never in front of the person (a sheet.json without the list has none).
    `lean_lines`: the kinds of Lean line the same message holds ("approve", "reject"). `why_closed`: when no round is
    open, why, in a few words. '' when there is nothing to say. Examples:
      [brain-kit] 审批单 INCEPTION 7bc3e710 第 1 轮记下 5 行：要改 AC-36、D-24（F-1）· AC-38、AC-39（F-2）· AC-15（F-3）·
                  A-3 · D-19。F-4…F-7 没提：这一轮不改。
      [brain-kit] 审批单：「驳回 A-3 汇率」没记：条目号要放在行首，写成「A-3 改：汇率」。
      [brain-kit] 审批单：现在没有开着的请求（文件在请求之后改过），「A-3 不对」没算进哪一轮。
      [brain-kit] 审批单 INCEPTION 335ff425 第 2 轮记下 3 行：D-19 可以 · F-8、F-9 不改。这条消息里也有 Lean 的批准行。
      [brain-kit] 审批单：F-1 是第 1 轮的，那一轮已经过去了，没记；这一轮是 F-8、F-9。
      [brain-kit] 审批单 INCEPTION 7bc3e710（这一版还没有审批单）记下 1 行：要改 A-3。
    An F-n that no open round's sheet has is from an earlier round (or unknown) and is named as such."""
    open_ = [(o, _sheet_view(sheets.get(o.sha256), o.sha256)) for o in turn.open]
    on_sheet = {}
    for _, s in open_:
        if s:
            on_sheet.update(s.findings)
    recorded, unbound, stale, questions, unclear = [], [], [], [], []
    for x in turn.lines:
        if x.effect is Effect.QUESTION:
            questions += [i for i in x.ids if i not in questions]
        elif x.effect is Effect.UNCLEAR:
            unclear.append(x)
        elif not turn.open:
            unbound.append(x)
        elif x.ids[0].startswith("F-"):
            stale += [i for i in x.ids if i not in on_sheet and i not in stale]
            known = tuple(i for i in x.ids if i in on_sheet)
            if known:
                recorded.append(LineVerdict(x.n, known, x.effect, x.words))
        else:
            recorded.append(x)
    out = []
    if recorded:
        out.append(_recorded(open_, on_sheet, recorded, {i for x in turn.lines for i in x.ids}, lean_lines))
    notes = []
    if unbound:
        quoted = "".join("「%s」" % _cut(_said(x), 20) for x in unbound[:MAX_QUOTED])
        more = "等 %d 行" % len(unbound) if len(unbound) > MAX_QUOTED else ""
        notes.append("现在没有开着的请求%s，%s%s没算进哪一轮" % ("（%s）" % why_closed if why_closed else "", quoted, more))
    if stale:
        notes.append(_stale_note(stale, sheets, open_))
    if questions:
        notes.append("%s 是提问，没记成意见" % "、".join(questions))
    notes += [_unclear(x) for x in unclear[:MAX_QUOTED]]
    if len(unclear) > MAX_QUOTED:
        notes.append("另外 %s 这 %d 行也没记，写法同上" % ("、".join(dict.fromkeys(i for x in unclear[MAX_QUOTED:]
                                                                       for i in x.ids)), len(unclear) - MAX_QUOTED))
    notes += list(turn.hints[:MAX_QUOTED])
    if len(turn.hints) > MAX_QUOTED:
        notes.append("另外 %d 行也没记" % (len(turn.hints) - MAX_QUOTED))
    if notes:
        out.append("[brain-kit] 审批单：" + "".join(n + "。" for n in notes))
    return "\n".join(out)


class SheetView(NamedTuple):
    n: int
    findings: Dict[str, Tuple[str, ...]]      # F-n -> its targets
    shown: Tuple[str, ...]                    # the F-n it showed (sheet.json shown_findings)


def _sheet_view(doc, sha256: str) -> Optional[SheetView]:
    """A pinned sheet.json as the receipt reads it, or None when it is not this round's: ids that are not item ids,
    and shown findings it does not have, are left out (the session repeats the receipt as it is)."""
    if not isinstance(doc, dict) or doc.get("sha256") != sha256 or not isinstance(doc.get("n"), int):
        return None
    out = {}
    for f in doc.get("findings") if isinstance(doc.get("findings"), list) else []:
        fid = f.get("fid") if isinstance(f, dict) else None
        if isinstance(fid, str) and fid.startswith("F-") and ITEM_ID_RE.fullmatch(fid):
            targets = f.get("targets") if isinstance(f.get("targets"), list) else []
            out[fid] = tuple(t for t in targets if isinstance(t, str) and ITEM_ID_RE.fullmatch(t))
    shown = doc.get("shown_findings") if isinstance(doc.get("shown_findings"), list) else []
    return SheetView(doc["n"], out, tuple(f for f in shown if isinstance(f, str) and f in out))


def _recorded(open_, on_sheet, recorded, mentioned, lean_lines) -> str:
    label = "、".join("%s %s 第 %d 轮" % (o.kind, o.sha256[:8], s.n) if s else
                     "%s %s（这一版还没有审批单）" % (o.kind, o.sha256[:8]) for o, s in open_)
    change, keep, dismissed = [], [], []
    for x in recorded:
        finding = x.ids[0].startswith("F-")
        if x.effect is Effect.CHANGE and finding:
            change += ["%s（%s）" % ("、".join(on_sheet[f]), f) if on_sheet[f] else f for f in x.ids]
        elif x.effect is Effect.CHANGE:
            change.append("、".join(x.ids))
        else:
            (dismissed if finding else keep).extend(x.ids)
    parts = (["要改 " + _dots(change)] if change else []) + \
            (["%s 可以" % "、".join(dict.fromkeys(keep))] if keep else []) + \
            (["%s 不改" % "、".join(dict.fromkeys(dismissed))] if dismissed else [])
    text = "[brain-kit] 审批单 %s记下 %d 行：%s。" % (label, len(recorded), _dots(parts))
    silent = [f for _, s in open_ if s for f in s.shown if f not in mentioned]
    if silent:
        text += "%s 没提：这一轮不改。" % _span(silent)
    if lean_lines:
        text += "这条消息里也有 Lean 的%s行。" % "和".join({"approve": "批准", "reject": "驳回"}[k] for k in lean_lines)
        if "approve" in lean_lines and change:
            text += "照上面改了，这次批准就失效，改完要重新请求。"
    return text


def _stale_note(fids, sheets, open_) -> str:
    shown = {o.sha256 for o, _ in open_}
    earlier = {}
    for sha256, doc in sheets.items():
        s = _sheet_view(doc, sha256)
        if s and sha256 not in shown:
            for f in s.findings:
                earlier.setdefault(f, s.n)
    rounds = {}
    for f in fids:
        if f in earlier:
            rounds.setdefault(earlier[f], []).append(f)
    said = ["%s 是第 %d 轮的" % ("、".join(fs), n) for n, fs in sorted(rounds.items())]
    if said:
        said[-1] += "，那%s轮已经过去了" % ("一" if len(rounds) == 1 else "几")
    unknown = [f for f in fids if f not in earlier]
    if unknown:
        said.append("%s 不在哪一轮的审批单上" % "、".join(unknown))
    now = [f for _, s in open_ if s for f in s.findings]
    if now:
        tail = "这一轮是 %s" % _span(now)
    elif any(s for _, s in open_):
        tail = "这一轮的审批单上没有 F 号"
    else:
        tail = "这一版还没有审批单"
    return "，".join(said) + "，没记；" + tail


def _unclear(x: LineVerdict) -> str:
    said, first = _cut(_said(x), 20), x.ids[0]
    if len({i.startswith("F-") for i in x.ids}) > 1:
        return "「%s」没记：F 号和条目号分两行写" % said
    if first.startswith("F-"):
        return "「%s」没看懂，没记：同意评审写「%s」或「%s」，不同意写「%s」" % (
            said, compose([(first, "change", "")]), compose([(first, "change", "怎么改")]), compose([(first, "keep", "")]))
    return "「%s」%s，没记：要改写「%s」，不改写「%s」" % (
        said, "没看懂" if x.words else "后面没写意见", compose([(first, "change", "原因")]), compose([(first, "keep", "")]))


def _said(x: LineVerdict) -> str:
    return "、".join(x.ids) + (" " + x.words if x.words else "")


def _dots(parts: Sequence[str]) -> str:
    out = parts[0]
    for p in parts[1:]:
        out += ("· " if out.endswith("）") else " · ") + p
    return out


def _span(fids: Sequence[str]) -> str:
    runs = []
    for n in sorted({int(f[2:]) for f in fids}):
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return "、".join("F-%d…F-%d" % (r[0], r[-1]) if len(r) > 2 else "、".join("F-%d" % n for n in r) for r in runs)


# ---------- the log: write and read (the only two places that know its format) ----------

def log_file(clone_id: str, part: int = 1) -> str:
    """aidlc-docs/audit/brain/<clone-id>.review.ndjson, and <clone-id>.<n>.review.ndjson for part n > 1: where the
    hook goes on once the chain of every part before it is broken (a merge that kept both branches' lines of one
    clone, a hand edit). Readers trust nothing after a break, so a fresh chain is started rather than recording
    stopped; the break stays named in the problems."""
    return "%s/%s%s%s" % (LOG_DIR, clone_id, "" if part == 1 else ".%d" % part, LOG_SUFFIX)


def _open_part(root: str, clone_id: str) -> Tuple[int, str, "LogFile"]:
    """(part, its path, its records): the clone's first log part whose chain holds, or that does not exist yet."""
    part = 1
    while True:
        rel = log_file(clone_id, part)
        log = _records(rel, _read_nofollow(root, rel) or b"")
        if log.tip is not None:
            return part, rel, log
        part += 1


def append(root: str, clone_id: str, turn: Turn) -> dict:
    """Append one TURN record to aidlc-docs/audit/brain/<clone-id>.review.ndjson under an exclusive lock, chained like
    Lean's ledger: {"seq", "ts", "event": "TURN", "schema", "intent", "data", "prev", "hash"}, hash = sha256 of the
    record without "hash" (json.dumps sort_keys, ensure_ascii False). One file per clone, as Lean: two clones never
    write one file; readers merge by ts. Returns the record written (turn.seq and turn.ts are assigned here).
    OSError when the file's chain is already broken (nothing written: readers would trust nothing after the break),
    or when the file or a folder on its way is a symlink; ValueError for a turn the reader would not take back."""
    return _append(root, clone_id, turn)


def _append(root, clone_id, turn, unless_last=None, part=1):
    if not isinstance(clone_id, str) or not CLONE_RE.match(clone_id):
        raise ValueError("%r is not a clone id" % (clone_id,))
    rel = log_file(clone_id, part)
    data = _data(turn)
    _turn({"seq": 0, "ts": "", "event": "TURN", "schema": SCHEMA, "intent": turn.intent, "data": data}, rel)
    path = os.path.join(_make_folder_nofollow(root, LOG_DIR), rel[len(LOG_DIR) + 1:])
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o666)
    with os.fdopen(fd, "r+b") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("%s is not a regular file of its own" % rel)
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        log = _records(rel, f.read())
        if log.tip is None:
            raise OSError(log.problems[-1])
        if unless_last is not None and log.turns and unless_last(log.turns[-1]):
            return None
        rec = {"seq": log.tip[1] + 1, "ts": _now(), "event": "TURN", "schema": SCHEMA, "intent": turn.intent,
               "data": data, "prev": log.tip[0]}
        rec["hash"] = _digest(rec)
        line = json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n"
        f.seek(0, os.SEEK_END)
        f.write(line)
    return rec


def read_log(list_files: Callable[[str], List[str]], read: Callable[[str], Optional[bytes]]) -> Tuple[List[Turn],
                                                                                                         List[str]]:
    """(every clone's TURN records, oldest first by ts; problems). Works on the working tree or a commit: brainkit
    passes its tree's files(prefix) (repo-relative paths below prefix, recursive) and raw(path) (bytes or None). A
    record whose chain breaks, or that does not parse, is left out and named in problems ("<file> line 7: chain
    broken"); nothing after a break in that file is trusted. A record whose chain holds but that is no TURN of this
    schema is left out and named; the records after it still count."""
    turns, problems = [], []
    for path in sorted(list_files(LOG_DIR)):
        name = path[len(LOG_DIR) + 1:]
        if not path.startswith(LOG_DIR + "/") or "/" in name or not name.endswith(LOG_SUFFIX) \
                or not CLONE_RE.match(name[:-len(LOG_SUFFIX)]):
            continue
        data = read(path)
        if data is None:
            problems.append("%s: cannot be read" % path)
            continue
        log = _records(path, data)
        turns += log.turns
        problems += log.problems
    turns.sort(key=lambda t: t.ts)
    return turns, problems


class LogFile(NamedTuple):
    turns: List[Turn]
    problems: List[str]
    tip: Optional[Tuple[str, int]]      # (hash, seq) of the last record; None once the chain breaks


def _records(rel, data) -> LogFile:
    turns, problems = [], []
    prev, seq = "GENESIS", 0
    for k, raw in enumerate(data.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except ValueError:
            problems.append("%s line %d: does not parse" % (rel, k))
            return LogFile(turns, problems, None)
        if not isinstance(rec, dict) or rec.get("prev") != prev or rec.get("seq") != seq + 1 \
                or rec.get("hash") != _digest(rec):
            problems.append("%s line %d: chain broken" % (rel, k))
            return LogFile(turns, problems, None)
        prev, seq = rec["hash"], rec["seq"]
        try:
            turns.append(_turn(rec, rel))
        except ValueError:
            problems.append("%s line %d: not a TURN record of schema %d" % (rel, k, SCHEMA))
    return LogFile(turns, problems, (prev, seq))


def _digest(rec) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8", "surrogatepass")) \
        .hexdigest()


def _data(turn: Turn) -> dict:
    return {"session": turn.session, "prompt_sha256": turn.prompt_sha256,
            "open": [{"kind": o.kind, "sha256": o.sha256, "files": dict(o.files), "decisions": o.decisions,
                      "sheet": o.sheet} for o in turn.open],
            "lines": [{"n": x.n, "ids": list(x.ids), "effect": Effect(x.effect).value, "words": x.words}
                      for x in turn.lines],
            "hints": list(turn.hints)}


def _turn(rec: dict, rel: str) -> Turn:
    try:
        return _parse_turn(rec, rel)
    except (KeyError, TypeError) as e:
        raise ValueError("not a TURN record: %s" % e)


def _parse_turn(rec: dict, rel: str) -> Turn:
    d = rec["data"]
    ok = rec["event"] == "TURN" and rec["schema"] == SCHEMA and isinstance(rec["ts"], str) \
        and _opt_str(rec["intent"]) and _opt_str(d["session"]) and _is_sha(d["prompt_sha256"]) \
        and isinstance(d["open"], list) and isinstance(d["lines"], list) and isinstance(d["hints"], list) \
        and all(isinstance(h, str) for h in d["hints"])
    if not ok:
        raise ValueError("not a TURN record")
    return Turn(rec["seq"], rec["ts"], rec["intent"], d["session"], d["prompt_sha256"],
                tuple(_round(o) for o in d["open"]), tuple(_line(x) for x in d["lines"]), tuple(d["hints"]), rel)


def _round(o: dict) -> OpenRound:
    files = o["files"]
    ok = o["kind"] in GATES and _is_sha(o["sha256"]) and isinstance(files, dict) and files \
        and all(isinstance(p, str) and _is_sha(h) for p, h in files.items()) \
        and all(v is None or _is_sha(v) for v in (o["decisions"], o["sheet"]))
    if not ok:
        raise ValueError("not an open round")
    return OpenRound(o["kind"], o["sha256"], dict(files), o["decisions"], o["sheet"])


def _line(x: dict) -> LineVerdict:
    ids = x["ids"]
    ok = isinstance(x["n"], int) and x["n"] >= 1 and isinstance(ids, list) and ids \
        and all(isinstance(i, str) and ITEM_ID_RE.match(i) for i in ids) and isinstance(x["words"], str)
    if not ok:
        raise ValueError("not a verdict line")
    return LineVerdict(x["n"], tuple(ids), Effect(x["effect"]), x["words"])


def _opt_str(v) -> bool:
    return v is None or isinstance(v, str)


def _is_sha(v) -> bool:
    return isinstance(v, str) and SHA256_RE.match(v) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- snapshots ----------

def blob_path(sha256: str) -> str:
    return "%s/%s" % (BLOB_DIR, sha256)


def put_blob(root: str, data: bytes) -> Optional[str]:
    """sha256 of data, after writing it to aidlc-docs/audit/brain/blobs/<sha256> if absent (O_EXCL, written to a
    temporary name first and linked into place, so a crash never leaves a short blob under the real name; a present
    blob is never rewritten). None when larger than MAX_BLOB."""
    if len(data) > MAX_BLOB:
        return None
    digest = hashlib.sha256(data).hexdigest()
    folder = _make_folder_nofollow(root, BLOB_DIR)
    final = os.path.join(folder, digest)
    if os.path.lexists(final):
        return digest
    tmp = os.path.join(folder, ".%s.%s.tmp" % (digest[:12], uuid.uuid4().hex))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.link(tmp, final)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)
    return digest


def read_blob(read: Callable[[str], Optional[bytes]], sha256: str) -> Optional[bytes]:
    """The blob's bytes, only when they hash to its name (a hand-edited or truncated blob reads as missing)."""
    if not _is_sha(sha256):
        return None
    data = read(blob_path(sha256))
    return data if data is not None and hashlib.sha256(data).hexdigest() == sha256 else None


def _make_folder_nofollow(root: str, rel: str) -> str:
    path = root
    for part in rel.split("/"):
        path = os.path.join(path, part)
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise OSError("%s is not a folder of this project (a symlink or a file on the way)" % rel)
    return path


def _read_nofollow(root: str, rel: str) -> Optional[bytes]:
    path = os.path.join(root, *rel.split("/"))
    if os.path.realpath(path) != os.path.join(os.path.realpath(root), *rel.split("/")):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read() if stat.S_ISREG(os.fstat(f.fileno()).st_mode) else None
