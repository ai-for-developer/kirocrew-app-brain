"""MR 巡检 (work source mr-patrol, sources.py). Each round of the bridge looks at every open MR of the runner's projects
whose kit.json switches it on. An MR with a problem the runner can fix goes to a brain-patrol session of its own (slot
patrol-<project id>-<iid>), working in a clone of its own (<work>/patrol/<project id>-<iid>: KiroCrew lets agents work
only under ~/workplace). Later problems of that MR go to the same session.

A problem is one of four. The MR head's pipeline failed: its failed jobs, each with the last LOG_LINES lines of its
log. A person left a review thread open: unresolved and resolvable, with no reply of patrol's own after it, and not
named in a turn that ended. A person wrote a plain comment on the MR (no thread, so nothing marks it resolved) since
patrol started in the project, not named in a turn that ended: the session answers it with a note on the MR that
quotes it. Or the MR conflicts with its target branch. The MR's problems make a PatrolItem. Its
fingerprint moves with the head, a failed pipeline, the conflict and the open review notes and comments, and nothing
else.

The ledger, <root>/patrol.sqlite, has one row per open MR of the projects patrol is on (MrRow). It is a state machine
(settle, Patrol._look and Patrol._send):
  idle -> working     the fingerprint was not worked to the end of a turn yet, and a session is free: at most CAP at
                      work or handed work this round, the others wait (first the MRs open before patrol started in
                      their project, then by when each MR last changed). The row is working before its message goes,
                      so a message KiroCrew did not answer, which may have landed, is never sent again.
  working -> idle     the turn ended (the review notes it named count as answered then), or the MR head moved
  working -> idle     a failed attempt: TIMEOUT_S without either (the turn is stopped), or a turn that ended without a
                      reply or with KiroCrew's error. A message KiroCrew refused is a failed attempt as well.
  working -> blocked  the MAX_ATTEMPTS-th failed attempt on one fingerprint: left alone until its fingerprint changes
  idle -> paused      dispatched MAX_STREAK times in a row with no person pushing or commenting in between (each push
                      of the runner's makes a new fingerprint): left alone until a person does
One comment on the MR says why it is blocked or paused, and what is not fixed. A row is forgotten, its turn stopped and
its clone deleted, once its MR is merged or closed, or patrol is no longer on in its project.
A round lists each project's open MRs once. It reads an MR's pipeline and threads only when the MR changed since, is
being worked, its pipeline had not finished, or RECHECK_S went by: GitLab does not move an MR's updated_at when a
pipeline ends, or when one runs again on the same head.
Skipped, with the reason on the page: MRs the hand-over owns (its labels), opted out (SKIP_LABEL), from a fork, claimed
by another work source, and those whose only problems are bots' comments.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, ClassVar, Mapping, Union

from .. import bk
from . import sources

KIND = sources.PATROL
AGENT = "brain-patrol"
CAP = 2                     # patrol sessions at work at once, counting those handed work this round
TIMEOUT_S = 30 * 60         # a turn with no end and no new head this long is a failed attempt
MAX_ATTEMPTS = 2            # failed attempts on one fingerprint before the MR is blocked
MAX_STREAK = 3              # dispatches in a row with no person pushing or commenting in between before it is paused
LOG_LINES = 80              # of each failed job's log
LOG_LINE_CHARS = 300
MAX_JOBS = 5
BODY_CHARS = 3000
NO_PIPELINE_S = 600         # an MR changed this recently with no pipeline at its head yet: read again next round
RECHECK_S = 600             # an MR that did not change is read again this often (a pipeline run again on its head)
SKIP_LABEL = "ai-skip"
FINAL_PIPELINE = ("success", "failed", "canceled", "skipped", "manual")
UNSURE_MERGE = ("checking", "unchecked", "preparing")      # detailed_merge_status before GitLab knows
BOT_RE = re.compile(r"^(?:project|group)_\d+_bot(?:_[0-9a-f]+)?\d*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SECTION_RE = re.compile(r"^section_(?:start|end):\d+:")
IDLE, WORKING, BLOCKED, PAUSED = "idle", "working", "blocked", "paused"


# ---- what an MR has ------------------------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FailedJob:
    name: str
    log: str                    # its last LOG_LINES lines, without terminal colours


@dataclass(frozen=True)
class CiFailed:
    pipeline_id: int
    jobs: tuple[FailedJob, ...]
    kind: ClassVar[str] = "ci_failed"


@dataclass(frozen=True)
class Review:
    discussion_id: str
    note_id: int
    author: str
    body: str
    position: str | None        # path:line of the diff it is on
    kind: ClassVar[str] = "review"


@dataclass(frozen=True)
class Comment:
    note_id: int
    author: str
    body: str
    kind: ClassVar[str] = "comment"


@dataclass(frozen=True)
class Conflict:
    target_branch: str
    kind: ClassVar[str] = "conflict"


Problem = Union[CiFailed, Review, Comment, Conflict]


def fingerprint(head_sha: str, pipeline_id: int | None, conflict: bool, note_ids: Any) -> str:
    raw = json.dumps([head_sha, pipeline_id, bool(conflict), sorted(int(n) for n in note_ids)])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class PatrolItem:
    project: str
    project_id: int
    iid: int
    title: str
    url: str | None
    source_branch: str
    target_branch: str
    head_sha: str
    problems: tuple[Problem, ...]

    @property
    def pipeline_id(self) -> int | None:
        return next((p.pipeline_id for p in self.problems if isinstance(p, CiFailed)), None)

    @property
    def conflict(self) -> bool:
        return any(isinstance(p, Conflict) for p in self.problems)

    @property
    def note_ids(self) -> tuple[int, ...]:
        return tuple(p.note_id for p in self.problems if isinstance(p, (Review, Comment)))

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.head_sha, self.pipeline_id, self.conflict, self.note_ids)


@dataclass(frozen=True)
class Read:
    """What a detail read of an MR found: kept in its row until the MR changes."""
    pipeline_id: int | None
    pipeline_status: str        # "" when its head has no pipeline yet
    jobs: tuple[FailedJob, ...] = ()            # the failed ones, of a failed pipeline
    reviews: tuple[Review, ...] = ()            # people's open review notes (the row knows which were answered)
    bot_notes: int = 0                          # open threads only bots wrote in
    people_note: int = 0                        # the newest note a person wrote on the MR, anywhere (its id)
    comments: tuple[Comment, ...] = ()          # people's plain comments since patrol started in the project

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "Read":
        return cls(d.get("pipeline_id"), str(d.get("pipeline_status") or ""),
                   tuple(FailedJob(**j) for j in d.get("jobs") or ()),
                   tuple(Review(**r) for r in d.get("reviews") or ()), int(d.get("bot_notes") or 0),
                   int(d.get("people_note") or 0), tuple(Comment(**c) for c in d.get("comments") or ()))


@dataclass(frozen=True)
class Skip:
    code: str                   # handoff | opt_out | fork | claimed | bots_only
    text: str


def problem_text(p: Problem) -> str:
    if isinstance(p, CiFailed):
        return "流水线 #%d 没过：%s" % (p.pipeline_id, "、".join(j.name for j in p.jobs) or "看流水线")
    if isinstance(p, Review):
        return "@%s 的评审意见%s" % (p.author, "（%s）" % p.position if p.position else "")
    if isinstance(p, Comment):
        return "@%s 的评论" % p.author
    return "和 %s 有冲突" % p.target_branch


def log_tail(raw: bytes | None, n: int = LOG_LINES) -> str:
    """The last *n* lines of a job log as a person reads it: colours, GitLab's section markers and what a carriage
    return wrote over gone."""
    lines = []
    for line in (raw or b"").decode("utf-8", "replace").split("\n"):
        line = ANSI_RE.sub("", line.rstrip("\r")).rsplit("\r", 1)[-1].rstrip()
        if not SECTION_RE.match(line):
            lines.append(line[:LOG_LINE_CHARS])
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines[-n:])


def username(user: Any) -> str:
    return str((user or {}).get("username") or "").lower() if isinstance(user, dict) else ""


def written_by(n: Mapping[str, Any], own: set[str], bots: set[str]) -> str:
    """own (patrol's account, *own*) | bot | person: who wrote the note *n*."""
    who = n.get("author")
    name = username(who)
    if name in own:
        return "own"
    if name in bots or BOT_RE.match(name) or (isinstance(who, dict) and who.get("bot") is True):
        return "bot"
    return "person"


def notes_of(discussions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Each discussion's notes, without GitLab's system notes."""
    return [[n for n in d.get("notes") or [] if isinstance(n, dict) and not n.get("system")] for d in discussions]


def open_reviews(discussions: list[dict[str, Any]], own: set[str], bots: set[str]) -> tuple[list[Review], int]:
    """(people's open review notes, open threads only bots wrote in). A thread counts while it is resolvable and not
    resolved. In it, a note of patrol's own account (*own*) answers every note before it; a bot's note is never a
    problem."""
    out, bot_threads = [], 0
    for d, notes in zip(discussions, notes_of(discussions)):
        mine = [n for n in notes if n.get("resolvable")]
        if not mine or all(n.get("resolved") for n in mine):
            continue
        tail, bot_said = [], False
        for n in notes:
            by = written_by(n, own, bots)
            if by == "own":
                tail, bot_said = [], False
            elif by == "bot":
                bot_said = True
            else:
                tail.append(n)
        bot_threads += bool(bot_said and not tail)
        for n in tail:
            pos = n.get("position") if isinstance(n.get("position"), dict) else {}
            path = pos.get("new_path") or pos.get("old_path")
            line = pos.get("new_line") or pos.get("old_line")
            out.append(Review(str(d.get("id") or ""), int(n["id"]), username(n.get("author")),
                              str(n.get("body") or "")[:BODY_CHARS], "%s:%s" % (path, line) if path and line else path))
    return out, bot_threads


def plain_comments(discussions: list[dict[str, Any]], own: set[str], bots: set[str], since: float) -> list[Comment]:
    """People's plain comments on the MR (GitLab's individual notes: not in a thread) written *since*."""
    out = []
    for d, notes in zip(discussions, notes_of(discussions)):
        if d.get("individual_note") is True:
            out += [Comment(int(n["id"]), username(n.get("author")), str(n.get("body") or "")[:BODY_CHARS])
                    for n in notes if written_by(n, own, bots) == "person" and _ts(n.get("created_at")) >= since]
    return out


def read_mr(gl: Any, project: str, mr: Mapping[str, Any], own: set[str], bots: set[str], since: float,
            before: Read | None = None) -> Read:
    """GitLab's MR (its head pipeline), the failed jobs and their logs when that pipeline failed (*before*'s while it
    is the same failed pipeline), its threads, and its plain comments written *since* (patrol started in the
    project)."""
    rest = bk.m("rest")
    iid = int(mr["iid"])
    hp = (gl.mr(project, iid) or {}).get("head_pipeline")
    hp = hp if isinstance(hp, dict) and hp.get("id") and hp.get("sha") in (None, mr.get("sha")) else None
    pid, status = (int(hp["id"]), str(hp.get("status") or "")) if hp else (None, "")
    jobs: list[FailedJob] = []
    if status == "failed" and pid and before is not None and (before.pipeline_id, before.pipeline_status) == (pid, status):
        jobs = list(before.jobs)
    elif status == "failed" and pid:
        for j in [j for j in gl.pipeline_jobs(project, pid) if isinstance(j, dict) and not j.get("allow_failure")][:MAX_JOBS]:
            jobs.append(FailedJob(str(j.get("name") or j.get("id")), log_tail(gl.job_trace(project, int(j["id"])))))
    threads = gl.pages("/projects/%s/merge_requests/%d/discussions" % (rest.q(project), iid), {}, 100, 20,
                       "discussions on !%d" % iid)
    threads = [d for d in threads if isinstance(d, dict)]
    reviews, bot_threads = open_reviews(threads, own, bots)
    people = [int(n["id"]) for notes in notes_of(threads) for n in notes if written_by(n, own, bots) == "person"]
    return Read(pid, status, tuple(jobs), tuple(reviews), bot_threads, max(people, default=0),
                tuple(plain_comments(threads, own, bots, since)))


def pushed_by_people(gl: Any, project: str, old: str, new: str, bots: set[str]) -> bool:
    """Whether a person pushed to the MR's source branch from its head *old* to *new*: a commit in old..new that none
    of *bots* (the runner's git emails) committed, or a head that moved back (the runner only adds commits). A range
    GitLab will not list (*old* rewritten away, or a GitLab without ranges) is judged by who committed *new*."""
    rest = bk.m("rest")
    path = "/projects/%s/repository/commits" % rest.q(project)
    try:
        rows = gl.call("GET", path, {"ref_name": "%s..%s" % (old, new), "per_page": 100})
    except rest.HttpError as exc:
        if not 400 <= exc.status < 500:
            raise
        rows = [gl.call("GET", "%s/%s" % (path, new))]
    commits = [c for c in rows or [] if isinstance(c, dict)]
    return not commits or any(str(c.get("committer_email") or "").lower() not in bots for c in commits)


def item_of(project: str, mr: Mapping[str, Any], read: Read, answered: tuple[int, ...]) -> PatrolItem:
    target = str(mr.get("target_branch") or "")
    unsure = mr.get("detailed_merge_status") in UNSURE_MERGE
    problems: list[Problem] = []
    if mr.get("detailed_merge_status") == "conflict" or (mr.get("has_conflicts") and not unsure):
        problems.append(Conflict(target))
    if read.pipeline_status == "failed" and read.pipeline_id:
        problems.append(CiFailed(read.pipeline_id, read.jobs))
    problems += [r for r in read.reviews + read.comments if r.note_id not in answered]
    return PatrolItem(project, int(mr.get("project_id") or mr.get("target_project_id") or 0), int(mr["iid"]),
                      str(mr.get("title") or ""), mr.get("web_url") or None, str(mr.get("source_branch") or ""), target,
                      str(mr.get("sha") or ""), tuple(problems))


def skip_of(kit: Mapping[str, Any], mr: Mapping[str, Any]) -> Skip | None:
    """The skip rules that the MR list alone can tell."""
    labels = {str(x) for x in mr.get("labels") or []}
    hand = [kit[k] for k in ("handoff_label", "running_label", "rejected_label") if kit.get(k) in labels]
    if hand:
        return Skip("handoff", "交接给运行机的 MR（标签 %s）：交接那边管它" % "、".join(hand))
    if SKIP_LABEL in labels:
        return Skip("opt_out", "打了标签 %s：不巡检" % SKIP_LABEL)
    if mr.get("source_project_id") != mr.get("target_project_id"):
        return Skip("fork", "从 fork 来的 MR：运行机推不了它的分支")
    return None


# ---- the ledger ----------------------------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MrRow:
    project: str
    iid: int
    project_id: int = 0                 # GitLab's id of the project: its workspace is <root>/<project_id>-<iid>
    state: str = IDLE
    slot: str = ""                      # its session, kept while the MR is open ("": none yet)
    gen: int = 0                        # sessions opened for it (a slot name is used once)
    head_sha: str = ""                  # the last dispatch's head, pipeline and conflict
    pipeline_id: int | None = None
    conflict: bool = False
    fingerprint: str = ""               # the last dispatch's
    attempts: int = 0                   # dispatches of that fingerprint
    streak: int = 0                     # dispatches since a person last pushed or commented (MAX_STREAK)
    people_note: int = 0                # Read.people_note at the last dispatch
    checked_head: str = ""              # the newest head since the last dispatch whose new commits are the runner's
    dispatched_at: float = 0.0
    send_id: str = ""
    sent_notes: tuple[int, ...] = ()    # the review notes and comments that dispatch named
    outcome: str = ""                   # how the last attempt ended: ended | moved | failed | timeout | refused | taken
    done_fp: str = ""                   # the fingerprint a turn ended on (its notes answered)
    answered: tuple[int, ...] = ()
    told: bool = False                  # it is blocked or paused, and the MR was told so
    claim: dict = field(default_factory=dict)
    seen: str = ""                      # the MR's updated_at and head when it was last read in detail
    read: Read | None = None
    read_at: float = 0.0

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "MrRow":
        d = dict(d)
        d["sent_notes"], d["answered"] = tuple(d.get("sent_notes") or ()), tuple(d.get("answered") or ())
        d["read"] = Read.of(d["read"]) if isinstance(d.get("read"), dict) else None
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


SCHEMA = """
CREATE TABLE IF NOT EXISTS mrs (project TEXT NOT NULL, iid INTEGER NOT NULL, state TEXT NOT NULL, doc TEXT NOT NULL,
  updated REAL NOT NULL, PRIMARY KEY (project, iid));
CREATE TABLE IF NOT EXISTS projects (project TEXT PRIMARY KEY, since REAL NOT NULL);
"""


class Ledger:
    """<root>/patrol.sqlite: a row per open MR (MrRow as JSON) and when patrol started in each project. Only the bridge's
    worker thread uses it."""

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def get(self, project: str, iid: int) -> MrRow | None:
        r = self.db.execute("SELECT doc FROM mrs WHERE project=? AND iid=?", (project, iid)).fetchone()
        return MrRow.of(json.loads(r[0])) if r else None

    def put(self, row: MrRow) -> None:
        self.db.execute("INSERT OR REPLACE INTO mrs (project, iid, state, doc, updated) VALUES (?, ?, ?, ?, ?)",
                        (row.project, row.iid, row.state, json.dumps(asdict(row), ensure_ascii=False), time.time()))

    def drop(self, project: str, iid: int) -> None:
        self.db.execute("DELETE FROM mrs WHERE project=? AND iid=?", (project, iid))

    def rows(self) -> list[MrRow]:
        return [MrRow.of(json.loads(r[0])) for r in self.db.execute("SELECT doc FROM mrs ORDER BY project, iid")]

    def since(self, project: str, now: float) -> float:
        """When patrol first looked at *project* (now, the first time it is asked)."""
        self.db.execute("INSERT OR IGNORE INTO projects (project, since) VALUES (?, ?)", (project, now))
        return float(self.db.execute("SELECT since FROM projects WHERE project=?", (project,)).fetchone()[0])

    def keep_projects(self, keep: list[str]) -> None:
        """Forget when patrol started in every project but *keep*: it starts again there when it is on again."""
        self.db.execute("DELETE FROM projects WHERE project NOT IN (%s)" % ",".join("?" * len(keep)), tuple(keep))

    def close(self) -> None:
        self.db.close()


# ---- the state machine ---------------------------------------------------------------------------------------- #

def _failed(row: MrRow, outcome: str) -> MrRow:
    return replace(row, state=BLOCKED if row.attempts >= MAX_ATTEMPTS else IDLE, outcome=outcome, told=False)


def settle(row: MrRow, head: str, turn: str, now: float) -> tuple[MrRow, bool]:
    """A row after this round's look at its MR (*head*) and at its last turn (*turn*: running, ended, failed, unknown)
    -> (the row, whether that turn is to be stopped). A turn the head moved past stops being the MR's work; when it
    ends later its review notes still count as answered."""
    late = now - row.dispatched_at >= TIMEOUT_S
    if row.state == WORKING:
        if turn == "ended":
            return replace(row, state=IDLE, outcome="ended", answered=_union(row.answered, row.sent_notes),
                           done_fp=fingerprint(row.head_sha, row.pipeline_id, row.conflict, ())), False
        if turn == "failed":
            return _failed(row, "failed"), False
        if head != row.head_sha:
            return replace(row, state=IDLE, outcome="moved"), False
        if late:
            return _failed(row, "timeout"), True
        return row, False
    if row.outcome == "moved" and turn == "ended":
        return replace(row, outcome="ended", answered=_union(row.answered, row.sent_notes)), False
    if row.outcome == "moved" and turn == "running" and late:
        return replace(row, outcome="timeout"), True
    if row.outcome == "moved" and turn == "failed":
        return replace(row, outcome="failed"), False
    return row, False


def standing(row: MrRow, item: PatrolItem) -> str:
    """ok (nothing to fix) | working | blocked | done (this fingerprint was worked to the end of a turn) | todo."""
    if row.state == WORKING:
        return "working"
    if not item.problems:
        return "ok"
    if row.state == PAUSED or (row.state == BLOCKED and row.fingerprint == item.fingerprint):
        return "blocked"
    if row.done_fp == item.fingerprint:
        return "done"
    return "todo"


def _union(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(sorted(set(a) | set(b)))


# ---- the session's side ---------------------------------------------------------------------------------------- #

AGENT_PROMPT = """你是团队运行机上的 MR 巡检（brain-patrol）。消息来自运行机，不是人：每条消息给你一个 MR、它的工作区，和要修的问题。

规矩：
1. 只修消息里列出的问题，别的不动。
2. 项目有测试就跑；没过就接着修。
3. 提交写清楚改了什么，推到 origin 上这个 MR 的源分支。
4. 和目标分支冲突的，把目标分支合并（git merge）进来解决。不要 rebase，不要强推（不加 --force）。
5. 你处理过的每条评审意见和评论都回复：改了什么，或者为什么不同意。用消息里给的命令回复。
6. 最后在 MR 上发一条简短的总结。
7. 修不了的，在 MR 上说清楚修不了什么，然后停下。
"""


def agent_spec(kit_dir: str) -> dict[str, Any]:
    """~/.kiro/agents/brain-patrol.json: brain-run's spec as the installed brain-kit ships it, with patrol's own prompt
    and no Lean hooks (an MR it fixes is not a Lean run)."""
    runnerd = bk.m("bridge.runnerd")
    spec = bk.m("common").load_json(os.path.join(kit_dir, runnerd.SPEC_TEMPLATE))
    spec.pop("hooks", None)
    spec.update(name=AGENT, prompt=AGENT_PROMPT,
                description="Team runner: fix what the runner lists for one open MR (CI, review threads, conflicts). "
                            "Bridge-driven.",
                _notes="Written by the Brain app's 一键建运行机 (team/patrol.py agent_spec): brain-run's spec of %s "
                       "without its Lean hooks, and patrol's own prompt. Run 一键建运行机 again to rewrite it." % kit_dir)
    return spec


@dataclass(frozen=True)
class Helper:
    """The command a session replies on the MR with (patrol_reply.py, as the bridge's GitLab account)."""
    python: str
    script: str
    config: str                 # bridge.json
    kit_dir: str

    def command(self, project: str, iid: int) -> str:
        return " ".join(shlex.quote(x) for x in (self.python, "-I", self.script, "--config", self.config,
                                                 "--brainkit", self.kit_dir, "--project", project, "--mr", str(iid)))


def _quoted(text: str, pad: str) -> list[str]:
    return [pad + "> " + line for line in str(text).replace("\r\n", "\n").split("\n")]


def message(item: PatrolItem, path: str, helper: Helper) -> str:
    """What the session is told: every problem with its ids, the workspace, and the commands to reply with."""
    cmd = helper.command(item.project, item.iid)
    out = ["MR 巡检：%s!%d「%s」" % (item.project, item.iid, item.title),
           "分支 %s → %s，MR 的最新提交 %s。" % (item.source_branch, item.target_branch, item.head_sha[:12]),
           "工作区：%s（已经检出 %s，就在这个提交上）。" % (path, item.source_branch), "", "只修下面这些问题："]
    for n, p in enumerate(item.problems, 1):
        if isinstance(p, Conflict):
            out.append("%d. 和目标分支 %s 有冲突：git fetch origin，再 git merge origin/%s，解决冲突后提交。不要 rebase，"
                       "不要强推。" % (n, p.target_branch, p.target_branch))
        elif isinstance(p, CiFailed):
            out.append("%d. 流水线 #%d 没过。没过的任务，和它日志的最后几行：" % (n, p.pipeline_id))
            for j in p.jobs:
                out.append("   - %s：" % j.name)
                out += _quoted(j.log or "（没读到日志）", "     ")
        elif isinstance(p, Comment):
            out.append("%d. MR 上的评论（不在讨论里）：评论 #%d，@%s：" % (n, p.note_id, p.author))
            out += _quoted(p.body, "   ")
        else:
            out.append("%d. 评审意见：讨论 %s，评论 #%d，@%s%s：" % (n, p.discussion_id, p.note_id, p.author,
                                                          "，在 %s" % p.position if p.position else ""))
            out += _quoted(p.body, "   ")
    out += ["", "做完以后：",
            "- 项目有测试就跑一遍，没过就接着修。",
            "- 提交写清楚改了什么，推到 origin 的 %s：git push origin HEAD:%s（不加 --force）。" % (item.source_branch,
                                                                                  item.source_branch)]
    if any(isinstance(p, Review) for p in item.problems):
        out += ["- 上面每条评审意见都回复（改了什么，或者为什么不同意），讨论号换成那条意见的：",
                "  %s --discussion <讨论号> '回复的话'" % cmd]
    if any(isinstance(p, Comment) for p in item.problems):
        out += ["- 上面每条 MR 上的评论都回复一条：它没有讨论可回，就在 MR 上发，先引用它的原话（每行前面加 > ），再说改了什么，"
                "或者为什么不同意：", "  %s --note '回复的话'" % cmd]
    out += ["- 最后在 MR 上发一条简短的总结：", "  %s --note '总结的话'" % cmd,
            "- 有修不了的，用 --note 在 MR 上说清楚修不了什么，然后停下。"]
    return "\n".join(out)


class Moved(Exception):
    """The source branch on origin is not at the head this round read: the MR is looked at again next round."""


class Refused(Exception):
    """The session surface's: KiroCrew answered no to setting a session up or to its message, so nothing of it went
    in. Its BrainError means no answer: a message may then have landed."""


@dataclass(frozen=True)
class Workspaces:
    """A clone per MR under *root*, made once; before each dispatch fetched and reset to the source branch on origin."""
    root: str
    clone_url: str                                  # runnerd.json's, {project} in it
    ident: tuple[tuple[str, str], ...] = ()         # git config the commits are made with (runnerd.json's bot)
    git_timeout: float = 300.0
    clone_timeout: float = 3600.0

    @classmethod
    def of(cls, runnerd_cfg: Mapping[str, Any]) -> "Workspaces":
        """Beside runnerd's runs (<work>/patrol), cloned and committed as runnerd does."""
        sec = bk.m("sign").seconds
        runs = os.path.abspath(os.path.expanduser(str(runnerd_cfg["runs_root"])))
        return cls(os.path.join(os.path.dirname(runs), "patrol"), str(runnerd_cfg["clone_url"]),
                   tuple(bk.m("bridge.runnerd").bot_identity(runnerd_cfg)),
                   sec(runnerd_cfg.get("git_timeout"), 300.0), sec(runnerd_cfg.get("clone_timeout"), 3600.0))

    def path(self, project_id: int, iid: int) -> str:
        return os.path.join(self.root, "%d-%d" % (project_id, iid))

    def drop(self, project_id: int, iid: int) -> None:
        """The MR's clone gone: patrol is done with it."""
        if project_id:
            shutil.rmtree(self.path(project_id, iid), ignore_errors=True)

    def prepare(self, item: PatrolItem) -> str:
        common = bk.m("common")
        path = self.path(item.project_id, item.iid)
        if not os.path.isdir(os.path.join(path, ".git")):
            os.makedirs(self.root, exist_ok=True)
            tmp = "%s.tmp-%d" % (path, os.getpid())       # a clone cut off half-way never passes for the workspace
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                common.git_net(self.root, "clone", "-q", "--no-checkout", self.clone_url.format(project=item.project),
                               tmp, timeout=self.clone_timeout)
                os.rename(tmp, path)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        common.git_net(path, "fetch", "-q", "--prune", "origin", timeout=self.git_timeout)
        for args in (("merge", "--abort"), ("rebase", "--abort")):      # what a cut-off turn left half-done
            common.git(path, *args, check=False)
        branch = item.source_branch
        common.git(path, "checkout", "-q", "-f", "-B", branch, "origin/" + branch)
        common.git(path, "reset", "-q", "--hard", "origin/" + branch)
        common.git(path, "clean", "-q", "-fd")
        for key, value in self.ident:
            common.git(path, "config", key, value)
        head = common.git_out(path, "rev-parse", "HEAD")
        if head != item.head_sha:
            raise Moved("origin/%s is at %s, the MR at %s" % (branch, head[:12], item.head_sha[:12]))
        return path


def slot_name(project_id: int, iid: int, gen: int) -> str:
    return "patrol-%d-%d" % (project_id, iid) + ("-%d" % gen if gen > 1 else "")


def blocked_text(row: MrRow, item: PatrolItem) -> str:
    what = "\n".join("- " + problem_text(p) for p in item.problems)
    if row.state == PAUSED:
        return ("MR 巡检：运行机连着修了 %d 次，中间没有人推送或评论，这些还没修好：\n%s\n\n运行机先停下，不管这个 MR；有人推送"
                "或评论以后再看。" % (MAX_STREAK, what))
    if row.outcome == "refused":
        return ("MR 巡检：运行机派了 %d 次，它的会话都没收下，这些还没修：\n%s\n\n请管运行机的人看一下它的 KiroCrew。运行机先不管"
                "这个 MR；它有新的提交、新的流水线或新的评审意见以后再看。" % (MAX_ATTEMPTS, what))
    return ("MR 巡检：运行机试了 %d 次，没修好这些：\n%s\n\n运行机先不管这个 MR；它有新的提交、新的流水线或新的评审意见以后再看。"
            % (MAX_ATTEMPTS, what))


# ---- the round ------------------------------------------------------------------------------------------------ #

@dataclass(eq=False)
class Entry:
    """One open MR as a round saw it."""
    project: str
    mr: Mapping[str, Any]
    kit: Mapping[str, Any]
    since: float
    row: MrRow | None = None
    item: PatrolItem | None = None
    skip: Skip | None = None
    standing: str = "ok"            # standing(), or skipped | queued | waiting | error
    note: str = ""


def _ts(stamp: Any) -> float:
    t = bk.m("rest").gl_time(stamp)
    return t.timestamp() if t is not None else 0.0


def _said(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300] or type(exc).__name__


class Patrol:
    """The work source mr-patrol, hosted by TeamRuntime: round() each bridge round on its worker thread, view() for the
    page from any thread (what the last round saw), held() for sources.holder."""

    def __init__(self, *, gl: Any, sessions: Any, kit: Callable[[str], Mapping[str, Any]], projects: list[str],
                 ledger_path: str, workspaces: Workspaces | None, helper: Helper,
                 claims: Callable[[], list[sources.Claim]], now: Callable[[], float] = time.time,
                 out: Callable[[str], None] = print) -> None:
        self.gl, self.sessions, self.kit, self.projects = gl, sessions, kit, list(projects)
        self.ledger_path, self.workspaces, self.helper, self.claims = ledger_path, workspaces, helper, claims
        self.now, self.out = now, out
        self._led: Ledger | None = None
        self._me = ""
        self._turns: dict[tuple[str, str], str] = {}
        self.last: dict[str, Any] = view_of([], [], None)

    @property
    def led(self) -> Ledger:
        if self._led is None:
            self._led = Ledger(self.ledger_path)
        return self._led

    def close(self) -> None:
        led, self._led = self._led, None
        if led is not None:
            led.close()

    def held(self) -> list[sources.Claim]:
        """The MRs patrol is working on or gave up on: their claims."""
        if self._led is None and not os.path.exists(self.ledger_path):
            return []
        return [r.claim for r in self.led.rows() if r.state in (WORKING, BLOCKED, PAUSED) and r.claim]

    def view(self) -> dict[str, Any]:
        return self.last

    # ---- one round ---------------------------------------------------------------------------------------- #

    def round(self) -> int:
        """Every open MR of the projects patrol is on, looked at; the ones with work dispatched, CAP at a time. -> how
        many open MRs were looked at. A project that cannot be read does not hold up the others: its error is raised at
        the end (BrainError)."""
        common = bk.m("common")
        now, self._turns = self.now(), {}
        others = [c for c in self.claims() if c["kind"] != KIND]
        errors, projects, entries, open_mrs, listed, off = [], [], [], set(), [], set()
        for project in self.projects:
            try:
                kit = self.kit(project)
            except (common.BrainError, ValueError) as exc:
                errors.append("%s: %s" % (project, _said(exc)))
                projects.append({"project": project, "on": False, "error": _said(exc)})
                continue
            if KIND not in sources.enabled(kit):
                off.add(project)
                projects.append({"project": project, "on": False, "error": ""})
                continue
            if self.workspaces is None:
                why = "巡检要 runnerd.json：克隆地址和提交用的身份都在它里面"
                errors.append("%s: %s" % (project, why))
                projects.append({"project": project, "on": True, "error": why})
                continue
            try:
                mrs = self.gl.pages("/projects/%s/merge_requests" % bk.m("rest").q(project),
                                    {"state": "opened", "with_merge_status_recheck": "true"}, 100, 20, "open MRs")
            except common.BrainError as exc:
                errors.append("%s: %s" % (project, _said(exc)))
                projects.append({"project": project, "on": True, "error": _said(exc)})
                continue
            projects.append({"project": project, "on": True, "error": ""})
            listed.append(project)
            since = self.led.since(project, now)
            for mr in [m for m in mrs if isinstance(m, dict) and m.get("iid")]:
                open_mrs.add((project, int(mr["iid"])))
                e = Entry(project, mr, kit, since)
                try:
                    self._look(e, others, now)
                except (common.BrainError, OSError, ValueError) as exc:
                    e.standing, e.note = "error", "这一轮没看成：%s" % _said(exc)
                    errors.append("%s!%s: %s" % (project, mr.get("iid"), _said(exc)))
                entries.append(e)
        if listed or self._led is not None or os.path.exists(self.ledger_path):
            keep = [p for p in self.projects if p not in off]
            self._forget(lambda r: r.project not in keep or (r.project in listed and (r.project, r.iid) not in open_mrs))
            self.led.keep_projects(keep)
        if listed:
            self._dispatch(entries, now)
        self.last = view_of(projects, entries, now)
        if errors:
            raise common.BrainError("not done this round: " + "; ".join(errors))
        return len(entries)

    def _look(self, e: Entry, others: list[sources.Claim], now: float) -> None:
        led, mr, kit = self.led, e.mr, e.kit
        claim = sources.mr_claim(KIND, e.project, mr, str(kit.get("branch_prefix") or "ai"))
        row = led.get(e.project, int(mr["iid"])) or MrRow(e.project, int(mr["iid"]))
        e.skip = skip_of(kit, mr)
        other = sources.holder(claim, others) if e.skip is None else None
        if other is not None:
            e.skip = Skip("claimed", "%s已经认领了 %s：这边不再派一次" % (sources.SOURCES[other["kind"]].words, other["key"]))
        if e.skip is not None:
            if row.state == WORKING:        # the hand-over (or another source) took it over: patrol's turn stops
                self._stop(row)
                row = replace(row, state=IDLE, outcome="taken")
                led.put(row)
            e.row, e.standing = row, "skipped"
            return
        row = replace(row, claim=dict(claim))
        if row.state == WORKING or row.outcome == "moved":
            row, stop = settle(row, str(mr.get("sha") or ""), self._turn(row), now)
            if stop:
                self._stop(row)
        if self._stale(row, mr, now):
            row = replace(row, read=read_mr(self.gl, e.project, mr, *self._who(kit), e.since, before=row.read),
                          seen=_seen(mr), read_at=now)
        item = item_of(e.project, mr, row.read, row.answered)
        row = self._watched(e, replace(row, project_id=item.project_id), item)
        if row.state == BLOCKED and row.fingerprint != item.fingerprint:
            row = replace(row, state=IDLE)
        if standing(row, item) == "todo" and row.streak >= MAX_STREAK:
            row = replace(row, state=PAUSED, told=False)
        if row.state in (BLOCKED, PAUSED) and not row.told:
            try:
                self.gl.comment(e.project, item.iid, blocked_text(row, item))
                row = replace(row, told=True)
            except bk.m("common").BrainError as exc:    # told next round
                self.out("%s!%d: the blocked comment is not posted yet: %s" % (e.project, item.iid, _said(exc)))
        led.put(row)
        e.row, e.item, e.standing = row, item, standing(row, item)
        if e.standing == "ok" and row.read.bot_notes:
            e.skip, e.standing = Skip("bots_only", "只有机器人写的评论，没有要修的"), "skipped"

    def _watched(self, e: Entry, row: MrRow, item: PatrolItem) -> MrRow:
        """*row*, its streak over when a person commented or pushed since its last dispatch (a pause lifts with it). A
        push is asked GitLab about once per head (checked_head)."""
        if not row.streak:
            return row
        acted = row.read.people_note > row.people_note
        if not acted and item.head_sha != row.checked_head:
            acted = pushed_by_people(self.gl, e.project, row.checked_head, item.head_sha, self._bot_emails(e.kit))
            row = row if acted else replace(row, checked_head=item.head_sha)
        return replace(row, streak=0, state=IDLE if row.state == PAUSED else row.state) if acted else row

    def _bot_emails(self, kit: Mapping[str, Any]) -> set[str]:
        """The git emails the runner commits as: kit.json's bot_emails and the workspaces' user.email."""
        named = [str(x) for x in kit.get("bot_emails") or []] + [v for k, v in self.workspaces.ident if k == "user.email"]
        return {x.strip().lower() for x in named if x.strip()}

    def _stale(self, row: MrRow, mr: Mapping[str, Any], now: float) -> bool:
        """Whether the MR is read in detail again: never read, changed since, being worked, its pipeline not over (or
        none yet, just after a change), or not read for RECHECK_S."""
        r = row.read
        if r is None or row.seen != _seen(mr) or row.state == WORKING or now - row.read_at >= RECHECK_S:
            return True
        if r.pipeline_status:
            return r.pipeline_status not in FINAL_PIPELINE
        return now - _ts(mr.get("updated_at")) < NO_PIPELINE_S

    def _who(self, kit: Mapping[str, Any]) -> tuple[set[str], set[str]]:
        """(patrol's own account: the bridge's, by kit.json and by its token; every bot: those and kit's bot_users)."""
        if not self._me:
            try:
                self._me = username(self.gl.current_user())
            except bk.m("common").BrainError as exc:
                self.out("patrol: GitLab does not say whose the bridge's token is: %s" % _said(exc))
        own = {x for x in (str(kit.get("bridge_user") or "").lower(), self._me) if x}
        return own, own | {str(u).lower() for u in kit.get("bot_users") or []}

    def _turn(self, row: MrRow) -> str:
        """running | ended | failed | unknown: the last turn of the row's session, once a round."""
        if not row.slot or not row.send_id:
            return "unknown"
        key = (row.slot, row.send_id)
        if key not in self._turns:
            try:
                self._turns[key] = self.sessions.patrol_turn(row.slot, row.send_id)
            except bk.m("common").BrainError as exc:
                self.out("%s: its turn cannot be read: %s" % (row.slot, _said(exc)))
                self._turns[key] = "unknown"
        return self._turns[key]

    def _stop(self, row: MrRow) -> None:
        if row.slot:
            try:
                self.sessions.patrol_stop(row.slot)
            except bk.m("common").BrainError as exc:
                self.out("%s: not stopped: %s" % (row.slot, _said(exc)))

    def _forget(self, done: Callable[[MrRow], bool]) -> None:
        """The rows patrol is *done* with (MRs merged or closed since, every MR of a project it is no longer on): their
        turns stopped, their clones deleted, then the rows gone, so they hold no session any more."""
        for row in self.led.rows():
            if done(row):
                if self._busy(row):
                    self._stop(row)
                if self.workspaces is not None:
                    self.workspaces.drop(row.project_id, row.iid)
                self.led.drop(row.project, row.iid)

    def _busy(self, row: MrRow) -> bool:
        """Its session is at work: a turn patrol waits on, or one the MR head moved past that has not ended yet."""
        return row.state == WORKING or (row.outcome == "moved" and self._turn(row) == "running")

    def _dispatch(self, entries: list[Entry], now: float) -> None:
        """The MRs with work to give, the ones open before patrol started first, then by when each last changed, while
        fewer than CAP sessions are at work (every project's, also one this round could not read) or were handed work
        this round, whatever KiroCrew made of it."""
        common = bk.m("common")
        busy = sum(1 for r in self.led.rows() if self._busy(r))
        todo = [e for e in entries if e.standing == "todo"]
        for e in sorted(todo, key=lambda e: (_ts(e.mr.get("created_at")) >= e.since, _ts(e.mr.get("updated_at")),
                                             e.project, int(e.mr["iid"]))):
            if e.row is not None and self._busy(e.row):
                e.standing, e.note = "waiting", "它的会话还在做上一轮：做完再派"
                continue
            if busy >= CAP:
                e.standing = "queued"
                continue
            try:
                path = self.workspaces.prepare(e.item)
            except Moved:
                e.standing, e.note = "waiting", "MR 刚有新的提交：下一轮再看"
                continue
            except (common.BrainError, OSError) as exc:     # git: tried again next round
                e.standing, e.note = "error", "工作区没准备好：%s" % _said(exc)
                self.out("%s!%s: its workspace is not ready: %s" % (e.project, e.mr.get("iid"), _said(exc)))
                continue
            busy += 1
            try:
                self._send(e, path, now)
            except common.BrainError as exc:                # its session not opened or set up: nothing was sent
                e.standing, e.note = "error", "没派出去：%s" % _said(exc)
                self.out("%s!%s: not dispatched: %s" % (e.project, e.mr.get("iid"), _said(exc)))

    def _send(self, e: Entry, path: str, now: float) -> None:
        """e's problems to its MR's session, which is opened the first time and kept for as long as the MR is open.
        The row is working, with the message's sendId, before the message goes. A message KiroCrew did not answer may
        have landed: it stays working (one of CAP, never sent again; its turn, or TIMEOUT_S, says how it went). One
        KiroCrew refused is a failed attempt. Raises BrainError when the session was not opened or set up and KiroCrew
        did not answer: nothing was sent, and it is no attempt."""
        item, row, led = e.item, e.row, self.led
        if not row.slot:
            name = slot_name(item.project_id, item.iid, row.gen + 1)
            row = replace(row, gen=row.gen + 1)
            led.put(row)                                # the name is used once, also when opening it fails
            self.sessions.open_patrol(name, AGENT, str(e.kit.get("model") or ""), "%s!%d" % (item.project, item.iid))
            row = replace(row, slot=name)
            led.put(row)
        e.row = row
        attempts = row.attempts + 1 if row.fingerprint == item.fingerprint else 1
        sid = "P-" + hashlib.sha256(("%s\n%s\n%d\n%r" % (row.slot, item.fingerprint, attempts, now)).encode()
                                    ).hexdigest()[:20]
        went = replace(row, state=WORKING, head_sha=item.head_sha, pipeline_id=item.pipeline_id, conflict=item.conflict,
                       fingerprint=item.fingerprint, attempts=attempts, streak=row.streak + 1,
                       people_note=row.read.people_note, checked_head=item.head_sha, dispatched_at=now, send_id=sid,
                       sent_notes=item.note_ids, outcome="")
        try:
            self.sessions.patrol_project(row.slot, path)
        except Refused as exc:
            self._refused(e, went, exc)
            return
        led.put(went)                                   # from here on the message may be in its session
        e.row, e.standing = went, "working"
        try:
            self.sessions.patrol_send(row.slot, message(item, path, self.helper), sid)
        except Refused as exc:
            self._refused(e, went, exc)
            return
        except bk.m("common").BrainError as exc:
            e.note = "送没送到不清楚（%s）：算它在做，不再送一次" % _said(exc)
            self.out("%s!%d: its message to %s may or may not have landed: %s" % (item.project, item.iid, row.slot,
                                                                                _said(exc)))
            return
        self.out("patrol: %s!%d dispatched to %s (attempt %d)" % (item.project, item.iid, row.slot, attempts))

    def _refused(self, e: Entry, went: MrRow, exc: Refused) -> None:
        row = _failed(went, "refused")
        self.led.put(row)
        e.row = row
        if row.state == BLOCKED:
            e.standing = "blocked"
        else:
            e.standing, e.note = "error", "没派出去（%s）：算一次，下一轮再派" % _said(exc)
        self.out("%s!%d: %s refused its message: %s" % (e.project, went.iid, went.slot, _said(exc)))


def _seen(mr: Mapping[str, Any]) -> str:
    return "%s %s" % (mr.get("updated_at"), mr.get("sha"))


def _note(standing: str, row: MrRow | None) -> str:
    if standing == "blocked" and row is not None and row.state == PAUSED:
        return "连着派了 %d 次，中间没有人推送或评论，在 MR 上说了：等人推送或评论" % MAX_STREAK
    if standing == "blocked" and row is not None:
        tried = "派了 %d 次，会话都没收下" if row.outcome == "refused" else "试了 %d 次没修好"
        return (tried % row.attempts) + "，在 MR 上说了：等它有新变化"
    return "这些已经交给会话做过一轮：等 MR 有新变化" if standing == "done" else ""


def view_of(projects: list[dict[str, Any]], entries: list[Entry], at: float | None) -> dict[str, Any]:
    """GET /team/patrol (TeamPatrol in ui/src/types.ts): the projects and every open MR the last round saw."""
    items = []
    for e in entries:
        row, item = e.row, e.item
        author = e.mr.get("author") if isinstance(e.mr.get("author"), dict) else {}
        note = e.note or _note(e.standing, row)
        items.append({"project": e.project, "iid": int(e.mr["iid"]), "title": str(e.mr.get("title") or ""),
                      "url": e.mr.get("web_url") or None, "author": str(author.get("username") or ""),
                      "state": e.standing, "problems": [{"kind": p.kind, "text": problem_text(p)}
                                                        for p in (item.problems if item else ())],
                      "skip": e.skip.text if e.skip else "", "skip_code": e.skip.code if e.skip else "",
                      "note": note, "attempts": row.attempts if row else 0, "slot": (row.slot or None) if row else None,
                      "updated_at": str(e.mr.get("updated_at") or "") or None})
    return {"on": any(p["on"] for p in projects), "cap": CAP, "working": sum(1 for i in items if i["state"] == "working"),
            "last_at": at, "projects": projects, "items": items}
