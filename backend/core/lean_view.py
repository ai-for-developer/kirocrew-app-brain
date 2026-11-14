"""AI-DLC Lean, read only: stages, approvals and the Session notes of one intent, from its audit ledger
(``aidlc-docs/audit/*.jsonl``, one hash-chained file per clone) and its files, in a worktree or at a git ref.

Nothing of Lean is imported or run: the project's own ``.kiro/aidlc-lean/`` is agent-writable, so the app never
executes it; ``lean status`` would also rewrite aidlc-state.md. What a pending approval needs is small and stable
(Lean 0.2): an APPROVAL_REQUESTED event not answered by a later APPROVED / REJECTED of the same file, while the file
still has the hash the request recorded (for a code-generation plan: with its task boxes read as unticked). The line
the person types is ``approve <KIND> <first 8 hex of that hash>`` (``lean request`` prints exactly that).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable, Optional

from .stages import BY_ID, LEAN, PHASES, label

DOCS = "aidlc-docs"
STAGE_STATE = {"STAGE_STARTED": "started", "STAGE_COMPLETED": "done", "STAGE_SKIPPED": "skipped"}
# the records that decide a stage's state: its own, and a PHASE_SKIPPED, which skips each stage it names
DECIDES = dict(STAGE_STATE, PHASE_SKIPPED="skipped")
IDEATION = frozenset(s.id for s in LEAN if s.phase == "ideation")
# the notes Lean writes itself (aidlc_lean.py: verify's build-and-test completion, the RELEASE approval's, `lean new`'s
# Ideation skip), said in words; a note the agent wrote is its own and stays as written
LEAN_NOTES = (
    # as 需求看板 says a verify round (board.py: 「自动检查通过（1 项）」), never 「1 项…都过了」 (BOARD-29)
    (re.compile(r"^verify PASS: (\d+) checks? on code fingerprint [0-9a-f]*$"), lambda m: "自动检查通过（%s 项）" % m.group(1)),
    (re.compile(r"^RELEASE approved \(sha [0-9a-f]*\)$"), lambda m: "发布审批通过了"),
    (re.compile(r"^not requested \(lean new without --ideation\)$"), lambda m: "这次没要构思这一段"),
)


def say_note(note: Any) -> str | None:
    """A stage's note as the page says it: Lean's own lines in words (LEAN_NOTES), the agent's as written."""
    if not isinstance(note, str) or not note.strip():
        return None
    for rx, say in LEAN_NOTES:
        m = rx.match(note.strip())
        if m:
            return say(m)
    return note


def phase_name(phase: Any) -> str:
    """A phase key read from a record, as a sentence names it: 构思; 一段 for one the stage table does not know."""
    return next((p.name for p in PHASES if p.key == phase), "一段")


def phase_skipped(phase: Any) -> str:
    """A PHASE_SKIPPED record (a whole phase skipped in one go) as a timeline says it: 构思：整段跳过."""
    return "%s：整段跳过" % phase_name(phase)


PASSED_OVER = "Lean 没记这一步，直接往后做了"
APPROVAL_EVENTS = ("APPROVAL_REQUESTED", "APPROVED", "REJECTED", "APPROVAL_REFUSED")
KIND_LABEL = {"REQUIREMENTS": "需求", "DESIGN": "设计", "INCEPTION": "需求和设计", "PLAN": "代码生成计划",
              "RELEASE": "发布", "IDEATION": "构思"}
# an approval line Lean did not take (APPROVAL_REFUSED), as the board says it, and why (aidlc_lean.py record_approval's
# checks), in words
REFUSED = "批准的那一行没被接受"
REFUSED_CHECK = {"target": "没有等它批准的请求", "hash": "那一行的编号和请求对不上（文件改过了，或者没带编号）",
                 "release": "发布还没准备好", "ideation": "要先做完构思的交接", "approvers": "批的人不在批准人名单里",
                 "team": "要别的团队的人批"}
# Lean's BLOCKED gates (aidlc_lean.py block()) and tools, in words
BLOCK_GATE = {"records": "记录文件只能由 Lean 自己写", "protected": "受保护的文件", "danger": "危险命令", "intent": "还没选需求",
              "contract": "别的团队的文件：要先开变更请求", "verify": "还没过自动检查", "requirements": "需求还没批",
              "design": "设计还没批", "inception": "需求和设计还没批", "ideation": "构思还没批", "released": "已经发布了",
              "plan": "代码生成计划还没批"}
