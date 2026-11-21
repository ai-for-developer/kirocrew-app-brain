"""Expand-contract rules for database migrations (`brain-ci migrations`), PostgreSQL syntax.

During a canary the old version runs against the new schema: a migration may add, never take away or change what
the old version uses, and may not lock a busy table. Each rule has a stable id; a statement that breaks one fails
the check, unless the line comment `-- brain: contract <rule-id> <reason>` stands directly above it (contiguous
comment lines, no blank line): then it is a warning that names the reason, for a reviewer to accept a deliberate
contract step (the old version stopped using the column a release ago). An edited merged migration has no such
escape. A table that a plain CREATE TABLE made earlier in this MR's migrations (files in path order) is new: nothing
old uses it, so no rule applies to it. CREATE TABLE IF NOT EXISTS makes no table new, since the table may be an old
one. A DROP TABLE before the CREATE TABLE still drops the old table.

Not a SQL parser: comments and string literals (including $tag$ quoting) are blanked before matching, statements end
at ';', keywords match in any case and across lines. A DO block's body runs at migration time and is checked like
the file; function bodies and dynamic SQL (EXECUTE '...') are not read."""
from __future__ import annotations

import re
from typing import Dict, NamedTuple

from . import riskpaths
from .common import BrainError

RULES = {
    "drop-column": "the old version still reads and writes the column while the canary runs",
    "drop-table": "the old version still uses the table while the canary runs",
    "rename-column": "the old version still uses the old name while the canary runs",
    "rename-table": "the old version still uses the old name while the canary runs",
    "alter-column-type": "the old version still reads and writes the old type, and the change can rewrite the table "
                         "under a lock",
    "add-column-not-null": "rows the old version inserts have no value for it, nor do existing rows: give it a "
                           "DEFAULT, or add it nullable first",
    "set-not-null": "the old version may still write NULLs, and the check scans the table under a lock that blocks "
                    "writes",
    "index-not-concurrent": "CREATE INDEX without CONCURRENTLY blocks writes to the table until the index is built",
    "edit-merged": "a merged migration has already run: editing it changes nothing where it ran, and environments drift "
                   "apart; put the change in a new migration",
}

NAME = r"[^\s(),;]+"                                # a name as written: x, "Order_Items", public.orders, ${schema}.x
CONTRACT_RE = re.compile(r"^\s*--\s*brain:\s*contract\s+(\S+)\s*(.*)$", re.I)
DOLLAR_RE = re.compile(r"\$(?:[^\W\d]\w*)?\$")
DO_RE = re.compile(r"\s*DO\b", re.I)
CREATE_TABLE_RE = re.compile(r"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:(?:TEMPORARY|TEMP|UNLOGGED)\s+)?TABLE\s+"
                             r"(IF\s+NOT\s+EXISTS\s+)?(%s)" % NAME, re.I)
DROP_TABLE_RE = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(%s(?:\s*,\s*%s)*)" % (NAME, NAME), re.I)
CREATE_INDEX_RE = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b(\s+CONCURRENTLY\b)?.*?\bON\s+(?:ONLY\s+)?(%s)" % NAME,
                             re.I | re.S)
