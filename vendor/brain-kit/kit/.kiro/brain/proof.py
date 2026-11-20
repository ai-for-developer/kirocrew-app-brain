#!/usr/bin/env python3
"""brain-kit: verification evidence (证据) that AI-DLC Lean's own `lean verify` takes. Installed by brain-kit as
.kiro/brain/proof.py; AI-DLC Lean is unchanged.

A person puts it in aidlc-docs/lean.json `checks` (the agent cannot change that file), so every `lean verify` runs it:
  python3 .kiro/brain/proof.py run <set> -- <command...>
        wraps a check (the tests): keeps its whole output and the files it writes into $BRAIN_PROOF_DIR (a driver of
        the project's own, e2e screenshots). Exits with the command's status, whatever happened to the evidence.
  python3 .kiro/brain/proof.py shots [--set ui] [--strict]
        starts the app (.brain/kit.json `proof`), drives headless Chrome through each row of the plans this clone
        shoots (construction/<unit>/proof-plan.md), keeps one screenshot per row, stops the app. Exits 0 whatever it
        saw (--strict: 1 when a row is not ok, so Lean's own gates refuse the commit and the release).
The agent and people also run:
  python3 .kiro/brain/proof.py look "<steps>" [--no-launch] [--out DIR]
        the same app and driver for your own eyes: one screenshot in a temp folder outside the repository, and the
        page's visible text. Never evidence: it writes nothing in the repository.
  python3 .kiro/brain/proof.py doctor
        read-only: whether Python reads output as UTF-8 (the seal needs it), Chrome, kit.json proof, the tool that
        checks the listener (`brain doctor` shows these lines).

Exposure, said once: shots and look start the project's dev server from kit.json proof.launch for the length of the
run, and Chrome over --remote-debugging-pipe (no debugging port). proof.url must be loopback; a launch command naming
a wildcard host is refused; after start the listener must be loopback only (lsof or ss; neither: nothing is shot),
else the server is stopped and nothing is shot. A port already answering before launch is refused: it may be an old
server serving old code. Plan rows can only browse proof.url: commands come from people (lean.json, proof.launch).

A set is one check's evidence from one verify, in one folder:
  aidlc-docs/<intent>/construction/build-and-test/proof/<clone-id>/<set>/
      output.txt     exactly what the check printed: what it saw, then one last line `brain-proof: {json}`, the
                     manifest
      <files>        the files the manifest lists, each with its sha256

The seal. Lean's cmd_verify hashes each check's whole stdout+stderr, as subprocess(text=True) decoded it, into its
VERIFY record (data.results[].output_sha256), in the ledger the agent cannot write (aidlc-docs/audit/). This tool
prints nothing on stderr and prints exactly the bytes of output.txt, already in the form Lean reads them (UTF-8, `\\n`
only), so sha256(output.txt) IS that output_sha256: the set is sealed by that VERIFY. A set no VERIFY holds (run by
hand, edited afterwards, cut short by a timeout) is not evidence, and the readers say so. Lean decodes UTF-8 only
where that is Python's preferred encoding: elsewhere no set with non-ASCII text is sealed, and doctor says so.

The code. The manifest's `code` is HEAD's commit and the blob of every code file that differs from it (Lean's record
paths left out), taken when the check starts; it is inside output.txt, so the seal covers it. The same is taken again
when the check ends: if the two differ, the manifest says so and no item of the set counts as passed. Readers
(brainkit.proof) judge an item current for the code under review when every code path that differs between the two
holds the same blob in both. Nothing here writes or trusts a time.

Why under aidlc-docs/: Lean counts it as records, so writing evidence leaves the code fingerprint as it is, a commit of
records alone needs no new verify, and it is not one of the files only Lean writes (evidence/,
build-and-test-summary.md). Why per clone: split children share an intent folder and a runner workspace is a new
clone: each writer has its own folder, as Lean's ledger has one file per clone, and the readers merge.

Stdlib only, and no brainkit import (as review_turn.py): Lean runs this file from the project (on the team runner the
copy in the hand-over commit: .kiro/brain/ is a kit path), and brainkit loads the same file from its own kit/ to read
what it wrote. This one file owns the set format (written AND read here), the seal predicate, the code state and
where sets live. Lean's own code is imported for the active intent, the clone id, the record paths and mask(): its
rules, executed.
"""
from __future__ import annotations

import base64
import enum
import hashlib
import json
import locale
import os
import re
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:          # not POSIX: no shots or look (Windows is out of scope)
    fcntl = None