BLOCK_TOOL = {"fs_write": "写文件", "write": "写文件", "execute_bash": "跑命令", "shell": "跑命令", "bash": "跑命令"}
BLOCKED = "Lean 拦下了一个不该做的改动"
CHECKBOX_RE = re.compile(rb"(?m)^([ \t]*[-*] )\[[xX ]\]")
PLAN_RE = re.compile(r"/construction/plans/[^/]+-code-generation-plan\.md$")

Reader = Callable[[str], Optional[bytes]]   # repo-relative path -> bytes or None
Lister = Callable[[str], list]              # repo-relative prefix -> repo-relative paths


def block_rule(gate: Any) -> str:
    """Which of Lean's rules a BLOCKED record broke, in words."""
    return BLOCK_GATE.get(str(gate or ""), "它的规则不让")


def block_what(path: Any, tool: Any) -> str | None:
    """What a BLOCKED record stopped: the file it would have written, else the kind of tool call, in words."""
    return str(path)[:120] if path else BLOCK_TOOL.get(str(tool or ""), "工具调用") if tool else None


def refused_why(check: Any) -> str | None:
    """Why Lean did not take an approval line (APPROVAL_REFUSED's check), in words; None when it is not one it knows."""
    return REFUSED_CHECK.get(str(check or ""))


def epoch(ts: Any) -> float | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        return datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def events(read: Reader, ls: Lister, intent: str) -> list[dict[str, Any]]:
    """The intent's ledger records of every clone, oldest first (a torn or foreign line is skipped)."""
    out = []
    for path in sorted(p for p in ls(DOCS + "/audit") if p.endswith(".jsonl")):
        raw = read(path)
        if not raw:
            continue
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("intent") == intent:
                rec["_at"] = epoch(rec.get("ts"))
                rec["_file"] = path
                out.append(rec)
    out.sort(key=lambda r: (r.get("ts") or "", r["_file"], r.get("seq") if isinstance(r.get("seq"), int) else 0))
    return out


def _data(e: dict[str, Any]) -> dict[str, Any]:
    d = e.get("data")
    return d if isinstance(d, dict) else {}


class _Decided:
    """stages()' fold, one record at a time (steps() reads the stages after each): each stage's (and unit's) deciding
    record, kept both with Ideation's phase skips and without them, since a later Ideation start or done makes them
    no longer count."""

    def __init__(self) -> None:
        self.all: dict[tuple, dict[str, Any]] = {}
        self.no_ideation_skip: dict[tuple, dict[str, Any]] = {}
        self.used = self.skip = -1
        self.n = 0

    def add(self, e: dict[str, Any]) -> None:
        ev, d = e.get("event"), _data(e)
        if ev == "PHASE_SKIPPED" and d.get("phase") == "ideation":
            self.skip = self.n
        elif ev in ("STAGE_STARTED", "STAGE_COMPLETED") and d.get("stage") in IDEATION:
            self.used = self.n
        self.n += 1
        if ev in STAGE_STATE:
            key = (str(d.get("stage") or ""), str(d.get("unit") or ""))
            self.all[key] = self.no_ideation_skip[key] = e
        elif ev == "PHASE_SKIPPED" and isinstance(d.get("stages"), list):
            for sid in d["stages"]:
                self.all[(str(sid), "")] = e
                if d.get("phase") != "ideation":
                    self.no_ideation_skip[(str(sid), "")] = e

    def rows(self) -> list[dict[str, Any]]:
        in_use = self.used > self.skip       # Lean's Project.ideation_in_use
        by_stage: dict[str, dict[str, dict[str, Any]]] = {}
        for (sid, u), e in (self.no_ideation_skip if in_use else self.all).items():
            by_stage.setdefault(sid, {})[u] = e
        out = []
        for s in LEAN:
            rows = by_stage.get(s.id, {})
            units = sorted((u, DECIDES[e["event"]]) for u, e in rows.items() if u)
            states = [DECIDES[e["event"]] for e in rows.values()]
            if not states:
                state = "optional" if s.optional and not (s.phase == "ideation" and in_use) else "todo"
            elif "started" in states:
                state = "started"
            elif all(x == "skipped" for x in states):
                state = "skipped"
            else:
                state = "done"
            latest = max(rows.values(), key=lambda e: e.get("ts") or "") if rows else None
            d = _data(latest) if latest else {}
            note = d.get("note") if latest and latest.get("event") == "STAGE_COMPLETED" else \
                d.get("reason") if latest and latest.get("event") in ("STAGE_SKIPPED", "PHASE_SKIPPED") else None
            out.append({"id": s.id, "n": s.n, "label": s.label, "phase": s.phase, "state": state,
                        "units": [{"unit": u, "state": x} for u, x in units] if s.per_unit else [],
                        "note": say_note(str(note)[:300]) if isinstance(note, str) else None,
                        "at": latest.get("_at") if latest else None})
        # a stage on the way that Lean went past without a word (a later stage Lean always asks for is recorded) was
        # not on this intent's way: skipped. An optional stage recorded early (Operations before the release) passes
        # nothing.
        end = max((i for i, (s, r) in enumerate(zip(LEAN, out)) if not s.optional and r["state"] != "todo"), default=0)
        for r in out[:end]:
            if r["state"] == "todo":
                r.update(state="skipped", note=PASSED_OVER)
        return out


