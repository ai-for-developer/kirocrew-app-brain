"""产出: what each round of a stage produced, for the 运行过程's drawer.

``GET /outputs/{repo}/{key}?node=<id>&round=<n>`` (node_output) lists a round's files: first the documents Lean
recorded for it (约定它交的: its outputs, its approval request's document, ``lean release``'s release.md, each with the
sha256 Lean wrote; a build-and-test round's evidence too, below), then the other files its commits changed (其他改动; a
unit's code generation keeps only that unit's Paths). Each file names the commit to read it at, and the version to
compare it with: the round before's, else the commit before it. ``GET /outputs/{repo}/{key}/file?ref&base&path&view&pin``
(file_view) reads one of them: its text (view source or content), its change (view diff), or a PNG or JPEG of it as a
picture (view image: a data: URL, sniffed by its first bytes, never by its name; never SVG).

证据 (brain-kit's proof module): a build-and-test round, one per VERIFY, hands in the evidence that verify sealed. Each
evidence check's set is the output.txt, in one of the branch's commits, whose blob hashes to the output_sha256 Lean
recorded for that check, then the files its manifest names at the sha256s it names, each labelled by its plan row (P-2 ·
AC-2 · 看什么, and how it went). Content-addressed like the seal itself: a set another verify left, or one made by hand,
is never listed as this round's, and set folders are never another round's 其他改动.

Both read the board's own fold (Fold: what Service._board_sync folded last for the requirement), never a second one,
and only what it names: a path none of the requirement's commits changed and Lean recorded for none of its rounds
(_own_paths), or a ref or a base outside its commits and their parents (AllowedRefs), is 404, never fetched. Files
are read from commits only (mrdesc_view.committed_file: a regular blob of at most 4 MB; a folder, a symlink or a
submodule is refused, on either side of a diff too). The one exception is a local run's document waiting for approval
before it is committed (Lean commits documents with the code): read from this machine's worktree only when the path
is the stage table's document of one of the requirement's nodes, a local round of it recorded that very sha256, no
part of the path is a symlink, and the bytes hash to it (pinned_file). A diff never runs a diff driver or a textconv
the repo's .gitattributes names. Lean's ledgers (aidlc-docs/audit, wherever aidlc-docs is) and the pages it writes
from them (aidlc-state.md, audit.md) are never listed nor read: they hold what people typed in their sessions, a
rejection's words among them. Emails become brain-kit's pseudonyms (G-<6 hex>) before anything is shown: release.md's
approval table carries git emails. A text over TEXT_MAX is cut at a line end first, so the scrubber never gets part
of a secret. git runs with GIT_NO_REPLACE_OBJECTS: a replace ref never swaps a commit's bytes.
"""

from __future__ import annotations

import base64
import difflib
import errno
import fnmatch
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import bk, mrdesc_view, stages
from .httpkit import AppError
from .repo import Git
from .runs import LOCAL, RunView

PATH_MAX = 400
TEXT_MAX = 300_000          # characters of a file or a diff sent to the page
BINARY_PROBE = 8000         # a NUL in the first bytes: a binary file
MAX_FILES = 200             # files listed for a round
MAX_TRIES = 24              # commits whose blob is hashed to find the version Lean recorded
DIFF_TIMEOUT_S = 20
VIEWS = ("content", "source", "diff", "image")
IMAGES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))   # the only pictures shown
HEX40 = re.compile(r"[0-9a-f]{40}")             # these four: whole strings only (fullmatch)
HEX64 = re.compile(r"[0-9a-f]{64}")
NODE_RE = re.compile(r"[a-z][a-z-]{0,39}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?")
ROUND_RE = re.compile(r"[1-9][0-9]{0,2}")
# Lean's ledgers, and aidlc-state.md and audit.md, wherever aidlc-docs is (no stage's document is either)
HIDDEN = re.compile(r"(?:^|/)aidlc-docs/(?:audit(?:/|$)|(?:[^/]+/)*(?:aidlc-state|audit)\.md$)")
# brain-kit trace.EMAIL_RE and trace.pseudonym, so a person is the same G-xxxxxx here as everywhere on the board; the
# part before the @ held to 64 characters (RFC 5321; the same matches up to it): unbounded, a 60 000-letter run took 4 s
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")
LANG = {"md": "markdown", "markdown": "markdown", "py": "python", "ts": "typescript", "tsx": "typescript",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript", "json": "json", "jsonl": "json", "yml": "yaml",
        "yaml": "yaml", "toml": "toml", "sh": "shell", "css": "css", "html": "html", "sql": "sql", "go": "go",
        "java": "java", "kt": "kotlin", "rs": "rust", "txt": "text"}

