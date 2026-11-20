"""测试环境报错 (work source staging-errors, sources.py). Each round of the bridge, after MR 巡检, reads the new lines of the
staging log of every project whose kit.json switches it on (bridge.json says where the log and the VERSION file are:
stagelog.py), and keeps one row per error signature in <root>/staging.sqlite, with the log's cursor written in the same
transaction as the counts, so a restart reads on where the last round stopped.

A row is a state machine (Row.state):
  new      seen, its Issue not open: one is opened (label staging-error, with the error's lines, the deployed commit and
           the MR that merged it, as GitLab's commits/:sha/merge_requests says), or found (an open Issue that names the
           signature: a request whose answer was lost), or opened again (a closed one seen on another deployed commit).
  issued   its Issue open, no fix at work. A brain-patrol session of its own (slot staging-<project id>-<issue>) is given
           the fix, at most CAP at work at once counting MR 巡检's, in a clone of its own (<work>/staging/<project
           id>-<issue>) at the default branch's head: a branch fix/staging-<id> from it, a test that shows the error if
           it can, the fix, the tests, a push, an MR 「修复：…」 that says Closes #<issue>, a reply on the Issue. The row
           is fixing before its message goes, so a message KiroCrew did not answer is never sent again.
  fixing   an MR from the fix branch is there: fix_mr. A failed attempt: the turn ended without one, ended with
           KiroCrew's error, or neither within TIMEOUT_S (the turn is stopped), or KiroCrew refused the message; issued
           again, or stopped on the MAX_ATTEMPTS-th.
  fix_mr   the MR merged: closed. Closed without merging (a person said no): stopped.
  stopped  left to people; the Issue was told why, once.
  closed   its Issue closed (by the MR's Closes #, or a person). Seen again on another deployed commit, it is new again,
           and its next fix branch is fix/staging-<id>-<n>.
Every open state whose Issue was closed is closed (its turn stopped). An open Issue gets a comment when its count jumps:
doubled, and up by JUMP or more, since the Issue last said it.
A project the source is no longer on, or bridge.json no longer names, is forgotten: its turns stopped, its clones
deleted, its rows and cursor gone. Its Issues stay; an open one is found again when its error comes back.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .. import bk
from . import patrol, sources, stagelog
from .stagelog import ErrorGroup

KIND = sources.STAGING
LABEL = "staging-error"
CAP = patrol.CAP                    # fix sessions at work at once, MR 巡检's counted
TIMEOUT_S = patrol.TIMEOUT_S
MAX_ATTEMPTS = patrol.MAX_ATTEMPTS
JUMP = 10
FORGET_S = 30 * 86400               # a closed row not seen this long is dropped
MARK = "staging-error-id: %s"       # in an Issue's description: the signature it is about
NEW, ISSUED, FIXING, FIX_MR, STOPPED, CLOSED = "new", "issued", "fixing", "fix_mr", "stopped", "closed"
OPEN = (ISSUED, FIXING, FIX_MR, STOPPED)
OUTCOME = {"ended": "会话结束了，没开 MR", "failed": "会话报错停了", "timeout": "30 分钟没做完，停掉了",
           "refused": "KiroCrew 没收下消息"}


@dataclass(frozen=True)
class Link:
    iid: int
    url: str | None
    title: str = ""

    @classmethod
    def of(cls, d: Any) -> "Link | None":
        if not isinstance(d, dict) or not d.get("iid"):
            return None
        return cls(int(d["iid"]), d.get("url", d.get("web_url")) or None, str(d.get("title") or ""))


@dataclass(frozen=True)
class Row:
    project: str
    group: ErrorGroup
    state: str = NEW
    issue: Link | None = None
    project_id: int = 0                 # GitLab's id of the project (its Issue's)
    origin: Link | None = None          # the MR that merged the commit deployed when the Issue was last (re)opened
    told: int = 0                       # the count the Issue last said
    cycle: int = 1                      # times its Issue was opened: a fix branch each
    closed_sha: str = ""                # the commit deployed when it was last seen before it closed
    slot: str = ""                      # its session, kept until it closes ("": none yet)
    gen: int = 0                        # sessions opened for it (a slot name is used once)
    send_id: str = ""
    dispatched_at: float = 0.0
    attempts: int = 0                   # failed fix attempts since its Issue was (re)opened
    outcome: str = ""                   # how the last one ended: OUTCOME's, or mr_closed
    fix_mr: Link | None = None
    said: bool = False                  # stopped, and its Issue was told

    @property
    def signature(self) -> str:
        return self.group.signature

    @property
    def branch(self) -> str:
        return "fix/staging-%s%s" % (stagelog.short_id(self.signature), "-%d" % self.cycle if self.cycle > 1 else "")

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "Row":
        d = dict(d, group=ErrorGroup.of(d["group"]))
        for k in ("issue", "origin", "fix_mr"):
            d[k] = Link.of(d.get(k))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---- the state machine ---------------------------------------------------------------------------------------- #

def seen(row: Row | None, project: str, g: ErrorGroup) -> Row:
    """*row* after a round read *g* more of its error: counted; a closed one seen on another deployed commit is new."""
    if row is None:
        return Row(project, g)
    row = replace(row, group=row.group.merged(g))
    if row.state == CLOSED and g.deployed_sha != row.closed_sha:
        return replace(row, state=NEW, cycle=row.cycle + 1, attempts=0, outcome="", fix_mr=None, said=False)
    return row


def failed(row: Row, outcome: str) -> Row:
    n = row.attempts + 1
    return replace(row, state=STOPPED if n >= MAX_ATTEMPTS else ISSUED, attempts=n, outcome=outcome, said=False)


def closed(row: Row) -> Row:
    return replace(row, state=CLOSED, closed_sha=row.group.deployed_sha, slot="", send_id="")


def jumped(row: Row) -> bool:
    return row.state in OPEN and row.group.count - row.told >= max(JUMP, row.told)


# ---- the ledger ----------------------------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS errors (project TEXT NOT NULL, signature TEXT NOT NULL, state TEXT NOT NULL,
  doc TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY (project, signature));
CREATE TABLE IF NOT EXISTS cursors (project TEXT PRIMARY KEY, doc TEXT NOT NULL);
"""


