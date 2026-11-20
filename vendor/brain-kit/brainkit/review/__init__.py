"""brainkit.review: the review sheet (审批单) before an INCEPTION, REQUIREMENTS or DESIGN approval. The public surface.

    from brainkit import review
    s = review.sheet(repo, cfg)                 # the open request's sheet; runs the two reviewers if not done yet
    print(review.render(s))                     # what the agent shows, verbatim
    review.log(repo, intent)                    # every round with its verdicts (brain review log)
    review.view(repo, intent, "INCEPTION")      # the app's JSON (Phase 2)
    review.states(repo, intent, "INCEPTION")    # per item: accepted | superseded | passed | rejected | open (eval)
    review.rejections(repo, intent)             # standing rejections: each needs a correction proposal (reflect)
    review.compose([("A-3", "change", "汇率不能写死")])   # chat lines for the app's clicks (Phase 2)
    review.blob(repo, sha256)                   # a pinned page version, verified (the app's 改动 view)
    review.page(path, data)                     # a page read as items: the one extractor (mrdesc, trace)

Callers never see Lean events, the log format, blob paths, stream-json or the merge: those live in review_turn.py
(the record, a kit file) and the private modules here (what is derived from it). Errors a person can act on are
BrainError with a sentence ("INCEPTION 7bc3e710 已过期：requirements.md 在请求之后改过；先 `lean request INCEPTION`").

Data flow:  Lean ledger + review log + blobs --_rounds--> rounds
            pages (verified bytes) --_items--> items, and changes against the previous seen round
            brief --_prereview--> findings (two models + code checks), merged by target
            all of it --_sheet--> Sheet --render--> Markdown;  --to_json--> runs/<KEY>/review/<KIND>-<sha12>/sheet.json
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .. import config, entries, lean, mrdesc, runrec, trace
from ..common import BrainError, git_out, load_json, parse_branch, utcnow, iso
from . import _items, _prereview, _rounds, _sheet
from .types import (ChangeKind, Effect, Finding, Gate, Item, ItemKind, ItemRejection, ItemState, Kind, Outcome, Page,
                    Round, SCHEMA, Sheet, Slot, Source, Verdict, ReviewerRun)

KIT_REVIEW_TURN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "kit", ".kiro", "brain", "review_turn.py")
_RT = []
F_RE = re.compile(r"^F-([1-9][0-9]*)$")


class NoRequest(BrainError):
    """Nothing to review now: no approval request is open for the active intent, or the open one is stale (its pages
    changed after `lean request`). `brain review` exits 1 with the sentence."""


def review_turn():
    """kit/.kiro/brain/review_turn.py of this brain-kit, loaded by path (a kit file, not a package module): the one
    owner of the grammar, the log format, the blob store and the round folder's name. brainkit reads with it; only
    the prompt hook writes. Registered in sys.modules before it runs: its dataclasses look their module up there."""
    if not _RT:
        name = "brainkit_review_turn"
        spec = importlib.util.spec_from_file_location(name, KIT_REVIEW_TURN)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        # a __pycache__ beside a kit file is a file the project's HEAD lacks: the adapter then refuses the session
        was = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = was
        _RT.append(mod)
    return _RT[0]


def page(path: str, data: Optional[bytes], bot_users: Sequence[str] = ()) -> Page:
    """One page read as items (requirements.md: AC-n and A-n; decisions.md: D-n; any other page: none)."""
    return _items.read_page(path, data, bot_users)


def items(path: str, data: Optional[bytes], bot_users: Sequence[str] = ()) -> List[Item]:
    return list(_items.read_page(path, data, bot_users).items)


def _sha(data: Optional[bytes]) -> Optional[str]:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _decisions_path(intent: str) -> str:
    return "%s/%s/%s" % (lean.DOCS, intent, _items.DECISIONS_PAGE)


def _cfg(repo: str) -> dict:
    try:
        return config.load(repo)
    except BrainError:
        return dict(config.DEFAULTS)


class _Record:
    """What one tree (the working tree or a ref) holds about one intent, read once: Lean's events, the TURNs that
    count, the rounds of the three gates, and the pinned bytes behind them. Everything here is derived."""

    def __init__(self, repo: str, intent: str, ref: Optional[str], bot_users: Sequence[str]):
        rt = review_turn()
        self.repo, self.intent, self.bots = repo, intent, tuple(bot_users)
        self.tree = mrdesc._Tree(repo, ref)         # the one byte-exact reader that refuses symlinks
        self.events = trace.lean_events(self.tree, intent)[0]
        turns, self.problems = rt.read_log(self.tree.files, self.tree.raw)
        self.turns = [t for t in turns if t.intent == intent]
        self.counted = rt.counted(self.turns, self.events, _rounds.epoch)
        self._blobs: Dict[str, Optional[bytes]] = {}
        self._sheets: Dict[str, Optional[Sheet]] = {}
        self.rounds = {k: [self._stale(r) for r in _rounds.rounds(Gate(intent, k), self.events, self.counted,
                                                                  self.sheet_of)] for k in Kind}

    @property
    def all_rounds(self) -> List[Round]:
        return sorted((r for rs in self.rounds.values() for r in rs), key=lambda r: r.requested_at)

    def blob(self, sha256: Optional[str]) -> Optional[bytes]:
        if not sha256:
            return None
        if sha256 not in self._blobs:
            self._blobs[sha256] = review_turn().read_blob(self.tree.raw, sha256)
        return self._blobs[sha256]

    def sheet_of(self, sha256: Optional[str]) -> Optional[Sheet]:
        """The sheet.json pinned under this blob sha, read back; None when missing or not a sheet."""
        if sha256 not in self._sheets:
            data, s = self.blob(sha256), None
            if data is not None:
                try:
                    s = _sheet.from_json(json.loads(data.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    s = None
            self._sheets[sha256] = s
        return self._sheets[sha256]

    def page(self, path: str, sha256: str) -> Optional[bytes]:
        """The bytes of a page version: its pinned blob, or the tree's file when it still hashes the same."""
        data = self.blob(sha256)
        if data is None:
            now = self.tree.raw(path)
            data = now if _sha(now) == sha256 else None
        return data

    def _stale(self, r: Round) -> Round:
        if r.outcome is Outcome.OPEN and any(_sha(self.tree.raw(p)) != h for p, h in r.files.items()):
            return replace(r, outcome=Outcome.STALE)
        return r

    def readable(self, r: Round) -> bool:
        return all(self.page(p, h) is not None for p, h in r.files.items())

    def decisions(self, r: Round) -> Optional[Tuple[str, bytes]]:
        """decisions.md as the person saw it with the round: its pinned blob; for a round nobody saw yet, the tree's
        file (decisions.md is append-only by the kit's rule); none when the round was seen without one."""
        path = _decisions_path(self.intent)
        data = self.blob(r.decisions_sha256) if r.decisions_sha256 else None
        if data is None and r.n is None:
            data = self.tree.raw(path)
        return (path, data) if data is not None else None

    def items_of(self, r: Round) -> Optional[Dict[str, Item]]:
        pages = {p: self.page(p, h) for p, h in r.files.items()}
        if any(v is None for v in pages.values()):
            return None
        return _items.gate_items(r.gate.kind.value, pages, self.decisions(r), self.bots)

    def shown_in(self, r: Round) -> frozenset:
        s = self.sheet_of(r.sheet_sha256) if r.sheet_sha256 else None
        return s.shown if s else frozenset()