SCHEMA = 1
MARK = "brain-proof: "                        # the manifest line: the last line of output.txt
DOCS = "aidlc-docs"
PROOF_DIR = "construction/build-and-test/proof"         # under aidlc-docs/<intent>/
OUTPUT = "output.txt"
SCRIPT = ".kiro/brain/proof.py"
LEAN_DIR = ".kiro/aidlc-lean"
SET_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
CLONE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")             # Lean's UNIT_NAME_RE
AC_ID_RE = re.compile(r"(?<![A-Za-z0-9-])((?:AC|BR)-[1-9][0-9]{0,3})(?![0-9])")    # as brainkit.mrdesc.AC_ID_RE
AC_PREFIX_RE = re.compile(r"^(?:(?:AC|BR)-[1-9][0-9]{0,3}[-_,.])+")    # AC-3-refund.png shows AC-3
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._-]{0,99}$")  # no '/', no '..'
OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")                # a git object id (sha1 or sha256 repos)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROOF_CHECK_RE = re.compile(r"(?:^|[\s'\"])(?:\./)?\.kiro/brain/proof\.py\s+(run|shots)(?=\s|$)")
OUTPUT_MAX = 2 * 1024 * 1024                  # characters of a wrapped command's output kept (its end)
READ_MAX = 64 * 1024 * 1024                   # bytes of it read at all (its end)
FILE_MAX = 4 * 1024 * 1024                    # bytes of one item file: the KiroCrew app's MAX_RAW
FILES_MAX = 50                                # files kept from $BRAIN_PROOF_DIR
DIRTY_MAX = 500                               # uncommitted code files a code state lists; more: it cannot say
PROBLEMS_MAX = 10
MOVED = "拍的过程中代码变了"
# The agent's plan, one per Lean unit (`app` when the intent has no unit-of-work.md), next to the unit's designs: split
# children share the intent folder, so one plan file would be a file several writers merge.
PLAN_DOC = "construction/%s/proof-plan.md"
PLAN_RE = re.compile(r"^%s/([^/]+)/construction/([^/]+)/proof-plan\.md$" % DOCS)
RESERVED_UNITS = ("plans", "build-and-test")  # Lean's: folders under construction/ that are not units
STEP_WORDS = ("open", "click", "type", "wait")          # the skill lists the same words: a test keeps them equal
ROW_ID_RE = re.compile(r"^P-[1-9][0-9]{0,2}$")
ROWS_MAX = 20                                 # plan rows shot per verify, all units together
NO_AC = ("", "-", "—", "–", "无")
STEP_SPLIT_RE = re.compile(r"\s+·\s+|\s*;\s*")


class Result(str, enum.Enum):
    OK = "ok"                # it went through: for a plan row, the shot is the state the row names
    FAILED = "failed"        # it did not (a wait timed out, the command failed, the code changed meanwhile)
    SKIPPED = "skipped"      # not taken: no Chrome, the app did not start, the port was busy, out of time


class Kind(str, enum.Enum):
    SHOTS = "shots"
    RUN = "run"


class SetError(ValueError):
    """output.txt that does not end in a manifest this version reads. `newer`: the schema it names, when that is a
    newer one (upgrade brain-kit). The readers show it as 看不懂, never as evidence."""

    def __init__(self, why: str, newer: Optional[int] = None) -> None:
        super().__init__(why)
        self.newer = newer


class SetWriteError(RuntimeError):
    pass


# ---------------------------------------------------------------- the plan (agent-written, data only)

class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    """One step of a row: open <path> | click <text> | type <field> = <text> | wait <text>."""
    word: str
    arg: str
    value: str = ""

    def text(self) -> str:
        return "%s %s = %s" % (self.word, self.arg, self.value) if self.word == "type" else "%s %s" % (self.word,
                                                                                                    self.arg)


@dataclass(frozen=True)
class PlanRow:
    """| ID | AC | 看什么 | 步骤 |: what a reviewer should see, and how to reach it. The shot is taken after the last
    step."""
    id: str                       # P-1
    ac: Tuple[str, ...]           # AC-2, BR-1; () for a row that proves no single AC
    what: str
    steps: Tuple[Step, ...]       # starts with open
    line: int


@dataclass(frozen=True)
class Plan:
    unit: str
    path: str                     # repo path of the unit's proof-plan.md
    sha256: Optional[str]         # of its bytes; None when there is none
    rows: Tuple[PlanRow, ...]
    problems: Tuple[str, ...]     # rows that could not be read, said instead of dropped


def plan_path(intent: str, unit: str) -> str:
    return "%s/%s/%s" % (DOCS, intent, PLAN_DOC % unit)


def plan_of(path: str) -> Optional[Tuple[str, str]]:
    """(intent, unit) when a repo path is a unit's proof-plan.md, else None."""
    m = PLAN_RE.match(path or "")
    if not m or not UNIT_RE.match(m.group(2)) or m.group(2) in RESERVED_UNITS:
        return None
    return m.group(1), m.group(2)


def parse_steps(cell: str) -> Tuple[Step, ...]:
    """`open /orders/123 · click 退款 · wait 已退款` (or `;` between steps). open takes a path of the app, starting
    with '/', never a full URL; the first step is an open. Raises PlanError naming the step it cannot read."""
    steps = []
    for part in (p.strip().strip("`").strip() for p in STEP_SPLIT_RE.split(cell.strip())):
        if not part:
            continue
        word, _, rest = part.partition(" ")
        rest = rest.strip()
        if word not in STEP_WORDS:
            raise PlanError("步骤看不懂（%s）：只能用 %s" % (part, "、".join(STEP_WORDS)))
        if word == "open" and (not rest.startswith("/") or rest.startswith("//") or any(c.isspace() for c in rest)):
            raise PlanError("open 后面要写程序里的路径，以 / 开头（%s）" % part)
        if word == "type":
            field, eq, value = rest.partition(" = ")
            if not eq or not field.strip() or not value.strip():
                raise PlanError("type 要写成 type <输入框> = <内容>（%s）" % part)
            steps.append(Step(word, field.strip(), value.strip()))
            continue
        if not rest:
            raise PlanError("%s 后面要写字（%s）" % (word, part))
        steps.append(Step(word, rest))
    if not steps:
        raise PlanError("没有步骤")
    if steps[0].word != "open":
        raise PlanError("第一步要是 open <路径>")
    return tuple(steps)


def _cells(line: str) -> List[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def parse_plan(raw: Optional[bytes], unit: str, path: str) -> Plan:
    """The first Markdown table (outside fenced blocks) whose header has ID, AC, 看什么 and 步骤. A row whose ID,
    AC cell or steps cannot be read, or whose id repeats an earlier row's, is left out and named in problems."""
    if raw is None:
        return Plan(unit, path, None, (), ())
    text = raw.decode("utf-8", "replace")
    rows, problems, seen = [], [], set()
    cols, fence = None, False                 # cols: None outside a table, () inside a table that is not the plan
    for n, line in enumerate(text.split("\n"), 1):
        in_table = not fence and line.strip().startswith("|")
        if cols and not in_table:
            break                             # the plan's table ended
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
        if not in_table:
            cols = None
            continue
        cells = _cells(line)
        if cols is None:
            head = [c.strip("`* ").lower() for c in cells]
            want = ("id", "ac", "看什么", "步骤")
            cols = tuple(head.index(w) for w in want) if all(w in head for w in want) else ()
            continue
        if not cols or all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
            continue
        cells += [""] * (max(cols) + 1 - len(cells))
        rid, ac_cell, what, step_cell = (cells[i] for i in cols)
        rid = rid.strip("`* ")
        where = "%s 第 %d 行：" % (path, n)
        try:
            if not ROW_ID_RE.match(rid):
                raise PlanError("ID 要写成 P-1、P-2（%s）" % (rid or "空"))
            if rid in seen:
                raise PlanError("%s 重复了" % rid)
            acs = tuple(AC_ID_RE.findall(ac_cell))
            if not acs and ac_cell.strip() not in NO_AC:
                raise PlanError("AC 要写 AC-1、BR-2 这样的号，或者 —（%s）" % ac_cell)
            if not what.strip():
                raise PlanError("看什么 是空的")
            steps = parse_steps(step_cell)
        except PlanError as e:
            problems.append(where + str(e))
            continue
        seen.add(rid)
        rows.append(PlanRow(rid, acs, " ".join(what.split()), steps, n))
    return Plan(unit, path, hashlib.sha256(raw).hexdigest(), tuple(rows), tuple(problems))


# ---------------------------------------------------------------- the set (written AND read here)

@dataclass(frozen=True)
class Page:
    path: str                     # where the browser ended up (a redirect to /login shows here)
    title: str


@dataclass(frozen=True)
class Item:
    unit: str                     # the plan's unit; "" for a file from $BRAIN_PROOF_DIR
    id: str                       # P-1 (unique within its unit); for a file from $BRAIN_PROOF_DIR, its name
    ac: Tuple[str, ...]           # a row's AC cell; for a file, the AC ids its name starts with (AC-3-refund.png)
    what: str
    steps: Tuple[str, ...]        # as run
    file: Optional[str]           # file name inside the set folder; None when nothing was taken
    sha256: Optional[str]         # of that file's bytes
    size: int
    result: Result
    note: str                     # why not ok, one line
    secs: float
    page: Optional[Page]


@dataclass(frozen=True)
class CodeState:
    """The code, as git names it without running anything of the project: HEAD's commit (None before the first
    commit) and the blob id of every code file that differs from it (None: deleted), sorted by path. dirty None: git
    could not say, or more than DIRTY_MAX files differ; such a state is compared with nothing."""
    commit: Optional[str]
    dirty: Optional[Tuple[Tuple[str, Optional[str]], ...]]

    def to_json(self) -> dict:
        return {"commit": self.commit, "dirty": None if self.dirty is None else dict(self.dirty)}


@dataclass(frozen=True)
class ProofSet:
    """The manifest of one set. No fingerprint and no time: the VERIFY that seals it has them."""
    schema: int
    name: str                     # ui, tests
    kind: Kind
    intent: str
    clone: str                    # Lean's clone id: also the folder's name
    exit: int                     # run: the command's; shots: this tool's
    code: CodeState               # when the check started
    items: Tuple[Item, ...]
    plans: Tuple[Tuple[str, str, Optional[str]], ...]   # (unit, path, sha256) of each plan as shots read it
    env: str                      # 「Chrome 157 · 1280×800」, or what was missing
    problems: Tuple[str, ...]     # what the capture itself said


def fold_text(raw: bytes) -> str:
    """Bytes as Lean's subprocess(text=True, errors="replace") reads them: UTF-8 with replacements, \\r\\n and a lone
    \\r folded to \\n. Text in this form prints and reads back unchanged."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _clean(text: str) -> str:
    """No lone surrogates (a file name git gave as bytes): they cannot be written as UTF-8."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _manifest(s: ProofSet) -> dict:
    return {"v": s.schema, "set": s.name, "kind": s.kind.value, "intent": s.intent, "clone": s.clone,
            "exit": s.exit, "code": s.code.to_json(), "env": s.env, "problems": list(s.problems),
            "plans": [list(p) for p in s.plans],
            "items": [{"unit": i.unit, "id": i.id, "ac": list(i.ac), "what": i.what, "steps": list(i.steps),
                       "file": i.file, "sha256": i.sha256, "size": i.size, "result": i.result.value,
                       "note": i.note, "secs": i.secs,
                       "page": None if i.page is None else {"path": i.page.path, "title": i.page.title}}
                      for i in s.items]}


def render_set(body: str, s: ProofSet) -> str:
    """output.txt: body (folded and masked), then MARK and the manifest as one JSON line. Deterministic."""
    if body and not body.endswith("\n"):
        body += "\n"
    return _clean(body + MARK + json.dumps(_manifest(s), ensure_ascii=False, sort_keys=True) + "\n")


def _str(d: Mapping, k: str, rx=None, empty=True) -> str:
    v = d.get(k)
    if not isinstance(v, str) or (not empty and not v) or (rx is not None and not rx.match(v)):
        raise SetError("%s 不对" % k)
    return v


def _int(d: Mapping, k: str) -> int:
    v = d.get(k)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SetError("%s 不对" % k)
    return v


def _strs(d: Mapping, k: str) -> Tuple[str, ...]:
    v = d.get(k)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SetError("%s 不对" % k)
    return tuple(v)


def safe_path(p) -> bool:
    """A repo-relative path a reader may look up: no absolute path, no '..', no backslash, no NUL."""
    return isinstance(p, str) and bool(p) and not p.startswith("/") and "\\" not in p and "\0" not in p and \
        ".." not in p.split("/")


def _code(v) -> CodeState:
    if not isinstance(v, dict):
        raise SetError("code 不对")
    commit, dirty = v.get("commit"), v.get("dirty")
    if commit is not None and not (isinstance(commit, str) and OID_RE.match(commit)):
        raise SetError("code.commit 不对")
    if dirty is None:
        return CodeState(commit, None)
    if not isinstance(dirty, dict) or not all(safe_path(p) and (b is None or (isinstance(b, str) and OID_RE.match(b)))
                                              for p, b in dirty.items()):
        raise SetError("code.dirty 不对")
    return CodeState(commit, tuple(sorted(dirty.items())))


def _item(v) -> Item:
    if not isinstance(v, dict):
        raise SetError("items 不对")
    f, sha = v.get("file"), v.get("sha256")
    if (f is None) != (sha is None) or (f is not None and not (isinstance(f, str) and FILE_NAME_RE.match(f)
                                                                and f != OUTPUT and isinstance(sha, str)
                                                                and SHA256_RE.match(sha))):
        raise SetError("items.file 不对")
    try:
        result = Result(v.get("result"))
    except ValueError:
        raise SetError("items.result 不对")
    page = v.get("page")
    if page is not None and not (isinstance(page, dict) and isinstance(page.get("path"), str)
                                 and isinstance(page.get("title"), str)):
        raise SetError("items.page 不对")
    secs = v.get("secs")
    if not isinstance(secs, (int, float)) or isinstance(secs, bool):
        raise SetError("items.secs 不对")
    unit = _str(v, "unit")
    if unit and not UNIT_RE.match(unit):
        raise SetError("items.unit 不对")
    ac = _strs(v, "ac")
    if not all(AC_ID_RE.fullmatch(a) for a in ac):
        raise SetError("items.ac 不对")
    return Item(unit=unit, id=_str(v, "id", empty=False), ac=ac, what=_str(v, "what"), steps=_strs(v, "steps"),
                file=f, sha256=sha, size=_int(v, "size"), result=result, note=_str(v, "note"), secs=float(secs),
                page=None if page is None else Page(page["path"], page["title"]))


def parse_set(raw: bytes) -> ProofSet:
    """The manifest of an output.txt: its last line. Raises SetError."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SetError("不是 UTF-8")
    if not text.endswith("\n"):
        raise SetError("最后一行没有换行：没写完")
    last = text[:-1].rsplit("\n", 1)[-1]
    if not last.startswith(MARK):
        raise SetError("最后一行不是 %s" % MARK.strip())
    try:
        m = json.loads(last[len(MARK):])
    except ValueError:
        raise SetError("最后一行不是 JSON")
    if not isinstance(m, dict):
        raise SetError("最后一行不是 JSON 对象")
    v = _int(m, "v")
    if v > SCHEMA:
        raise SetError("证据格式 v%d" % v, newer=v)
    if v != SCHEMA:
        raise SetError("证据格式 v%d 不对" % v)
    try:
        kind = Kind(m.get("kind"))
    except ValueError:
        raise SetError("kind 不对")
    plans = m.get("plans")
    if not isinstance(plans, list) or not all(
            isinstance(p, list) and len(p) == 3 and isinstance(p[0], str) and safe_path(p[1])
            and (p[2] is None or (isinstance(p[2], str) and SHA256_RE.match(p[2]))) for p in plans):
        raise SetError("plans 不对")
    items = m.get("items")
    if not isinstance(items, list):
        raise SetError("items 不对")
    return ProofSet(schema=v, name=_str(m, "set", SET_NAME_RE), kind=kind, intent=_str(m, "intent", empty=False),
                    clone=_str(m, "clone", CLONE_RE), exit=_int(m, "exit"), code=_code(m.get("code")),
                    items=tuple(_item(x) for x in items), plans=tuple(tuple(p) for p in plans),
                    env=_str(m, "env"), problems=_strs(m, "problems"))


def output_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def is_proof_check(cmd) -> bool:
    """A lean.json check (or one a VERIFY record names) that runs this file's run or shots."""
    return isinstance(cmd, str) and PROOF_CHECK_RE.search(cmd) is not None


def check_label(cmd) -> Optional[str]:
    """How a page names a proof check: 「截图」 for shots, 「命令输出 tests：pytest -q」 for run; None for another check."""
    m = PROOF_CHECK_RE.search(cmd) if isinstance(cmd, str) else None
    if not m:
        return None
    if m.group(1) == "shots":
        return "截图"
    rest = cmd[m.end():].split()
    name = rest[0] if rest else "?"
    wrapped = " ".join(rest[rest.index("--") + 1:]) if "--" in rest else ""
    return "命令输出 %s%s" % (name, "：" + wrapped if wrapped else "")


def sealing_verify(raw: bytes, verifies: Sequence[Mapping]) -> Optional[Mapping]:
    """The newest VERIFY record (Lean ledger records, oldest first) in which a proof check's output_sha256 is
    sha256(raw), or None. Content-addressed: nothing in the manifest is trusted to find it. Two verifies that printed
    the same bytes both hold it; the newest wins."""
    h = output_sha256(raw)
    for e in reversed(verifies):
        d = e.get("data") if isinstance(e, Mapping) else None
        res = d.get("results") if isinstance(d, Mapping) else None
        if e.get("event") == "VERIFY" and isinstance(res, list) and any(
                isinstance(r, Mapping) and r.get("output_sha256") == h and is_proof_check(r.get("cmd")) for r in res):
            return e
    return None


def set_path(intent: str, clone: str, name: str) -> str:
    """aidlc-docs/<intent>/construction/build-and-test/proof/<clone>/<name>: the one place the layout is spelled."""
    return "%s/%s/%s/%s/%s" % (DOCS, intent, PROOF_DIR, clone, name)


SET_OUTPUT_RE = re.compile(r"^%s/([^/]+)/%s/([^/]+)/([^/]+)/%s$" % (DOCS, re.escape(PROOF_DIR), re.escape(OUTPUT)))


def set_output_of(path: str) -> Optional[Tuple[str, str, str]]:
    """(intent, clone, set) when a repo path is a set's output.txt, else None (a .new or .old folder is not a set)."""
    m = SET_OUTPUT_RE.match(path or "")
    if not m or not CLONE_RE.match(m.group(2)) or not SET_NAME_RE.match(m.group(3)):
        return None
    return m.group(1), m.group(2), m.group(3)


# ---------------------------------------------------------------- the code a capture is of (git only)

def _git(root: str, args: Sequence[str], data: Optional[bytes] = None, timeout: float = 60) -> Optional[bytes]:
    kw = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    try:
        p = subprocess.run(["git"] + list(args), cwd=root, capture_output=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _oid(out: Optional[bytes]) -> Optional[str]:
    s = (out or b"").decode("ascii", "replace").strip()
    return s if OID_RE.match(s) else None


def code_state(root: str, is_record: Callable[[str], bool]) -> CodeState:
    """The code of the working tree at `root` now. Paths come from `git status` (staged, unstaged and untracked
    together: what is on disk is what runs), relative to root; a rename names both its paths. is_record(path) True
    leaves a path out (Lean's records), as does .aidlc-lean/ (per-clone state) and an untracked nested repository.
    Blobs as `git add` would store them, without writing any object: files through `git hash-object` (its eol and
    clean filters apply), a symlink as the hash of its link text (hash-object of the path would follow the link and
    hash the target), a submodule as its HEAD commit (the gitlink a commit stores)."""
    commit = _oid(_git(root, ["rev-parse", "-q", "--verify", "HEAD^{commit}"]))
    out = _git(root, ["--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                      "--ignore-submodules=dirty"])
    prefix = _git(root, ["rev-parse", "--show-prefix"])
    if out is None or prefix is None:
        return CodeState(commit, None)
    prefix = os.fsdecode(prefix.strip())
    found, fields, i = set(), out.split(b"\0"), 0
    while i < len(fields):
        f = fields[i]
        i += 1
        if len(f) < 4:
            continue
        found.add(f[3:])
        if b"R" in f[:2] or b"C" in f[:2]:            # -z: the new path, then the old one
            if i < len(fields) and fields[i]:
                found.add(fields[i])
            i += 1
    paths = []
    for b in found:
        p = os.fsdecode(b)
        if _clean(p) != p:
            return CodeState(commit, None)
        if prefix:
            if not p.startswith(prefix):
                continue                              # outside the project: not its code (Lean's rule)
            p = p[len(prefix):]
        if p and not p.endswith("/") and not p.startswith(".aidlc-lean/") and not is_record(p):
            paths.append(p)
    paths.sort()
    if len(paths) > DIRTY_MAX:
        return CodeState(commit, None)
    blobs, files = {}, []
    for p in paths:
        full = os.path.join(root, p)
        try:
            if os.path.islink(full):
                blobs[p] = _oid(_git(root, ["hash-object", "--stdin"], data=os.fsencode(os.readlink(full))))
            elif os.path.isdir(full):
                blobs[p] = _oid(_git(full, ["rev-parse", "-q", "--verify", "HEAD^{commit}"]))
            elif os.path.isfile(full):
                files.append(p)
                continue
            elif not os.path.lexists(full):
                blobs[p] = None
                continue
            else:
                return CodeState(commit, None)
        except OSError:                               # it changed while we looked: this state cannot be told
            return CodeState(commit, None)
        if blobs[p] is None:
            return CodeState(commit, None)
    if files:
        out = _git(root, ["hash-object", "--"] + files)
        ids = (out or b"").decode("ascii", "replace").split()
        if out is None or len(ids) != len(files) or not all(OID_RE.match(x) for x in ids):
            return CodeState(commit, None)
        blobs.update(zip(files, ids))
    return CodeState(commit, tuple(sorted(blobs.items())))


def moved(a: CodeState, b: CodeState) -> List[str]:
    """What differs between two code states of one working tree (the start and the end of a check), named for a
    person; [] when they are the same."""
    if a == b:
        return []
    out = ["提交 %s → %s" % ((a.commit or "无")[:8], (b.commit or "无")[:8])] if a.commit != b.commit else []
    if a.dirty is None or b.dirty is None:
        return out + ["没提交的代码文件（太多，或者 git 没说清）"]
    da, db = dict(a.dirty), dict(b.dirty)
    return out + sorted(p for p in set(da) | set(db) if p not in da or p not in db or da[p] != db[p])


def _ids(items: Sequence[str], limit: int = 3) -> str:
    items = list(items)
    return "、".join(items[:limit]) + ("等 %d 个" % len(items) if len(items) > limit else "")


# ---------------------------------------------------------------- Lean, executed (never copied)

def project_root() -> str:
    """The project this file is installed in (<root>/.kiro/brain/proof.py next to Lean), else the working folder."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.isfile(os.path.join(here, LEAN_DIR, "aidlc_lean.py")):
        return here
    return os.getcwd()


def lean(root: str):
    """The project's own aidlc_lean module, or None. No __pycache__ is written into .kiro/aidlc-lean/ (a file the
    project's HEAD lacks under a kit path)."""
    path = os.path.join(root, LEAN_DIR)
    if not os.path.isfile(os.path.join(path, "aidlc_lean.py")):
        return None
    sys.dont_write_bytecode = True
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import aidlc_lean
    except Exception:  # noqa: BLE001  a project's Lean that does not import keeps no evidence
        return None
    return aidlc_lean


@dataclass(frozen=True)
class Target:
    """Where this verify's evidence goes, the way `lean verify` files its records."""
    root: str
    intent: str
    clone: str
    mask: Callable[[str], str]
    is_record: Callable[[str], bool]
    units: Tuple[str, ...]        # the units whose plans this clone shoots (Lean's units, `app` without them)


def target(root: str) -> Tuple[Optional[Target], str]:
    """(target, "") or (None, why no evidence is kept): no Lean, no active intent, or a released one (Lean leaves a
    released intent's records as they are)."""
    L = lean(root)
    if L is None:
        return None, "这里没有能用的 AI-DLC Lean（.kiro/aidlc-lean/）"
    try:
        pj = L.Project(root)
        intent = pj.active()
        if not intent:
            return None, "Lean 没有进行中的 intent"
        if pj.released(intent):
            return None, "intent %s 已经发布" % intent
        clone = pj.clone_id()
        names, info, _ = pj.units(intent)
        key = getattr(L, "team_key", lambda x: str(x or ""))
        team = key(pj.team() or "")
        # a split child's Lean team is its unit (brain split): it never shoots another unit's rows on code it lacks
        mine = [n for n in names if team and team in (key(n), key((info.get(n) or {}).get("owner", "")))]
    except (Exception, SystemExit) as e:  # noqa: BLE001  lean.json Lean cannot read: no evidence, the check runs
        return None, "Lean 读不了这个项目（%s）" % e
    if not CLONE_RE.match(clone or ""):
        return None, "Lean 的 clone id 不能做文件夹名（%r）" % clone
    return Target(root, intent, clone, L.mask, lambda p: pj.is_record_path(p),
                  tuple(u for u in (mine or names) if UNIT_RE.match(u))), ""


# ---------------------------------------------------------------- writing a set (idempotent)

def _remove(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def begin_set(t: Target, name: str) -> str:
    """A fresh, empty <set>.new/ to build the set in; a .new or .old left by a killed run is removed first. Refuses
    (SetWriteError) when the folder would not be inside this project's aidlc-docs/ (a symlink on the way)."""
    final = os.path.join(t.root, set_path(t.intent, t.clone, name))
    parent = os.path.dirname(final)
    docs = os.path.realpath(os.path.join(t.root, DOCS))

    def inside(p: str) -> None:
        real = os.path.realpath(p)
        if real != docs and not real.startswith(docs + os.sep):
            raise SetWriteError("%s 不在这个项目的 %s/ 里（路上有符号链接）" % (os.path.relpath(parent, t.root), DOCS))

    have = parent
    while not os.path.isdir(have) and os.path.dirname(have) != have:
        have = os.path.dirname(have)
    inside(have)                                   # before anything is made: makedirs follows links
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        raise SetWriteError("建不了 %s（%s）" % (os.path.relpath(parent, t.root), e.strerror or e))
    inside(parent)
    for leftover in (final + ".new", final + ".old"):
        _remove(leftover)
    os.mkdir(final + ".new")
    return final + ".new"


def finish_set(t: Target, name: str, new: str, body: str, s: ProofSet) -> str:
    """Write output.txt last, then swap <set>.new/ into place (<set> -> .old, .new -> <set>, .old removed). Returns
    output.txt's text: the caller prints exactly it."""
    text = render_set(body, s)
    with open(os.path.join(new, OUTPUT), "wb") as f:
        f.write(text.encode("utf-8"))
    final = new[:-len(".new")]
    if os.path.lexists(final):
        os.rename(final, final + ".old")
    os.rename(new, final)
    _remove(final + ".old")
    return text


def emit(text: str) -> None:
    """The only write to stdout: text as UTF-8 bytes, so the locale never changes them."""
    sys.stdout.flush()
    sys.stdout.buffer.write(_clean(text).encode("utf-8"))
    sys.stdout.flush()


# ---------------------------------------------------------------- run: wrap a check

def run_command(argv: Sequence[str], root: str, out_dir: Optional[str]) -> Tuple[bytes, int, int]:
    """(its output, stderr merged into stdout, at most the last READ_MAX bytes; its exit status; bytes left out).
    No shell: lean.json's line already went through one. BRAIN_PROOF_DIR is out_dir (unset without one)."""
    env = dict(os.environ)
    env.pop("BRAIN_PROOF_DIR", None)
    if out_dir:
        env["BRAIN_PROOF_DIR"] = out_dir
    try:
        p = subprocess.Popen(list(argv), cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    except OSError as e:
        return ("没能运行 %s：%s\n" % (argv[0], e.strerror or e)).encode("utf-8"), 127, 0
    buf, cut = bytearray(), 0
    while True:
        chunk = p.stdout.read(1 << 16)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 2 * READ_MAX:
            cut += len(buf) - READ_MAX
            del buf[:len(buf) - READ_MAX]
    p.stdout.close()
    return bytes(buf), p.wait(), cut


def shown_output(raw: bytes, cut: int, mask: Callable[[str], str]) -> str:
    """The command's output as output.txt keeps it: folded, masked (before cutting: a key block cut in half is still
    masked), at most its last OUTPUT_MAX characters from a line start, with a first line saying what was left out."""
    text = mask(fold_text(raw))
    if len(text) > OUTPUT_MAX:
        tail = text[-OUTPUT_MAX:]
        tail = tail[tail.find("\n") + 1:] if "\n" in tail else tail
        cut += len(text[:len(text) - len(tail)].encode("utf-8"))
        text = tail
    if cut:
        text = "（输出太长，前面 %.1f MB 没留，只留最后这些）\n%s" % (cut / 1048576.0, text)
    return text


def take_files(src: str, dst: str, exit_code: int) -> Tuple[List[Item], List[str]]:
    """The files a wrapped command left in src ($BRAIN_PROOF_DIR, any depth, '/' read as '-'), moved into the set
    folder dst as items; src is removed. What is not kept is named in the problems."""
    items, problems, names = [], [], set()
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if not os.path.islink(os.path.join(d, x)))
        for f in sorted(files):
            full = os.path.join(d, f)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            name = rel.replace("/", "-")
            why = None
            if os.path.islink(full) or not os.path.isfile(full):
                why = "不是普通文件"
            elif not FILE_NAME_RE.match(name) or name == OUTPUT:
                why = "文件名不能用（只能是字母、数字、汉字和 . _ -）"
            elif name in names:
                why = "和另一个文件重名"
            elif os.path.getsize(full) > FILE_MAX:
                why = "超过 4 MB"
            elif len(items) >= FILES_MAX:
                why = "超过 %d 个文件" % FILES_MAX
            if why:
                problems.append("$BRAIN_PROOF_DIR 里的 %s 没留：%s" % (rel, why))
                continue
            with open(full, "rb") as fh:
                data = fh.read()
            os.replace(full, os.path.join(dst, name))
            names.add(name)
            ok = exit_code == 0
            items.append(Item(unit="", id=name, ac=tuple(AC_ID_RE.findall((AC_PREFIX_RE.match(name) or [""])[0])),
                              what="", steps=(), file=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                              result=Result.OK if ok else Result.FAILED, note="" if ok else "命令 exit %d" % exit_code,
                              secs=0.0, page=None))
    shutil.rmtree(src, ignore_errors=True)
    return items, problems


def no_better_than_failed(items: Sequence[Item], why: str) -> Tuple[Item, ...]:
    """The items of a capture during which the code changed: nothing in it counts as passed."""
    out = []
    for i in items:
        if i.result is Result.SKIPPED:
            out.append(i)
            continue
        out.append(Item(i.unit, i.id, i.ac, i.what, i.steps, i.file, i.sha256, i.size, Result.FAILED,
                        why + ("；" + i.note if i.note else ""), i.secs, i.page))
    return tuple(out)


def cap(problems: Sequence[str]) -> Tuple[str, ...]:
    problems = list(problems)
    if len(problems) > PROBLEMS_MAX:
        problems = problems[:PROBLEMS_MAX - 1] + ["另有 %d 条" % (len(problems) - PROBLEMS_MAX + 1)]
    return tuple(problems)


RUN_USAGE = "usage: python3 %s run <set> -- <command...>   (set: a-z, 0-9 and -, starting with a letter)\n" % SCRIPT


def cmd_run(args: Sequence[str]) -> int:
    """run <set> -- <command...>: exit = the command's, whatever happened to the evidence."""
    args = list(args)
    if len(args) < 3 or args[1] != "--" or not SET_NAME_RE.match(args[0]):
        emit(RUN_USAGE)
        return 2
    name, argv = args[0], args[2:]
    root = project_root()
    t, why = target(root)
    new = None
    if t is not None:
        try:
            new = begin_set(t, name)
            os.mkdir(os.path.join(new, "in"))
        except (OSError, SetWriteError) as e:
            why = "证据没写成：%s" % e
            new = None
    start = code_state(root, t.is_record) if new else None
    raw, code, cut = run_command(argv, root, os.path.join(new, "in") if new else None)
    mask = t.mask if t else (lambda x: x)
    try:
        body = shown_output(raw, cut, mask)
    except Exception:  # noqa: BLE001  never let the evidence change the check's own result
        body = fold_text(raw)
    tail = "\n" if body and not body.endswith("\n") else ""
    if new is None:
        emit(body + tail + "（%s：这次 verify 不留证据）\n" % why)
        return code
    try:
        items, problems = take_files(os.path.join(new, "in"), new, code)
        changed = moved(start, code_state(root, t.is_record))
        if changed:
            items = list(no_better_than_failed(items, MOVED))
            problems.append(mask("%s（%s）：这次的证据都不算通过" % (MOVED, _ids(changed))))
        s = ProofSet(schema=SCHEMA, name=name, kind=Kind.RUN, intent=t.intent, clone=t.clone, exit=code, code=start,
                     items=tuple(items), plans=(), env="", problems=cap(problems))
        text = finish_set(t, name, new, body, s)
    except Exception as e:  # noqa: BLE001  the check's own result stands; a set not written is not evidence
        text = body + tail + "（证据没写成：%s）\n" % e
    emit(text)
    return code


# ---------------------------------------------------------------- the app and the browser (capture)

LOOPBACK = ("127.0.0.1", "localhost", "::1")
WILDCARD_RE = re.compile(r"(?<![0-9.])0\.0\.0\.0(?![0-9.])|\[::\]|(?<![:\w])::(?![:\w])")
WINDOW = (1280, 800)
WAIT_S = 10                                   # a step waiting for its text or element
LOAD_S = 15                                   # a page load
READY_S = 60                                  # the app answering after launch
STOP_S = 5                                    # SIGTERM, then SIGKILL
CDP_S = 30                                    # one answer from Chrome
BUDGET_S = 300                                # a whole `shots` run: well under Lean's check_timeout_sec (1800)
VISIBLE_LINES = 30
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
CHROME_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium")
CHROME_FLAGS = ("--headless=new", "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
                "--hide-scrollbars", "--force-device-scale-factor=1", "--disable-extensions", "--mute-audio",
                "--disable-background-networking", "--disable-component-update", "--disable-sync",
                "--password-store=basic", "--use-mock-keychain")
NO_CHROME = "没有 Chrome（装 Chrome 或 Chromium，或者设 CHROME）"


@dataclass(frozen=True)
class AppConfig:
    """.brain/kit.json `proof`, read at this boundary (people edit kit.json; the bot may not; frozen on the runner)."""
    url: str                      # http://127.0.0.1:5173: loopback only
    host: str
    port: int
    launch: str                   # the command that serves THIS checkout's code; no wildcard host
    ready: str                    # the path polled until it answers below 500
    size: Tuple[int, int]


def app_config(root: str) -> Tuple[Optional[AppConfig], List[str]]:
    """(config, []) or (None, why it cannot be used): `proof` missing, url not on this machine, a launch command that
    names a wildcard host."""
    try:
        with open(os.path.join(root, ".brain", "kit.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        return None, [".brain/kit.json 读不了（%s）" % e]
    p = cfg.get("proof") if isinstance(cfg, dict) else None
    if not isinstance(p, dict):
        return None, [".brain/kit.json 没有 proof（url 和 launch：verify 怎么启动程序）"]
    url = p.get("url") if isinstance(p.get("url"), str) else ""
    u = urllib.parse.urlsplit(url)
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        port = 0
    why = []
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK or not port or u.path not in ("", "/"):
        why.append("kit.json proof.url 要是本机的地址，比如 http://127.0.0.1:5173（现在是 %r）" % url)
    launch = p.get("launch") if isinstance(p.get("launch"), str) else ""
    if not launch.strip():
        why.append("kit.json proof.launch 没写：verify 要用它启动这份代码的程序")
    elif WILDCARD_RE.search(launch):
        why.append("kit.json proof.launch 让程序监听所有网卡（%s）：改成只听 127.0.0.1" % WILDCARD_RE.search(launch).group(0))
    ready = p.get("ready", "/")
    if not (isinstance(ready, str) and ready.startswith("/")):
        why.append("kit.json proof.ready 要是以 / 开头的路径")
    size = p.get("size", list(WINDOW))
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(n, int) and not isinstance(n, bool)
                                                              and 200 <= n <= 4000 for n in size)):
        why.append("kit.json proof.size 要写成 [宽, 高]，比如 [1280, 800]")
    if why:
        return None, why
    return AppConfig("%s://%s:%d" % (u.scheme, u.hostname if ":" not in u.hostname else "[%s]" % u.hostname, port),
                     u.hostname, port, launch, ready, (size[0], size[1])), []


def _answers(url: str, timeout: float = 2.0) -> Optional[int]:
    """The HTTP status the url gives (an error status counts), or None when nothing answers. No proxy: the app is on
    this machine; a loopback https dev server's own certificate is accepted."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _port_taken(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=0.5).close()
        return True
    except OSError:
        return False


def listeners(port: int) -> Optional[List[str]]:
    """The addresses something listens on at TCP `port` ("127.0.0.1:5173", "*:5173"), from lsof (macOS) or ss
    (Linux); None when neither is here."""
    if shutil.which("lsof"):
        p = subprocess.run(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-Fn"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return [x[1:] for x in p.stdout.splitlines() if x.startswith("n")]
    if shutil.which("ss"):
        p = subprocess.run(["ss", "-ltnH", "sport = :%d" % port], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return [x.split()[3] for x in p.stdout.splitlines() if len(x.split()) >= 4]
    return None


def not_loopback(addresses: Sequence[str]) -> List[str]:
    """The listening addresses other machines could reach: anything but 127.x and ::1 (a wildcard among them)."""
    out = []
    for a in addresses:
        host = a.rsplit(":", 1)[0].strip("[]")
        if not (host.startswith("127.") or host in ("::1", "localhost", "::ffff:127.0.0.1")):
            out.append(a)
    return out


def _started(pid: int) -> Optional[str]:
    """When process pid started, as ps prints it: with the pid, it names one process (a pid alone may be reused)."""
    p = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _stop_group(pgid: int, leader: Optional[subprocess.Popen] = None) -> None:
    """SIGTERM to a process group this tool started, STOP_S seconds, then SIGKILL. Never by name."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.time() + STOP_S
        while time.time() < end:
            if leader is not None and leader.poll() is not None:
                return
            if leader is None and not _alive(pgid):
                return
            time.sleep(0.1)


class AppNotUsable(RuntimeError):
    pass


class RunningApp:
    """The dev server for one capture: started from kit.json proof.launch in its own process group, recorded in the
    clone's git dir (brain-proof/server.json: pid and start time) so a server a killed run left is recognised as
    ours and stopped next time, and stopped on every way out. A port that answers and is not ours is refused (it may
    serve old code); after start the listener must be loopback only, else the server is stopped and nothing is shot.
    launch=False (`look --no-launch`): a server someone started at proof.url; only the listener is checked."""

    def __init__(self, root: str, cfg: AppConfig, launch: bool = True) -> None:
        self.root, self.cfg, self.launch = root, cfg, launch
        self.proc: Optional[subprocess.Popen] = None
        out = _git(root, ["rev-parse", "--git-path", "brain-proof"])
        d = os.fsdecode(out.strip()) if out else os.path.join(".git", "brain-proof")
        self.dir = d if os.path.isabs(d) else os.path.join(root, d)
        self.record, self.log = os.path.join(self.dir, "server.json"), os.path.join(self.dir, "server.log")
        self.lines: List[str] = []

    def __enter__(self) -> "RunningApp":
        try:
            self._start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def _orphan(self) -> None:
        try:
            with open(self.record, encoding="utf-8") as f:
                rec = json.load(f)
            pid, started = int(rec["pid"]), rec["started"]
        except (OSError, ValueError, KeyError, TypeError):
            return
        if _alive(pid) and started and _started(pid) == started:
            _stop_group(pid)
            self.lines.append("停掉了上次留下的程序（pid %d）" % pid)
        try:
            os.unlink(self.record)
        except OSError:
            pass

    def _start(self) -> None:
        c = self.cfg
        if self.launch:
            self._orphan()
            if _port_taken(c.host, c.port):
                raise AppNotUsable("端口 %d 已经有程序在听，不是这次 verify 启动的：不拍（它可能是旧代码）" % c.port)
            os.makedirs(self.dir, exist_ok=True)
            with open(self.log, "wb") as log:
                self.proc = subprocess.Popen(c.launch, shell=True, cwd=self.root, start_new_session=True,
                                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                             env=dict(os.environ, BROWSER="none"))
            with open(self.record, "w", encoding="utf-8") as f:
                json.dump({"pid": self.proc.pid, "started": _started(self.proc.pid), "port": c.port}, f)
        t0 = time.time()
        while _answers(c.url + c.ready) is None:
            if self.proc is not None and self.proc.poll() is not None:
                raise AppNotUsable("程序没起来（exit %s）：%s" % (self.proc.returncode, self.log_tail(5) or "没有输出"))
            if time.time() - t0 > (READY_S if self.launch else 2):
                raise AppNotUsable("%s 在 %d 秒里没答应%s" % (c.url + c.ready, READY_S if self.launch else 2,
                                                       "：" + self.log_tail(5) if self.launch else ""))
            time.sleep(0.2)
        found = listeners(c.port)
        if found is None:
            raise AppNotUsable("查不了程序监听在哪（没有 lsof，也没有 ss）：不拍")
        if not found or not_loopback(found):
            raise AppNotUsable("程序监听在 %s（别的机器也连得上）：不拍。让 proof.launch 只听 127.0.0.1"
                               % ("、".join(not_loopback(found)) if found else "查不到的地址"))
        self.lines.append("程序起来了：%s（%.1f 秒），只在本机监听（%s）" % (c.url, time.time() - t0, "、".join(found)))

    def stop(self) -> None:
        if self.proc is None:
            return
        _stop_group(self.proc.pid, self.proc)
        self.proc.wait()
        left = listeners(self.cfg.port) or []
        self.lines.append("程序停了%s" % ("，可端口 %d 还有程序在听（%s）" % (self.cfg.port, "、".join(left)) if left else ""))
        self.proc = None
        try:
            os.unlink(self.record)
        except OSError:
            pass

    def log_tail(self, lines: int = 20) -> str:
        try:
            with open(self.log, "rb") as f:
                tail = fold_text(f.read()[-8192:]).strip().splitlines()[-lines:]
        except OSError:
            return ""
        return " / ".join(x.strip() for x in tail if x.strip())


def find_chrome() -> Optional[str]:
    """$CHROME when it is set (and only it), else the first of CHROME_NAMES on PATH, else the macOS app."""
    env = os.environ.get("CHROME")
    if env:
        return env if os.path.isfile(env) and os.access(env, os.X_OK) else None
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in CHROME_APPS if os.access(p, os.X_OK)), None)


class ChromeError(RuntimeError):
    """fatal: Chrome stopped answering or went away (the rows after it are not taken). Otherwise Chrome answered
    with an error, often for a moment while a page commits (an error page, a navigation a click started)."""

    def __init__(self, why: str, fatal: bool = True) -> None:
        super().__init__(why)
        self.fatal = fatal


class Chrome:
    """Headless Chrome over --remote-debugging-pipe: it reads commands on fd 3 and writes on fd 4, so no port is
    opened. A throwaway profile, removed after it exits; its own process group, stopped by pid. Two traps, found by
    running it: the first os.pipe() of a process may be fds 3 and 4, and dup2(3, 3) is a no-op that keeps
    close-on-exec, so the pipe ends are moved above 10 before posix_spawn puts them on 3 and 4; Chrome logs to
    stderr, which goes to /dev/null (the seal: this tool prints nothing on stderr). Chrome's own sandbox stays on."""

    def __init__(self, path: str, size: Tuple[int, int]) -> None:
        self.size, self.n, self.buf, self.session = size, 0, b"", None
        self.profile = tempfile.mkdtemp(prefix="brain-proof-chrome-")
        cmd_r, cmd_w = os.pipe()
        evt_r, evt_w = os.pipe()
        hi_r, hi_w = fcntl.fcntl(cmd_r, fcntl.F_DUPFD_CLOEXEC, 10), fcntl.fcntl(evt_w, fcntl.F_DUPFD_CLOEXEC, 10)
        for fd in (cmd_r, evt_w):
            os.close(fd)
        actions = [(os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                   (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                   (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
                   (os.POSIX_SPAWN_DUP2, hi_r, 3), (os.POSIX_SPAWN_DUP2, hi_w, 4)]
        argv = [path] + list(CHROME_FLAGS) + ["--user-data-dir=" + self.profile, "about:blank"]
        try:
            self.pid = os.posix_spawn(path, argv, dict(os.environ), file_actions=actions, setpgroup=0)
        except OSError as e:
            for fd in (cmd_w, evt_r):
                os.close(fd)
            shutil.rmtree(self.profile, ignore_errors=True)
            raise ChromeError("Chrome 没起来（%s）" % (e.strerror or e))
        finally:
            os.close(hi_r)
            os.close(hi_w)
        self.w, self.r = cmd_w, evt_r
        try:
            self.version = re.sub(r"^(?:Headless)?Chrome/", "Chrome ",
                                  str(self.send("Browser.getVersion").get("product") or "Chrome"))
        except ChromeError:
            self.close()
            raise

    def __enter__(self) -> "Chrome":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send(self, method: str, params: Optional[dict] = None, timeout: float = CDP_S) -> dict:
        """One command and its answer (JSON ended by NUL); events in between are skipped, a dialog is accepted."""
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if self.session and not method.startswith(("Target.", "Browser.")):
            msg["sessionId"] = self.session
        try:
            os.write(self.w, json.dumps(msg).encode("utf-8") + b"\0")
        except OSError as e:
            raise ChromeError("Chrome 断开了（%s）" % (e.strerror or e))
        end = time.time() + timeout
        while True:
            while b"\0" not in self.buf:
                left = end - time.time()
                if left <= 0 or not select.select([self.r], [], [], left)[0]:
                    raise ChromeError("Chrome %d 秒没回答（%s）" % (timeout, method))
                chunk = os.read(self.r, 1 << 16)
                if not chunk:
                    raise ChromeError("Chrome 断开了")
                self.buf += chunk
            raw, self.buf = self.buf.split(b"\0", 1)
            got = json.loads(raw.decode("utf-8", "replace"))
            if got.get("method") == "Page.javascriptDialogOpening":
                self.n += 1
                os.write(self.w, json.dumps({"id": self.n, "method": "Page.handleJavaScriptDialog", "params": {
                    "accept": True}, "sessionId": got.get("sessionId")}).encode("utf-8") + b"\0")
            if got.get("id") == msg["id"]:
                if "error" in got:
                    raise ChromeError("%s：%s" % (method, got["error"].get("message")), fatal=False)
                return got.get("result") or {}

    def tab(self) -> "Tab":
        tid = self.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        self.send("Page.enable")
        self.send("Emulation.setDeviceMetricsOverride", {"width": self.size[0], "height": self.size[1],
                                                         "deviceScaleFactor": 1, "mobile": False})
        return Tab(self)

    def close(self) -> None:
        if getattr(self, "pid", None):
            try:
                self.send("Browser.close", timeout=3)
            except ChromeError:
                pass
            end, done = time.time() + STOP_S, False
            while not done and time.time() < end:
                try:
                    done = os.waitpid(self.pid, os.WNOHANG) != (0, 0)
                except ChildProcessError:
                    done = True
                if not done:
                    time.sleep(0.1)
            if not done:
                _stop_group(self.pid)
                try:
                    os.waitpid(self.pid, 0)
                except ChildProcessError:
                    pass
            self.pid = None
        for fd in ("w", "r"):
            if getattr(self, fd, None) is not None:
                os.close(getattr(self, fd))
                setattr(self, fd, None)
        shutil.rmtree(self.profile, ignore_errors=True)


FIND_JS = {
    "click": """(() => { const t = %s;
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const all = [...document.querySelectorAll('button,a,[role=button],[role=link],[role=tab],[role=menuitem],summary,'
    + 'input[type=submit],input[type=button]')].filter(shown);
  const text = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim();
  const el = all.find(e => text(e) === t) || all.find(e => text(e).includes(t));
  if (!el) return false;
  el.scrollIntoView({block: 'center'}); el.click(); return true; })()""",
    "focus": """(() => { const f = %s;
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const label = [...document.querySelectorAll('label')].find(l => l.innerText.trim() === f)
    || [...document.querySelectorAll('label')].find(l => l.innerText.includes(f));
  const fields = [...document.querySelectorAll('input,textarea')].filter(shown);
  const el = (label && (label.control || label.querySelector('input,textarea')))
    || fields.find(e => [e.placeholder, e.name, e.id, e.getAttribute('aria-label')].includes(f))
    || fields.find(e => (e.placeholder || '').includes(f));
  if (!el) return false;
  el.focus(); if (el.select) el.select(); return true; })()""",
    "wait": "!!document.body && document.body.innerText.includes(%s)",
}


class Tab:
    """One page of the app: each plan step, the screenshot, where it ended up."""

    def __init__(self, chrome: Chrome) -> None:
        self.c = chrome

    def _eval(self, js: str):
        """The value of js in the page; None while the page has no context to run it in (it is committing)."""
        try:
            got = self.c.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        except ChromeError as e:
            if e.fatal:
                raise
            return None
        return None if "exceptionDetails" in got else (got.get("result") or {}).get("value")

    def _settled(self, method: str, params: Optional[dict] = None, secs: float = 3) -> dict:
        """A command a page that is still committing may refuse for a moment (Not attached to an active page)."""
        end = time.time() + secs
        while True:
            try:
                return self.c.send(method, params)
            except ChromeError as e:
                if e.fatal or time.time() > end:
                    raise
                time.sleep(0.25)

    def _until(self, js: str, secs: float) -> bool:
        end = time.time() + secs
        while True:
            if self._eval(js):
                return True
            if time.time() > end:
                return False
            time.sleep(0.2)

    def run(self, step: Step, base: str) -> Optional[str]:
        """Do one step; None when it went through, else why not, in plain words (the row's note)."""
        if step.word == "open":
            got = self.c.send("Page.navigate", {"url": base + step.arg})
            if got.get("errorText"):
                return "打不开 %s（%s）" % (step.arg, got["errorText"])
            return None if self._until("document.readyState === 'complete'", LOAD_S) else \
                "%s 在 %d 秒里没打开完" % (step.arg, LOAD_S)
        if step.word == "click":
            return None if self._until(FIND_JS["click"] % json.dumps(step.arg), WAIT_S) else \
                "没找到「%s」（%d 秒）" % (step.arg, WAIT_S)
        if step.word == "type":
            if not self._until(FIND_JS["focus"] % json.dumps(step.arg), WAIT_S):
                return "没找到输入框「%s」（%d 秒）" % (step.arg, WAIT_S)
            self.c.send("Input.insertText", {"text": step.value})
            return None
        return None if self._until(FIND_JS["wait"] % json.dumps(step.arg), WAIT_S) else \
            "没等到「%s」（%d 秒）" % (step.arg, WAIT_S)

    def shot(self) -> bytes:
        """The window as PNG: the first screen, not the whole page."""
        return base64.b64decode(self._settled("Page.captureScreenshot", {"format": "png"})["data"])

    def page(self) -> Page:
        got = self._eval("JSON.stringify([location.pathname + location.search + location.hash, document.title])")
        path, title = json.loads(got) if isinstance(got, str) else ("", "")
        return Page(path, title)

    def visible_text(self, lines: int = VISIBLE_LINES) -> List[str]:
        got = self._eval("document.body ? document.body.innerText : ''") or ""
        return [x.strip() for x in str(got).splitlines() if x.strip()][:lines]


def skipped(unit: str, row: PlanRow, why: str) -> Item:
    return Item(unit, row.id, row.ac, row.what, tuple(s.text() for s in row.steps), None, None, 0, Result.SKIPPED,
                why, 0.0, None)


def shoot(tab: Tab, rows: Sequence[Tuple[str, PlanRow]], base: str, out_dir: str,
          budget_s: float = BUDGET_S) -> Tuple[List[Item], List[str]]:
    """(items, lines): each row's steps from a fresh open, then one screenshot (also after a failed step: the shot is
    what was there), written as <unit>-<id>.png into out_dir. Rows past the budget, and every row after Chrome stops
    answering, are not taken."""
    items, lines, end, broken = [], [], time.time() + budget_s, None
    several = len({u for u, _ in rows}) > 1
    for unit, row in rows:
        if broken or time.time() >= end:
            items.append(skipped(unit, row, broken or "超过 %d 秒，没拍" % budget_s))
            continue
        t0, note = time.time(), None
        try:
            for step in row.steps:
                note = tab.run(step, base)
                if note:
                    break
            png, page = tab.shot(), tab.page()
        except ChromeError as e:
            if e.fatal:
                broken = "浏览器没反应（%s）" % e
            items.append(skipped(unit, row, broken or "%s；截图没拍成（%s）" % (note or "步骤没走完", e)))
            continue
        name = "%s-%s.png" % (unit, row.id)
        if len(png) > FILE_MAX:
            items.append(skipped(unit, row, "截图超过 4 MB，没留"))
            continue
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(png)
        items.append(Item(unit, row.id, row.ac, row.what, tuple(s.text() for s in row.steps), name,
                          hashlib.sha256(png).hexdigest(), len(png), Result.FAILED if note else Result.OK, note or "",
                          round(time.time() - t0, 2), page))
    for i in items:
        lines.append("%-6s %s %s %s" % ({"ok": "ok", "failed": "没看到", "skipped": "没拍"}[i.result.value],
                                         "%s/%s" % (i.unit, i.id) if several else i.id, ",".join(i.ac) or "—",
                                         i.note or i.what))
    return items, lines


def capture(root: str, rows: Sequence[Tuple[str, PlanRow]], out_dir: str) -> Tuple[List[Item], List[str], str,
                                                                                    List[str]]:
    """(items, lines, env, problems) of one `shots` run: every row is either shot or not taken with the reason."""
    cfg, why = app_config(root)
    if cfg is None:
        return [skipped(u, r, why[0]) for u, r in rows], [], "", why
    path = find_chrome()
    if path is None:
        return [skipped(u, r, NO_CHROME) for u, r in rows], [], NO_CHROME, [NO_CHROME]
    app = RunningApp(root, cfg)
    try:
        with app, Chrome(path, cfg.size) as chrome:
            before = len(app.lines)
            items, lines = shoot(chrome.tab(), rows, cfg.url, out_dir)
            env = "%s · %d×%d" % (chrome.version, cfg.size[0], cfg.size[1])
    except (AppNotUsable, ChromeError) as e:
        return [skipped(u, r, str(e)) for u, r in rows], app.lines, "", [str(e)]
    return items, app.lines[:before] + lines + app.lines[before:], env, []


def plans_of(t: Target) -> List[Plan]:
    out = []
    for unit in t.units:
        path = plan_path(t.intent, unit)
        try:
            with open(os.path.join(t.root, path), "rb") as f:
                raw = f.read()
        except OSError:
            raw = None
        out.append(parse_plan(raw, unit, path))
    return out


SHOTS_USAGE = "usage: python3 %s shots [--set <name>] [--strict]\n" % SCRIPT


def cmd_shots(args: Sequence[str]) -> int:
    """shots [--set ui] [--strict]: exit 0 whatever it saw; --strict: 1 when a row is not ok or there was nothing to
    shoot (Lean's own gates then refuse the commit and the release), 2 when the set could not be written."""
    args = list(args)
    strict = "--strict" in args
    rest = [a for a in args if a != "--strict"]
    name = rest[1] if len(rest) == 2 and rest[0] == "--set" else "ui" if not rest else ""
    if not SET_NAME_RE.match(name):
        emit(SHOTS_USAGE)
        return 2
    root = project_root()
    t, why = target(root)
    if t is None:
        emit("（%s：这次 verify 不拍截图）\n" % why)
        return 1 if strict else 0
    try:
        new = begin_set(t, name)
    except (OSError, SetWriteError) as e:
        emit("（证据没写成：%s）\n" % e)
        return 2 if strict else 0
    start = code_state(root, t.is_record)
    plans = plans_of(t)
    rows = [(p.unit, r) for p in plans for r in p.rows]
    problems = [x for p in plans for x in p.problems]
    items: List[Item] = []
    lines: List[str] = []
    env = ""
    if len(rows) > ROWS_MAX:
        items += [skipped(u, r, "超过 %d 行，没拍" % ROWS_MAX) for u, r in rows[ROWS_MAX:]]
        problems.append("证据计划超过 %d 行：后面 %d 行没拍" % (ROWS_MAX, len(rows) - ROWS_MAX))
        rows = rows[:ROWS_MAX]
    if not rows:
        problems.append("没有证据计划（%s）" % "、".join(p.path for p in plans))
    else:
        try:
            got, lines, env, more = capture(root, rows, new)
        except Exception as e:  # noqa: BLE001  the set is still written, with what went wrong
            got, more = [skipped(u, r, "拍的时候出错：%s" % e) for u, r in rows], ["拍的时候出错：%s" % e]
        items, problems = got + items, problems + more
    changed = moved(start, code_state(root, t.is_record))
    if changed:
        items = list(no_better_than_failed(items, MOVED))
        problems.append("%s（%s）：这次的证据都不算通过" % (MOVED, _ids(changed)))
    code = 1 if strict and (not items or any(i.result is not Result.OK for i in items)) else 0
    s = ProofSet(schema=SCHEMA, name=name, kind=Kind.SHOTS, intent=t.intent, clone=t.clone, exit=code, code=start,
                 items=tuple(items), plans=tuple((p.unit, p.path, p.sha256) for p in plans), env=t.mask(env),
                 problems=cap([t.mask(x) for x in problems]))
    body = t.mask("\n".join(lines + ["问题：" + x for x in s.problems]))
    try:
        emit(finish_set(t, name, new, body, s))
    except OSError as e:
        emit(body + "\n（证据没写成：%s）\n" % e)
        return 2 if strict else 0
    return code


LOOK_USAGE = 'usage: python3 %s look "<steps>" [--no-launch] [--out DIR]   (steps: open /path · click 字 · wait 字)\n' % SCRIPT


def cmd_look(args: Sequence[str]) -> int:
    """look "<steps>" [--no-launch] [--out DIR]: the same app and driver for your own eyes. One screenshot into DIR
    (default a new temp folder outside the repository), where the page ended up, each step's result and the visible
    text. Never evidence: it writes nothing in the repository. --no-launch: a server you started at proof.url."""
    rest, out, no_launch = list(args), None, False
    while "--no-launch" in rest:
        rest.remove("--no-launch")
        no_launch = True
    if "--out" in rest:
        k = rest.index("--out")
        out = rest[k + 1] if k + 1 < len(rest) else ""
        del rest[k:k + 2]
    if len(rest) != 1 or out == "" or rest[0].startswith("--"):
        print(LOOK_USAGE, end="")
        return 2
    args = rest
    try:
        steps = parse_steps(args[0])
    except PlanError as e:
        print("步骤看不懂：%s" % e)
        return 2
    root = project_root()
    cfg, why = app_config(root)
    if cfg is None:
        print("\n".join(why))
        return 2
    out = os.path.abspath(out) if out else tempfile.mkdtemp(prefix="brain-proof-look-")
    real_root = os.path.realpath(root)
    if os.path.realpath(out) == real_root or os.path.realpath(out).startswith(real_root + os.sep):
        print("--out 不能在仓库里：Lean 会把那里多出来的文件算成代码，证据就成了旧代码的")
        return 2
    path = find_chrome()
    if path is None:
        print(NO_CHROME)
        return 2
    os.makedirs(out, exist_ok=True)
    app = RunningApp(root, cfg, launch=not no_launch)
    try:
        with app, Chrome(path, cfg.size) as chrome:
            tab, done = chrome.tab(), []
            for step in steps:
                why_not = tab.run(step, cfg.url)
                done.append("%s %s" % (step.text(), "好了" if why_not is None else why_not))
                if why_not:
                    break
            png, page, text = tab.shot(), tab.page(), tab.visible_text()
    except (AppNotUsable, ChromeError) as e:
        print("\n".join(app.lines + [str(e)]))
        return 2
    shot = os.path.join(out, "look-%s.png" % time.strftime("%H%M%S"))
    with open(shot, "wb") as f:
        f.write(png)
    print("\n".join(["截图：%s（不是证据）" % shot, "页面：%s 「%s」" % (page.path, page.title), "步骤：" + " · ".join(done),
                     "看到的字："] + ["  " + x for x in text]))
    return 0 if all(d.endswith("好了") for d in done) and len(done) == len(steps) else 1


def encoding_problem() -> str:
    """'' when Python here reads a command's output as UTF-8, else what that does to evidence and the fix. Lean's
    verify decodes each check's output in this encoding (subprocess(text=True)) and hashes it as UTF-8: a non-ASCII
    output.txt is then never what it hashed, so no VERIFY seals the set."""
    enc = locale.getpreferredencoding(False)
    if enc.replace("-", "").replace("_", "").lower() == "utf8":
        return ""
    return ("这台机器的 Python 按 %s 读命令输出，不是 UTF-8：lean verify 会读错证据里的中文，有中文的证据都不算。"
            "跑 lean verify 的终端设成 UTF-8：export LC_ALL=zh_CN.UTF-8（或者 LANG=zh_CN.UTF-8）" % enc)


def cmd_doctor(args: Sequence[str]) -> int:
    """Lines `OK|WARN <text>`, read-only: Python reading output as UTF-8 (WARN only), Chrome, kit.json proof, the
    tool that checks the listener."""
    root = project_root()
    enc = encoding_problem()
    out = ["WARN " + enc] if enc else []
    path = find_chrome()
    if path is None:
        out.append("WARN %s：verify 拍不了截图，每一行记成没拍成" % NO_CHROME)
    else:
        try:
            p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            version = p.stdout.strip() or path
        except (OSError, subprocess.SubprocessError):
            version = path
        out.append("OK   Chrome：%s" % version)
    cfg, why = app_config(root)
    out += ["OK   kit.json proof：%s，启动命令 %s" % (cfg.url, cfg.launch)] if cfg else ["WARN " + x for x in why]
    out.append("OK   查监听地址用 %s" % ("lsof" if shutil.which("lsof") else "ss") if shutil.which("lsof") or
               shutil.which("ss") else "WARN 没有 lsof，也没有 ss：查不了程序监听在哪，verify 不拍截图")
    print("\n".join(out))
    return 0


COMMANDS = {"run": cmd_run, "shots": cmd_shots, "look": cmd_look, "doctor": cmd_doctor}
USAGE = "".join((RUN_USAGE, SHOTS_USAGE, LOOK_USAGE, "usage: python3 %s doctor\n" % SCRIPT))


def main(argv: Sequence[str]) -> int:
    warnings.simplefilter("ignore")           # a warning on stderr would change what Lean hashes
    if not argv or argv[0] not in COMMANDS:
        emit(USAGE)
        return 2
    try:
        return COMMANDS[argv[0]](argv[1:])
    except Exception as e:  # noqa: BLE001  said on stdout: a traceback on stderr would fail the check for a bug here
        emit("（brain-proof 出错：%s: %s）\n" % (type(e).__name__, e))
        return 0 if argv[0] == "shots" and "--strict" not in argv else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
