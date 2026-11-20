"""The staging service's log and VERSION file, as 测试环境报错 (team/staging.py) reads them, and the errors in that log.

Where they are is bridge.json's, per project: ``"projects": [{"project": "grp/repo", "staging": {"log": {"kind":
"file", "path": …}, "version": {"kind": "file", "path": …}}}]``. A kind is a reader in LOGS or VERSIONS; ``file`` is the
only one so far, and an ``http`` one goes beside it. A log reader starts from a cursor of its own (JSON, kept in the
ledger) and gives the cursor after each line it read, so the next round, or the next start, reads on from there. The
first read of a log starts at its end: errors logged before the runner watched are not its.

errors_in finds the errors in plain text or JSON lines: a line at level ERROR or above, a Python traceback (with the
ERROR line that logged it), and a request answered 5xx (one right after a traceback is the request that raised it, and
joins it). Each error has a signature: a traceback's is its exception type and innermost project frame, any other's its
message with the numbers and ids taken out. grouped() counts them by signature.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Protocol, Sequence

from .patrol import ANSI_RE

SAMPLE_LINES = 80           # the most lines an excerpt keeps
LINE_CHARS = 300
SIGNATURE_CHARS = 160
READ_BYTES = 1 << 20        # read from a log in one round at most: the rest is read the next round
JOIN_LINES = 3              # a 5xx this few lines after a traceback is the request that raised it

TRACEBACK = "Traceback (most recent call last):"
CHAINED = ("During handling of the above exception, another exception occurred:",
           "The above exception was the direct cause of the following exception:")
FRAME_RE = re.compile(r'^\s+File "([^"]+)", line \d+, in (\S+)')
EXC_RE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")
LIBRARY_RE = re.compile(r"[/\\](?:site|dist)-packages[/\\]|[/\\]lib[/\\]python\d|^<")
LEVEL_RE = re.compile(r"(?<![\w-])(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|SEVERE|CRITICAL|FATAL|ALERT|EMERG(?:ENCY)?"
                      r"|PANIC)(?![\w-])|(?i:(?:\[|\blevel=|\blvl=)(trace|debug|info|notice|warn(?:ing)?|error|err|severe"
                      r"|crit(?:ical)?|fatal|alert|emerg(?:ency)?|panic)\b)")
ERROR_LEVELS = {"ERROR", "ERR", "SEVERE", "CRITICAL", "CRIT", "FATAL", "ALERT", "EMERG", "EMERGENCY", "PANIC"}
REQUEST_RE = re.compile(r'"(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) (\S+)(?: HTTP/[\d.]+)?" (\d{3})\b')
ID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
                   r"|\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
JSON_LEVEL = ("level", "levelname", "severity", "lvl")
JSON_MESSAGE = ("message", "msg", "event")
JSON_TRACE = ("exc_info", "exception", "traceback", "stack_trace", "exc_text", "stack")


# ---- where the log and the deployed commit are read from ---------------------------------------------------- #

@dataclass(frozen=True)
class Chunk:
    lines: tuple[str, ...]
    cursors: tuple[dict[str, Any], ...]     # cursors[n]: where the next read starts once the first n lines are used


class LogSource(Protocol):
    def read(self, cursor: Mapping[str, Any] | None) -> Chunk: ...


class VersionSource(Protocol):
    def read(self) -> str: ...


@dataclass(frozen=True)
class FileLog:
    """A log file the service appends to. Its cursor is the file (device and inode) and a byte offset in it: another
    file at the path (a rotated log) or a shorter one (truncated) is read from its start. Only whole lines are read."""
    path: str
    limit: int = READ_BYTES

    def read(self, cursor: Mapping[str, Any] | None) -> Chunk:
        with open(self.path, "rb") as fh:
            st = os.fstat(fh.fileno())
            ident = "%d:%d" % (st.st_dev, st.st_ino)
            if cursor is None:
                return Chunk((), ({"file": ident, "at": st.st_size},))
            at = int(cursor.get("at") or 0) if cursor.get("file") == ident else 0
            at = at if at <= st.st_size else 0
            fh.seek(at)
            data = fh.read(self.limit)
        lines, cursors, start = [], [{"file": ident, "at": at}], 0
        while (nl := data.find(b"\n", start)) >= 0:
            lines.append(data[start:nl].decode("utf-8", "replace").rstrip("\r"))
            cursors.append({"file": ident, "at": at + nl + 1})
            start = nl + 1
        if not lines and len(data) >= self.limit:       # one line longer than a whole read: taken as it is
            lines.append(data.decode("utf-8", "replace"))
            cursors.append({"file": ident, "at": at + len(data)})
        return Chunk(tuple(lines), tuple(cursors))


@dataclass(frozen=True)
class FileVersion:
    """A VERSION file: the deployed commit's sha, its first word."""
    path: str

    def read(self) -> str:
        with open(self.path, "rb") as fh:
            words = fh.read(4096).decode("utf-8", "replace").split()
        sha = words[0].lower() if words else ""
        if not SHA_RE.match(sha):
            raise ValueError("%s 里不是一个提交号：%r" % (self.path, sha[:60]))
        return sha