class Ledger:
    """<root>/staging.sqlite: a row per error signature (Row as JSON) and each project's log cursor. Only the bridge's
    worker thread uses it."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def rows(self, project: str | None = None) -> list[Row]:
        q = "SELECT doc FROM errors" + (" WHERE project=?" if project is not None else "") + " ORDER BY project, signature"
        return [Row.of(json.loads(r[0])) for r in self.db.execute(q, () if project is None else (project,))]

    def get(self, project: str, signature: str) -> Row | None:
        r = self.db.execute("SELECT doc FROM errors WHERE project=? AND signature=?", (project, signature)).fetchone()
        return Row.of(json.loads(r[0])) if r else None

    def put(self, row: Row) -> None:
        self.db.execute("INSERT OR REPLACE INTO errors (project, signature, state, doc, updated) VALUES (?, ?, ?, ?, ?)",
                        (row.project, row.signature, row.state, json.dumps(asdict(row), ensure_ascii=False), time.time()))

    def drop(self, row: Row) -> None:
        self.db.execute("DELETE FROM errors WHERE project=? AND signature=?", (row.project, row.signature))

    def cursor(self, project: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT doc FROM cursors WHERE project=?", (project,)).fetchone()
        return json.loads(r[0]) if r else None

    def take(self, project: str, cursor: Mapping[str, Any], groups: list[ErrorGroup]) -> None:
        """This round's errors of *project* counted and its log's cursor moved past them, together or not at all."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for g in groups:
                self.put(seen(self.get(project, g.signature), project, g))
            self.db.execute("INSERT OR REPLACE INTO cursors (project, doc) VALUES (?, ?)", (project, json.dumps(cursor)))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def projects(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT project FROM errors UNION SELECT project FROM cursors")}

    def forget(self, project: str) -> None:
        self.db.execute("DELETE FROM errors WHERE project=?", (project,))
        self.db.execute("DELETE FROM cursors WHERE project=?", (project,))

    def close(self) -> None:
        self.db.close()


# ---- what GitLab and the session are told --------------------------------------------------------------------- #

def _when(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fenced(lines: tuple[str, ...]) -> list[str]:
    """*lines* in a Markdown code block whose fence no line of them can close."""
    fence = "`" * max([3] + [len(x) - len(x.lstrip("`")) + 1 for x in (y.lstrip() for y in lines)])
    return [fence + "text", *lines, fence]


def issue_title(row: Row) -> str:
    return "测试环境报错：" + stagelog.short(row.signature, 80)


def _commit(web: str, row: Row) -> str:
    sha = row.group.deployed_sha
    return "[%s](%s/%s/-/commit/%s)" % (sha[:12], web, row.project, sha)


def _origin_words(origin: Link | None) -> str:
    if origin is None:
        return "没查到（GitLab 说没有 MR 合进过这个提交）"
    return "[!%d%s](%s)" % (origin.iid, " %s" % origin.title if origin.title else "", origin.url or "")


def issue_text(row: Row, origin: Link | None, web: str, base: str) -> str:
    g = row.group
    return "\n".join([
        "运行机在测试环境的日志里看到了这个报错。", "",
        "- 特征：`%s`" % g.signature.replace("`", "'"),
        "- 部署的提交：%s" % _commit(web, row),
        "- 带进这个提交的 MR：%s" % _origin_words(origin),
        "- 第一次看到：%s；到现在 %d 次" % (_when(g.first_seen), g.count), "",
        "日志（最近的一次，最多 %d 行）：" % stagelog.SAMPLE_LINES, "", *_fenced(g.sample), "",
        "运行机会从 %s 开分支 %s 去修，修好以后开 MR（写着 Closes 这个 Issue），等人审、人合并。" % (base, row.branch), "",
        "<!-- %s -->" % (MARK % stagelog.short_id(g.signature))])


def reopen_text(row: Row, origin: Link | None, web: str) -> str:
    g = row.group
    return "\n".join([
        "这个报错在新的部署里又出现了：部署的提交 %s，带进它的 MR %s；一共 %d 次。运行机重新打开这个 Issue 接着修，修复分支是 %s。"
        % (_commit(web, row), _origin_words(origin), g.count, row.branch), "", "日志（最近的一次）：", "", *_fenced(g.sample)])


def jump_text(row: Row, web: str) -> str:
    g = row.group
    return "这个报错又出现了 %d 次，一共 %d 次；最近一次在 %s，部署的提交 %s。" % (g.count - row.told, g.count, _when(g.last_seen),
                                                                  _commit(web, row))


def stopped_text(row: Row) -> str:
    after = "这个 Issue 关掉以后，要是在新的部署里又出现，运行机会重新打开它再修。"
    if row.outcome == "mr_closed" and row.fix_mr is not None:
        return "修复 MR !%d 被关了，没有合并：运行机不再自己修这个报错，交给人。%s" % (row.fix_mr.iid, after)
    return "运行机试了 %d 次，没修好这个报错（最后一次：%s）：它不再自己修，交给人。%s" % (
        row.attempts, OUTCOME.get(row.outcome, row.outcome), after)


def issue_command(helper: patrol.Helper, project: str, iid: int) -> str:
    """The command a fix session replies on its Issue and opens its MR with (patrol_reply.py --issue)."""
    return " ".join(shlex.quote(x) for x in (helper.python, "-I", helper.script, "--config", helper.config,
                                             "--brainkit", helper.kit_dir, "--project", project, "--issue", str(iid)))


def message(row: Row, path: str, base: str, helper: patrol.Helper) -> str:
    """What the fix session is told: the error with its Issue, the deployed commit and its MR, the workspace, the
    steps, and the commands to open the MR and reply with."""
    g, iid, br = row.group, row.issue.iid, row.branch
    cmd = issue_command(helper, row.project, iid)
    title = "修复：" + stagelog.short(g.signature, 60)
    came = ("，它是 MR !%d%s合进来的" % (row.origin.iid, "「%s」" % row.origin.title if row.origin.title else " ")
            if row.origin else "")
    out = ["测试环境报错：%s#%d「%s」 %s" % (row.project, iid, issue_title(row), row.issue.url or ""),
           "这一条不是 MR 巡检：还没有 MR。照下面的步骤做，修复分支和 MR 你来开。",
           "测试环境部署的是提交 %s%s。" % (g.deployed_sha[:12], came),
           "工作区：%s（已经检出 %s，在它最新的提交上）。" % (path, base), "",
           "日志里的报错（一共 %d 次，这是最近的一次）：" % g.count]
    out += ["   > " + x for x in g.sample]
    if row.attempts:
        out += ["", "这是第 %d 次修：上一次%s。origin 上分支 %s 已经有提交的话，接着它做。" % (
            row.attempts + 1, OUTCOME.get(row.outcome, row.outcome), br)]
    out += ["", "要做的：",
            "1. 从 %s 开分支 %s：git checkout -b %s（origin 上已经有这个分支：git fetch origin，再 git checkout -B %s origin/%s）。"
            % (base, br, br, br, br),
            "2. 能复现就先写一个测试复现它，再修；跑项目的测试，没过就接着修。提交写清楚改了什么，推上去：git push origin "
            "HEAD:%s（不加 --force）。" % br,
            "3. 开 MR，标题「%s」，描述写改了什么、怎么验证的（命令会在描述末尾加上 Closes #%d）：" % (title, iid),
            "   %s --open-mr --source %s --target %s --title %s '描述'" % (cmd, br, base, shlex.quote(title)),
            "4. 在 Issue 上回复改了什么、MR 在哪：",
            "   %s --note '回复的话'" % cmd,
            "修不了的：不开 MR，用 --note 在 Issue 上说清楚修不了什么，然后停下。"]
    return "\n".join(out)


def slot_name(project_id: int, iid: int, gen: int) -> str:
    return "staging-%d-%d" % (project_id, iid) + ("-%d" % gen if gen > 1 else "")


def claim_of(row: Row) -> sources.Claim:
    return sources.make_claim(KIND, row.project, {"type": "issue", "iid": row.issue.iid}, link=row.issue.url,
                              asked_by="", title=issue_title(row))


def beside(ws: patrol.Workspaces | None) -> patrol.Workspaces | None:
    """MR 巡检's workspaces, cloned and committed the same way, under <work>/staging."""
    return replace(ws, root=os.path.join(os.path.dirname(ws.root), "staging")) if ws is not None else None


def _said(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300] or type(exc).__name__


# ---- the round ------------------------------------------------------------------------------------------------ #

class StagingWatch:
    """The work source staging-errors, hosted by TeamRuntime: round() each bridge round on its worker thread (after
    MR 巡检's: *shared* says how many of its sessions are at work), view() for the page from any thread, held() for
    sources.holder."""

    def __init__(self, *, gl: Any, sessions: Any, kit: Callable[[str], Mapping[str, Any]], projects: list[Any],
                 ledger_path: str, workspaces: patrol.Workspaces | None, helper: patrol.Helper,
                 claims: Callable[[], list[sources.Claim]], shared: Callable[[], int] = lambda: 0,
                 now: Callable[[], float] = time.time, out: Callable[[str], None] = print) -> None:
        self.gl, self.sessions, self.kit, self.ledger_path = gl, sessions, kit, ledger_path
        self.workspaces, self.helper, self.claims, self.shared, self.now, self.out = (
            workspaces, helper, claims, shared, now, out)
        self.projects = [p["project"] if isinstance(p, dict) else str(p) for p in projects]
        self.staging: dict[str, stagelog.Staging | str] = {}       # str: why bridge.json's block cannot be used
        for p in projects:
            if isinstance(p, dict) and "staging" in p:
                try:
                    self.staging[p["project"]] = stagelog.staging_of(p["staging"])
                except ValueError as exc:
                    self.staging[p["project"]] = str(exc)
        base = str(getattr(gl, "base", ""))
        self.web = base[:-len("/api/v4")] if base.endswith("/api/v4") else base
        self._led: Ledger | None = None
        self._kits: dict[str, Mapping[str, Any]] = {}
        self._notes: dict[tuple[str, str], str] = {}
        self._errors: list[str] = []
        self.last: dict[str, Any] = view_of([], [], {}, None)

    @property
    def led(self) -> Ledger:
        if self._led is None:
            self._led = Ledger(self.ledger_path)
        return self._led

    def close(self) -> None:
        led, self._led = self._led, None
        if led is not None:
            led.close()

    def _ledger_there(self) -> bool:
        return self._led is not None or os.path.exists(self.ledger_path)

    def held(self) -> list[sources.Claim]:
        """The Issues it has open: their claims."""
        if not self._ledger_there():
            return []
        return [claim_of(r) for r in self.led.rows() if r.issue is not None and r.state != CLOSED]

    def view(self) -> dict[str, Any]:
        return self.last

    def round(self) -> int:
        """Every project it is on: its new log lines read and counted, its Issues and fixes moved on; then the fixes
        to give, CAP at a time. -> how many projects' logs were read. A project or an error it could not do this round
        does not hold up the others: what went wrong is raised at the end (BrainError)."""
        common = bk.m("common")
        now = self.now()
        self._kits, self._notes, self._errors = {}, {}, []
        projects, off, read = [], set(), 0
        for project in self.projects:
            try:
                kit = self.kit(project)
            except (common.BrainError, ValueError) as exc:
                self._errors.append("%s: %s" % (project, _said(exc)))
                projects.append({"project": project, "on": False, "error": _said(exc), "deployed_sha": ""})
                continue
            if KIND not in sources.enabled(kit):
                off.add(project)
                projects.append({"project": project, "on": False, "error": "", "deployed_sha": ""})
                continue
            self._kits[project] = kit
            try:
                sha = self._look(project, kit, now)
            except (common.BrainError, OSError, ValueError) as exc:
                self._errors.append("%s: %s" % (project, _said(exc)))
                projects.append({"project": project, "on": True, "error": _said(exc), "deployed_sha": ""})
                continue
            projects.append({"project": project, "on": True, "error": "", "deployed_sha": sha})
            read += 1
        if self._ledger_there():
            for project in self.led.projects():
                if project in off or project not in self.projects:
                    self._forget(project)
        if self._kits and self._ledger_there():
            self._dispatch(now)
        self.last = view_of(projects, self.led.rows() if self._ledger_there() else [], self._notes, now)
        if self._errors:
            raise common.BrainError("not done this round: " + "; ".join(self._errors))
        return read

    def _look(self, project: str, kit: Mapping[str, Any], now: float) -> str:
        """*project*'s Issues and fixes as GitLab and the sessions say, then its new errors counted, then its Issues
        told. -> the deployed commit."""
        cfg = self.staging.get(project)
        if cfg is None:
            raise ValueError("bridge.json 的 projects 里 %s 没写 staging：测试环境的日志和 VERSION 文件在哪" % project)
        if isinstance(cfg, str):
            raise ValueError("bridge.json 里 %s 的 staging 用不了：%s" % (project, cfg))
        sha = cfg.version.read()
        chunk = cfg.log.read(self.led.cursor(project))
        events, used = stagelog.errors_in(chunk.lines)
        self._each([r for r in self.led.rows(project) if r.state in OPEN], lambda r: self._watch(r, now))
        self.led.take(project, chunk.cursors[used], stagelog.grouped(events, now, sha))
        self._each(self.led.rows(project), lambda r: self._tell(r, kit))
        for r in self.led.rows(project):
            if r.state == CLOSED and now - r.group.last_seen >= FORGET_S:
                self.led.drop(r)
        return sha

    def _each(self, rows: list[Row], step: Callable[[Row], None]) -> None:
        for row in rows:
            try:
                step(row)
            except bk.m("common").BrainError as exc:
                self._notes[(row.project, row.signature)] = "这一轮没做成：%s" % _said(exc)
                self._errors.append("%s「%s」: %s" % (row.project, stagelog.short(row.signature, 40), _said(exc)))

    def _watch(self, row: Row, now: float) -> None:
        """An open row as its Issue, its fix MR and its session's turn say."""
        rest = bk.m("rest")
        try:
            issue = self.gl.call("GET", "/projects/%s/issues/%d" % (rest.q(row.project), row.issue.iid)) or {}
        except rest.HttpError as exc:
            if exc.status != 404:
                raise
            issue = {"state": "closed"}                 # deleted: nobody waits on it
        if issue.get("state") == "closed":
            if row.state == FIXING:
                self._stop(row)
            self._drop_clone(row)
            self.led.put(closed(row))
            return
        if row.state == FIXING:
            turn = self._turn(row)
            mr = self._fix_mr(row)
            if mr is not None:
                row = replace(row, state=FIX_MR, fix_mr=mr)
            elif turn in ("ended", "failed"):
                row = failed(row, turn)
            elif now - row.dispatched_at >= TIMEOUT_S:
                self._stop(row)
                row = failed(row, "timeout")
        elif row.state == FIX_MR:
            state = (self.gl.mr(row.project, row.fix_mr.iid) or {}).get("state")
            if state == "merged":
                self._drop_clone(row)
                row = closed(row)
            elif state == "closed":
                row = replace(row, state=STOPPED, outcome="mr_closed", said=False)
        self.led.put(row)

    def _tell(self, row: Row, kit: Mapping[str, Any]) -> None:
        """A new row's Issue opened (or found, or opened again); an open one's comment when its count jumped, or when
        it stopped."""
        rest = bk.m("rest")
        path = "/projects/%s/issues" % rest.q(row.project)
        if row.state == NEW:
            origin = self._origin(row)
            if row.issue is None:
                made = self._find(row) or self.gl.call("POST", path, body={
                    "title": issue_title(row), "labels": LABEL,
                    "description": issue_text(row, origin, self.web, str(kit.get("default_branch") or "main"))})
                row = replace(row, issue=Link.of(made), project_id=int(made.get("project_id") or 0))
            else:
                self.gl.call("PUT", "%s/%d" % (path, row.issue.iid), body={"state_event": "reopen"})
                self.gl.call("POST", "%s/%d/notes" % (path, row.issue.iid), body={"body": reopen_text(row, origin, self.web)})
            self.led.put(replace(row, state=ISSUED, origin=origin, told=row.group.count))
            self.out("staging: %s#%d %s" % (row.project, row.issue.iid, issue_title(row)))
            return
        if row.state == STOPPED and not row.said:
            self.gl.call("POST", "%s/%d/notes" % (path, row.issue.iid), body={"body": stopped_text(row)})
            row = replace(row, said=True)
            self.led.put(row)
        if jumped(row):
            self.gl.call("POST", "%s/%d/notes" % (path, row.issue.iid), body={"body": jump_text(row, self.web)})
            self.led.put(replace(row, told=row.group.count))

    def _origin(self, row: Row) -> Link | None:
        """The MR that merged the deployed commit (a merged one first), or None when GitLab knows none."""
        rest = bk.m("rest")
        try:
            mrs = self.gl.call("GET", "/projects/%s/repository/commits/%s/merge_requests" % (
                rest.q(row.project), rest.q(row.group.deployed_sha))) or []
        except rest.HttpError as exc:
            if 400 <= exc.status < 500:
                return None
            raise
        mrs = [m for m in mrs if isinstance(m, dict) and m.get("iid")]
        return Link.of(next((m for m in mrs if m.get("state") == "merged"), mrs[0] if mrs else None))

    def _find(self, row: Row) -> dict[str, Any] | None:
        """An open Issue of this signature: one opened by a request whose answer was lost, or before the ledger was."""
        mark = MARK % stagelog.short_id(row.signature)
        issues = self.gl.pages("/projects/%s/issues" % bk.m("rest").q(row.project), {"labels": LABEL, "state": "opened"},
                               100, 10, "staging-error issues")
        return next((i for i in issues if isinstance(i, dict) and mark in str(i.get("description") or "")), None)

    def _fix_mr(self, row: Row) -> Link | None:
        mrs = self.gl.call("GET", "/projects/%s/merge_requests" % bk.m("rest").q(row.project),
                           {"source_branch": row.branch, "state": "all"}) or []
        return Link.of(next((m for m in mrs if isinstance(m, dict)), None))

    def _turn(self, row: Row) -> str:
        if not row.slot or not row.send_id:
            return "unknown"
        try:
            return self.sessions.patrol_turn(row.slot, row.send_id)
        except bk.m("common").BrainError as exc:
            self.out("%s: its turn cannot be read: %s" % (row.slot, _said(exc)))
            return "unknown"

    def _stop(self, row: Row) -> None:
        if row.slot:
            try:
                self.sessions.patrol_stop(row.slot)
            except bk.m("common").BrainError as exc:
                self.out("%s: not stopped: %s" % (row.slot, _said(exc)))

    def _drop_clone(self, row: Row) -> None:
        if self.workspaces is not None and row.issue is not None and row.project_id:
            self.workspaces.drop(row.project_id, row.issue.iid)

    def _forget(self, project: str) -> None:
        for row in self.led.rows(project):
            if row.state == FIXING:
                self._stop(row)
            self._drop_clone(row)
        self.led.forget(project)

    # ---- the fixes ------------------------------------------------------------------------------------------ #

    def _dispatch(self, now: float) -> None:
        """The issued rows, the earliest seen first, while fewer than CAP sessions are at work: its own fixing ones and
        MR 巡检's."""
        common = bk.m("common")
        rows = self.led.rows()
        busy = self.shared() + sum(1 for r in rows if r.state == FIXING)
        others = [c for c in self.claims() if c["kind"] != KIND]
        for row in sorted((r for r in rows if r.state == ISSUED and r.project in self._kits),
                          key=lambda r: (r.group.first_seen, r.project, r.signature)):
            key = (row.project, row.signature)
            other = sources.holder(claim_of(row), others)
            if other is not None:
                self._notes[key] = "%s已经认领了 %s：这边不再派一次" % (sources.SOURCES[other["kind"]].words, other["key"])
                continue
            if busy >= CAP:
                self._notes[key] = "排队：同时最多 %d 个会话在修（MR 巡检的也算）" % CAP
                continue
            if self.workspaces is None:
                self._notes[key] = "派修复要 runnerd.json：克隆地址和提交用的身份都在它里面"
                continue
            try:
                base, path = self._prepare(row)
            except patrol.Moved:
                self._notes[key] = "主分支刚有新的提交：下一轮再派"
                continue
            except (common.BrainError, OSError) as exc:
                self._notes[key] = "工作区没准备好：%s" % _said(exc)
                self.out("%s: its workspace is not ready: %s" % (issue_title(row), _said(exc)))
                continue
            busy += 1
            try:
                self._send(row, path, base, now)
            except common.BrainError as exc:            # its session not opened or set up: nothing was sent
                self._notes[key] = "没派出去：%s" % _said(exc)
                self.out("%s: not dispatched: %s" % (issue_title(row), _said(exc)))

    def _prepare(self, row: Row) -> tuple[str, str]:
        """(the default branch, the row's clone checked out at its head on origin)."""
        base = str(self._kits[row.project].get("default_branch") or "main")
        rest = bk.m("rest")
        head = self.gl.call("GET", "/projects/%s/repository/branches/%s" % (rest.q(row.project), rest.q(base))) or {}
        sha = str((head.get("commit") or {}).get("id") or "")
        item = patrol.PatrolItem(row.project, row.project_id, row.issue.iid, issue_title(row), row.issue.url, base, base,
                                 sha, ())
        return base, self.workspaces.prepare(item)

    def _send(self, row: Row, path: str, base: str, now: float) -> None:
        """The fix to the row's session, opened the first time and kept until the row closes. The row is fixing, with
        the message's sendId, before the message goes; one KiroCrew did not answer may have landed and is not sent
        again (its turn, or TIMEOUT_S, says how it went). One KiroCrew refused is a failed attempt. Raises BrainError
        when the session was not opened or set up and KiroCrew did not answer: nothing was sent, and it is no
        attempt."""
        key = (row.project, row.signature)
        if not row.slot:
            name = slot_name(row.project_id, row.issue.iid, row.gen + 1)
            row = replace(row, gen=row.gen + 1)
            self.led.put(row)                           # the name is used once, also when opening it fails
            self.sessions.open_patrol(name, patrol.AGENT, str(self._kits[row.project].get("model") or ""),
                                      "%s#%d" % (row.project, row.issue.iid))
            row = replace(row, slot=name)
            self.led.put(row)
        sid = "S-" + hashlib.sha256(("%s\n%s\n%d\n%r" % (row.slot, row.signature, row.attempts, now)).encode()
                                    ).hexdigest()[:20]
        went = replace(row, state=FIXING, send_id=sid, dispatched_at=now)
        try:
            self.sessions.patrol_project(row.slot, path)
        except patrol.Refused as exc:
            self._refused(went, exc)
            return
        self.led.put(went)                              # from here on the message may be in its session
        try:
            self.sessions.patrol_send(row.slot, message(went, path, base, self.helper), sid)
        except patrol.Refused as exc:
            self._refused(went, exc)
            return
        except bk.m("common").BrainError as exc:
            self._notes[key] = "送没送到不清楚（%s）：算它在修，不再送一次" % _said(exc)
            self.out("%s: its message to %s may or may not have landed: %s" % (issue_title(row), row.slot, _said(exc)))
            return
        self.out("staging: %s#%d dispatched to %s (attempt %d)" % (row.project, row.issue.iid, row.slot,
                                                                   row.attempts + 1))

    def _refused(self, went: Row, exc: Exception) -> None:
        row = failed(went, "refused")
        self.led.put(row)
        if row.state != STOPPED:
            self._notes[(row.project, row.signature)] = "没派出去（%s）：算一次，下一轮再派" % _said(exc)
        self.out("%s: %s refused its message: %s" % (issue_title(row), went.slot, _said(exc)))


def _link(x: Link | None) -> dict[str, Any] | None:
    return {"iid": x.iid, "url": x.url, "title": x.title} if x is not None else None


def _note(row: Row) -> str:
    if row.state == STOPPED and row.outcome == "mr_closed":
        return "修复 MR 被关了，没有合并，在 Issue 上说了：交给人"
    if row.state == STOPPED:
        return "试了 %d 次没修好（最后一次：%s），在 Issue 上说了：交给人" % (row.attempts, OUTCOME.get(row.outcome, row.outcome))
    if row.state == ISSUED and row.attempts:
        return "上一次没修好（%s）：再派一次" % OUTCOME.get(row.outcome, row.outcome)
    if row.state == NEW and row.issue is not None:
        return "在新的部署里又出现了：重新打开 Issue"
    if row.state == CLOSED:
        return "Issue 关了：在新的部署里又出现的话，重新打开"
    return ""


def view_of(projects: list[dict[str, Any]], rows: list[Row], notes: Mapping[tuple[str, str], str],
            at: float | None) -> dict[str, Any]:
    """GET /team/staging (TeamStaging in ui/src/types.ts): the projects and every error signature the ledger holds."""
    items = [{"project": r.project, "signature": r.signature, "count": r.group.count, "state": r.state,
              "issue": _link(r.issue), "origin": _link(r.origin), "fix_mr": _link(r.fix_mr),
              "first_seen": r.group.first_seen, "last_seen": r.group.last_seen, "deployed_sha": r.group.deployed_sha,
              "attempts": r.attempts, "slot": r.slot or None, "note": notes.get((r.project, r.signature)) or _note(r)}
             for r in rows]
    return {"on": any(p["on"] for p in projects), "cap": CAP, "working": sum(1 for r in rows if r.state == FIXING),
            "last_at": at, "projects": projects, "items": items}
