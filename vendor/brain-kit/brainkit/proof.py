"""Which evidence (证据) counts, for which code, and what is missing: the reading side.

kit/.kiro/brain/proof.py owns the set format, the seal, the plan grammar and the code state; it is loaded by path, as
brainkit.review.review_turn() loads review_turn.py. This module joins its sets with Lean's records and a version of
the code, and says what is missing in plain Chinese, once: brainkit.mrdesc, the KiroCrew app (through bind()'s JSON,
docs/proof.md) and a later gate read the same kinds and the same sentences.

The freshness rule, here only. An item proves the code its set names (the manifest's `code`, sealed with it). It is
current for the code under review when every code path that differs between the two holds the same blob in both, or
is absent in both: the paths the two commits differ in, and the uncommitted files of either. Record paths
(lean.is_record_path) never count, so a records commit (the evidence itself, MR.md, decisions.md) never makes
evidence old. git only, and nothing of the project runs: the app and CI judge a commit the way a local session judges
its working tree.

The seal decides whether a set counts; its code decides whether it is current. mrdesc's verify line keeps Lean's own
fingerprint judgment.
"""
from __future__ import annotations

import enum
import hashlib
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import ci, lean
from .common import BrainError

SCHEMA = 1                    # of bind()'s JSON
KIT_PROOF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit", ".kiro", "brain",
                         "proof.py")
TREE_ARGS_MAX = 1000          # paths ls-tree takes as arguments; more: the whole tree is listed
_KIT = []

Files = Callable[[str], List[str]]           # repo paths under a prefix (mrdesc._Tree.files, the app's reader)
Raw = Callable[[str], Optional[bytes]]       # one file's bytes, None when missing (never through a symlink)


def kit():
    """kit/.kiro/brain/proof.py of this brain-kit, loaded once by path (a kit file, not a package module). Its read
    API: parse_set, sealing_verify, set_output_of, is_proof_check, check_label, parse_plan, plan_of, code_state,
    CodeState, Item, Result, Kind, SetError. Registered in sys.modules before it runs: its dataclasses look their
    module up there. No __pycache__ is written beside it (a file the project's HEAD lacks under a kit path)."""
    if not _KIT:
        name = "brainkit_proof_kit"
        spec = importlib.util.spec_from_file_location(name, KIT_PROOF)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        was = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = was
        _KIT.append(mod)
    return _KIT[0]


# ---------------------------------------------------------------- the code: is an item about the code under review

class Freshness(str, enum.Enum):
    CURRENT = "current"           # 是这份代码的
    STALE = "stale"               # 旧代码的: Verdict.changed names every code path that differs
    UNKNOWN = "unknown"           # 核对不了: Verdict.why says why


@dataclass(frozen=True)
class Verdict:
    freshness: Freshness
    changed: Tuple[str, ...] = ()
    why: str = ""


CURRENT = Verdict(Freshness.CURRENT)


def capture(repo: str, lcfg: Mapping):
    """The working tree's code now, as the kit captures it inside verify, with brain-kit's record-path rule."""
    return kit().code_state(repo, lambda p: lean.is_record_path(p, lcfg))


def at_commit(commit: str):
    """A commit read as it is."""
    return kit().CodeState(commit, ())


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--literal-pathspecs", "-C", repo] + list(args), capture_output=True,
                          stdin=subprocess.DEVNULL)