def stages(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lean's 26 stages (stages.LEAN, in Lean's order), each with its state: done, skipped, started, todo, or optional
    (Lean never asks for it and nothing recorded it: it is not on this intent's way). A stage's state is its own last
    record's, or that of a later PHASE_SKIPPED naming it (Lean's stage_event); once Ideation is in use again, its
    phase skips no longer count. A per-unit stage has each unit's state."""
    decided = _Decided()
    for e in evs:
        decided.add(e)
    return decided.rows()


def phases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lean's phases in order, each with its state: skipped (every stage of it skipped by a record, one PHASE_SKIPPED
    or each its own, not passed over; *note*: their reason when one reason covers them all), optional (nothing recorded
    in a phase Lean never asks for), else used. Lean's progress counts only the stages of a used phase and says the
    skipped ones once: 构思、运维两段整段不需要."""
    out = []
    for p in PHASES:
        mine = [r for r in rows if r["phase"] == p.key]
        if not mine:
            continue
        states, notes = {r["state"] for r in mine}, {r["note"] for r in mine}
        state = "skipped" if states == {"skipped"} and PASSED_OVER not in notes else \
            "optional" if states == {"optional"} else "used"
        out.append({"key": p.key, "n": p.n, "name": p.name, "state": state,
                    "note": next(iter(notes)) if state == "skipped" and len(notes) == 1 else None})
    return out


def _way(rows: list[dict[str, Any]], ph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stages Lean's progress counts: those of a used phase, but an optional one only once recorded."""
    used = {p["key"] for p in ph if p["state"] == "used"}
    return [r for r in rows if r["phase"] in used and r["state"] != "optional"]


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lean's progress over the stages on the way (_way: the ones 交付总览, 需求详情 and 需求看板 count): the current
    stage is the one furthest along that is started; else the next one to do after the last one done or skipped; else
    (nothing left after it) that last one, done; None while none of them is recorded. done_n / skipped_n / next say
    the progress the one way the app says it (ui/src/vocab.ts leanProgress): next is the stage to do now (the started
    one, else the first not reached), None once every stage on the way is done or skipped. skipped_phases: the phases
    skipped as a whole, by name, said once instead of counted."""
    ph = phases(rows)
    way = _way(rows, ph)
    started = [r for r in way if r["state"] == "started"]
    cur = started[-1] if started else None
    if cur is None:
        done = [i for i, r in enumerate(way) if r["state"] in ("done", "skipped")]
        if done:
            cur = next((r for r in way[done[-1] + 1:] if r["state"] == "todo"), way[done[-1]])
    nxt = cur if cur and cur["state"] in ("started", "todo") else \
        None if cur else next((r for r in way if r["state"] == "todo"), None)
    return {"stage": cur["id"] if cur else None, "stage_label": cur["label"] if cur else None,
            "stage_n": cur["n"] if cur else None, "stage_state": cur["state"] if cur else None,
            "done": sum(1 for r in way if r["state"] in ("done", "skipped")), "total": len(way),
            "done_n": sum(1 for r in way if r["state"] == "done"),
            "skipped_n": sum(1 for r in way if r["state"] == "skipped"),
            "next": {"id": nxt["id"], "label": nxt["label"], "n": nxt["n"]} if nxt else None,
            "skipped_phases": [p["name"] for p in ph if p["state"] == "skipped"]}


def stage_text(lean: dict[str, Any]) -> str | None:
    """「做完 4 · 跳过 6 · 下一步：发布 · 构思整段不需要」, said exactly as ui/src/vocab.ts leanProgress says it: a next
    stage already started is 「在做：发布」, the end 「全部做完（跳过 6）」, the phases skipped as a whole last; None before
    Lean recorded a stage on the way."""
    if not lean.get("stage"):
        return None
    nxt = lean.get("next")
    if not nxt:
        parts = ["全部做完（跳过 %d）" % lean["skipped_n"] if lean.get("skipped_n") else "全部做完"]
    else:
        parts = ["做完 %d" % lean["done_n"]] + (["跳过 %d" % lean["skipped_n"]] if lean.get("skipped_n") else [])
        started = lean.get("stage_state") == "started" and lean.get("stage") in (None, nxt["id"])
        parts.append("%s：%s" % ("在做" if started else "下一步", nxt["label"]))
    whole = lean.get("skipped_phases") or []
    if whole:
        parts.append("%s%s整段不需要" % ("、".join(whole), "两段" if len(whole) == 2 else ""))
    return " · ".join(parts)


def steps(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lean's progress (summary) after each stage record that changed it, oldest first, the first one (at 0) before
    any: what 回放 shows at a moment. The last one is the progress now."""
    out = [{"at": 0, "lean": summary(stages([]))}]
    decided = _Decided()
    for e in evs:
        if e.get("event") in DECIDES:
            decided.add(e)
            lean = summary(decided.rows())
            if lean != out[-1]["lean"] and isinstance(e.get("_at"), (int, float)):
                out.append({"at": e["_at"], "lean": lean})
    return out