def _path(d: Mapping[str, Any]) -> str:
    p = d.get("path")
    if not isinstance(p, str) or not p.strip():
        raise ValueError("要写 path")
    return os.path.expanduser(p.strip())


LOGS: dict[str, Callable[[Mapping[str, Any]], LogSource]] = {"file": lambda d: FileLog(_path(d))}
VERSIONS: dict[str, Callable[[Mapping[str, Any]], VersionSource]] = {"file": lambda d: FileVersion(_path(d))}


@dataclass(frozen=True)
class Staging:
    log: LogSource
    version: VersionSource


def staging_of(raw: Any) -> Staging:
    """bridge.json's staging block of a project as its two readers; ValueError, in words, when it cannot be."""
    if not isinstance(raw, dict):
        raise ValueError("staging 要写成 {\"log\": {…}, \"version\": {…}}")
    made = []
    for key, kinds in (("log", LOGS), ("version", VERSIONS)):
        d = raw.get(key)
        kind = d.get("kind") if isinstance(d, dict) else None
        if kind not in kinds:
            raise ValueError("staging.%s 的 kind 要是 %s 之一" % (key, "、".join(kinds)))
        try:
            made.append(kinds[kind](d))
        except ValueError as exc:
            raise ValueError("staging.%s：%s" % (key, exc)) from None
    return Staging(*made)


# ---- the errors in a log ------------------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ErrorEvent:
    signature: str
    lines: tuple[str, ...]          # as the log has them: the excerpt


@dataclass(frozen=True)
class ErrorGroup:
    """One signature's errors: from when it was first seen to when last, how many, the newest one's lines and the
    commit deployed then."""
    signature: str
    first_seen: float
    last_seen: float
    count: int
    sample: tuple[str, ...]
    deployed_sha: str

    def merged(self, newer: "ErrorGroup") -> "ErrorGroup":
        return replace(newer, first_seen=self.first_seen, count=self.count + newer.count)

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "ErrorGroup":
        return cls(str(d["signature"]), float(d["first_seen"]), float(d["last_seen"]), int(d["count"]),
                   tuple(d.get("sample") or ()), str(d.get("deployed_sha") or ""))


def grouped(events: Sequence[ErrorEvent], now: float, sha: str) -> list[ErrorGroup]:
    """*events* by signature, in the order each first came: its count, and the excerpt of its newest one."""
    out: dict[str, ErrorGroup] = {}
    for e in events:
        g = out.get(e.signature)
        out[e.signature] = ErrorGroup(e.signature, now, now, (g.count if g else 0) + 1, e.lines, sha)
    return list(out.values())


def short_id(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]