BAD_PATH = "文件路径不对（要是仓库里的相对路径）：刷新页面再试"
BAD_NODE = "页面说的这一步不对：刷新页面再试"
BAD_ROUND = "页面说的第几轮不对：刷新页面再试"
BAD_VIEW = "只能看内容、源码、改动或图片：刷新页面再试"
BAD_SHA = "页面说的提交不对：刷新页面再试"
BAD_PIN = "页面说的文件指纹不对：刷新页面再试"
NO_NODE = "看板上没有这一步：刷新运行过程再看"
NO_ROUND = "这一步没有这一轮：刷新运行过程再看"
NOT_OURS = "这个提交不是这个需求的：刷新运行过程，从运行过程里点开文件"
NOT_OURS_FILE = "这个文件不是这个需求的：刷新运行过程，从运行过程里点开文件"
NOT_SHOWN = ("Lean 的账本和照账本写的 aidlc-state.md、audit.md 不在这里显示，里面有人在会话里打的字："
             "到运行过程的「事件」里看它记下的事")
NOT_RECORDED = "Lean 没有为这一步记下这个文件的这一版：从运行过程里点开文件"
NO_WORKTREE = "这台机器上没有这个需求的工作区：这一版只能等提交以后看"
CHANGED = "这一版还没提交，工作区里的文件也和 Lean 记下的不一样了：到会话里看"
SYMLINK = "这是一个符号链接（或者路径里有符号链接），这里不读它指向的东西：到会话里看"
SUBMODULE = "这是一个子模块，这里不显示：到它自己的仓库里看"
NOT_A_FILE = "这不是一个普通文件（是文件夹或者特殊文件），这里不读它：到会话里看"
MISSING = "这一版里没有这个文件（可能这一轮删掉了它）：看「改动」"
GONE = "工作区里没有这个文件了：到会话里看"
TOO_BIG = "文件太大（超过 4 MB），这里不显示：到会话里看"
NO_DIFF = "这两版比不出来：刷新看板再试"
NO_IMAGE = "这不是 PNG 或 JPEG 图片（按文件开头的字节认），这里不当图片显示"


def pseudonymize(text: str) -> str:
    return EMAIL_RE.sub(lambda m: "G-" + hashlib.sha256(m.group(0).strip().lower().encode()).hexdigest()[:6].upper(), text)


def safe_path(p: Any) -> str:
    """A repo-relative path as a file of a commit is named: no absolute path, no `..`, `.` or empty part, no backslash,
    no control character, at most PATH_MAX characters. Lean's ledgers are refused (403)."""
    if not isinstance(p, str) or not p or len(p) > PATH_MAX or p.startswith("/") or "\\" in p \
            or any(ord(c) < 32 or ord(c) == 127 for c in p) or any(x in ("", ".", "..") for x in p.split("/")):
        raise AppError(BAD_PATH, "invalid_path")
    if HIDDEN.search(p):
        raise AppError(NOT_SHOWN, "not_shown", 403)
    return p


def _lang(path: str) -> str:
    return LANG.get(path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else "", "text")


class _NoReplaceGit(Git):
    """git with GIT_NO_REPLACE_OBJECTS: a replace ref (refs/replace/, anyone with the repo can add one) cannot swap
    what a commit holds."""

    def run(self, *args: str, env: Optional[dict] = None, **kw: Any) -> Any:
        return super().run(*args, env=dict(env or {}, GIT_NO_REPLACE_OBJECTS="1"), **kw)


