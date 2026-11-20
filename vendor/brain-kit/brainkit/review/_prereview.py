"""brainkit.review._prereview: two other models review exactly the requested version before the person sees the
sheet. Private: import brainkit.review.

How it runs (one way everywhere: a Kiro CLI session, a KiroCrew session's shell, later the app's job and the runner):
`brain review` starts two headless Kiro CLI runs in parallel and waits for both. The command is the measured one
(2026-11-12: 10 nested runs, 8 of them from inside KiroCrew sessions):

    kiro-cli chat --agent-engine v3 --agent brain-reviewer --model <model> --effort medium --trust-tools= \
             --output-format stream-json "<PROMPT>"  < /dev/null

  - slot a runs kit.json `model`, slot b `review_model`, read at run time: the agent spec has no model.
  - the reasoning effort is brain-kit's (EFFORT), never the person's own kiro-cli default: the same `brain review`
    gives the same run on every machine.
  - `brain-reviewer` is a user-level agent spec (~/.kiro/agents/brain-reviewer.json, shipped as
    crew/brain-reviewer.json; `brain crew-agent` installs it, `brain review` installs it when absent and it can):
    read-only tools, no MCP servers, no hooks. The default agent starts MCP servers and hooks; a KiroCrew session
    cannot write ~/.kiro/agents itself. Its reads are not limited to its folder and it keeps kiro-cli's memory: the
    user's call (2026-11-14: every agent may use the web, safety rules stay light for now).
  - each run's cwd is a fresh folder of its own under ~/.brain/review/, outside every project, holding ONLY copies (no
    `.kiro/`: a project .kiro/ would fire Lean's prompt hook and log the reviewer's prompt as a person's turn): the
    requested pages as the bytes Lean hashed, decisions.md, the Brain's main files, AGENTS.md, brief.md.
  - the answer is the `finalText` of the last stream-json line (`runFinished`); refused when `finalTextTruncated`.
    Every tool call in that stream (tool, path, URL, query; never what it returned) is kept as <slot>.tools.txt.
  - the model is proven from the run's own kiro-cli record (~/.kiro/sessions/*/<sessionId>/session.json modelId and
    the turns' reasoningModelId; a model's self-report proves nothing): check_model. A mismatch or a
    missing record shows on the sheet as 模型没核实; the findings still count.
The lead agent's part is one shell command (`brain review`), a few minutes long, like `lean verify`.
"""
from __future__ import annotations

import collections
import glob
import hashlib
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .. import mrdesc, usage
from .. import trace as T
from ..common import CTRL_RE, load_json, write_json
from . import _sheet
from .types import Finding, FindingType, Item, ItemKind, Resolution, ReviewerRun, Slot, Source

TIMEOUT_SECONDS = 600       # per reviewer; on timeout the sheet goes out without that reviewer (said in its header)
EFFORT = "medium"           # measured 2026-11-14 on 368 trial lines: opus max reset 3/3; high 493 s, near the timeout; medium 114 s
MAX_FINDINGS = 12           # per reviewer, most important first (the brief says so; extra rows are dropped, counted)
KIRO = "kiro-cli"
AGENT = "brain-reviewer"
# Names of tools and permission capabilities that only read; the reviewer spec may name no other (kiro-cli's
# rewrite of crew/brain-reviewer.json names read, web, web_fetch, web_search, fs_read and skill).
READ_ONLY = frozenset(("read", "fs_read", "web", "web_search", "web_fetch", "skill"))
SANDBOX = os.path.join(".brain", "review")      # under the home folder: where the reviewers run
PROMPT = "预审：读当前文件夹里的 brief.md，照它做，只按它最后写的格式回答。"
TYPE_WORDS = {              # a reviewer may answer in English
    FindingType.CONTRADICTION: ("矛盾", "冲突", "contradict", "conflict", "inconsistent"),
    FindingType.UNTESTABLE: ("测不了", "无法测试", "不可测", "untestable", "not testable", "vague"),
    FindingType.MISSING: ("漏", "缺", "missing", "edge case", "not covered", "gap"),
    FindingType.BRAIN: ("brain",),
}
TYPE_RANK = (FindingType.CONTRADICTION, FindingType.UNTESTABLE, FindingType.MISSING, FindingType.BRAIN,
             FindingType.OTHER, FindingType.CHECK)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRIEF_TEMPLATE = os.path.join(ROOT, "templates", "review", "reviewer-brief.md")
REVIEWER_SPEC = os.path.join(ROOT, "crew", "brain-reviewer.json")
POLL_SECONDS = 0.2
STOP_GRACE = 3.0            # seconds between SIGTERM and SIGKILL of a reviewer's process group
# what a tool's timeout or a closed terminal or session sends brain: while reviewers run, they raise SystemExit
STOP_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n))
PROBLEM_CHARS = 500
QUOTE_CHARS = 200
BRIEF_BIND_RE = re.compile(r"(?m)^review-of: (\S+) ([0-9a-f]{64})$")
REVIEW_OF_RE = re.compile(r"(?i)^review-of:\s*(\S+)\s+([0-9a-f]{64})(?![0-9a-z])")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEP_RE = re.compile(r"^:?-{2,}:?$")
# an id as a reviewer writes it, alone or as a range (D-1…D-5, AC-10~13); any prefix, so a wrong one is kept as unknown
ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4})-([0-9]{1,4})(?![0-9])"
                         r"(?:[ \t]*(?:\.{2,}|~|–|—|至|到)[ \t]*(?:([A-Za-z]{1,4})-)?([0-9]{1,4})(?![0-9]))?")