def short(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def normalized(text: str) -> str:
    """A message without what changes from one error to the next: ids, hashes and numbers."""
    return " ".join(NUM_RE.sub("<n>", ID_RE.sub("<id>", text)).split())[:SIGNATURE_CHARS]


def _excerpt(lines: Sequence[str]) -> tuple[str, ...]:
    lines = [x.rstrip()[:LINE_CHARS] for x in lines]
    if len(lines) <= SAMPLE_LINES:
        return tuple(lines)
    head = SAMPLE_LINES // 8
    tail = SAMPLE_LINES - head - 1
    return tuple(lines[:head] + ["…（中间 %d 行略去）" % (len(lines) - head - tail)] + lines[-tail:])


def _level(line: str) -> str:
    """The line's level, the first level word in it (INFO …, [error] …, level=error …), upper case; '' when none."""
    m = LEVEL_RE.search(line)
    return (m.group(1) or m.group(2)).upper() if m else ""


def _message(line: str) -> str:
    """What an ERROR line says after its level."""
    m = LEVEL_RE.search(line)
    rest = line[m.end():].lstrip(" :]-|\t") if m else line
    return normalized(rest or line)


def _request(method: str, path: str, status: int) -> str:
    shape = "/".join("<n>" if s.isdigit() else "<id>" if ID_RE.fullmatch(s) else s
                     for s in path.split("?", 1)[0].split("/"))
    return "HTTP %d %s %s" % (status, method, shape[:SIGNATURE_CHARS])


def _short_path(path: str) -> str:
    return "/".join(path.replace("\\", "/").split("/")[-2:])


def traceback_signature(lines: Sequence[str]) -> str:
    """A traceback's signature: the last exception's type and the innermost frame of project code where it was raised
    (a library's frame only when there is no other), the file by its last two parts."""
    start = max((i for i, x in enumerate(lines) if TRACEBACK in x), default=0)
    frames = [m for m in (FRAME_RE.match(x) for x in lines[start:]) if m]
    excs = [m.group(1) for m in (EXC_RE.match(x) for x in lines[start + 1:] if x[:1] not in (" ", "\t")) if m]
    exc = excs[-1] if excs else "Traceback"
    frame = ([f for f in frames if not LIBRARY_RE.search(f.group(1))] or frames or [None])[-1]
    return exc if frame is None else "%s at %s in %s" % (exc, _short_path(frame.group(1)), frame.group(2))


def _under(line: str, prefix: str) -> str | None:
    """*line* without the *prefix* every line of a traceback has (a container's name, say); None without it."""
    return line[len(prefix):] if line.startswith(prefix) else None


def _traceback_end(lines: Sequence[str], i: int, prefix: str) -> tuple[int, bool]:
    """(the index after the traceback whose header is lines[i], whether it is whole): it ends after its exception line
    (and the tracebacks chained to it), or at a line that is not its own. One the lines end inside is not whole yet."""
    j, said = i + 1, False
    while j < len(lines):
        body = _under(lines[j], prefix)
        if body is None:
            return j, True
        text = body.strip()
        if body[:1] in (" ", "\t"):
            j += 1
        elif text in CHAINED or text == TRACEBACK:
            j, said = j + 1, False
        elif not text:
            nxt = next((k for k in range(j + 1, len(lines)) if (_under(lines[k], prefix) or "x").strip()), None)
            if nxt is None or (_under(lines[nxt], prefix) or "").strip() not in CHAINED + (TRACEBACK,):
                return j, said or nxt is not None
            j = nxt
        elif not said and EXC_RE.match(text):
            j, said = j + 1, True
        else:
            return j, True
    return j, said


def _json_error(d: Mapping[str, Any], line: str) -> ErrorEvent | None:
    level = next((str(d[k]).upper() for k in JSON_LEVEL if isinstance(d.get(k), str)), "")
    message = next((str(d[k]) for k in JSON_MESSAGE if isinstance(d.get(k), str)), "")
    trace = next((d[k] for k in JSON_TRACE if isinstance(d.get(k), str) and d[k].strip()), "")
    status = next((d[k] for k in ("status", "status_code") if isinstance(d.get(k), int)), 0)
    shown = _excerpt([line] + trace.split("\n"))
    if TRACEBACK in trace:
        return ErrorEvent(traceback_signature(trace.split("\n")), shown)
    if level in ERROR_LEVELS:
        return ErrorEvent(normalized(message or line), shown)
    if status >= 500:
        path = next((str(d[k]) for k in ("path", "url", "uri") if isinstance(d.get(k), str)), "")
        return ErrorEvent(_request(str(d.get("method") or "").upper() or "?", path or "?", status), shown)
    return None


def _json(line: str) -> dict[str, Any] | None:
    if not line.lstrip().startswith("{"):
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def errors_in(lines: Sequence[str]) -> tuple[list[ErrorEvent], int]:
    """The errors in *lines*, and how many of the lines they used: a traceback still being written at the end is left
    for the next read, unless the read began with it (it was left once already)."""
    lines = [ANSI_RE.sub("", x) for x in lines]
    events: list[ErrorEvent] = []
    joinable: int | None = None                 # the line after the newest traceback, while a 5xx may join it
    i = 0
    while i < len(lines):
        line = lines[i]
        d = _json(line)
        if d is not None:
            e = _json_error(d, line)
            if e is not None:
                events.append(e)
                joinable = None
            i += 1
            continue
        req = REQUEST_RE.search(line)
        if req and int(req.group(3)) >= 500:
            if joinable is not None and i - joinable < JOIN_LINES:
                events[-1] = replace(events[-1], lines=_excerpt(events[-1].lines + (line,)))
            else:
                events.append(ErrorEvent(_request(req.group(1), req.group(2), int(req.group(3))), _excerpt([line])))
            joinable = None
            i += 1
            continue
        logged = _level(line) in ERROR_LEVELS and TRACEBACK not in line
        head = i + 1 if logged and i + 1 < len(lines) and TRACEBACK in lines[i + 1] else i
        if TRACEBACK in lines[head]:
            prefix = lines[head][:lines[head].index(TRACEBACK)]
            end, whole = _traceback_end(lines, head, prefix)
            if not whole and i > 0:
                return events, i
            body = [_under(x, prefix) or "" for x in lines[head:end]]
            events.append(ErrorEvent(traceback_signature(body), _excerpt(lines[i:end])))
            i = joinable = end
            continue
        if logged:
            end = i + 1
            while end < len(lines) and lines[end][:1] in (" ", "\t") and lines[end].strip():
                end += 1
            events.append(ErrorEvent(_message(line), _excerpt(lines[i:end])))
            i, joinable = end, None
            continue
        i += 1
    return events, len(lines)