def _is_commit(repo: str, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", "%s^{commit}" % sha).returncode == 0


def blobs_at(repo: str, commit: str, paths: Sequence[str]) -> Optional[Dict[str, str]]:
    """{path: object id at `commit`} for those of `paths` it has as a file, a link or a submodule (a missing path is
    not a key); None when git cannot say."""
    if not paths:
        return {}
    want = set(paths)
    p = _git(repo, "ls-tree", "-r", "-z", commit, *(["--"] + sorted(want) if len(want) <= TREE_ARGS_MAX else []))
    if p.returncode != 0:
        return None
    out = {}
    for rec in p.stdout.split(b"\0"):
        meta, _, name = rec.partition(b"\t")
        parts = meta.split()
        path = name.decode("utf-8", "surrogateescape")
        if len(parts) == 3 and path in want:
            out[path] = parts[2].decode("ascii", "replace")
    return out


def compare(repo: str, was, now, lcfg: Mapping) -> Verdict:
    """Is `was` (the code an item names) the code `now` (under review)? The one freshness rule (module docstring)."""
    if was.dirty is None:
        return Verdict(Freshness.UNKNOWN, why="拍的时候没提交的代码文件太多（或者 git 没说清），比不出来")
    if now.dirty is None:
        return Verdict(Freshness.UNKNOWN, why="现在没提交的代码文件太多（或者 git 没说清），比不出来")
    wd = {p: b for p, b in was.dirty if not lean.is_record_path(p, lcfg)}
    nd = {p: b for p, b in now.dirty if not lean.is_record_path(p, lcfg)}
    if was.commit is None or now.commit is None:
        if was.commit is not None or now.commit is not None:
            return Verdict(Freshness.UNKNOWN, why="其中一边还没有提交，比不出来")
        differ = sorted(p for p in set(wd) | set(nd) if wd.get(p) != nd.get(p))
        return Verdict(Freshness.STALE, tuple(differ)) if differ else CURRENT
    if not _is_commit(repo, was.commit):
        return Verdict(Freshness.UNKNOWN, why="拍的时候的提交 %s 不在这个仓库里（变基了，或者还没取回来）"
                       % was.commit[:8])
    try:
        paths = set() if was.commit == now.commit else {
            p for p in ci.changed_paths(repo, was.commit, now.commit) if not lean.is_record_path(p, lcfg)}
    except BrainError:
        return Verdict(Freshness.UNKNOWN, why="git 比不出 %s 和 %s 的差别" % (was.commit[:8], now.commit[:8]))
    paths |= set(wd) | set(nd)
    bw = blobs_at(repo, was.commit, [p for p in paths if p not in wd])
    bn = blobs_at(repo, now.commit, [p for p in paths if p not in nd])
    if bw is None or bn is None:
        return Verdict(Freshness.UNKNOWN, why="git 读不出提交里的文件")
    differ = sorted(p for p in paths if (wd[p] if p in wd else bw.get(p)) != (nd[p] if p in nd else bn.get(p)))
    return Verdict(Freshness.STALE, tuple(differ)) if differ else CURRENT


# ---------------------------------------------------------------- what is missing, said once

class GapKind(str, enum.Enum):
    """Everything a reader says is missing or wrong. A row's state is "current" or one of the row kinds below; the
    later gate is a table from these kinds to warn or refuse."""
    NO_PROOF_CHECK = "no_proof_check"     # lean.json has no proof check, and the newest verify ran none
    NO_VERIFY = "no_verify"               # lean.json has one, and the newest verify (if any) did not run it
    LOST = "lost"                         # the newest verify ran a proof check, and no set holds what it printed
    VOID = "void"                         # a set no verify printed (made by hand, edited), or a file changed after
    NEWER_FORMAT = "newer_format"         # a set a newer brain-kit wrote
    PROBLEM = "problem"                   # what the capture said (no Chrome, port busy, a plan line not read)
    PLANNED = "planned"                   # row: in the plan, and no verify has shot it yet (new, or steps changed)
    NOT_TAKEN = "not_taken"               # row: verify tried and took nothing (no Chrome, the app did not start)
    MISSED = "missed"                     # row: the shot did not show what the row waits for (or failed otherwise)
    OLD = "old"                           # row or log: evidence of other code (the code changed after it)
    UNKNOWN = "unknown"                   # row or log: whether it is this code cannot be told
    UNCOMMITTED = "uncommitted"           # working tree only: evidence the MR cannot see yet
    AC_WITHOUT = "ac_without"             # an AC with no test and no evidence
    UI_WITHOUT_SHOT = "ui_without_shot"   # UI paths (kit.json proof.ui_paths) changed, and no current screenshot


TEXT: Mapping[GapKind, str] = {
    GapKind.NO_PROOF_CHECK: "这个项目的 lean verify 不留证据：aidlc-docs/lean.json 的 checks 里没有 .kiro/brain/proof.py"
                            "（`brain doctor` 给出要加的行）",
    GapKind.NO_VERIFY: "aidlc-docs/lean.json 有证据检查，要等下一次 lean verify",
    GapKind.LOST: "最近一次 verify 的证据检查（%s，exit %s）没留下证据：被打断了，或者它写的文件没提交",
    GapKind.VOID: "%s 不是 lean verify 留下的样子（拍完以后改过、手工跑的，或者 verify 时编码不是 UTF-8），不算证据：重跑 lean verify",
    GapKind.NEWER_FORMAT: "%s 是新版 brain-kit 写的（证据格式 v%s），这个 brain-kit 读不了：升级 brain-kit",
    GapKind.PROBLEM: "拍的时候：%s",
    GapKind.PLANNED: "%s 在证据计划里，还没被 lean verify 拍过（新加的，或者改过步骤）",
    GapKind.NOT_TAKEN: "%s 没拍成：%s",
    GapKind.MISSED: "%s 没看到要的：%s",
    GapKind.OLD: "%s 是旧代码的：拍完以后改了 %s。合并前要重跑 lean verify",
    GapKind.UNKNOWN: "%s 核对不了是不是这份代码的：%s",
    GapKind.UNCOMMITTED: "%s 还没提交，MR 里看不到",
    GapKind.AC_WITHOUT: "%s 没有证据（没有测试，也没有截图）",
    GapKind.UI_WITHOUT_SHOT: "改了界面（%s），没有这份代码的截图",
}
ROW_KINDS = (GapKind.PLANNED, GapKind.NOT_TAKEN, GapKind.MISSED, GapKind.OLD, GapKind.UNKNOWN, GapKind.VOID)


@dataclass(frozen=True)
class Gap:
    kind: GapKind
    text: str                     # TEXT[kind], filled in
    refs: Tuple[str, ...] = ()    # the rows (P-2, or web/P-2 when the intent has several units), sets, files or ACs


def gap(kind: GapKind, *args: Any, refs: Sequence[str] = ()) -> Gap:
    return Gap(kind, TEXT[kind] % args, tuple(refs))


def _ids(items: Sequence[str], limit: int = 3) -> str:
    items = list(items)
    return "、".join(items[:limit]) + ("等 %d 个" % len(items) if len(items) > limit else "")


# ---------------------------------------------------------------- the sets, as found

@dataclass(frozen=True)
class Seal:
    """The VERIFY record whose proof check printed exactly a set's output.txt."""
    at: str
    ledger: str                   # the record's file, as the caller's events name it (mrdesc: its repo path)
    seq: Optional[int]
    ok: bool
    fingerprint: Optional[str]
    cmd: str                      # the proof check, as Lean recorded it (masked)
    exit: Optional[int]

    def to_json(self) -> dict:
        return {"at": self.at, "ledger": self.ledger, "seq": self.seq, "ok": self.ok,
                "fingerprint": self.fingerprint, "cmd": self.cmd, "exit": self.exit}


@dataclass(frozen=True)
class Entry:
    """One manifest item with its file looked up: ok when the file there hashes to the manifest's sha256."""
    item: Any                     # the kit's Item
    path: Optional[str]           # repo path; None when nothing was taken
    ok: bool

    def to_json(self) -> dict:
        i = self.item
        return {"unit": i.unit, "id": i.id, "ac": list(i.ac), "what": i.what, "result": i.result.value,
                "note": i.note, "path": self.path, "sha256": i.sha256, "size": i.size, "ok": self.ok}


@dataclass(frozen=True)
class Found:
    """One set folder of the intent."""
    dir: str
    name: str
    clone: str
    output: str                   # repo path of its output.txt
    output_sha256: str
    lines: int                    # what the check printed before the manifest
    manifest: Any                 # the kit's ProofSet; None when it cannot be read
    error: str                    # why it cannot be read
    newer: Optional[int]          # the schema of a newer brain-kit's set
    seal: Optional[Seal]          # None: no verify printed it
    order: int                    # its seal's place among the verifies, oldest first; -1 without one
    entries: Tuple[Entry, ...]

    @property
    def counts(self) -> bool:
        """Evidence: readable, sealed, and in the folder its manifest names."""
        m = self.manifest
        return m is not None and self.seal is not None and (m.name, m.clone) == (self.name, self.clone)

    def to_json(self) -> dict:
        m = self.manifest
        return {"dir": self.dir, "set": self.name, "kind": m.kind.value if m else None, "clone": self.clone,
                "output": self.output, "output_sha256": self.output_sha256, "lines": self.lines,
                "sealed": self.counts, "verify": self.seal.to_json() if self.seal else None, "error": self.error,
                "env": m.env if m else "", "problems": list(m.problems) if m else [],
                "files": [e.to_json() for e in self.entries]}


def _results(e: Mapping) -> List[Mapping]:
    d = e.get("data")
    res = d.get("results") if isinstance(d, Mapping) else None
    return [r for r in res if isinstance(r, Mapping)] if isinstance(res, list) else []


def _seal(e: Mapping, sha: str) -> Seal:
    d = e.get("data") if isinstance(e.get("data"), Mapping) else {}
    r = next(r for r in _results(e) if r.get("output_sha256") == sha)
    fp = d.get("fingerprint")
    return Seal(at=str(e.get("ts") or ""), ledger=str(e.get("_file") or ""),
                seq=e.get("seq") if isinstance(e.get("seq"), int) else None, ok=d.get("ok") is True,
                fingerprint=fp if isinstance(fp, str) else None, cmd=str(r.get("cmd") or ""),
                exit=r.get("exit") if isinstance(r.get("exit"), int) else None)


def _sha(data: Optional[bytes]) -> Optional[str]:
    return None if data is None else hashlib.sha256(data).hexdigest()


def find_sets(files: Files, raw: Raw, docs: str, verifies: Sequence[Mapping], clone: Optional[str] = None) -> List[Found]:
    """Every set folder under <docs>/construction/build-and-test/proof/ (clone: only the folders of the clone whose
    id ends in -<clone>, a split child's own), parsed, sealed against `verifies` (oldest first) and with each item's
    file checked."""
    K = kit()
    root = "%s/%s/" % (docs, K.PROOF_DIR)
    out = []
    for path in files(root.rstrip("/")):
        where = K.set_output_of(path)
        if not where or not path.startswith(root) or (clone and not where[1].endswith("-" + clone)):
            continue
        data = raw(path)
        if data is None:
            continue
        sha = K.output_sha256(data)
        sealing = K.sealing_verify(data, verifies)
        manifest, error, newer = None, "", None
        try:
            manifest = K.parse_set(data)
        except K.SetError as e:
            error, newer = str(e), e.newer
        d = path[:-len("/" + K.OUTPUT)]
        entries = tuple(Entry(i, "%s/%s" % (d, i.file) if i.file else None,
                              bool(i.file) and _sha(raw("%s/%s" % (d, i.file))) == i.sha256)
                        for i in (manifest.items if manifest else ()))
        order = next((n for n, e in enumerate(verifies) if e is sealing), -1)
        out.append(Found(dir=d, name=where[2], clone=where[1], output=path, output_sha256=sha,
                         lines=max(0, data.count(b"\n") - 1), manifest=manifest, error=error, newer=newer,
                         seal=_seal(sealing, sha) if sealing is not None else None, order=order, entries=entries))
    return sorted(out, key=lambda f: (f.order, f.dir))


# ---------------------------------------------------------------- one plan row, one log, as a reviewer sees them

@dataclass(frozen=True)
class Shown:
    """The capture a row or a log shows, and how it binds to the code under review."""
    found: Found
    entry: Optional[Entry]        # the row's item (None for a log: the set itself)
    verdict: Verdict
    uncommitted: bool             # working tree only: a file of it differs from HEAD, or is untracked

    def to_json(self) -> dict:
        code = self.found.manifest.code
        out = {"verify": self.found.seal.to_json(), "freshness": self.verdict.freshness.value,
               "changed": list(self.verdict.changed), "why": self.verdict.why, "uncommitted": self.uncommitted,
               "code": {"commit": code.commit, "dirty": None if code.dirty is None else len(code.dirty)}}
        if self.entry is not None:
            i = self.entry.item
            out.update(path=self.entry.path, sha256=i.sha256, size=i.size, result=i.result.value, note=i.note,
                       secs=i.secs, page=None if i.page is None else {"path": i.page.path, "title": i.page.title})
        return out


@dataclass(frozen=True)
class Row:
    """One plan row: the newest sealed capture of it that has a file is shown, judged by its own code; `last` is the
    newest try when that is another one (a runner without Chrome keeps the owner's screenshot)."""
    set: str
    unit: str
    id: str
    label: str                    # P-2, or web/P-2 when the intent has several units
    ac: Tuple[str, ...]
    what: str
    steps: Tuple[str, ...]
    state: str                    # "current" or a ROW_KINDS value
    shown: Optional[Shown]
    last: Optional[Tuple[Found, Entry]]
    replanned: bool               # the plan's row now says other steps or words than the shown capture

    def to_json(self) -> dict:
        last = None
        if self.last:
            f, e = self.last
            last = {"result": e.item.result.value, "note": e.item.note, "path": e.path, "ok": e.ok,
                    "verify": f.seal.to_json()}
        return {"set": self.set, "unit": self.unit, "id": self.id, "label": self.label, "ac": list(self.ac),
                "what": self.what, "steps": list(self.steps), "state": self.state, "replanned": self.replanned,
                "shown": self.shown.to_json() if self.shown else None, "last": last}


@dataclass(frozen=True)
class Log:
    """The newest sealed run set of a name: a wrapped command's whole output and the files it left."""
    shown: Shown

    def to_json(self) -> dict:
        f = self.shown.found
        return dict(self.shown.to_json(), set=f.name, path=f.output, exit=f.manifest.exit, lines=f.lines,
                    label=kit().check_label(f.seal.cmd) or f.name, problems=list(f.manifest.problems),
                    files=[e.to_json() for e in f.entries])


@dataclass(frozen=True)
class PlanFile:
    unit: str
    path: str
    sha256: Optional[str]
    rows: int
    problems: Tuple[str, ...]
    shot: Optional[str]           # the sha256 of this plan as the newest shots capture read it

    def to_json(self) -> dict:
        return {"unit": self.unit, "path": self.path, "sha256": self.sha256, "rows": self.rows,
                "problems": list(self.problems), "shot": self.shot}


@dataclass(frozen=True)
class Evidence:
    """An intent's evidence, judged against one version of the code. to_json() is what the app reads (docs/proof.md
    「给 app 的 JSON」)."""
    configured: bool              # lean.json names a proof check, or the newest verify ran one
    commit: Optional[str]         # the code under review: this commit ...
    worktree: bool                # ... and, when True, the uncommitted files of the working tree
    dirty: Optional[int]
    rows: Tuple[Row, ...]
    logs: Tuple[Log, ...]
    sets: Tuple[Found, ...]
    plans: Tuple[PlanFile, ...]
    env: str
    by_ac: Mapping[str, Tuple[str, ...]]
    without: Tuple[str, ...]
    gaps: Tuple[Gap, ...]

    @property
    def state(self) -> str:
        """One word for the whole: off, none, stale, unknown, failed (a row missed, or a file changed after), current.
        Rows still planned or not taken do not change it: the gaps say they are missing."""
        shown = [r.state for r in self.rows if r.shown] + [
            {Freshness.STALE: GapKind.OLD.value, Freshness.UNKNOWN: GapKind.UNKNOWN.value}.get(
                lg.shown.verdict.freshness, "current") for lg in self.logs]
        if not self.configured and not shown:
            return "off"
        if not shown:
            return "none"
        states = set(shown) | {r.state for r in self.rows}
        for kinds, word in (((GapKind.OLD,), "stale"), ((GapKind.UNKNOWN,), "unknown"),
                            ((GapKind.MISSED, GapKind.VOID), "failed")):
            if any(k.value in states for k in kinds):
                return word
        return "current"

    def counts(self) -> Dict[str, int]:
        out = {"shots": sum(1 for r in self.rows if r.shown), "logs": len(self.logs)}
        out.update({k: sum(1 for r in self.rows if r.state == k) for k in ("current",) + tuple(
            g.value for g in ROW_KINDS)})
        return out

    def to_json(self) -> dict:
        return {"schema": SCHEMA, "judged": {"commit": self.commit, "worktree": self.worktree, "dirty": self.dirty},
                "configured": self.configured, "state": self.state, "counts": self.counts(), "env": self.env,
                "rows": [r.to_json() for r in self.rows], "logs": [lg.to_json() for lg in self.logs],
                "sets": [f.to_json() for f in self.sets], "plans": [p.to_json() for p in self.plans],
                "by_ac": {k: list(v) for k, v in self.by_ac.items()}, "without": list(self.without),
                "gaps": [{"kind": g.kind.value, "text": g.text, "refs": list(g.refs)} for g in self.gaps]}


def _uncommitted(repo: str, prefix: str) -> set:
    p = _git(repo, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", prefix)
    if p.returncode != 0:
        return set()
    out, fields, i = set(), p.stdout.split(b"\0"), 0
    while i < len(fields):
        f = fields[i]
        i += 1
        if len(f) >= 4:
            out.add(f[3:].decode("utf-8", "surrogateescape"))
            if b"R" in f[:2] or b"C" in f[:2]:
                i += 1
    return out


def _plans(files: Files, raw: Raw, docs: str, units: Optional[Sequence[str]]) -> List[Any]:
    K = kit()
    out = []
    for path in files("%s/construction" % docs):
        where = K.plan_of(path)
        if where and "%s/%s" % (K.DOCS, where[0]) == docs and (units is None or where[1] in units):
            out.append(K.parse_plan(raw(path), where[1], path))
    return out


def bind(repo: str, files: Files, raw: Raw, docs: str, events: Sequence[Mapping], lcfg: Mapping,
         commit: Optional[str] = None, *, clone: Optional[str] = None, units: Optional[Sequence[str]] = None,
         acs: Sequence[str] = (), tested: Sequence[str] = (), ui_changed: Sequence[str] = ()) -> Evidence:
    """The intent's evidence (docs: aidlc-docs/<intent>) against one version of the code. files/raw read the tree
    under review: `commit` (a ref's commit), or the working tree when commit is None. events: the intent's Lean
    records, oldest first (a split child: its own clone's, with clone= its 6-hex id and units= its unit). lcfg: that
    tree's lean.json. acs: the requirement's AC ids in order; tested: those with a test; ui_changed: changed paths
    that match kit.json proof.ui_paths (both named only once verify keeps evidence: configured)."""
    K = kit()
    lcfg = lcfg if isinstance(lcfg, Mapping) else {}
    verifies = [e for e in events if isinstance(e, Mapping) and e.get("event") == "VERIFY"]
    sets = find_sets(files, raw, docs, verifies, clone)
    plans = _plans(files, raw, docs, units)
    checks = lcfg.get("checks") if isinstance(lcfg.get("checks"), list) else []
    ran = [e for e in verifies if any(K.is_proof_check(r.get("cmd")) for r in _results(e))]
    newest_ran = bool(verifies) and verifies[-1] in ran
    configured = newest_ran or any(K.is_proof_check(c) for c in checks)
    target = at_commit(commit) if commit else capture(repo, lcfg)
    worktree = commit is None
    dirty = (set(_uncommitted(repo, "%s/%s" % (docs, K.PROOF_DIR))) if worktree else set())
    verdicts: Dict[Any, Verdict] = {}

    def shown(found: Found, entry: Optional[Entry]) -> Shown:
        code = found.manifest.code
        if code not in verdicts:
            verdicts[code] = compare(repo, code, target, lcfg)
        paths = [found.output] + ([entry.path] if entry is not None else [e.path for e in found.entries if e.path])
        return Shown(found, entry, verdicts[code], any(p in dirty for p in paths))

    several = len({p.unit for p in plans} | {e.item.unit for f in sets if f.counts for e in f.entries
                                             if f.manifest.kind is K.Kind.SHOTS}) > 1
    label = (lambda unit, rid: "%s/%s" % (unit, rid)) if several else (lambda unit, rid: rid)
    counting = [f for f in sets if f.counts]
    shot_sets: Dict[str, List[Found]] = {}
    for f in counting:
        if f.manifest.kind is K.Kind.SHOTS:
            shot_sets.setdefault(f.name, []).append(f)
    planned_rows = {(p.unit, r.id): r for p in plans for r in p.rows}
    rows, seen = [], set()
    for name in sorted(shot_sets):
        tries = shot_sets[name]
        for entry in tries[-1].entries:
            i = entry.item
            if (units is not None and i.unit not in units) or (name, i.unit, i.id) in seen:
                continue
            seen.add((name, i.unit, i.id))
            best = next(((f, e) for f in reversed(tries) for e in f.entries
                         if (e.item.unit, e.item.id) == (i.unit, i.id) and e.path and e.ok), None)
            sh = shown(*best) if best else None
            last = None if best and best[1] is entry else (tries[-1], entry)
            if sh is None:
                state = GapKind.VOID if entry.path and not entry.ok else GapKind.NOT_TAKEN
            elif sh.verdict.freshness is Freshness.STALE:
                state = GapKind.OLD
            elif sh.verdict.freshness is Freshness.UNKNOWN:
                state = GapKind.UNKNOWN
            elif sh.entry.item.result is K.Result.FAILED:
                state = GapKind.MISSED
            else:
                state = None
            pr = planned_rows.get((i.unit, i.id))
            item = sh.entry.item if sh else i
            replanned = pr is not None and (tuple(s.text() for s in pr.steps), pr.what) != (item.steps, item.what)
            rows.append(Row(name, i.unit, i.id, label(i.unit, i.id), i.ac, i.what, i.steps,
                            state.value if state else "current", sh, last, replanned))
    default = sorted(shot_sets)[0] if shot_sets else "ui"
    for p in plans:
        for r in p.rows:
            if not any((row.unit, row.id) == (p.unit, r.id) for row in rows):
                rows.append(Row(default, p.unit, r.id, label(p.unit, r.id), r.ac, r.what,
                                tuple(s.text() for s in r.steps), GapKind.PLANNED.value, None, None, False))
    logs = []
    for name in sorted({f.name for f in counting if f.manifest.kind is K.Kind.RUN}):
        newest = [f for f in counting if f.manifest.kind is K.Kind.RUN and f.name == name][-1]
        logs.append(Log(shown(newest, None)))
    newest_shots = max((f for fs in shot_sets.values() for f in fs), key=lambda f: f.order, default=None)
    plan_files = tuple(PlanFile(p.unit, p.path, p.sha256, len(p.rows), p.problems, next(
        (sha for unit, path, sha in (newest_shots.manifest.plans if newest_shots else ()) if path == p.path), None))
        for p in plans)

    by_ac: Dict[str, List[str]] = {}
    for r in rows:
        if r.shown:
            for a in r.ac:
                by_ac.setdefault(a, []).append(r.label)
    for lg in logs:
        for e in lg.shown.found.entries:
            if e.path and e.ok:
                for a in e.item.ac:
                    by_ac.setdefault(a, []).append(e.item.id)
    without = tuple(a for a in acs if a not in by_ac and a not in set(tested)) if configured else ()

    gaps: List[Gap] = []
    if not configured:
        gaps.append(gap(GapKind.NO_PROOF_CHECK))
    elif not newest_ran:
        gaps.append(gap(GapKind.NO_VERIFY))
    if newest_ran:
        held = {f.output_sha256 for f in sets}
        for r in _results(verifies[-1]):
            if K.is_proof_check(r.get("cmd")) and r.get("output_sha256") not in held:
                gaps.append(gap(GapKind.LOST, K.check_label(r.get("cmd")), r.get("exit"), refs=[str(r.get("cmd"))]))
    for f in sets:
        if f.newer is not None:
            gaps.append(gap(GapKind.NEWER_FORMAT, f.dir, f.newer, refs=[f.dir]))
        elif not f.counts:
            gaps.append(gap(GapKind.VOID, f.dir + "/", refs=[f.dir]))
        else:
            gaps += [gap(GapKind.VOID, e.path, refs=[e.path]) for e in f.entries if e.path and not e.ok]
    by_state: Dict[str, List[Row]] = {}
    for r in rows:
        by_state.setdefault(r.state, []).append(r)
    for r in by_state.get(GapKind.MISSED.value, []):
        gaps.append(gap(GapKind.MISSED, r.label, r.shown.entry.item.note or "没走通", refs=[r.label]))
    judged = [(r.label, r.shown.verdict) for r in rows if r.shown] + [(lg.shown.found.name, lg.shown.verdict)
                                                                     for lg in logs]
    for kind, fresh in ((GapKind.OLD, Freshness.STALE), (GapKind.UNKNOWN, Freshness.UNKNOWN)):
        groups: Dict[Verdict, List[str]] = {}
        for ref, v in judged:
            if v.freshness is fresh:
                groups.setdefault(v, []).append(ref)
        for v, refs in groups.items():
            gaps.append(gap(kind, _ids(refs, 6), _ids(v.changed) if kind is GapKind.OLD else v.why, refs=refs))
    for r in by_state.get(GapKind.NOT_TAKEN.value, []):
        gaps.append(gap(GapKind.NOT_TAKEN, r.label, r.last[1].item.note or "没拍", refs=[r.label]))
    planned = [r.label for r in rows if r.state == GapKind.PLANNED.value or r.replanned]
    if planned:
        gaps.append(gap(GapKind.PLANNED, _ids(planned, 6), refs=planned))
    if worktree:
        pending = [r.label for r in rows if r.shown and r.shown.uncommitted] + [
            lg.shown.found.name for lg in logs if lg.shown.uncommitted]
        if pending:
            gaps.append(gap(GapKind.UNCOMMITTED, _ids(pending, 6), refs=pending))
    if without:
        gaps.append(gap(GapKind.AC_WITHOUT, _ids(without, 6), refs=without))
    if ui_changed and configured and not by_state.get("current"):
        gaps.append(gap(GapKind.UI_WITHOUT_SHOT, _ids(sorted(ui_changed), 2), refs=sorted(ui_changed)))
    said = set()
    for text in [x for f in ([newest_shots] if newest_shots else []) for x in f.manifest.problems] + [
            x for p in plans for x in p.problems] + [x for lg in logs for x in lg.shown.found.manifest.problems]:
        if text not in said:
            said.add(text)
            gaps.append(gap(GapKind.PROBLEM, text))
    return Evidence(configured=configured, commit=target.commit, worktree=worktree,
                    dirty=None if target.dirty is None else len(target.dirty), rows=tuple(rows), logs=tuple(logs),
                    sets=tuple(sets), plans=plan_files, env=newest_shots.manifest.env if newest_shots else "",
                    by_ac={k: tuple(v) for k, v in by_ac.items()}, without=without, gaps=tuple(gaps))