RANGE_MAX = 100
BLOCK_RE = re.compile(r"<!-- (round 2|sent back) -->\n(.*?)<!-- /\1 -->\n", re.S)
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TURN_MODEL_RE = re.compile(r'"reasoningModelId"\s*:\s*"([^"\\]*)"')
QUOTE_MARKS = frozenset("「」『』“”‘’\"'`*")
RECOMMENDED_RE = re.compile(r"按推荐|按建议|照推荐|(?i:as recommended|per (?:the )?recommendation)")
NONE_WORDS = frozenset(("", "-", "—", "–", "无", "无。", "none", "n/a", "na", "没有", "没有问题", "无问题", "nothing"))
FINDING_COLUMNS = (("n", ("#", "序号", "no.")),
                   ("targets", ("条目", "涉及", "item", "items", "target", "targets", "ids", "id")),
                   ("type", ("类型", "种类", "type", "kind", "category")),
                   ("problem", ("问题", "problem", "issue", "finding", "findings")),
                   ("quote", ("引文", "原文", "依据", "quote", "evidence", "excerpt")))
VERDICT_COLUMNS = (("verdict", ("结论", "verdict", "result", "status")),
                   ("target", ("退回", "sent back", "line", "item", "条目", "id")),
                   ("note", ("说明", "理由", "note", "why", "reason", "comment")))
UNRESOLVED_WORDS = ("没改完", "没改到", "没改", "未改", "没做到", "没解决", "未解决", "部分", "unresolved",
                    "not resolved", "partly", "partially")
RESOLVED_WORDS = ("改到了", "改好了", "做到了", "已解决", "resolved", "done", "yes")


@dataclass(frozen=True)
class RawFinding:
    """One table row of one reviewer (or of a code check), parsed and checked against the round."""
    slot: Slot
    n: int                              # its row number in that answer
    targets: Tuple[str, ...]            # item ids of the round it names, in its order
    type: FindingType
    problem: str                        # one line, made inert
    quote: str = ""                     # what it quoted from the pages
    quote_found: Optional[bool] = None  # the quote is in the requested bytes (whitespace and NFKC aside); None: none
    unknown_targets: Tuple[str, ...] = ()   # ids it named that the round does not have


@dataclass(frozen=True)
class SentBack:
    """Round >= 2: one line the person typed against the previous round, for the brief."""
    target: str                         # the line's first id as typed: F-1, A-3, D-19
    ids: Tuple[str, ...]                # the items it is about (F-1: its targets; A-3: A-3)
    words: str                          # what they typed after the ids (masked)
    now: str                            # what code says became of it: "AC-36 加「…」；D-24 由 D-28 取代" / "没改"


class ToolCall(NamedTuple):
    """One tool call of a reviewer's run as its stream-json shows it, never what the tool gave back."""
    tool: str                           # kiro-cli's name for it: Read File, List Directory, Fetch URL, web_search
    target: str                         # what it named: a path (see _shown), a URL, a query
    status: str                         # completed, failed, or the last one it reached (the run ended first)
    reason: str = ""                    # failed: the first line kiro-cli gave back


@dataclass(frozen=True)
class Answer:
    """One reviewer's run: its record, the text it answered (as captured), and the tool calls it made."""
    run: ReviewerRun
    text: str
    tools: Tuple[ToolCall, ...] = ()


class _Read(NamedTuple):
    """An answer's tables, bound to its round. `usable` False: nothing is taken and `problem` says why."""
    rows: Tuple[Dict[str, str], ...]        # findings table rows: column key -> cell
    verdicts: Tuple[Dict[str, str], ...]    # sent-back table rows
    problem: Optional[str]
    usable: bool


@dataclass
class _Child:
    slot: Slot
    model: str
    box: str
    t0: float = 0.0
    end: float = 0.0
    proc: Optional[subprocess.Popen] = None
    timed_out: bool = False
    note: str = ""                      # why it never started


# ---------- the brief ----------

def brief(kind: str, sha256: str, files: Dict[str, str], copies: Sequence[str], items: Dict[str, Item],
          sent_back: Sequence[SentBack] = (), changed: Sequence[str] = (), round_n: int = 1) -> str:
    """brief.md, the same file for both reviewers (rendered from templates/review/reviewer-brief.md; kept in the
    round's folder and committed: it records what they were shown). `files`: the requested pages, path -> sha256;
    `copies`: the file names in the reviewers' folder; `items`: the round's items (the ids a finding may name).
    Round 1: contradictions (AC and AC, AC and design, a page and a Brain entry), missing edge cases (each external
    call failing or timing out, two people at once, limits and empty results, time zones and day boundaries, retries
    and duplicates), untestable ACs (no observable result; 及时 / 合理 / 逐次加长 without a number), Brain conflicts
    (entry id quoted). Not the intent or the scope, no style. Round >= 2 (`sent_back`, `changed`): for each line the
    person sent back, its words and what changed, answer 改到了 / 没改完 in the second table; new findings only about
    what changed and what it touches. Each finding quotes the pages word for word (the quote is checked)."""
    with open(BRIEF_TEMPLATE, encoding="utf-8") as f:
        text = f.read()
    keep = {"round 2": round_n >= 2, "sent back": bool(sent_back)}
    text = BLOCK_RE.sub(lambda m: m.group(2) if keep[m.group(1)] else "", text)
    pages = [os.path.basename(p) for p in files]
    context = [c for c in copies if c not in pages]
    return text.format(KIND=_kind(kind), SHA256=sha256, ROUND=round_n, MAX=MAX_FINDINGS,
                       PAGES=_code(pages), CONTEXT=_code(context) or "（没有）", IDS=_ranges(items) or "（没有）",
                       CHANGED=_ranges(changed) or "没有", SENT_BACK="\n".join(_sent_row(s) for s in sent_back),
                       FIRST=sent_back[0].target if sent_back else "")


def _kind(kind) -> str:
    return str(getattr(kind, "value", kind))


def _code(names: Sequence[str]) -> str:
    return "、".join("`%s`" % n for n in names)


def _sent_row(s: SentBack) -> str:
    about = s.target if tuple(s.ids) in ((), (s.target,)) else "%s（%s）" % (s.target, "、".join(s.ids))
    return "| %s | %s | %s |" % (mrdesc._cell(about), mrdesc._cell(s.words, PROBLEM_CHARS),
                                 mrdesc._cell(s.now, PROBLEM_CHARS))