# ---------- which request, which run ----------

def _kind(kind: Optional[str]) -> Optional[Kind]:
    if kind is None:
        return None
    try:
        return Kind(kind.upper())
    except ValueError:
        raise BrainError("审批单只有 INCEPTION、REQUIREMENTS、DESIGN 三种，没有 %s" % kind)


def _stale_text(repo: str, e: dict) -> str:
    d = e.get("data") or {}
    changed = [p.rsplit("/", 1)[-1] for p, h in _rounds.files_of(e).items()
               if _sha(mrdesc._Tree(repo, None).raw(p)) != h]
    return "%s %s 已过期：%s 在请求之后改过；先 `lean request %s`，再 `brain review`。" % (
        d.get("kind"), str(d.get("sha256"))[:8], "、".join(changed) or "页面", d.get("kind"))


def _open_request(repo: str, kind: Optional[Kind]) -> Tuple[str, dict]:
    out = lean.open_requests(repo)
    intent = out.get("intent")
    if not intent:
        raise NoRequest("Lean 现在没有当前的 intent：先 `lean use <intent>`，再 `lean request INCEPTION`。")
    gates = {k.value for k in Kind}
    reqs = [e for e in out.get("requests") or () if (e.get("data") or {}).get("kind") in gates]
    stale = [e for e in out.get("stale") or () if (e.get("data") or {}).get("kind") in gates]
    if kind is not None:
        reqs = [e for e in reqs if e["data"]["kind"] == kind.value]
        stale = [e for e in stale if e["data"]["kind"] == kind.value]
    if len(reqs) == 1:
        return intent, reqs[0]
    if reqs:
        raise BrainError("开着 %d 个审批请求（%s）：写 `brain review <KIND>` 选一个。" % (
            len(reqs), "、".join("%s %s" % (e["data"]["kind"], e["data"]["sha256"][:8]) for e in reqs)))
    if stale:
        raise NoRequest(_stale_text(repo, stale[-1]))
    what = kind.value if kind else "INCEPTION"
    raise NoRequest("%s现在没有开着的审批请求：先 `lean request %s`%s，再 `brain review`。" % (
        kind.value + " " if kind else "", what, "" if kind else "（或 REQUIREMENTS、DESIGN）"))