ALTER_TABLE_RE = re.compile(r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(%s)" % NAME, re.I)
ADD_COLUMN_RE = re.compile(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
                           r"(?!(?:CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK|EXCLUDE)\b)(%s)(.*)" % NAME, re.I | re.S)


def _action(pattern):
    """A matcher for one ALTER TABLE action: the column it names ('' for the table itself), or None."""
    rx = re.compile(pattern, re.I | re.S)

    def match(action):
        m = rx.match(action)
        return None if m is None else (m.group(1) if m.groups() else "")
    return match


def _adds_not_null(action):
    m = ADD_COLUMN_RE.match(action)
    if m is None:
        return None
    rest = re.sub(r"\bIS\s+NOT\s+NULL\b|\bDEFAULT\s+NULL\b", " ", m.group(2), flags=re.I)
    if (re.search(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", rest, re.I)
            and not re.search(r"\bDEFAULT\b|\bGENERATED\b|\b(?:SMALL|BIG)?SERIAL[248]?\b", rest, re.I)):
        return m.group(1)
    return None


# one action of ALTER TABLE <table> ..., (a part between top-level commas) -> the rule it breaks
ACTIONS = (
    ("rename-table", _action(r"RENAME\s+TO\b")),
    ("rename-column", _action(r"RENAME\s+(?:COLUMN\s+)?(?!(?:COLUMN|CONSTRAINT|TO)\b)(%s)\s+TO\b" % NAME)),
    ("drop-column", _action(r"DROP\s+(?!CONSTRAINT\b)(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(%s)" % NAME)),
    ("alter-column-type", _action(r"ALTER\s+(?:COLUMN\s+)?(?!(?:COLUMN|CONSTRAINT)\b)(%s)\s+(?:SET\s+DATA\s+)?TYPE\b"
                                  % NAME)),
    ("set-not-null", _action(r"ALTER\s+(?:COLUMN\s+)?(?!(?:COLUMN|CONSTRAINT)\b)(%s)\s+SET\s+NOT\s+NULL\b" % NAME)),
    ("add-column-not-null", _adds_not_null),
)


class Statement(NamedTuple):
    line: int                   # the line of the file its first word is on, from 1
    sql: str                    # its text from that word, comments and string literals blanked
    contracts: Dict[str, str]   # {rule id: reason} of the `-- brain: contract` lines directly above it


def settings(cfg) -> list:
    """The migration file patterns (migrations.paths, gates/risk-paths rules) of the kit config. BrainError when wrong."""
    c = cfg.get("migrations")
    paths = c.get("paths") if isinstance(c, dict) else None
    if not isinstance(paths, list) or not paths or not all(isinstance(x, str) and x.strip() for x in paths):
        raise BrainError("migrations.paths must be a list of path patterns (gates/risk-paths rules), like "
                         "[\"migrations/*.sql\"]")
    bad = [why for why in (riskpaths.problem(x) for x in paths) if why]
    if bad:
        raise BrainError("migrations.paths: %s" % "; ".join(bad))
    return [x.strip() for x in paths]


def _blank(text) -> str:
    return re.sub(r"[^\n]", " ", text)


def _word(c) -> bool:
    return c.isalnum() or c in "_$"


def _strip(text) -> tuple:
    """(text with comments and string literals blanked, [(start, end)] of each literal's content). Same length and
    line breaks as `text`. Postgres rules: '' inside '...', backslash escapes in E'...', nested /* */, $tag$...$tag$.
    Inside "quoted names" every character that is not a letter or digit becomes '_' (so a ';' there ends nothing)."""
    out, bodies, i, n = [], [], 0, len(text)
    while i < n:
        c, j = text[i], None
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif text.startswith("/*", i):
            j, depth = i + 2, 1
            while j < n and depth:
                if text.startswith("/*", j):
                    j, depth = j + 2, depth + 1
                elif text.startswith("*/", j):
                    j, depth = j + 2, depth - 1
                else:
                    j += 1
        elif c == "'":
            esc = i > 0 and text[i - 1] in "eE" and not (i > 1 and _word(text[i - 2]))
            j = i + 1
            while j < n and text[j] != "'" or text.startswith("''", j):
                j += 2 if (esc and text[j] == "\\") or text.startswith("''", j) else 1
            bodies.append((i + 1, min(j, n)))
            j = min(j + 1, n)
        elif c == '"':
            j = i + 1
            while j < n and (text[j] != '"' or text.startswith('""', j)):
                j += 2 if text.startswith('""', j) else 1
            j = min(j + 1, n)
            out.append('"' + re.sub(r"[^\w\n]", "_", text[i + 1:j - 1]) + text[j - 1:j])
            i = j
            continue
        elif c == "$" and not (i > 0 and _word(text[i - 1])) and DOLLAR_RE.match(text, i):
            tag = DOLLAR_RE.match(text, i).group()
            end = text.find(tag, i + len(tag))
            bodies.append((i + len(tag), n if end < 0 else end))
            j = n if end < 0 else end + len(tag)
        if j is None:
            out.append(c)
            i += 1
        else:
            out.append(_blank(text[i:j]))
            i = j
    return "".join(out), bodies


def _contracts_above(lines, clean_lines, i) -> dict:
    """{rule id: reason} of the `-- brain: contract` lines directly above line i (from 0): the comment lines right
    before it, up to a blank line or code."""
    out = {}
    j = i - 1
    while j >= 0 and lines[j].strip() and not clean_lines[j].strip():
        m = CONTRACT_RE.match(lines[j])
        if m:
            out.setdefault(m.group(1).lower(), m.group(2).strip())
        j -= 1
    return out


def statements(text, first_line=1, outer=None) -> list:
    """[Statement] of a migration file. A DO block stands for the statements of its body, each also covered by the
    contract lines above the DO."""
    clean, bodies = _strip(text)
    lines, clean_lines = text.split("\n"), clean.split("\n")
    out = []
    for m in re.finditer(r"[^;]+", clean):
        sql = m.group()
        if not sql.strip():
            continue
        start = m.start() + len(sql) - len(sql.lstrip())
        row = clean.count("\n", 0, start)
        contracts = {**(outer or {}), **_contracts_above(lines, clean_lines, row)}
        body = [b for b in bodies if m.start() <= b[0] < m.end()] if DO_RE.match(sql) else []
        if body:
            b0, b1 = body[0]
            out += statements(text[b0:b1], first_line + text.count("\n", 0, b0), contracts)
        else:
            out.append(Statement(first_line + row, sql.lstrip(), contracts))
    return out


def _table(name) -> str:
    """A table name the way Postgres compares it: quoted parts as written, the others in lower case. public.orders
    and orders stay two names, so a table written both ways counts as old."""
    return ".".join(p[1:-1] if len(p) > 1 and p[0] == p[-1] == '"' else p.lower() for p in name.split("."))


def created_tables(sql) -> set:
    """The tables one statement makes with a plain CREATE TABLE. It fails when the table exists, so they are new."""
    return {_table(m.group(2)) for m in CREATE_TABLE_RE.finditer(sql) if not m.group(1)}


def _top_level_parts(text, at) -> list:
    """[(offset, part)]: `text` from `at` split at the commas outside parentheses, each part without leading space."""
    parts, depth, start = [], 0, at
    for i in range(at, len(text) + 1):
        c = text[i] if i < len(text) else ","
        depth += (c == "(") - (c == ")")
        if c == "," and depth <= 0:
            part = text[start:i]
            parts.append((start + len(part) - len(part.lstrip()), part.strip()))
            start = i + 1
    return parts


def hits(sql, new) -> list:
    """[(rule id, what, offset in sql)] that one statement breaks; tables in `new` (made by a plain CREATE TABLE
    earlier in this MR's migrations) are exempt."""
    out = []
    for m in DROP_TABLE_RE.finditer(sql):
        old = [x.strip() for x in m.group(1).split(",") if _table(x.strip()) not in new]
        if old:
            out.append(("drop-table", ", ".join(old), m.start()))
    for m in CREATE_INDEX_RE.finditer(sql):
        if not m.group(1) and _table(m.group(2)) not in new:
            out.append(("index-not-concurrent", m.group(2), m.start()))
    for m in ALTER_TABLE_RE.finditer(sql):
        table = m.group(1)
        if _table(table) in new:
            continue
        for at, part in _top_level_parts(sql, m.end()):
            for rule, match in ACTIONS:
                what = match(part)
                if what is not None:
                    out.append((rule, table + "." + what if what else table, at))
                    break
    return out


def _escape(rule) -> str:
    return "-- brain: contract %s <reason>" % rule


def check(new, edited, target) -> tuple:
    """(errors, warnings, lines) for the migration files an MR adds or changes: `new` {path: text} (not on the target
    branch), `edited` [path] (on the target branch `target`, changed or deleted by the MR)."""
    errors = ["%s edit-merged: merged on %s already; %s (no contract comment lifts this rule)"
              % (p, target, RULES["edit-merged"]) for p in edited]
    warnings, made = [], set()
    for path, text in sorted(new.items()):
        for st in statements(text.lstrip("\ufeff")):
            for rule, what, at in hits(st.sql, made):
                line = st.line + st.sql.count("\n", 0, at)
                where, reason = "%s:%d %s %s" % (path, line, rule, what), st.contracts.get(rule)
                if reason:
                    warnings.append("%s: contract step (%s): %s" % (where, reason, RULES[rule]))
                elif reason is not None:
                    errors.append("%s: %s. The `%s` line above it gives no reason" % (where, RULES[rule], _escape(rule)))
                else:
                    errors.append("%s: %s. A deliberate contract step: put `%s` directly above the statement"
                                  % (where, RULES[rule], _escape(rule)))
            made |= created_tables(st.sql)
    shown = sorted(set(new) | set(edited))
    lines = (["%d migration file%s added or changed: %s" % (len(shown), "" if len(shown) == 1 else "s",
                                                           ", ".join(shown[:10]) + (", ..." if len(shown) > 10 else ""))]
             if shown else ["no migration files added or changed"])
    return errors, warnings, lines