def _ranges(ids) -> str:
    """D-1…D-5、D-7、AC-2: ids grouped by prefix in order of first appearance, three or more in a row as a range."""
    groups: Dict[str, List[int]] = collections.OrderedDict()
    for i in ids:
        prefix, _, num = i.rpartition("-")
        if prefix and num.isdigit():
            groups.setdefault(prefix, []).append(int(num))
        else:
            groups.setdefault(i, [])
    out = []
    for prefix, nums in groups.items():
        if not nums:
            out.append(prefix)
            continue
        nums = sorted(set(nums))
        run = [nums[0]]
        for n in nums[1:] + [None]:
            if n is not None and n == run[-1] + 1:
                run.append(n)
                continue
            out += ["%s-%d…%s-%d" % (prefix, run[0], prefix, run[-1])] if len(run) > 2 else \
                ["%s-%d" % (prefix, x) for x in run]
            run = [n]
    return "、".join(out)


# ---------- running the two reviewers ----------

def run(work_dir: str, brief_text: str, copies: Dict[str, bytes], slots: Sequence[Tuple[Slot, str]],
        timeout: float = TIMEOUT_SECONDS, progress: Optional[Callable[[str], None]] = None) -> List[Answer]:
    """Run the reviewers in parallel (one subprocess.Popen per slot, then wait with `timeout` each; a stuck one is
    killed with its process group). Each slot gets its own folder _sandbox(work_dir)/<slot>/, outside the project,
    holding `copies` (name -> bytes; names may have one folder level, "brain/TECH.md") and brief.md, and nothing else;
    all of it is deleted afterwards, also on failure. The child's environment is this one without the calling
    session's identity (KIRO_SESSION_ID, KIROCREW_*). Writes work_dir/<slot>.md (a first line naming the model, the seconds and the model check, then the
    answer as captured), work_dir/<slot>.tools.txt (its tool calls) and work_dir/reviewers.json. Never raises for a
    reviewer's failure: that Answer's run says failed / timeout / bad_output, and the sheet goes out with the other.
    No kiro-cli on PATH: every slot `skipped`. `progress(line)`: one Chinese line per start and end."""
    say = progress or (lambda line: None)
    bind = BRIEF_BIND_RE.search(brief_text)
    if bind is None:
        raise ValueError("the brief names no version: it has no `review-of: <KIND> <sha256>` line")
    for name in copies:
        _check_name(name)
    os.makedirs(work_dir, exist_ok=True)
    label = _labels(slots)
    if shutil.which(KIRO) is None:
        say("预审：找不到 %s，这次没有预审" % KIRO)
        answers = [Answer(ReviewerRun(s, m, "skipped", None, 0, note="没有 %s：这次没有预审" % KIRO), "")
                   for s, m in slots]
        _record(work_dir, answers, {})
        return answers
    env = {k: v for k, v in os.environ.items() if k != "KIRO_SESSION_ID" and not k.startswith("KIROCREW_")}
    root = _sandbox(work_dir)
    shutil.rmtree(root, ignore_errors=True)
    children = [_Child(s, m, os.path.join(root, s.value)) for s, m in slots]
    done: Dict[Slot, Tuple[Answer, dict]] = {}
    trapped = _trap_stop_signals()
    try:
        for c in children:
            _start(c, brief_text, copies, env)
            say("预审：%s 开始" % label[c.slot] if c.proc else "预审：%s 没能开始：%s" % (label[c.slot], c.note))
        live = [c for c in children if c.proc is not None]
        while live:
            for c in list(live):
                if c.proc.poll() is None and time.monotonic() - c.t0 < timeout:
                    continue
                if c.proc.poll() is None:
                    c.timed_out = True
                    _stop(c.proc)
                c.end = time.monotonic()
                live.remove(c)
                done[c.slot] = _finish(c, bind.group(1), bind.group(2), timeout)
                say(_said(label[c.slot], done[c.slot][0].run))
            if live:
                time.sleep(POLL_SECONDS)
    finally:
        for c in children:
            if c.proc is not None and c.proc.poll() is None:
                _stop(c.proc)
        shutil.rmtree(root, ignore_errors=True)
        for sig, old in trapped.items():
            signal.signal(sig, signal.SIG_DFL if old is None else old)
    answers = [done[c.slot][0] if c.slot in done else
               Answer(ReviewerRun(c.slot, c.model, "failed", None, 0, note=c.note), "") for c in children]
    _record(work_dir, answers, {s: ev for s, (_, ev) in done.items()})
    return answers