def _run_key(repo: str, cfg: dict, intent: str) -> str:
    branch = git_out(repo, "rev-parse", "--abbrev-ref", "HEAD")
    b = parse_branch(branch, cfg["branch_prefix"])
    if not b:
        raise BrainError("现在的分支 %s 不是 run 分支（%s/<你>/<KEY>-<名字>）：审批单写在 runs/<KEY>/review/ 下，在 "
                         "run 分支上跑 `brain review`。" % (branch, cfg["branch_prefix"]))
    rel = runrec.path(cfg["runs_dir"], b["key"])
    if not os.path.isfile(os.path.join(repo, rel)):
        raise BrainError("这个分支上没有 %s：先 `brain start`。" % rel)
    rec = load_json(os.path.join(repo, rel))
    if rec.get("intent") and rec["intent"] != intent:
        raise BrainError("run %s 的 intent 是 %s，Lean 当前的 intent 是 %s：先 `lean use %s`。" % (
            b["key"], rec["intent"], intent, rec["intent"]))
    return b["key"]


def _intent(repo: str, ref: Optional[str]) -> str:
    """The run's intent on this branch (from run.json in the tree read), else Lean's active intent."""
    cfg = _cfg(repo)
    b = parse_branch(git_out(repo, "rev-parse", "--abbrev-ref", "HEAD"), cfg["branch_prefix"])
    if b:
        raw = mrdesc._Tree(repo, ref).raw(runrec.path(cfg["runs_dir"], b["key"]).replace(os.sep, "/"))
        try:
            intent = json.loads(raw.decode("utf-8")).get("intent") if raw else None
        except (ValueError, UnicodeDecodeError, AttributeError):
            intent = None
        if intent:
            return intent
    intent = lean.open_requests(repo).get("intent") if lean.installed(repo) else None
    if not intent:
        raise BrainError("不知道是哪个 intent：在 run 分支上跑，或者先 `lean use <intent>`。")
    return intent


# ---------- the sheet ----------

def _first_fid(rec: _Record, cfg: dict) -> int:
    """1 + the highest F id of every sheet of the intent this tree has: each round folder's sheet.json and every
    sheet the hook pinned (F ids never repeat within an intent)."""
    sheets = [rec.sheet_of(o.sheet) for t in rec.turns for o in t.open if o.sheet]
    for path in rec.tree.files(cfg["runs_dir"]):
        if path.endswith("/" + review_turn().SHEET_FILE) and "/review/" in path:
            try:
                sheets.append(_sheet.from_json(json.loads(rec.tree.read(path) or "")))
            except ValueError:
                continue
    top = [int(F_RE.match(f.fid).group(1)) for s in sheets if s and s.gate.intent == rec.intent
           for f in s.findings if F_RE.match(f.fid)]
    return max(top, default=0) + 1


def _before(rec: _Record, prev: Round, kind: Kind, items_now: Dict[str, Item], pages: Dict[str, bytes]
            ) -> _sheet.Before:
    psheet = rec.sheet_of(prev.sheet_sha256) if prev.sheet_sha256 else None
    return _sheet.before(prev, kind, {p: rec.page(p, h) for p, h in prev.files.items()}, rec.decisions(prev),
                         psheet, items_now, pages, rec.bots)