class GitReader:
    """The drawer's reads of the requirement's git repository: tree entries, blobs, parents, diffs."""

    _digests: Dict[Tuple[str, str, str], Optional[str]] = {}
    _parents: Dict[Tuple[str, str], Optional[str]] = {}

    def __init__(self, git: Git) -> None:
        self.git = _NoReplaceGit(git.repo)

    def entry(self, sha: str, rel: str) -> Optional[Tuple[str, str, int]]:
        return mrdesc_view.tree_entry(self.git, sha, rel)

    def blob(self, sha: str, rel: str) -> Optional[bytes]:
        return mrdesc_view.committed_file(self.git, sha, rel)

    def digest(self, sha: str, rel: str) -> Optional[str]:
        """sha256 of *rel* at *sha* (a commit's file never changes: kept)."""
        k = (self.git.repo, sha, rel)
        if k not in self._digests:
            raw = self.blob(sha, rel)
            if len(self._digests) > 4096:
                self._digests.clear()
            self._digests[k] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        return self._digests[k]

    def parents(self, shas: Sequence[str]) -> Dict[str, Optional[str]]:
        """Each commit's first parent (None: a root commit, or not in the repo)."""
        todo = [s for s in shas if (self.git.repo, s) not in self._parents]
        if todo:
            p = self.git.run("rev-list", "--no-walk=unsorted", "--parents", *todo, timeout=DIFF_TIMEOUT_S)
            got = {}
            for line in p.stdout.splitlines() if p.returncode == 0 else []:
                if not line.strip():
                    continue
                sha, *parents = line.split()
                got[sha] = parents[0] if parents else None
            if len(self._parents) > 8192:
                self._parents.clear()
            for s in todo:
                self._parents[(self.git.repo, s)] = got.get(s)
        return {s: self._parents[(self.git.repo, s)] for s in shas}

    def diff(self, base: str, ref: str, rel: str) -> Optional[str]:
        """`git diff base ref -- rel` as plain text: no external diff, no textconv, whatever .gitattributes says; a
        file that is not UTF-8 decoded as its source is (a character it cannot be becomes �)."""
        p = self.git.run("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                         "--src-prefix=a/", "--dst-prefix=b/", base, ref, "--", rel, timeout=DIFF_TIMEOUT_S,
                         text=False, env=mrdesc_view.LITERAL)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


@dataclass(frozen=True)
class Fold:
    """What the drawer reads of one requirement's board fold (Service._board_sync keeps the last one). *commits*:
    board._branch_commits (full shas, oldest first); *changes*: board.changes_from_log of them; *units*: the planned
    units with their Paths; *worktree*: this machine's checkout of the run branch, the only place a pinned document is
    read from (None on the team runner's page, or when it is not checked out here). *reader*: None when this machine
    has no copy of the repository. *runs*: who did the rounds, which of them this machine can read and their sessions
    here; *turns*: the session turns Lean recorded (the session tabs, turns.py)."""
    reader: Optional[GitReader]
    intent: Optional[str]
    branch: Optional[str]
    nodes: Tuple[stages.Node, ...]
    commits: Tuple[Mapping[str, Any], ...]
    changes: Mapping[str, Sequence[Mapping[str, Any]]]
    units: Tuple[stages.UnitPlan, ...] = ()
    worktree: Optional[str] = None
    runs: Tuple[RunView, ...] = ()
    turns: Tuple[stages.Turn, ...] = ()


class AllowedRefs:
    """The commits a drawer may read: the run branch's, as the fold has them, and each one's first parent (the version
    before it). Nothing else is ever read, and nothing is fetched."""

    def __init__(self, fold: Fold) -> None:
        own = [str(c["sha"]) for c in fold.commits]
        self.parent = fold.reader.parents(own) if fold.reader is not None and own else {}
        self.shas = frozenset(own) | {p for p in self.parent.values() if p}

    def need(self, sha: str) -> str:
        if sha not in self.shas:
            raise AppError(NOT_OURS, "unknown_commit", 404)
        return sha


def _reader(fold: Fold) -> GitReader:
    if fold.reader is None:
        raise AppError("这台机器上没有这个需求的仓库：在运行机上，或者等它推上来以后再看", "no_repo_here", 404)
    return fold.reader


@dataclass(frozen=True)
class NodeQuery:
    """?node&round as the page sends them, checked: a stage id (of a unit: code-generation/api); 第 n 轮, or None for
    the latest."""
    node: str
    round: Optional[int]


