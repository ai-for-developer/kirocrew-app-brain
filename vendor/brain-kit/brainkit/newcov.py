"""New code coverage (`brain-ci new-coverage`): how much of the code an MR adds its tests run.

Read from the project's Cobertura report (coverage.xml: coverage.py and pytest-cov, Istanbul, JaCoCo through a
converter) and the MR's diff. Counted: the lines the MR adds that the report lists as executable, in files the report
knows, except tests (TEST_PATHS) and the target branch's new_coverage.ignore. Line coverage: a line the report gives a
hit is covered. A file no test imports is in the report only when the test job measures the whole source tree
(coverage `source`, `--cov=<package>`); otherwise it is not counted at all."""
from __future__ import annotations

import posixpath
import re
from typing import List, NamedTuple
from xml.etree import ElementTree as ET

from . import riskpaths
from .common import BrainError

# tests are never the new code that needs tests (gates/risk-paths rules: no '/' but a trailing one is any depth)
TEST_PATHS = ("test/", "tests/", "__tests__/", "spec/", "test_*.py", "*_test.py", "conftest.py", "*_test.go",
              "*.test.*", "*.spec.*")
HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


class FileCoverage(NamedTuple):
    path: str
    lines: List[int]        # the executable lines the MR adds, ascending
    uncovered: List[int]    # those no test ran


def settings(cfg) -> tuple:
    """(block, warn, patterns not counted) from the kit config's new_coverage. BrainError when it is wrong."""
    c = cfg.get("new_coverage")
    if not isinstance(c, dict):
        raise BrainError("new_coverage must be an object like {\"block\": 60, \"warn\": 80, \"ignore\": []}")
    probs = ["new_coverage.%s must be a number from 0 to 100" % k for k in ("block", "warn")
             if isinstance(c.get(k), bool) or not isinstance(c.get(k), (int, float)) or not 0 <= c[k] <= 100]
    if not probs and c["block"] > c["warn"]:
        probs.append("new_coverage.block (%g) is above new_coverage.warn (%g)" % (c["block"], c["warn"]))
    ignore = c.get("ignore")
    if not isinstance(ignore, list) or not all(isinstance(x, str) and x.strip() for x in ignore):
        probs.append("new_coverage.ignore must be a list of paths (gates/risk-paths rules)")
    else:
        probs += ["new_coverage.ignore: %s" % why for why in (riskpaths.problem(x) for x in ignore) if why]
    if probs:
        raise BrainError("; ".join(probs))
    return c["block"], c["warn"], list(TEST_PATHS) + [x.strip() for x in ignore]


def parse_report(data: bytes) -> list:
    """[(candidate paths, {line number: hits})] for each file of a Cobertura report. The candidates are its filename
    and the filename joined to each <source> folder (absolute ones are from the machine that ran the tests).
    BrainError when the data is not a Cobertura report."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise BrainError("not valid XML (%s)" % e)
    if root.tag != "coverage":
        raise BrainError("not a Cobertura report: its root element is <%s>, not <coverage>" % root.tag)
    sources = [s.text.strip().replace("\\", "/").rstrip("/") for s in root.iter("source") if (s.text or "").strip()]
    files = {}
    for cls in root.iter("class"):
        name = (cls.get("filename") or "").strip().replace("\\", "/")
        if not name:
            continue
        lines = files.setdefault(name, {})
        for line in cls.findall("lines/line"):
            try:
                n, hits = int(line.get("number")), int(line.get("hits"))
            except (TypeError, ValueError):
                raise BrainError("%s: a <line> without a whole number and hits" % name)
            lines[n] = max(hits, lines.get(n, 0))
    absolute = re.compile(r"^(?:/|[A-Za-z]:/)")
    return [([name] + ([] if absolute.match(name) else [s + "/" + name for s in sources]), lines)
            for name, lines in files.items()]


def resolve(entries, tracked) -> dict:
    """{repo path: {line: hits}}: each report file as the longest end of one of its candidate paths that is a file in
    `tracked` (the repository at HEAD). The tests may have run in another folder on another machine. Files that end in
    no tracked path are left out."""
    out = {}
    for candidates, lines in entries:
        best = ""
        for c in candidates:
            parts = [p for p in posixpath.normpath(c).split("/") if p]
            tail = next((t for t in ("/".join(parts[i:]) for i in range(len(parts))) if t in tracked), "")
            best = max(best, tail, key=len)
        if best:
            into = out.setdefault(best, {})
            for n, hits in lines.items():
                into[n] = max(hits, into.get(n, 0))
    return out


def _unquote(name) -> str:
    """A path as git prints it in a diff header: in "C quotes" when it has control characters, '"' or '\\'."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    out, s, i = bytearray(), name[1:-1], 0
    while i < len(s):
        if s[i] == "\\" and re.match(r"[0-7]{3}", s[i + 1:i + 4]):
            out.append(int(s[i + 1:i + 4], 8) & 0xFF)
            i += 4
        elif s[i] == "\\" and s[i + 1:i + 2] in C_ESCAPES:
            out.append(C_ESCAPES[s[i + 1]])
            i += 2
        else:
            out += s[i].encode("utf-8", "surrogateescape")
            i += 1
    return out.decode("utf-8", "surrogateescape")