def _title(pages: Dict[str, bytes]) -> str:
    for data in pages.values():
        m = re.search(r"(?m)^#[ \t]+(?:Requirements|Application Design)?[ \t]*[:：]?[ \t]*(\S.*?)[ \t]*$",
                      data.decode("utf-8", "replace"))
        if m:
            return m.group(1)
    return ""


def _copies(repo: str, cfg: dict, intent: str, pages: Dict[str, bytes], decisions) -> Dict[str, bytes]:
    """What each reviewer's folder holds: the requested pages as Lean hashed them, decisions.md, the Brain's main
    files, AGENTS.md, and the intent's reverse-engineering/architecture.md when it has one."""
    tree = mrdesc._Tree(repo, None)
    out = {p.rsplit("/", 1)[-1]: b for p, b in pages.items()}
    if decisions is not None:
        out[_items.DECISIONS_PAGE] = decisions[1]
    for f in entries.Brain.load(os.path.join(repo, cfg["brain_dir"])).main_files():
        data = tree.raw("%s/%s" % (cfg["brain_dir"].strip("/"), f.name))
        if data is not None:
            out["brain/" + f.name] = data
    for rel, name in (("AGENTS.md", "AGENTS.md"),
                      ("%s/%s/inception/reverse-engineering/architecture.md" % (lean.DOCS, intent), "architecture.md")):
        data = tree.raw(rel)
        if data is not None:
            out[name] = data
    return out


def _reviews(d: str, cfg: dict, kind: Kind, sha: str, files: Dict[str, str], items_now: Dict[str, Item],
             copies: Dict[str, bytes], before: Optional[_sheet.Before], n: int, prereview: bool, progress
             ) -> Tuple[List[ReviewerRun], list, list, list]:
    """(reviewer runs, slot a's findings, slot b's, resolutions): the two headless reviewers on copies of exactly
    the requested bytes, or `skipped` with why (--no-prereview, no kiro-cli, no usable reviewer spec)."""
    slots = [(Slot.A, cfg["model"]), (Slot.B, cfg["review_model"])]

    def skipped(why):
        return [ReviewerRun(s, m, "skipped", None, 0, note=why) for s, m in slots], [], [], []
    if not prereview:
        return skipped("这次用了 --no-prereview")
    if shutil.which(_prereview.KIRO) is None:
        return skipped("这台机器上没有 %s" % _prereview.KIRO)
    usable, note = _prereview.ensure_reviewer_spec()
    if not usable:
        return skipped(note)
    sent = []
    if before is not None:
        sent = [_prereview.SentBack(p.line.ids[0], p.line.about, p.line.words, p.now)
                for p in _sheet.previous(before, items_now) if p.line.effect is Effect.CHANGE]
    changed = [c.id for c in before.changes if c.change is not ChangeKind.UNCHANGED] if before and before.carries \
        else []
    brief = _prereview.brief(kind.value, sha, files, sorted(copies), items_now, sent, changed, n)
    _write_text(os.path.join(d, "brief.md"), brief)
    text = "\n".join(b.decode("utf-8", "replace") for b in copies.values())
    runs, found, res = [], {Slot.A: [], Slot.B: []}, []
    for ans in _prereview.run(d, brief, copies, slots, timeout=_prereview.TIMEOUT_SECONDS, progress=progress):
        run = ans.run
        if run.status == "ok":
            raw, rs, problem = _prereview.parse(run.slot, ans.text, kind.value, sha, items_now, text)
            found[run.slot] = raw
            res += rs
            run = replace(run, findings=len(raw), **({"status": "bad_output", "note": problem} if problem and not raw
                                                      else {}))
        runs.append(run)
    return runs, found[Slot.A], found[Slot.B], res