def node_query(node: Any, rnd: Any) -> NodeQuery:
    if not isinstance(node, str) or not NODE_RE.fullmatch(node):
        raise AppError(BAD_NODE, "invalid_node")
    if rnd not in (None, "") and (not isinstance(rnd, str) or not ROUND_RE.fullmatch(rnd)):
        raise AppError(BAD_ROUND, "invalid_round")
    return NodeQuery(node, int(rnd) if rnd else None)


@dataclass(frozen=True)
class FileQuery:
    """?ref&base&path&view&pin, checked: *ref* a commit (40 hex) or ``worktree`` with *pin* (Lean's sha256 of it)."""
    path: str
    view: str
    ref: str
    base: Optional[str]
    pin: Optional[str]


def file_query(q: Mapping[str, Any]) -> FileQuery:
    path = safe_path(q.get("path"))
    view = q.get("view") or "source"
    if view not in VIEWS:
        raise AppError(BAD_VIEW, "invalid_view")
    ref, base, pin = q.get("ref"), q.get("base") or None, q.get("pin") or None
    if not isinstance(ref, str) or not (ref == "worktree" or HEX40.fullmatch(ref)) or \
            base is not None and (not isinstance(base, str) or not HEX40.fullmatch(base)):
        raise AppError(BAD_SHA, "invalid_ref")
    if ref == "worktree" and (not isinstance(pin, str) or not HEX64.fullmatch(pin)):
        raise AppError(BAD_PIN, "invalid_pin")
    return FileQuery(path, view, ref, base, pin if ref == "worktree" else None)


def round_of(fold: Fold, q: NodeQuery) -> Tuple[stages.Node, Optional[stages.Round]]:
    n = next((x for x in fold.nodes if x.id == q.node), None)
    if n is None:
        raise AppError(NO_NODE, "no_node", 404)
    if q.round is None:
        return n, (n.rounds[-1] if n.rounds else None)
    r = next((x for x in n.rounds if x.n == q.round), None)
    if r is None:
        raise AppError(NO_ROUND, "no_round", 404)
    return n, r


def _doc(fold: Fold, n: stages.Node) -> Optional[str]:
    return stages.doc_path(n.stage, n.unit, fold.intent)


def _touched(fold: Fold, path: str) -> List[str]:
    return [str(c["sha"]) for c in fold.commits if path in _paths(fold, str(c["sha"]))]


def _version(fold: Fold, path: str, h: Optional[str], r: stages.Round) -> Optional[str]:
    """The commit holding the version of *path* Lean recorded (*h*, its sha256): the earliest commit that changed the
    path and whose blob hashes to it, else the round's newest or the branch's newest whose blob does. No sha256 (a
    review written in a commit): the round's newest commit that changed it."""
    rd = _reader(fold)
    if h is None:
        mine = [s for s in r.commits if path in _paths(fold, s)]
        return mine[-1] if mine else None
    tries = list(dict.fromkeys(_touched(fold, path)[:MAX_TRIES] + list(r.commits[-1:]) +
                               [str(c["sha"]) for c in fold.commits[-1:]]))
    return next((s for s in tries if rd.digest(s, path) == h), None)


def _paths(fold: Fold, sha: str) -> List[str]:
    return [str(x.get("path")) for x in fold.changes.get(sha) or []]


def _change(fold: Fold, sha: Optional[str], path: str) -> Optional[Mapping[str, Any]]:
    return next((x for x in fold.changes.get(sha or "") or [] if x.get("path") == path), None)


def _declared(fold: Fold, n: stages.Node, r: stages.Round) -> List[Tuple[str, Optional[str]]]:
    """What the round hands in, as Lean recorded it: (path, sha256); the node's own document first. The review is
    written in a commit and has no sha256: its document when one of its commits changed it."""
    got: Dict[str, Optional[str]] = {p: h for p, h in r.outputs if h}
    doc = _doc(fold, n)
    if doc and doc not in got and any(doc in _paths(fold, s) for s in r.commits):
        got[doc] = None
    order = sorted(got, key=lambda p: (p != doc, p))
    return [(p, got[p]) for p in order if not HIDDEN.search(p)]


def _in_unit(fold: Fold, n: stages.Node, path: str) -> bool:
    if n.stage.id != "code-generation" or not n.unit:
        return True
    plan = next((u for u in fold.units if u.name == n.unit), None)
    globs = [g.rstrip("/") + ("**" if g.endswith("/") else "") for g in (plan.paths if plan else ())]
    return not globs or any(fnmatch.fnmatchcase(path, g) for g in globs)