def added_lines(patch: str) -> dict:
    """{path: line numbers} that a `git diff -U0 --src-prefix=a/ --dst-prefix=b/` patch adds, by the new path. Each
    hunk is read for exactly as many lines as its header counts, so no added line is taken for a file header."""
    out, path, rows, i = {}, None, patch.split("\n"), 0
    while i < len(rows):
        row = rows[i]
        i += 1
        if row.startswith("diff --git "):
            path = None
        elif row.startswith("+++ "):
            name = _unquote(row[4:].rstrip("\t"))         # git ends the name with a tab when it has a space
            path = name[2:] if name.startswith("b/") else None
        elif path is not None and HUNK_RE.match(row):
            m = HUNK_RE.match(row)
            old = int(m.group(1)) if m.group(1) is not None else 1
            new = int(m.group(3)) if m.group(3) is not None else 1
            n = int(m.group(2))
            while (old > 0 or new > 0) and i < len(rows):
                row = rows[i]
                i += 1
                if row.startswith("+"):
                    out.setdefault(path, set()).add(n)
                    n, new = n + 1, new - 1
                elif row.startswith("-"):
                    old -= 1
                elif not row.startswith("\\"):                # context (diff.interHunkContext; blank: suppressBlankEmpty)
                    n, new, old = n + 1, new - 1, old - 1
    return out


def measure(added, measured, skip) -> list:
    """[FileCoverage] of each file with added lines that the report counts as executable, except paths matching a
    pattern in `skip` (gates/risk-paths rules)."""
    out = []
    for path in sorted(added):
        lines = measured.get(path)
        if lines is None or any(riskpaths.matches(path, p) for p in skip):
            continue
        exe = sorted(n for n in added[path] if n in lines)
        if exe:
            out.append(FileCoverage(path, exe, [n for n in exe if lines[n] <= 0]))
    return out


def _pct(covered, total) -> str:
    """covered/total as a percentage, rounded down to 0.1: 59.96 % shows as 59.9 %, never as the 60 % it is below."""
    tenths = covered * 1000 // total
    return "%d.%d%%" % (tenths // 10, tenths % 10)


def _ranges(nums, width=100) -> str:
    """3-5, 9, 12: the line numbers, a run of consecutive ones as a range, cut after about `width` characters."""
    parts = []
    for n in nums:
        if parts and parts[-1][1] == n - 1:
            parts[-1][1] = n
        else:
            parts.append([n, n])
    out = ""
    for a, b in parts:
        part = str(a) if a == b else "%d-%d" % (a, b)
        if out and len(out) + len(part) > width:
            return out + ", ..."
        out = out + ", " + part if out else part
    return out


def table(files, limit=10) -> list:
    """Rows of the files with uncovered added lines, worst covered first, at most `limit` of them."""
    worst = sorted((f for f in files if f.uncovered),
                   key=lambda f: ((len(f.lines) - len(f.uncovered)) / len(f.lines), -len(f.uncovered), f.path))
    if not worst:
        return []
    width = max([len("file")] + [len(f.path) for f in worst[:limit]])
    rows = ["  %-*s  %9s  %s" % (width, "file", "covered", "uncovered added lines")]
    rows += ["  %-*s  %9s  %s" % (width, f.path, "%d/%d" % (len(f.lines) - len(f.uncovered), len(f.lines)),
                                  _ranges(f.uncovered)) for f in worst[:limit]]
    if len(worst) > limit:
        rows.append("  ... and %d more files with uncovered added lines" % (len(worst) - limit))
    return rows


def verdict(files, block, warn) -> tuple:
    """(errors, warnings, lines) for the measured files: below `block` % covered fails, below `warn` % warns."""
    total = sum(len(f.lines) for f in files)
    if not total:
        return [], [], ["no added executable lines in the files the report measures: nothing to check"]
    covered = total - sum(len(f.uncovered) for f in files)
    pct = _pct(covered, total)
    lines = ["%d of %d added executable lines are covered (%s) in %d file%s; block below %g%%, warn below %g%%"
             % (covered, total, pct, len(files), "" if len(files) == 1 else "s", block, warn)] + table(files)
    if covered * 100 < block * total:
        return (["new code coverage %s is below %g%% (new_coverage.block on the target branch): add tests that run "
                 "the uncovered lines above" % (pct, block)], [], lines)
    if covered * 100 < warn * total:
        return [], ["new code coverage %s is below %g%% (new_coverage.warn): no test runs the uncovered lines above"
                    % (pct, warn)], lines
    return [], [], lines