def _make(repo: str, cfg: dict, intent: str, kind: Kind, sha: str, files: Dict[str, str], pages: Dict[str, bytes],
          d: str, prereview: bool, progress) -> Sheet:
    bots = cfg["bot_users"]
    rec = _Record(repo, intent, None, bots)
    this = next(r for r in rec.rounds[kind] if r.sha256 == sha)
    n = this.n or 1 + sum(1 for r in rec.rounds[kind] if r.n is not None and r.requested_at <= this.requested_at)
    warnings = ["审批记录有一段读不了：%s" % p for p in rec.problems[:2]]
    dec_raw = rec.tree.raw(_decisions_path(intent))
    decisions = (_decisions_path(intent), dec_raw) if dec_raw is not None else None
    if decisions is None:
        warnings.append("decisions.md 还没有")
    items_now = _items.gate_items(kind.value, pages, decisions, bots)
    dups = [x for p, b in list(pages.items()) + ([decisions] if decisions else [])
            for x in _items.read_page(p, b, bots).duplicates]
    prev = _rounds.previous_seen(rec.all_rounds, this, rec.readable)
    before = _before(rec, prev, kind, items_now, pages) if prev else None
    if prev is not None and prev.decisions_sha256 and rec.blob(prev.decisions_sha256) is None:
        warnings.append("上一轮的 decisions.md 没存档：决定按第一轮那样列")
    brain = entries.Brain.load(os.path.join(repo, cfg["brain_dir"]))
    main = brain.main()
    copies = _copies(repo, cfg, intent, pages, decisions)
    runs, a, b, resolutions = _reviews(d, cfg, kind, sha, files, items_now, copies, before, n, prereview, progress)
    check_on = items_now
    if before is not None and before.carries:
        moved = {c.id for c in before.changes if c.change is not ChangeKind.UNCHANGED}
        check_on = {i: it for i, it in items_now.items() if i in moved}
    retired = frozenset(brain.archived()) | frozenset(brain.purged())
    checks = _prereview.code_checks(check_on, dups, None if brain.problems else frozenset(main), retired)
    findings = _prereview.merge(a, b, checks, _first_fid(rec, cfg), list(items_now))
    return _sheet.build(Gate(intent, kind), sha, n, files, items_now, findings, runs, before,
                        title=_title(pages), page_lines={p: _items.read_page(p, x).lines for p, x in pages.items()},
                        decisions_sha256=_sha(dec_raw), resolutions=resolutions, brain_empty=not main,
                        warnings=warnings, built_at=iso(utcnow()))