def _why_none(r: stages.Round, h: Optional[str]) -> str:
    sha8 = (h or "")[:8]
    if r.run != LOCAL:
        return "Lean 记下了这一版（指纹 %s），这台机器的提交里还没有它：等运行机把它推上来，取回以后再看" % sha8
    return "Lean 记下了这一版（指纹 %s），可它没提交过，工作区里的也改了：这台机器上读不到这一版了" % sha8


def _earlier(fold: Fold, n: stages.Node, r: stages.Round, path: str, h: Optional[str], ref: Optional[str]
             ) -> List[Dict[str, Any]]:
    """The versions of *path* the node's earlier rounds handed in, newest first, each once: 和第 N 轮比."""
    out: List[Dict[str, Any]] = []
    for x in reversed([x for x in n.rounds if x.n < r.n]):
        hx = dict(x.outputs).get(path)
        if h is not None and hx is None:
            continue
        sha = _version(fold, path, hx, x)
        if sha and sha != ref and all(v["ref"] != sha for v in out):
            out.append({"round": x.n, "ref": sha})
    return out


def _out_file(fold: Fold, allowed: AllowedRefs, n: stages.Node, r: stages.Round, path: str, h: Optional[str],
              proof: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A declared file: read at the commit holding the version Lean recorded (evidence: its sealed manifest), compared
    with the round before's version, else the commit before; not committed but waiting in this machine's worktree:
    the pinned read. *proof*: an evidence file's label (_evidence)."""
    ref = _version(fold, path, h, r)
    vs = _earlier(fold, n, r, path, h, ref)
    out = {"path": path, "output": True, "status": "M", "added": None, "removed": None, "ref": ref, "pin": None,
           "base": None, "base_round": None, "vs": vs, "why": None, "proof": proof}
    if ref is None:
        if r.run == LOCAL and fold.worktree and h and path == _doc(fold, n):
            out.update(ref="worktree", pin=h, status="M" if vs else "A")
        elif proof is not None and _touched(fold, path):     # committed, never with the bytes its set sealed
            P = bk.m("proof")
            out["why"] = P.TEXT[P.GapKind.VOID] % path
        else:
            out["why"] = _why_none(r, h)
    else:
        ch, parent = _change(fold, ref, path), allowed.parent.get(ref)
        if ch is not None:
            had = ch.get("status") != "A"
            out.update(status=str(ch.get("status") or "M"), added=ch.get("added"), removed=ch.get("removed"))
        else:
            had = parent is not None and _reader(fold).entry(parent, path) is not None
            out["status"] = "M" if had else "A"
        out["base"] = parent if had else None
    if vs:
        out.update(base=vs[0]["ref"], base_round=vs[0]["round"])
    return out


def _others(fold: Fold, allowed: AllowedRefs, n: stages.Node, r: stages.Round, skip: set,
            sets: Optional[str] = None) -> List[Dict[str, Any]]:
    """The other files the round's commits changed: added up per file (A while its first change adds it, D when its
    last deletes it), read at the round's last commit that changed it, compared with the commit before its first.
    Nothing under *sets* (the intent's evidence folder): a set is the round's whose VERIFY sealed it, never the round
    its records commit happens to fall in."""
    by: Dict[str, Dict[str, Any]] = {}
    for sha in r.commits:
        for ch in fold.changes.get(sha) or []:
            p = str(ch.get("path") or "")
            if not p or p in skip or HIDDEN.search(p) or not _in_unit(fold, n, p) or (sets and p.startswith(sets)):
                continue
            had = by.get(p)
            st = str(ch.get("status") or "M")
            if had is None:
                by[p] = {"path": p, "output": False, "status": st, "added": int(ch.get("added") or 0),
                         "removed": int(ch.get("removed") or 0), "ref": sha, "pin": None, "first": sha,
                         "base_round": None, "vs": [], "why": None, "proof": None}
            else:
                had.update(added=had["added"] + int(ch.get("added") or 0), removed=had["removed"] + int(ch.get("removed") or 0),
                           ref=sha, status="D" if st == "D" else "A" if had["status"] == "A" else st)
    out = []
    for p in sorted(by):
        f = by[p]
        first = f.pop("first")
        f["base"] = None if f["status"] == "A" else allowed.parent.get(first)
        out.append(f)
    return out


def proof_kit() -> Any:
    """brain-kit's evidence kit (brainkit.proof.kit(): the set layout and its reader); None when this brain-kit has no
    proof module or its kit file does not load."""
    if not bk.has("proof"):
        return None
    try:
        return bk.m("proof").kit()
    except Exception:  # noqa: BLE001 - a brain-kit without its kit file: no evidence, never a broken drawer
        return None


def _sets_dir(fold: Fold, K: Any) -> Optional[str]:
    return "%s/%s/%s/" % (K.DOCS, fold.intent, K.PROOF_DIR) if K is not None and fold.intent else None


def _item_label(i: Any, several: bool) -> str:
    """A set's item as the drawer names it: a plan row 「P-2 · AC-2 · 点退款以后状态是「已退款」」 (web/P-2 when the set
    shot rows of several units); a file a wrapped command left, by its ACs and its name."""
    return " · ".join(x for x in ("%s/%s" % (i.unit, i.id) if several and i.unit else i.id, "、".join(i.ac), i.what) if x)


def _evidence(fold: Fold, n: stages.Node, r: stages.Round, sets: Optional[str]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """(path, sha256, label) of what a build-and-test round's VERIFY sealed: per evidence check, the output.txt among
    the branch's commits whose blob hashes to the output_sha256 Lean recorded for it, then each file its manifest
    names, at the sha256 it names. A set no commit holds yet is left out: none of its files can be read here."""
    if sets is None or n.stage.id != "build-and-test":
        return []
    K, rd = proof_kit(), _reader(fold)
    outs = [(str(c["sha"]), p) for c in fold.commits for p in _paths(fold, str(c["sha"]))
            if p.startswith(sets) and K.set_output_of(p)]
    got: List[Tuple[str, str, Dict[str, Any]]] = []
    for c in r.end.checks:
        hit = next(((s, p) for s, p in outs if rd.digest(s, p) == c.output_sha256), None) \
            if c.output_sha256 and K.is_proof_check(c.cmd) else None
        if hit is None:
            continue
        try:
            items = K.parse_set(rd.blob(*hit) or b"").items
        except K.SetError:          # a newer brain-kit's set, or a broken one: its output alone
            items = ()
        name, folder = K.set_output_of(hit[1])[2], hit[1].rsplit("/", 1)[0]
        several = len({i.unit for i in items if i.unit}) > 1
        got.append((hit[1], c.output_sha256, {"set": name, "label": K.check_label(c.cmd) or name, "result": None,
                                              "note": ""}))
        got += [("%s/%s" % (folder, i.file), i.sha256, {"set": name, "label": _item_label(i, several),
                                                         "result": i.result.value, "note": i.note})
                for i in items if i.file]
    return got


def node_output(fold: Fold, q: NodeQuery) -> Dict[str, Any]:
    """One round of one node: its files (declared first, a build-and-test round's evidence after Lean's), the branch
    and its last commit; at most MAX_FILES files."""
    n, r = round_of(fold, q)
    if r is None:
        return {"node": n.id, "round": None, "run": None, "branch": fold.branch, "head": None, "files": [], "more": 0,
                "why": "这一步还没开始：开始以后，它每一轮交的文件都在这里"}
    _reader(fold)
    allowed = AllowedRefs(fold)
    sets = _sets_dir(fold, proof_kit())
    declared = [_out_file(fold, allowed, n, r, p, h) for p, h in _declared(fold, n, r)]
    listed = {f["path"] for f in declared}
    for p, h, label in _evidence(fold, n, r, sets):
        if p not in listed:
            listed.add(p)
            declared.append(_out_file(fold, allowed, n, r, p, h, label))
    files = declared + _others(fold, allowed, n, r, listed, sets)
    return {"node": n.id, "round": r.n, "run": r.run, "branch": fold.branch, "head": r.commits[-1] if r.commits else None,
            "files": files[:MAX_FILES], "more": max(0, len(files) - MAX_FILES),
            "why": None if files else "这一轮没有提交文件" if r.ended is not None else "这一轮还没提交文件"}


# ---------------------------------------------------------------- one file

def _own_paths(fold: Fold) -> set:
    """The requirement's files: the paths its commits changed and the ones Lean recorded for its rounds."""
    out = {p for c in fold.commits for p in _paths(fold, str(c["sha"]))}
    for n in fold.nodes:
        for r in n.rounds:
            out |= {p for p, _ in r.outputs + (r.ask.files if r.ask else ())}
    return out


def _pins(fold: Fold, path: str) -> set:
    """The sha256s a local round of a node recorded for its own document *path*: what a pinned read may match."""
    out = set()
    for n in fold.nodes:
        if _doc(fold, n) != path:
            continue
        for r in n.rounds:
            if r.run == LOCAL:
                out |= {h for p, h in list(r.outputs) + list(r.ask.files if r.ask else ()) if p == path and h}
    return out


def _asked(fold: Fold, path: str, h: Optional[str]) -> Optional[Dict[str, str]]:
    """The approval this very version of *path* was asked for (its kind and sha256's first 8), if any."""
    if not h:
        return None
    for n in fold.nodes:
        for r in reversed(n.rounds):
            if r.ask is not None and (path, h) in r.ask.files:
                return {"kind": r.ask.kind, "sha8": h[:8]}
    return None


def _is_link(name: str, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode)
    except OSError:
        return False


def pinned_file(worktree: str, rel: str, sha256: str) -> bytes:
    """*rel* in the worktree, only if every part of the path is a real folder or file (each opened with O_NOFOLLOW
    under the worktree's real path), the file is regular and at most 4 MB, and its bytes hash to *sha256*."""
    nofollow, cloexec = getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_CLOEXEC", 0)
    parts = rel.split("/")
    try:
        fd = os.open(os.path.realpath(worktree), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | cloexec)
    except OSError:
        raise AppError(NO_WORKTREE, "no_worktree", 404)
    name = parts[0]
    try:
        for name in parts[:-1]:
            nxt = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow | cloexec, dir_fd=fd)
            os.close(fd)
            fd = nxt
        name = parts[-1]
        ffd = os.open(name, os.O_RDONLY | nofollow | cloexec | getattr(os, "O_NONBLOCK", 0), dir_fd=fd)
        try:
            st = os.fstat(ffd)
            if not stat.S_ISREG(st.st_mode):
                raise AppError(NOT_A_FILE, "not_a_file", 403)
            if st.st_size > mrdesc_view.MAX_RAW:
                raise AppError(TOO_BIG, "too_big", 413)
            chunks, left = [], mrdesc_view.MAX_RAW + 1
            while left > 0:
                b = os.read(ffd, min(left, 1 << 20))
                if not b:
                    break
                chunks.append(b)
                left -= len(b)
            data = b"".join(chunks)
        finally:
            os.close(ffd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK) or _is_link(name, fd):
            raise AppError(SYMLINK, "symlink", 403)
        raise AppError(GONE, "missing", 404)
    finally:
        os.close(fd)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise AppError(CHANGED, "changed", 409)
    return data


def shown_text(text: str, limit: int = TEXT_MAX) -> Tuple[str, bool]:
    """(text, truncated): cut at its last line end within *limit*, then emails pseudonymized. Cut first, at a line
    end: the pattern reads at most *limit* characters, and the scrubber after it never sees part of a secret."""
    if len(text) <= limit:
        return pseudonymize(text), False
    cut = text[:limit]
    return pseudonymize(cut[:cut.rfind("\n") + 1]), True


def image_mime(raw: bytes) -> Optional[str]:
    """A blob's picture type by its first bytes, PNG or JPEG; None for anything else, whatever its name. Never SVG: it
    is a document that can carry script and links."""
    return next((mime for magic, mime in IMAGES if raw.startswith(magic)), None)


def _text(raw: bytes) -> Tuple[str, bool, bool]:
    """(text, binary, truncated): decoded, then _shown."""
    if b"\0" in raw[:BINARY_PROBE]:
        return "", True, False
    s, cut = shown_text(raw.decode("utf-8", "replace"))
    return s, False, cut


def _file_or_refuse(rd: GitReader, sha: str, path: str, absent_ok: bool = False) -> None:
    """*path* at *sha* is a file the drawer reads: a side of a diff may lack it (added, deleted). A folder is no file
    (its diff would show every file under it), a symlink or a submodule is refused, a file over 4 MB too."""
    e = rd.entry(sha, path)
    if e is None and absent_ok:
        return
    if e is None or e[0] == "040000":
        raise AppError(MISSING, "missing", 404)
    if e[0] == "120000":
        raise AppError(SYMLINK, "symlink", 403)
    if e[0] == "160000":
        raise AppError(SUBMODULE, "submodule", 403)
    if e[2] > mrdesc_view.MAX_RAW:
        raise AppError(TOO_BIG, "too_big", 413)


def _blob_or_refuse(rd: GitReader, sha: str, path: str) -> bytes:
    _file_or_refuse(rd, sha, path)
    raw = rd.blob(sha, path)
    if raw is None:
        raise AppError(TOO_BIG, "too_big", 413)
    return raw


def _added_diff(path: str, raw: bytes) -> str:
    lines = raw.decode("utf-8", "replace").splitlines()
    return "diff --git a/{0} b/{0}\nnew file\n--- /dev/null\n+++ b/{0}\n@@ -0,0 +1,{1} @@\n".format(path, len(lines)) + \
        "".join("+%s\n" % x for x in lines)


def file_view(fold: Fold, q: FileQuery) -> Dict[str, Any]:
    """One file of the requirement: at a commit of it (*ref*), or the pinned worktree version (ref ``worktree`` with
    *pin*, the sha256 Lean recorded). View source and content: its text; diff: its change from *base* (none: all of it
    added); image: a PNG or JPEG as a data: URL (base64 in the answer: the dashboard's CSP admits data: images, and
    the picture is read through the same checks and the same owner-only route as its text)."""
    path, view, ref, pin = q.path, q.view, q.ref, q.pin
    rd = _reader(fold)
    if path not in _own_paths(fold):
        raise AppError(NOT_OURS_FILE, "unknown_file", 404)
    allowed = AllowedRefs(fold)
    base = allowed.need(q.base) if q.base else None
    pinned = ref == "worktree"
    if pinned:
        if pin not in _pins(fold, path):
            raise AppError(NOT_RECORDED, "not_recorded", 404)
        if not fold.worktree:
            raise AppError(NO_WORKTREE, "no_worktree", 404)
        raw = pinned_file(fold.worktree, path, pin or "")
        h = pin
    else:
        ref = allowed.need(ref)
        raw = _blob_or_refuse(rd, ref, path) if view != "diff" or base is None else None
        h = hashlib.sha256(raw).hexdigest() if raw is not None else rd.digest(ref, path)
    out = {"path": path, "view": view, "at": ref, "base": base, "pinned": pinned, "lang": _lang(path),
           "asked": _asked(fold, path, h)}
    if view == "image":
        mime = image_mime(raw)
        if mime is None:
            raise AppError(NO_IMAGE, "not_an_image", 415)
        return dict(out, text="", binary=True, truncated=False, size=len(raw),
                    image="data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")))
    if view != "diff":
        text, binary, cut = _text(raw)
        return dict(out, text=text, binary=binary, truncated=cut, size=len(raw))
    if base is not None:
        _file_or_refuse(rd, base, path, absent_ok=True)
        if pinned:
            old = rd.blob(base, path) or b""
            if b"\0" in old[:BINARY_PROBE] or b"\0" in raw[:BINARY_PROBE]:
                return dict(out, text="", binary=True, truncated=False, size=len(raw))
            text = "diff --git a/{0} b/{0}\n".format(path) + "".join(difflib.unified_diff(
                old.decode("utf-8", "replace").splitlines(True), raw.decode("utf-8", "replace").splitlines(True),
                "a/" + path, "b/" + path))
        else:
            _file_or_refuse(rd, ref, path, absent_ok=True)
            got = rd.diff(base, ref, path)
            if got is None:
                raise AppError(NO_DIFF, "no_diff", 404)
            text = got
    else:
        if b"\0" in raw[:BINARY_PROBE]:
            return dict(out, text="", binary=True, truncated=False, size=len(raw))
        text = _added_diff(path, raw)
    s, cut = shown_text(text)
    return dict(out, text=s, binary=False, truncated=cut, size=len(text))