REVIEW_DOC = BY_ID["review"].doc   # Lean's release review (skill aidlc-lean-release)
INTENT_RE = re.compile(r"^[0-9]{6}-[a-z0-9][a-z0-9-]{0,80}$")
# what Lean's release skill tells the person to say in a new chat: the review is made by a context that did not write
# the code (and is not told how by the one that did)
REVIEW_LINE = ("Review intent %s against its requirements, design and plans, using the diff, and write "
               "construction/build-and-test/review.md.")


def review_line(intent: Any) -> str | None:
    """Lean's review line for *intent* (run.json is agent-writable: an intent not in Lean's format gets none)."""
    return REVIEW_LINE % intent if isinstance(intent, str) and INTENT_RE.match(intent) else None


def review_current(review_at: float | None, exists: bool, evs: list[dict[str, Any]]) -> bool:
    """Lean's review_is_current: review.md is there and newer than the last verify. A review read at a commit has no
    time of its own (*review_at* None): its being there counts."""
    if not exists:
        return False
    verified = [e["_at"] for e in evs if e.get("event") == "VERIFY" and isinstance(e.get("_at"), (int, float))]
    return review_at is None or not verified or review_at > max(verified)


def verify_fails(evs: list[dict[str, Any]]) -> int:
    """How many `lean verify` runs in a row one check has failed since it last passed, the most of the checks the
    last run ran (0 when none ran): how many rounds the AI has spent on the same red check. Each check is its command
    (VERIFY's results); a record without them counts as one check, passed or not by its ok."""
    streak: dict[str, int] = {}
    now: list[str] = []
    for e in evs:
        if e.get("event") != "VERIFY":
            continue
        d = _data(e)
        results = [r for r in d.get("results") or [] if isinstance(r, dict)] or \
            [{"cmd": "", "exit": 0 if d.get("ok") else 1}]
        now = [str(r.get("cmd") or "") for r in results]
        for cmd, r in zip(now, results):
            streak[cmd] = 0 if r.get("exit") == 0 else streak.get(cmd, 0) + 1
    return max((streak[c] for c in now), default=0)