def _write_text(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_once(path: str, text: str) -> bool:
    """Write `path` only when it does not exist (linked into place from a full temporary file, so a reader never
    sees half of it); False when it was there already."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        os.unlink(tmp)


@contextlib.contextmanager
def _lock(repo: str, name: str):
    """One `brain review` at a time per round of this checkout: a second one waits, then finds the sheet written. The
    lock file lives in the git directory, never in the committed round folder."""
    path = git_out(repo, "rev-parse", "--git-path", "brain-review-%s.lock" % name)
    path = path if os.path.isabs(path) else os.path.join(repo, path)
    with open(path, "a+") as f:
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield


def _read_sheet(path: str, sha: str) -> Optional[Sheet]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            s = _sheet.from_json(json.load(f))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise BrainError("%s 读不了（%s）：审批单写好就不再改。agent 不能动这个文件夹，请人删掉它，再跑 `brain review` "
                         "会重新预审。" % (path, e))
    if s.sha256 != sha:
        raise BrainError("%s 是 %s 的审批单，不是 %s 的：agent 不能动这个文件夹，请人删掉它，再跑 `brain review`。"
                         % (path, s.sha256[:8], sha[:8]))
    return s


def sheet(repo: str, cfg: dict, kind: Optional[str] = None, *, prereview: bool = True, progress=None) -> Sheet:
    """The sheet of the request that is open now for the active intent (kind: the only open gate request when None;
    BrainError naming them when there are several; NoRequest when there is none or it is stale). Idempotent: the
    first call writes runs/<KEY>/review/<KIND>-<sha12>/ (brief.md, a.md, b.md, reviewers.json, sheet.json, sheet.md);
    later calls for the same request return the same Sheet without running anything (sheet.json is write-once: the
    person's `F-1 对` refers to that F-1). A run that stopped before sheet.json leaves nothing a rerun cannot redo.
    `prereview=False` builds the sheet without the reviewers (said in its header). `progress(line)`: one line per
    reviewer start and end (the CLI prints them to stderr)."""
    intent, req = _open_request(repo, _kind(kind))
    k, sha = Kind(req["data"]["kind"]), req["data"]["sha256"]
    files = _rounds.files_of(req)
    key = _run_key(repo, cfg, intent)
    pages = {}
    tree = mrdesc._Tree(repo, None)
    for p, h in files.items():
        data = tree.raw(p)
        if _sha(data) != h:
            raise NoRequest(_stale_text(repo, req))
        pages[p] = data
    d = os.path.join(repo, review_turn().review_dir(cfg["runs_dir"], key, k.value, sha))
    os.makedirs(d, exist_ok=True)
    js, md = os.path.join(d, review_turn().SHEET_FILE), os.path.join(d, "sheet.md")
    with _lock(repo, "%s-%s" % (k.value, sha[:12])):
        s = _read_sheet(js, sha)
        if s is None:
            s = _make(repo, cfg, intent, k, sha, files, pages, d, prereview, progress)
            if not _write_once(js, json.dumps(_sheet.to_json(s), ensure_ascii=False, indent=2, sort_keys=True)
                               + "\n"):
                s = _read_sheet(js, sha)
        if not os.path.exists(md):
            _write_text(md, _sheet.render(s))
    return s


def render(s: Sheet) -> str:
    """The Markdown the agent shows verbatim (see _sheet.render for what makes it inert)."""
    return _sheet.render(s)


# ---------- the record, read back ----------

def _record(repo: str, intent: Optional[str], ref: Optional[str]) -> _Record:
    return _Record(repo, intent or _intent(repo, ref), ref, _cfg(repo)["bot_users"])


def log(repo: str, intent: Optional[str] = None, kind: Optional[str] = None, ref: Optional[str] = None) -> List[Round]:
    """Every round of the intent's gates (or one gate) with outcome, number, pinned versions and verdicts, in order of
    first request; from the working tree, or from `ref` (after the run merged). `brain review log` prints it."""
    rec = _record(repo, intent, ref)
    k = _kind(kind)
    return [r for r in rec.all_rounds if k is None or r.gate.kind is k]


def turns(repo: str, intent: Optional[str] = None, ref: Optional[str] = None) -> List[dict]:
    """The intent's TURNs as `brain review log --all` lists them: {at, ref, session, counted (a person's turn: Lean
    logged the same prompt as HUMAN_TURN), open: ["INCEPTION 7bc3e710"], lines: [{ids, effect, words}], hints}; and
    the log's problems as {problem}."""
    rec = _record(repo, intent, ref)
    counted = {id(t) for t in rec.counted}
    out = [{"at": t.ts, "ref": t.ref, "session": t.session, "counted": id(t) in counted,
            "open": ["%s %s" % (o.kind, o.sha256[:8]) for o in t.open],
            "lines": [{"ids": list(x.ids), "effect": x.effect.value, "words": x.words} for x in t.lines],
            "hints": list(t.hints)} for t in rec.turns]
    return out + [{"problem": p} for p in rec.problems]


def _current_sheet(rec: _Record, r: Round) -> Optional[Sheet]:
    """The round's sheet: the one pinned for it, else its folder's sheet.json in this tree."""
    s = rec.sheet_of(r.sheet_sha256) if r.sheet_sha256 else None
    if s is None:
        tail = "/review/%s-%s/%s" % (r.gate.kind.value, r.sha256[:12], review_turn().SHEET_FILE)
        for path in rec.tree.files(_cfg(rec.repo)["runs_dir"]):
            if path.endswith(tail):
                try:
                    s = _sheet.from_json(json.loads(rec.tree.read(path) or ""))
                except ValueError:
                    continue
                break
    return s