def _trap_stop_signals() -> dict:
    """{signal: the handler it had} for STOP_SIGNALS, which now raise SystemExit so run()'s cleanup stops the
    reviewers: each runs in a session of its own, so the signal reaches only brain, and its default action skips the
    cleanup. Nothing off the main thread (Python sets handlers only there)."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    return {sig: signal.signal(sig, _stopped) for sig in STOP_SIGNALS}


def _stopped(signum, frame):
    raise SystemExit(128 + signum)


def _sandbox(work_dir: str) -> str:
    """~/.brain/review/<round folder's name>-<12 hex of its path>: where that round's reviewers run, outside every
    project. The same folder for the same round (one `brain review` at a time holds the round's lock), so what a
    killed run left there is deleted before the next run of that round starts."""
    path = os.path.abspath(work_dir)
    return os.path.join(os.path.expanduser("~"), SANDBOX, "%s-%s" % (
        os.path.basename(path), hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]))


def _check_name(name: str) -> None:
    parts = name.split("/")
    if (os.path.isabs(name) or "\\" in name or not 1 <= len(parts) <= 2 or any(p in ("", ".", "..") for p in parts)
            or parts[0] == ".kiro" or name == "brief.md"):
        raise ValueError("not a file name for the reviewers' folder: %r" % name)


def _labels(slots: Sequence[Tuple[Slot, str]]) -> Dict[Slot, str]:
    names = {s: _sheet.MODEL_NAMES.get(m, m) for s, m in slots}
    twice = {n for n in names.values() if list(names.values()).count(n) > 1}
    return {s: "%s（%s）" % (n, s.value) if n in twice else n for s, n in names.items()}


def _argv(model: str) -> List[str]:
    return [KIRO, "chat", "--agent-engine", "v3", "--agent", AGENT, "--model", model, "--effort", EFFORT,
            "--trust-tools=", "--output-format", "stream-json", PROMPT]


def _start(c: _Child, brief_text: str, copies: Dict[str, bytes], env: Dict[str, str]) -> None:
    """A fresh folder of copies, then the reviewer in it. A folder or a start that fails leaves c.proc None and
    c.note saying why (that reviewer failed; the other still runs)."""
    try:
        os.makedirs(c.box)
        for name, data in copies.items():
            path = os.path.join(c.box, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        with open(os.path.join(c.box, "brief.md"), "w", encoding="utf-8") as f:
            f.write(brief_text)
    except OSError as e:
        c.note = "没能准备评审用的文件夹：%s" % (e.strerror or e)
        return
    try:
        with open(c.box + ".out", "wb") as out, open(c.box + ".err", "wb") as err:
            c.t0 = time.monotonic()
            c.proc = subprocess.Popen(_argv(c.model), cwd=c.box, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      env=env, start_new_session=True)
    except OSError as e:
        c.note = "启动不了 %s：%s" % (KIRO, e.strerror or e)


def _stop(proc: subprocess.Popen) -> None:
    """The reviewer and everything it started (its own process group): TERM, then KILL after STOP_GRACE."""
    for sig, wait in ((signal.SIGTERM, STOP_GRACE), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except (OSError, AttributeError):
            proc.kill()
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _finish(c: _Child, kind: str, sha256: str, timeout: float) -> Tuple[Answer, dict]:
    secs = round(c.end - c.t0, 1)
    stream = _text(c.box + ".out")
    tools = tuple(tool_calls(stream, c.box))

    def answer(status, note="", text="", findings=0, check="", session=None):
        return Answer(ReviewerRun(c.slot, c.model, status, secs, findings, note, check, session), text, tools)

    if c.timed_out:
        return answer("timeout", "超过 %s 没交，停了" % _dur(timeout)), {}
    text, data = final_text(stream)
    if not data or data.get("status", "success") != "success":
        why = _tail(c.box + ".err")
        return answer("failed", "%s 没交回答（退出码 %s）%s" % (KIRO, c.proc.returncode, "：" + why if why else "")), {}
    session = data.get("sessionId") if isinstance(data.get("sessionId"), str) else None
    check, evidence = check_model(session, c.model)
    if data.get("finalTextTruncated"):
        return answer("bad_output", "回答被截断了，不算", text, 0, check, session), evidence
    if not text.strip():
        return answer("bad_output", "回答是空的", text, 0, check, session), evidence
    read = _read(text, kind, sha256)
    if not read.usable:
        return answer("bad_output", read.problem or "", text, 0, check, session), evidence
    return answer("ok", read.problem or "", text, len(read.rows), check, session), evidence


def _text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _tail(path: str) -> str:
    """The last stderr line worth showing (kiro-cli's [INFO] lines are not), one line."""
    lines = [line.strip() for line in _text(path).splitlines() if line.strip()]
    useful = [line for line in lines if not line.startswith(("[INFO]", "[DEBUG]"))]
    return mrdesc._one((useful or lines or [""])[-1], 160)


def _dur(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return "%d 秒" % s
    return "%d 分钟" % (s // 60) if s % 60 == 0 else "%d 分 %d 秒" % (s // 60, s % 60)


def _said(label: str, r: ReviewerRun) -> str:
    if r.status == "ok":
        return "预审：%s 交了，%s，%d 条%s%s" % (label, _dur(r.seconds or 0), r.findings,
                                           "（%s）" % r.note if r.note else "",
                                           "" if r.model_check == "match" else "；模型没核实")
    if r.status == "timeout":
        return "预审：%s %s" % (label, r.note)
    return "预审：%s %s：%s" % (label, "交的回答不能用" if r.status == "bad_output" else "失败", r.note)


def _header(r: ReviewerRun) -> str:
    parts = [r.slot.value, r.model, r.status, _dur(r.seconds) if r.seconds is not None else "没跑",
             "模型核实：%s" % (r.model_check or "没查")]
    return " · ".join(parts + [p for p in (r.session, r.note) if p])


def _record(work_dir: str, answers: Sequence[Answer], evidence: Dict[Slot, dict]) -> None:
    for a in answers:
        with open(os.path.join(work_dir, a.run.slot.value + ".md"), "w", encoding="utf-8") as f:
            f.write(_header(a.run) + "\n" + a.text)
        with open(os.path.join(work_dir, a.run.slot.value + ".tools.txt"), "w", encoding="utf-8") as f:
            f.write(_tools_text(a))
    write_json(os.path.join(work_dir, "reviewers.json"),
               {"reviewers": [dict(asdict(a.run), slot=a.run.slot.value, model_evidence=evidence.get(a.run.slot, {}))
                              for a in answers]})


def final_text(stream_json: str) -> Tuple[str, dict]:
    """(finalText, the runFinished event's data) of a `--output-format stream-json` run's stdout; ('', {}) without a
    runFinished line. Lines that are not JSON are skipped."""
    data = None
    for line in stream_json.splitlines():
        if '"runFinished"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "runFinished":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if data is None:
        return "", {}
    text = data.get("finalText")
    return (text if isinstance(text, str) else ""), data


def tool_calls(stream_json: str, box: str) -> List[ToolCall]:
    """The tool calls of a `--output-format stream-json` run in the order they started: kiro-cli 3.3.0 streams a
    sessionUpdate `tool_call` (title, kind, rawInput, locations), then `tool_call_update`s with a status (the shapes
    are in tests/test_review_prereview.py). `box` is the reviewer's folder, for the paths (see _shown)."""
    calls: Dict[str, dict] = collections.OrderedDict()
    for line in stream_json.splitlines():
        if '"tool_call' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        data = event.get("data") if isinstance(event, dict) else None
        update = data.get("update") if isinstance(data, dict) else None
        if not isinstance(update, dict) or update.get("sessionUpdate") not in ("tool_call", "tool_call_update"):
            continue
        call = calls.setdefault(str(update.get("toolCallId")), {})
        call.update((k, v) for k, v in update.items() if v is not None)
    return [ToolCall(str(c.get("title") or c.get("kind") or "?"), _target(c, box), str(c.get("status") or ""),
                     _reason(c.get("content")) if c.get("status") == "failed" else "") for c in calls.values()]


def _target(call: dict, box: str) -> str:
    raw = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
    where = call.get("locations") if isinstance(call.get("locations"), list) else []
    paths = [w.get("path") for w in where if isinstance(w, dict)] + [raw.get("path")]
    path = next((p for p in paths if isinstance(p, str) and p), None)
    if path is not None:
        return _shown(path, box)
    for key in ("url", "query"):
        if isinstance(raw.get(key), str):
            return raw[key]
    return json.dumps({k: v for k, v in raw.items() if k != "_meta"}, ensure_ascii=False, sort_keys=True)


def _shown(path: str, box: str) -> str:
    """A path a reviewer's tool named: relative to its folder when inside it, else absolute with the home folder as ~
    (the folder itself is deleted after the run; its name would say nothing)."""
    full = os.path.normpath(os.path.join(box, os.path.expanduser(path)))
    try:
        real, inside = os.path.realpath(full), os.path.realpath(box)
    except ValueError:      # a NUL in the path: Python 3.12 will not resolve it
        real, inside = full, box
    if os.path.commonpath([real, inside]) == inside:
        return os.path.relpath(real, inside)
    home = os.path.expanduser("~")
    for p, h in ((full, home), (real, os.path.realpath(home))):
        if p == h or p.startswith(h + os.sep):
            return "~" + p[len(h):]
    return full


def _reason(content) -> str:
    """The first line kiro-cli gave back for a failed call (a failed call returns no file)."""
    for part in content if isinstance(content, list) else ():
        inner = part.get("content") if isinstance(part, dict) else None
        text = inner.get("text") if isinstance(inner, dict) else None
        if isinstance(text, str) and text.strip():
            return text.strip().splitlines()[0]
    return ""


def _tools_text(a: Answer) -> str:
    """<slot>.tools.txt, for a person: a first line (slot, model, how many calls, how many failed or did not finish),
    then one numbered line per call: tool · what it named, and 失败：<reason> or 没做完 when it did not complete. What
    the reviewer wrote is made inert and has no control character left (a NUL would make git call the file binary, an
    escape sequence would rewrite what a terminal shows)."""
    r = a.run
    if r.seconds is None:
        return "%s · %s · 没跑\n" % (r.slot.value, r.model)
    if not a.tools:
        return "%s · %s · 没有工具调用\n" % (r.slot.value, r.model)
    failed = sum(1 for t in a.tools if t.status == "failed")
    unfinished = sum(1 for t in a.tools if t.status not in ("completed", "failed"))
    head = "%s · %s · %d 次工具调用" % (r.slot.value, r.model, len(a.tools))
    head += ("，%d 次失败" % failed if failed else "") + ("，%d 次没做完" % unfinished if unfinished else "")
    lines = [head] + ["%d. %s · %s%s" % (n, _logged(t.tool, 60), _logged(t.target), _ending(t))
                      for n, t in enumerate(a.tools, 1)]
    return "\n".join(lines) + "\n"


def _ending(t: ToolCall) -> str:
    if t.status == "completed":
        return ""
    if t.status != "failed":
        return " · 没做完"
    return " · 失败" + ("：" + _logged(t.reason) if t.reason else "")


def _logged(text: str, limit: int = PROBLEM_CHARS) -> str:
    return CTRL_RE.sub("?", mrdesc._one(text, limit))


def check_model(session_id: Optional[str], model: str, sessions_root: Optional[str] = None) -> Tuple[str, dict]:
    """(verdict, evidence): did this kiro-cli v3 session run on `model`? Reads
    <sessions_root or ~/.kiro/sessions>/*/<session_id>/session.json (modelId) and messages.jsonl (every turn's
    reasoningModelId, "qdev::<model>"). match: both say `model`; mismatch: either names another; inconclusive: no
    record, several, or no per-turn model id (the controls are in tests/test_review_prereview.py). The evidence also
    names the memory access the record gives the session (its memoryConfig)."""
    root = sessions_root or os.path.join(os.path.expanduser("~"), ".kiro", "sessions")
    evidence = {"session": session_id, "want": model}
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return "inconclusive", dict(evidence, why="no session id")
    hits = glob.glob(os.path.join(glob.escape(root), "*", session_id, "session.json"))
    if len(hits) != 1:
        return "inconclusive", dict(evidence, why="%d session records" % len(hits))
    folder = os.path.dirname(hits[0])
    evidence["dir"] = T.scrub(folder)
    try:
        meta = load_json(hits[0])
    except (OSError, ValueError):
        return "inconclusive", dict(evidence, why="session.json unreadable")
    turns = collections.Counter(TURN_MODEL_RE.findall(_text(os.path.join(folder, "messages.jsonl"))))
    served = {m.split("::", 1)[-1] for m in turns}
    meta = meta if isinstance(meta, dict) else {}
    evidence.update(session_modelId=meta.get("modelId"), turn_models=dict(turns), memory=meta.get("memoryConfig"))
    if evidence["session_modelId"] != model or (served and served != {model}):
        return "mismatch", evidence
    if not served:
        return "inconclusive", dict(evidence, why="no reasoningModelId in the turns (only the session's selected model)")
    return "match", evidence


# ---------- reading an answer ----------

def parse(slot: Slot, text: str, kind: str, sha256: str, known: Dict[str, Item],
          pages_text: str) -> Tuple[List[RawFinding], List[Resolution], Optional[str]]:
    """(findings, resolutions, problem) of one answer. It must start with `review-of: <KIND> <sha256>` naming this
    round (else problem "answered another version": nothing taken). Then the findings table (the first Markdown table
    whose header names 条目/item and 问题/problem; columns found by header words, so an English answer works too) and,
    round >= 2, the sent-back table (header naming 结论/verdict). Targets the round does not have go to
    unknown_targets; the type from TYPE_WORDS (else OTHER); the quote checked against `pages_text`; text made inert
    (one line, no mention, no HTML). At most MAX_FINDINGS rows (the problem says how many were dropped). A table of
    one row `无` / `none` is a valid empty review."""
    read = _read(text, _kind(kind), sha256)
    if not read.usable:
        return [], [], read.problem
    hay = _plain(pages_text)
    findings = []
    for n, row in enumerate(read.rows, 1):
        ids = _ids(row["targets"])
        quote = _unquote(row["quote"])
        findings.append(RawFinding(slot, n, tuple(i for i in ids if i in known), _type(row["type"]),
                                   mrdesc._one(row["problem"], PROBLEM_CHARS), mrdesc._one(quote, QUOTE_CHARS),
                                   _found(quote, hay), tuple(i for i in ids if i not in known)))
    resolutions = []
    for row in read.verdicts:
        target = next(iter(_ids(row["target"], ranges=False)), None)
        if target:
            resolutions.append(Resolution(target, slot, _resolved(row["verdict"]),
                                          mrdesc._one(row["note"], PROBLEM_CHARS)))
    return findings, resolutions, read.problem


def _read(text: str, kind: str, sha256: str) -> _Read:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip() and not FENCE_RE.match(line)), None)
    m = REVIEW_OF_RE.match(lines[first].strip().strip("`*> \t")) if first is not None else None
    if not m:
        return _Read((), (), "回答第一行不是 review-of，不知道答的是哪一版", False)
    if m.group(1).strip("`*").upper() != kind.upper() or m.group(2).lower() != sha256.lower():
        return _Read((), (), "答的不是这一版（review-of 那一行对不上）", False)
    findings = verdicts = None
    for header, rows in _tables(text):
        cols = _columns(header, FINDING_COLUMNS)
        if findings is None and cols["targets"] is not None and cols["problem"] is not None:
            findings = [r for r in (_cells(row, cols) for row in rows)
                        if any(_bare(v) not in NONE_WORDS for k, v in r.items() if k != "n")]
            continue
        cols = _columns(header, VERDICT_COLUMNS)
        if verdicts is None and cols["verdict"] is not None and cols["target"] is not None:
            verdicts = [_cells(row, cols) for row in rows]
    if findings is None:
        rest = [_bare(line) for line in lines[first + 1:]
                if line.strip() and not FENCE_RE.match(line) and not line.strip().startswith("|")]
        if not rest or any(r not in NONE_WORDS for r in rest):
            return _Read((), tuple(verdicts or ()), "回答里没有问题表", False)
        findings = []
    dropped = len(findings) - MAX_FINDINGS
    return _Read(tuple(findings[:MAX_FINDINGS]), tuple(verdicts or ()),
                 "另有 %d 条超出 %d 条的上限，没取" % (dropped, MAX_FINDINGS) if dropped > 0 else None, True)


def _tables(text: str) -> List[Tuple[List[str], List[List[str]]]]:
    """[(header cells, data rows)] of each Markdown table in `text`: a run of lines starting with '|', separator rows
    left out."""
    out: List[Tuple[List[str], List[List[str]]]] = []
    current = None
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            current = None
            continue
        cells = T._cells(s)
        if current is None:
            current = ([_bare(c) for c in cells], [])
            out.append(current)
        elif not all(SEP_RE.match(c) for c in cells if c):
            current[1].append(cells)
    return out


def _bare(cell: str) -> str:
    return unicodedata.normalize("NFKC", cell or "").strip().strip("`*").strip().casefold()


def _hit(cell: str, word: str) -> bool:
    if word.isascii():
        return re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(word), cell) is not None
    return word in cell


def _columns(header: Sequence[str], spec) -> Dict[str, Optional[int]]:
    """{column key: index} by header words, each index used once, in `spec` order."""
    used, out = set(), {}
    for key, words in spec:
        out[key] = next((i for i, h in enumerate(header) if i not in used and any(_hit(h, w) for w in words)), None)
        if out[key] is not None:
            used.add(out[key])
    return out


def _cells(row: Sequence[str], cols: Dict[str, Optional[int]]) -> Dict[str, str]:
    return {k: (row[i] if i is not None and i < len(row) else "") for k, i in cols.items()}


def _ids(cell: str, ranges: bool = True) -> List[str]:
    """The ids a cell names, upper-cased, in its order, ranges expanded (D-1…D-3 -> D-1, D-2, D-3)."""
    out: List[str] = []
    for m in ID_TOKEN_RE.finditer(unicodedata.normalize("NFKC", cell or "")):
        prefix, a = m.group(1).upper(), int(m.group(2))
        found = ["%s-%d" % (prefix, a)]
        if ranges and m.group(4):
            other, b = (m.group(3) or prefix).upper(), int(m.group(4))
            if other == prefix and a < b <= a + RANGE_MAX:
                found = ["%s-%d" % (prefix, n) for n in range(a, b + 1)]
            elif m.group(3):
                found.append("%s-%d" % (other, b))
        out += [i for i in found if i not in out]
    return out


def _type(cell: str) -> FindingType:
    c = _bare(cell)
    for t in (FindingType.BRAIN, FindingType.CONTRADICTION, FindingType.UNTESTABLE, FindingType.MISSING):
        if any(w in c for w in TYPE_WORDS[t]):
            return t
    return FindingType.OTHER


def _unquote(cell: str) -> str:
    q = (cell or "").strip()
    return "" if _bare(q) in NONE_WORDS else q.strip("".join(QUOTE_MARKS)).strip()


def _plain(text: str) -> str:
    """The form a quote is compared in: NFKC, case folded, without whitespace and quote marks."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(ch for ch in t if not ch.isspace() and ch not in QUOTE_MARKS)


def _found(quote: str, hay: str) -> Optional[bool]:
    """The quote is in the pages, in order when it skips text with … (None: no quote)."""
    parts = [_plain(p) for p in re.split(r"\.{2,}", unicodedata.normalize("NFKC", quote or ""))]
    parts = [p for p in parts if p]
    if not parts:
        return None
    at = 0
    for p in parts:
        at = hay.find(p, at)
        if at < 0:
            return False
        at += len(p)
    return True


def _resolved(cell: str) -> Optional[bool]:
    c = _bare(cell)
    if any(_hit(c, w) for w in UNRESOLVED_WORDS):
        return False
    if any(_hit(c, w) for w in RESOLVED_WORDS):
        return True
    return None


# ---------- what code finds ----------

def code_checks(items: Dict[str, Item], duplicates: Sequence[Tuple[str, int]], brain_ids: Optional[frozenset],
                retired_ids: frozenset = frozenset()) -> List[RawFinding]:
    """Slot CHECK findings, found by code (never by a model, never counted as one): decisions whose Who is outside
    the vocabulary (agent, owner, brain <id>, check, <pseudonym> <voucher>) as ONE row naming them all and how many of
    them say 按推荐 in Why; Brain ids an item cites that the Brain does not have (`brain_ids` None: Brain not read) or
    that were archived, withdrawn or purged (`retired_ids`); an id written twice on a page; an AC with no source at
    all. Type CHECK, problem in Chinese."""
    rows: List[RawFinding] = []

    def add(targets, problem):
        rows.append(RawFinding(Slot.CHECK, len(rows) + 1, tuple(targets), FindingType.CHECK, problem))

    who = [i for i in items.values() if i.kind is ItemKind.DECISION and i.source is Source.UNKNOWN]
    if who:
        asis = sum(1 for i in who if any(RECOMMENDED_RE.search(c) for c in i.cells if c != i.main))
        add([i.id for i in who], "Who 没写，或不是约定的写法（agent、owner、check、brain 加条目号、化名加凭证号），看不出"
                                 "是谁定的：%s%s" % (_ranges([i.id for i in who]),
                                                     "；其中 %d 条的 Why 写的是「按推荐」" % asis if asis else ""))
    cited: Dict[str, List[str]] = collections.OrderedDict()
    for it in items.values():
        for c in it.cites:
            if usage.CITE_RE.fullmatch(c):
                cited.setdefault(c, []).append(it.id)
    retired = [(c, ids) for c, ids in cited.items() if c in retired_ids]
    unknown = [(c, ids) for c, ids in cited.items()
               if brain_ids is not None and c not in brain_ids and c not in retired_ids]
    if unknown:
        add(_citers(unknown), "引用了 Brain 里没有的条目：%s" % _cited(unknown))
    if retired:
        add(_citers(retired), "引用的 Brain 条目已经归档、撤回或删除了：%s" % _cited(retired))
    twice: Dict[str, List[int]] = collections.OrderedDict()
    for iid, line in duplicates:
        twice.setdefault(iid, []).append(line)

    def where(iid, later):
        first = items.get(iid)
        page = os.path.basename(first.path) + " " if first else ""
        return "%s（%s第 %s 行）" % (iid, page, "、".join(str(n) for n in ([first.line] if first else []) + later))

    if twice:
        add([i for i in twice if i in items], "编号写重了，只认第一处：%s" % "、".join(where(i, n) for i, n in twice.items()))
    nosource = [i.id for i in items.values() if i.kind is ItemKind.AC and i.source is Source.UNKNOWN]
    if nosource:
        add(nosource, "没写来源，看不出依据：%s" % _ranges(nosource))
    return rows


def _citers(cited: Sequence[Tuple[str, List[str]]]) -> List[str]:
    return list(dict.fromkeys(i for _, ids in cited for i in ids))


def _cited(cited: Sequence[Tuple[str, List[str]]]) -> str:
    return "、".join("%s（%s）" % (c, "、".join(ids)) for c, ids in cited)


# ---------- one list for the sheet ----------

def merge(a: Sequence[RawFinding], b: Sequence[RawFinding], check: Sequence[RawFinding],
          first_fid: int = 1, order: Sequence[str] = ()) -> List[Finding]:
    """One list of findings for the sheet, F-<first_fid>... in sheet order (first_fid continues the intent's
    numbering, see types.Finding). Both reviewers first. Agreement is decided by TARGETS, by code: "both models
    flagged AC-36" is decidable and is the signal; "the same issue in other words" would need a third model.
      - edges only ACROSS slots a and b: findings that share a target; components by union-find; a component with
        members of both slots is one Finding, by {A, B}: targets the union (in `order`, the round's item order),
        type the best ranked (TYPE_RANK), notes[slot] that slot's problems joined with "；" (two at most, then "…")
      - a component of more than 4 raw findings or 4 targets is split back into its cross-slot pairs (a chain
        a1-b1-a2-b2 over unrelated items must not become one line); a finding with no target never merges
      - CHECK findings stay single (code is not a model)
      - order: both first; then TYPE_RANK; then the first target's position in `order`; CHECK last."""
    nodes = list(a) + list(b)
    parent = list(range(len(nodes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges = []
    for i in range(len(a)):
        for j in range(len(a), len(nodes)):
            shared = len(set(nodes[i].targets) & set(nodes[j].targets))
            if shared:
                edges.append((i, j, shared))
                parent[root(i)] = root(j)
    components: Dict[int, List[int]] = collections.OrderedDict()
    for i in range(len(nodes)):
        components.setdefault(root(i), []).append(i)
    groups: List[List[int]] = []
    for members in components.values():
        if len({nodes[i].slot for i in members}) < 2:
            groups += [[i] for i in members]
        elif len(members) > 4 or len({t for i in members for t in nodes[i].targets}) > 4:
            groups += _pairs(members, edges)
        else:
            groups.append(members)
    pos = {t: k for k, t in enumerate(order)}
    built = [_finding([nodes[i] for i in g], pos) for g in groups] + [_finding([c], pos) for c in check]
    keys = [(f.type is FindingType.CHECK, not f.both, TYPE_RANK.index(f.type),
             pos.get(f.targets[0], len(pos)) if f.targets else len(pos) + 1, k) for k, f in enumerate(built)]
    ranked = [f for _, f in sorted(zip(keys, built), key=lambda kf: kf[0])]
    return [Finding("F-%d" % (first_fid + k), f.targets, f.type, f.by, f.notes, f.evidence, f.quote_ok,
                    f.unknown_targets) for k, f in enumerate(ranked)]


def _pairs(members: Sequence[int], edges: Sequence[Tuple[int, int, int]]) -> List[List[int]]:
    """A component cut back into cross-slot pairs, greedily (each a-finding with the free b-finding it shares the
    most targets with); what is left over stays single."""
    inside, used, out = set(members), set(), []
    for i, j, _ in sorted((e for e in edges if e[0] in inside and e[1] in inside), key=lambda e: (e[0], -e[2], e[1])):
        if i not in used and j not in used:
            used |= {i, j}
            out.append([i, j])
    return out + [[k] for k in members if k not in used]


def _finding(members: Sequence[RawFinding], pos: Dict[str, int]) -> Finding:
    targets = sorted(dict.fromkeys(t for m in members for t in m.targets), key=lambda t: pos.get(t, len(pos)))
    slots = sorted({m.slot for m in members}, key=lambda s: s.value)
    notes, evidence, quote_ok = {}, {}, {}
    for s in slots:
        mine = [m for m in members if m.slot is s]
        problems = [m.problem for m in mine]
        notes[s.value] = "；".join(problems[:2]) + ("；…" if len(problems) > 2 else "")
        quotes = list(dict.fromkeys(m.quote for m in mine if m.quote))
        if quotes:
            evidence[s.value] = "；".join(quotes)
        checked = [m.quote_found for m in mine if m.quote_found is not None]
        if checked:
            quote_ok[s.value] = all(checked)
    return Finding("", tuple(targets), min((m.type for m in members), key=TYPE_RANK.index), frozenset(slots), notes,
                   evidence, quote_ok, tuple(dict.fromkeys(t for m in members for t in m.unknown_targets)))


# ---------- the reviewer agent spec ----------

def ensure_reviewer_spec(home: Optional[str] = None, install: bool = True, force: bool = False) -> Tuple[bool, str]:
    """(usable, note): <home or ~>/.kiro/agents/brain-reviewer.json is brain-kit's reviewer spec (crew/
    brain-reviewer.json). `install`: write it when it is absent and the folder is writable (False: only say how it
    is, for `brain doctor`). `force`: also replace a spec that is not ours (`brain crew-agent --force`), keeping it
    as a copy next to it (_keep_copy). A spec kiro-cli rewrote into its own format (chat.enableAutoAgentUpgrade,
    leaving .bak files) is still ours while it is named brain-reviewer, can only read (see _differs) and has no hook
    and no MCP server; anything else there is left alone unless forced, `usable` False with a note naming `brain
    crew-agent --force`. The note is one Chinese sentence in every case (the doctor prints it as a row)."""
    path = _user_spec(home)
    shown = "~/.kiro/agents/%s.json" % AGENT
    ship = load_json(REVIEWER_SPEC)
    existed = os.path.lexists(path)
    if existed:
        try:
            off = _differs(load_json(path), ship)
        except (OSError, ValueError):
            off = None
        if off == []:
            return True, "%s 是 brain-kit 的评审 agent（只读，模型由 kit.json 定）" % shown
        why = "读不了或不是 JSON" if off is None else "不是 brain-kit 的评审 agent（%s 不一样）" % "、".join(off)
        if not force:
            return False, "%s %s：没动它；要换成 brain-kit 的，运行 `brain crew-agent --force`" % (shown, why)
    elif not install:
        return False, "还没装 %s：运行 `brain crew-agent` 装上它（`brain review` 第一次预审时也会装）" % shown
    try:
        kept = _keep_copy(path) if existed else None
        write_json(path, ship)
    except OSError as e:
        return False, "装不上 %s（%s）：在普通终端里运行 `brain crew-agent` 装上它" % (shown, e.strerror or e)
    if kept is None:
        return True, "装好了 %s" % shown
    return True, "换成了 brain-kit 的 %s（原来那份留在 %s）" % (shown, os.path.join(os.path.dirname(shown), kept))


def _keep_copy(path: str) -> str:
    """Copy `path` (a symlink as itself) to the first free <path>.bak, <path>.bak.1, ...: kiro-cli's names for the
    copies it keeps, so one it left is never overwritten. Returns the copy's file name."""
    for n in itertools.count():
        copy = path + ".bak" + (".%d" % n if n else "")
        if not os.path.lexists(copy):
            shutil.copy2(path, copy, follow_symlinks=False)
            return os.path.basename(copy)


def _user_spec(home: Optional[str] = None) -> str:
    """<home or ~>/.kiro/agents/brain-reviewer.json: the spec `brain crew-agent` installs and kiro-cli runs."""
    return os.path.join(home or os.path.expanduser("~"), ".kiro", "agents", AGENT + ".json")


def _differs(have, ship: dict) -> List[str]:
    """The fields that make a spec not brain-kit's reviewer, in any format kiro-cli writes: another name, a tool or an
    allowed tool or an allow rule that does more than read (READ_ONLY), any hook or MCP server at all. Not "equal to
    the file we wrote": kiro-cli rewrites our own spec on first use (adds the `web` alias, permission rules and
    `hooks: []`), and refusing that would refuse it again after every `brain crew-agent --force`."""
    if not isinstance(have, dict):
        return ["整个文件"]

    def reads_only(v):
        return isinstance(v, list) and all(isinstance(x, str) and x in READ_ONLY for x in v)

    def allows_only_reading(p):
        rules = p.get("rules") if isinstance(p, dict) else None
        return p is None or (isinstance(rules, list) and all(isinstance(r, dict) for r in rules) and
                             reads_only([r.get("capability") for r in rules if r.get("effect") == "allow"]))

    def empty(v):
        return not v or (isinstance(v, dict) and not any(v.values()))

    off = ["name"] if have.get("name") != ship["name"] else []
    off += [k for k in ("tools", "allowedTools") if not reads_only(have.get(k))]
    off += [] if allows_only_reading(have.get("permissions")) else ["permissions"]
    return off + [k for k in ("hooks", "mcpServers") if not empty(have.get(k))]