def idle_step(rows: list[dict[str, Any]], *, review_ok: bool, default_branch: str) -> str:
    """What the person tells an idle local session, from Lean's state: the stage it stopped in, or the next one (the
    release starts with a review in a new session: the one that wrote the code cannot review it)."""
    lean = summary(rows)
    nxt = lean["next"]
    if nxt is None:
        return "Lean 的阶段都做完了：可以把分支合并进 %s 了" % default_branch
    if lean["stage_state"] == "started":
        return "「%s」做到一半，会话停下了：看它最后问了什么，回它" % nxt["label"]
    way = _way(rows, phases(rows))
    before = way[:[r["id"] for r in way].index(nxt["id"])]
    if not before:
        return "会话停下了，Lean 还没记下阶段：看它最后说了什么，回它"
    done = [r for r in before if r["state"] == "done"]
    prev = "%s做完了" % done[-1]["label"] if done else "%s跳过了" % before[-1]["label"]
    if nxt["id"] == "release":
        if review_ok:
            return "审查写好了，下一步是发布：回会话让它接着发布（它会请你批准发布）"
        return "%s，下一步是发布：先开一个新会话做审查（这个会话写了代码，不能自己审）" % prev
    return "%s，下一步是%s：在会话里让它接着做" % (prev, nxt["label"])


def _approval_sha(path: str, raw: bytes | None) -> str | None:
    if raw is None:
        return None
    if PLAN_RE.search("/" + path):
        raw = CHECKBOX_RE.sub(rb"\1[ ]", raw)
    return hashlib.sha256(raw).hexdigest()


def _files(d: dict[str, Any]) -> dict[str, str]:
    """{path: sha256} an approval event covers (INCEPTION covers two files)."""
    if isinstance(d.get("files"), dict):
        return {str(k): str(v) for k, v in d["files"].items()}
    return {str(d["path"]): str(d.get("sha256") or "")} if d.get("path") else {}


def approvals(evs: list[dict[str, Any]], read: Reader) -> list[dict[str, Any]]:
    """Each kind's latest state (PLAN: per unit): approved | pending | rejected | stale | refused; a pending one
    carries the exact approval line. A request counts as answered when a later approval or rejection covers one of its
    files, whatever its kind (an INCEPTION approval answers a REQUIREMENTS request), as Lean's pending_request does."""
    cache: dict[str, str | None] = {}

    def now_sha(path: str) -> str | None:
        if path not in cache:
            cache[path] = _approval_sha(path, read(path))
        return cache[path]

    latest: dict[tuple, int] = {}
    seq = [e for e in evs if e.get("event") in APPROVAL_EVENTS]
    for i, e in enumerate(seq):
        kind = str(_data(e).get("kind") or "")
        if not kind or kind.startswith("CR-"):
            continue
        k = (kind, str(_data(e).get("unit") or ""))
        if e["event"] == "APPROVAL_REFUSED" and k in latest and seq[latest[k]]["event"] in ("APPROVED", "REJECTED"):
            continue  # a refused line after a decision does not undo it
        latest[k] = i
    out = []
    for (kind, unit), i in sorted(latest.items(), key=lambda kv: (seq[kv[1]].get("ts") or "")):
        e = seq[i]
        d = _data(e)
        files = _files(d)
        sha = str(d.get("sha256") or "") or None
        ev, via = e["event"], None
        if ev == "APPROVAL_REQUESTED":  # answered later by a decision on one of its files?
            for later in seq[i + 1:]:
                if later["event"] in ("APPROVED", "REJECTED") and set(_files(_data(later))) & set(files):
                    ev, via = later["event"], str(_data(later).get("kind") or "") or None
                    files = {p: h for p, h in _files(_data(later)).items() if p in files} or files
        fresh = all(now_sha(p) == h for p, h in files.items()) if files else False
        state = {"APPROVED": "approved" if fresh else "stale", "REJECTED": "rejected",
                 "APPROVAL_REQUESTED": "pending" if fresh else "stale", "APPROVAL_REFUSED": "refused"}[ev]
        out.append({"kind": kind, "unit": unit or None, "state": state, "sha8": sha[:8] if sha else None,
                    "at": e.get("_at"), "line": "approve %s %s" % (kind, sha[:8]) if state == "pending" and sha else None,
                    "path": d.get("path") or (" + ".join(d.get("paths") or []) or None),
                    "via": via if via and via != kind else None})
    return out