def view(repo: str, intent: str, kind: str, ref: Optional[str] = None) -> dict:
    """The app's contract (Phase 2; schema SCHEMA), see design.md "Phase 2 (app): the exact contract". `state`: none
    (nothing requested), waiting_sheet / sheet_out (the last request is open, without / with its sheet), or the last
    round's outcome: approved, rejected, superseded, stale (its sheet, if any, is still `sheet`)."""
    rec = _record(repo, intent, ref)
    k = _kind(kind)
    rs = rec.rounds[k]
    cur = rs[-1] if rs else None
    s = _current_sheet(rec, cur) if cur else None
    state = "none" if cur is None else ("sheet_out" if s else "waiting_sheet") if cur.outcome is Outcome.OPEN \
        else cur.outcome.value
    rounds_out = []
    for r in rs:
        rs_sheet = rec.sheet_of(r.sheet_sha256) if r.sheet_sha256 else None
        rounds_out.append({
            "n": r.n, "sha256": r.sha256, "sha8": r.sha256[:8], "requested_at": r.requested_at, "seen_at": r.seen_at,
            "outcome": r.outcome.value, "lean_words": r.lean_words,
            "files": [{"path": p, "sha256": h, "pinned": rec.blob(h) is not None} for p, h in r.files.items()],
            "verdicts": [{"target": v.target, "effect": v.effect.value, "words": v.words, "at": v.at,
                          "via": v.via.value, "resolves_to": list(v.resolves_to)} for v in r.verdicts],
            "counts": {"shown": len(rs_sheet.shown) if rs_sheet else 0,
                       "collapsed": sum(len(v) for v in rs_sheet.collapsed.values()) if rs_sheet else 0,
                       "change": sum(1 for v in r.verdicts if v.effect is Effect.CHANGE),
                       "keep": sum(1 for v in r.verdicts if v.effect is Effect.KEEP)}})
    pages = list(cur.files) if cur else [
        "%s/%s/%s" % (lean.DOCS, rec.intent, p) for p in
        {Kind.INCEPTION: (_items.REQ_PAGE, _items.DESIGN_PAGE), Kind.REQUIREMENTS: (_items.REQ_PAGE,),
         Kind.DESIGN: (_items.DESIGN_PAGE,)}[k]]
    return {"schema": SCHEMA, "gate": {"intent": rec.intent, "kind": k.value, "pages": pages}, "state": state,
            "rounds": rounds_out, "sheet": _sheet.to_json(s) if s else None,
            "answer": {"how": "在会话里发这几行", "examples": list(s.examples) if s else []}}


def states(repo: str, intent: str, kind: str, ref: Optional[str] = None) -> Dict[str, ItemState]:
    """Per item of the gate's approved version: accepted | superseded | passed | rejected; all open before that."""
    rec = _record(repo, intent, ref)
    return _rounds.item_states(Gate(rec.intent, _kind(kind)), rec.all_rounds, rec.items_of, rec.shown_in)


def rejections(repo: str, intent: str, ref: Optional[str] = None) -> List[ItemRejection]:
    """Standing rejections of every gate of the intent. trace adds them to `humans` (kind rejection, channel local,
    rejected_items), so evaluate.gaps' batch rule asks for a correction per item; reflect lists them next to the
    voucher ones."""
    rec = _record(repo, intent, ref)
    return [x for k in Kind for x in _rounds.rejections(Gate(rec.intent, k), rec.all_rounds, rec.items_of,
                                                         rec.shown_in)]


def compose(choices: Sequence[Tuple[str, str, str]]) -> str:
    """review_turn.compose: the chat lines for [(id, change|keep, reason)]. Pure; nothing is recorded until the
    person sends the lines in the session."""
    return review_turn().compose(choices)


def reviewer_spec(install: bool = True, force: bool = False) -> Tuple[bool, str]:
    """(usable, note) of ~/.kiro/agents/brain-reviewer.json, the read-only agent both reviewers run as. `install`:
    write it when absent (`brain crew-agent`, `brain review`); False only says how it is (`brain doctor`). `force`:
    also replace a spec that is not brain-kit's (`brain crew-agent --force`). The note is one Chinese sentence in
    every case."""
    return _prereview.ensure_reviewer_spec(install=install, force=force)


def log_problems(repo: str, ref: Optional[str] = None) -> List[str]:
    """What is wrong with any clone's review log in the tree (`brain doctor`'s chain row): a record that does not
    parse or breaks the chain (nothing after it in that file is trusted), or that is no TURN. [] when all read."""
    tree = mrdesc._Tree(repo, ref)
    return review_turn().read_log(tree.files, tree.raw)[1]


def blob(repo: str, sha256: str, ref: Optional[str] = None) -> Optional[bytes]:
    """A pinned version (page, decisions.md or sheet.json) by its sha256, verified, or None."""
    return review_turn().read_blob(mrdesc._Tree(repo, ref).raw, sha256)


__all__ = ["Effect", "Finding", "Gate", "Item", "ItemKind", "ItemRejection", "ItemState", "Kind", "NoRequest",
           "Outcome", "Page", "Round", "SCHEMA", "Sheet", "Slot", "Source", "Verdict", "blob", "compose", "items",
           "log", "log_problems", "page", "rejections", "render", "review_turn", "reviewer_spec", "sheet", "states",
           "turns", "view"]