def notes(text: str | None) -> dict[str, Any]:
    """aidlc-state.md's Session notes: Status, Next, Open questions and blockers."""
    text = text or ""
    status = None
    m = re.search(r"^Status:\s*(.+)$", text, re.M)
    if m:
        status = m.group(1).strip()[:300]

    def items(head: str) -> list[str]:
        mm = re.search(r"^### %s[^\n]*$(.*?)(?=^### |^## |\Z)" % head, text, re.M | re.S)
        if not mm:
            return []
        out = []
        for line in mm.group(1).splitlines():
            s = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            if s and not s.startswith("<!--") and s.lower() != "none":
                out.append(s[:300])
        return out[:20]

    return {"status": status, "next": items("Next"), "open_questions": items("Open questions")}


def audit_labels(paths: list[str]) -> dict[str, str]:
    """{audit file: label}. Lean names its file after the clone (<host>-<6 hex>.jsonl): the page shows the label
    brainkit.trace.audit_labels gives it (aidlc-docs/audit/L-<6 hex of sha256(path)>.jsonl), never the host name."""
    try:
        from . import bk
        if bk.has("trace") and hasattr(bk.m("trace"), "audit_labels"):
            return dict(bk.m("trace").audit_labels(paths))
    except Exception:  # noqa: BLE001 - the same rule below
        pass
    out, used = {}, set()
    for p in sorted(set(paths)):
        h, n = hashlib.sha256(p.encode()).hexdigest().upper(), 6
        while "L-" + h[:n] in used:
            n += 2
        used.add("L-" + h[:n])
        out[p] = "%s/audit/L-%s.jsonl" % (DOCS, h[:n])
    return out


def stage_events(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Timeline items from the ledger (when brainkit.trace is not there): stages, approvals, blocked tool calls,
    verify rounds, human turns. Never the text a person typed, nor the audit file's host name."""
    out = []
    labels = audit_labels([str(e.get("_file") or "") for e in evs])
    for e in evs:
        ev, d = e.get("event"), _data(e)
        at = e.get("ts") if isinstance(e.get("ts"), str) else None
        src = "%s#%s" % (labels.get(str(e.get("_file") or ""), DOCS + "/audit"), e.get("seq"))
        if ev in STAGE_STATE:
            said = "%s：%s" % (label(d.get("stage")), {"started": "开始", "done": "完成", "skipped": "跳过"}[STAGE_STATE[ev]])
            if d.get("unit"):
                said += "（%s）" % d["unit"]
            out.append({"at": at, "kind": "stage", "label": said, "detail": say_note(d.get("note") or d.get("reason")),
                        "who": None, "src": src})
        elif ev == "PHASE_SKIPPED":
            out.append({"at": at, "kind": "stage", "label": phase_skipped(d.get("phase")), "detail": say_note(d.get("reason")),
                        "who": None, "src": src})
        elif ev in APPROVAL_EVENTS:
            kind = str(d.get("kind") or "")
            name = KIND_LABEL.get(kind, kind)
            said = "%s（%s）" % (REFUSED, name) if ev == "APPROVAL_REFUSED" else \
                {"APPROVAL_REQUESTED": "请求批准", "APPROVED": "批准", "REJECTED": "退回"}[ev] + name
            out.append({"at": at, "kind": "approval", "label": said,
                        "detail": refused_why(d.get("check")) if ev == "APPROVAL_REFUSED" else None, "who": None, "src": src})
        elif ev == "BLOCKED":
            out.append({"at": at, "kind": "blocked", "label": "%s：%s" % (BLOCKED, block_rule(d.get("gate"))),
                        "detail": block_what(d.get("path"), d.get("tool")), "who": None, "src": src})
        elif ev == "VERIFY":
            out.append({"at": at, "kind": "verify", "label": "自动检查%s" % ("通过" if d.get("ok") else "没过"),
                        "detail": None, "who": None, "src": src})
        elif ev in ("SESSION_START", "HUMAN_TURN"):
            out.append({"at": at, "kind": "session", "label": "会话开始" if ev == "SESSION_START" else "人发了一条消息",
                        "detail": None, "who": None, "src": src})
    return out
