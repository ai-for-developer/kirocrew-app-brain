"""MR 巡检 (work source mr-patrol, sources.py). Each round of the bridge looks at every open MR of the runner's projects
whose kit.json switches it on. An MR with a problem the runner can fix goes to a brain-patrol session of its own (slot
patrol-<project id>-<iid>), working in a clone of its own (<work>/patrol/<project id>-<iid>: KiroCrew lets agents work
only under ~/workplace). Later problems of that MR go to the same session.

A problem is one of four. The MR head's pipeline failed (one on the head, or on its merge into the target branch: merged
results): its failed jobs, each with the last LOG_LINES lines of its log, those of the pipelines its failed trigger jobs
started (child pipelines), and the failed checks external CI reported into it as commit statuses (Jenkins' GitLab
plugin: a pipeline of source external has nothing else), which GitLab has no job or trigger job of: each with its link
and the last lines of its log from Jenkins (jenkins.py), or why there is none. A person left a review thread open:
unresolved and resolvable, with no reply of patrol's own after it written by a session that could have been told it
(open_reviews: a note added while a session worked is not answered by its reply), and not named in a turn that ended. A
person wrote a plain comment on the MR (no thread, so nothing marks it resolved) since patrol started in the project,
not named in a turn that ended: the session answers it with a note on the MR that quotes it. Or the MR conflicts with
its target branch. The MR's problems make a PatrolItem. Its fingerprint moves with the head, a failed pipeline, a new
build of a failed check, the conflict and the open review notes and comments, and nothing else.

The ledger, <root>/patrol.sqlite, has one row per open MR of the projects patrol is on (MrRow). It is a state machine
(settle, Patrol._look and Patrol._send):
  idle -> working     the MR has work no turn ended on at its head, and a session is free: at most CAP at
                      work or handed work this round, 测试环境报错's fixes counted, the others wait (first the MRs open
                      before patrol started in their project, then by when each MR last changed). The row is working
                      before its message goes, so a message KiroCrew did not answer, which may have landed, is never
                      sent again.
  working -> idle     the turn ended (the review notes it named count as answered then, and the kinds of problem it
                      was given are done at its head: a turn that pushed nothing did all it could there, so the
                      conflict and the failed CI it was given, whichever pipeline or build fails there later, are not
                      sent again at that head; a person's new note is, and a kind it was not given), or the MR head
                      moved
  working -> idle     a failed attempt: TIMEOUT_S without either (the turn is stopped), or a turn that ended without a
                      reply or with KiroCrew's error. A message KiroCrew refused is a failed attempt as well, and so is
                      its clone not ready for the same fingerprint a round again (a git lock a stopped turn left in it
                      is removed once LOCK_STALE_S old, and waited for until then)
  working -> blocked  the MAX_ATTEMPTS-th failed attempt on one fingerprint: left alone until its fingerprint changes
  idle -> paused      dispatched MAX_STREAK times in a row with no person pushing or commenting in between (each push
                      of the runner's makes a new fingerprint): left alone until a person does
  any -> rules        brain-kit's brain-push-rules failed the head pipeline for commits of the runner's (it lets the
                      bot commit on run branches only): any it pushes would be refused too, so the MR is left alone for
                      good (its turn stopped)
One comment on the MR says why it is blocked, paused or refused, and what is not fixed (for rules: what a person
does). A row is forgotten, its turn stopped and its clone deleted, once its MR is merged or closed, or patrol is no
longer on in its project; one of a project bridge.json now names in another case (GitLab's paths are not
case-sensitive) moves to that spelling (Ledger.respell).
A round lists each project's open MRs once. It reads an MR's pipeline and threads only when the MR changed since, is
being worked, its pipeline had not finished, or RECHECK_S went by: GitLab does not move an MR's updated_at when a
pipeline ends, or when one runs again on the same head. A Jenkins build run again on the same head lands in the same
pipeline: an external pipeline's checks are read each time its MR is, a build's log once.
Skipped, with the reason on the page: MRs the hand-over owns (its labels), opted out (SKIP_LABEL), from a fork, claimed
by another work source, and those whose only problems are bots' comments.
"""

from __future__ import annotations

import contextlib
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

from .. import bk, snapshot
from ..constants import MERGE_LABEL
from . import sources
from .jenkins import RUNNING as BUILD_RUNNING, WAIT_S, Jenkins, Logs, build_path

KIND = sources.PATROL
AGENT = "brain-patrol"
CAP = 2                     # patrol sessions at work at once, counting those handed work this round and 测试环境报错's
TIMEOUT_S = 30 * 60         # a turn with no end and no new head this long is a failed attempt
MAX_ATTEMPTS = 2            # failed attempts on one fingerprint before the MR is blocked
MAX_STREAK = 3              # dispatches in a row with no person pushing or commenting in between before it is paused
LOG_LINES = 80              # of each failed job's log
LOG_LINE_CHARS = 300
MAX_JOBS = 5
MAX_DEPTH = 2               # trigger jobs followed down from the MR's pipeline: to a child's child, no further
BODY_CHARS = 3000
NO_PIPELINE_S = 600         # an MR changed this recently with no pipeline at its head yet: read again next round
RECHECK_S = 600             # an MR that did not change is read again this often (a pipeline run again on its head)
LOCK_STALE_S = 5 * 60       # a git lock this old in a clone no turn works in was left by a turn stopped mid-commit
SKIP_LABEL = "ai-skip"
BUILD_WAIT = "Jenkins 的构建还没跑完：跑完再派（最多等 %d 分钟）" % (WAIT_S // 60)
FINAL_PIPELINE = ("success", "failed", "canceled", "skipped", "manual")
UNSURE_MERGE = ("checking", "unchecked", "preparing")      # detailed_merge_status before GitLab knows
BOT_RE = re.compile(r"^(?:project|group)_\d+_bot(?:_[0-9a-f]+)?\d*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SECTION_RE = re.compile(r"^section_(?:start|end):\d+:")
IDLE, WORKING, BLOCKED, PAUSED, RULES = "idle", "working", "blocked", "paused", "rules"
# the last line of brain-kit's `brain-ci push-rules` (its CI job brain-push-rules) when it refuses an MR
RULES_RE = re.compile(r"^brain-ci push-rules: \S+ by \S+ FAILED$", re.M)


# ---- what an MR has ------------------------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FailedJob:
    name: str
    log: str                    # its last LOG_LINES lines, without terminal colours


@dataclass(frozen=True)
class FailedCheck:
    """A failed commit status of the pipeline that no GitLab job of it stands for: what external CI (Jenkins' GitLab
    plugin) reported, the newest of its name."""
    status_id: int              # a new build of the check is a new status
    name: str
    url: str                    # its target_url (a Jenkins build's: at the address the runner reaches Jenkins at)
    log: str | None             # the last LOG_LINES lines of its log; None: not read, *why* says why
    why: str = ""
    later: bool = False         # not read yet: the round's time for Jenkins ran out, or the build still runs
                                # (jenkins.Logs); read next round


@dataclass(frozen=True)
class CiFailed:
    pipeline_id: int
    jobs: tuple[FailedJob, ...]
    checks: tuple[FailedCheck, ...] = ()
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


def fingerprint(head_sha: str, pipeline_id: int | None, conflict: bool, note_ids: Any, status_ids: Any = ()) -> str:
    """*status_ids*: the failed checks' commit status ids (FailedCheck), counted only when there are some."""
    parts = [head_sha, pipeline_id, bool(conflict), sorted(int(n) for n in note_ids)]
    if status_ids:
        parts.append(sorted(int(s) for s in status_ids))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:20]


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
    def status_ids(self) -> tuple[int, ...]:
        return tuple(c.status_id for p in self.problems if isinstance(p, CiFailed) for c in p.checks)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.head_sha, self.pipeline_id, self.conflict, self.note_ids, self.status_ids)


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
    checks: tuple[FailedCheck, ...] = ()        # the failed checks external CI reported, of a failed pipeline
    last_note: int = 0                          # the newest note on the MR, anyone's (its id: GitLab's ids only grow)

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "Read":
        return cls(d.get("pipeline_id"), str(d.get("pipeline_status") or ""),
                   tuple(FailedJob(**j) for j in d.get("jobs") or ()),
                   tuple(Review(**r) for r in d.get("reviews") or ()), int(d.get("bot_notes") or 0),
                   int(d.get("people_note") or 0), tuple(Comment(**c) for c in d.get("comments") or ()),
                   tuple(FailedCheck(**c) for c in d.get("checks") or ()), int(d.get("last_note") or 0))


@dataclass(frozen=True)
class Skip:
    code: str                   # handoff | opt_out | fork | claimed | bots_only
    text: str


def problem_text(p: Problem) -> str:
    if isinstance(p, CiFailed):
        return "流水线 #%d 没过：%s" % (p.pipeline_id, "、".join([j.name for j in p.jobs] + [c.name for c in p.checks])
                                   or "看流水线")
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


def open_reviews(discussions: list[dict[str, Any]], own: set[str], bots: set[str],
                 marks: tuple[int, ...] = ()) -> tuple[list[Review], int]:
    """(people's open review notes, open threads only bots wrote in). A thread counts while it is resolvable and not
    resolved. In it, a note of patrol's own account (*own*) answers the notes before it its session could have been
    told: those up to the newest of *marks* (the newest note before each dispatch, MrRow.marks) below its own id, every
    one before it when none is (a reply from before patrol's ledger). So a person's note added while the session
    worked is still open after its reply. A bot's note is never a problem."""
    out, bot_threads = [], 0
    for d, notes in zip(discussions, notes_of(discussions)):
        mine = [n for n in notes if n.get("resolvable")]
        if not mine or all(n.get("resolved") for n in mine):
            continue
        tail, bot_said = [], False
        for n in notes:
            by = written_by(n, own, bots)
            if by == "own":
                told = max((m for m in marks if m < int(n["id"])), default=None)
                tail, bot_said = [x for x in tail if told is not None and int(x["id"]) > told], False
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


def read_mr(gl: Any, logs: Logs, project: str, mr: Mapping[str, Any], own: set[str], bots: set[str],
            since: float, before: Read | None = None, marks: tuple[int, ...] = ()) -> Read:
    """GitLab's MR (its head pipeline, at_head); when that pipeline failed, its failed jobs with their logs, its child
    pipelines' too (pipeline_failures), and the failed checks external CI reported into it (failed_checks); its
    threads, and its plain comments written *since* (patrol started in the project). *before*'s jobs while it is the
    same failed pipeline; its checks are read again unless it is GitLab's own and had none: a Jenkins build run again
    lands in the same pipeline."""
    rest = bk.m("rest")
    iid = int(mr["iid"])
    hp = (gl.mr(project, iid) or {}).get("head_pipeline")
    hp = hp if isinstance(hp, dict) and hp.get("id") and at_head(gl, project, mr, hp) else None
    pid, status = (int(hp["id"]), str(hp.get("status") or "")) if hp else (None, "")
    external = bool(hp) and hp.get("source") == "external"
    same = before is not None and (before.pipeline_id, before.pipeline_status) == (pid, status)
    jobs: tuple[FailedJob, ...] = ()
    checks: tuple[FailedCheck, ...] = ()
    if status == "failed" and pid and same and not external and not before.checks:
        jobs = before.jobs
    elif status == "failed" and pid:
        found, job_ids = ([], set()) if external else pipeline_failures(gl, project, pid, logs=not same)
        jobs = before.jobs if same else tuple(found)
        checks = failed_checks(gl, logs, project, str(hp.get("sha") or mr.get("sha") or ""), pid, job_ids,
                               before.checks if before is not None else ())
    threads = gl.pages("/projects/%s/merge_requests/%d/discussions" % (rest.q(project), iid), {}, 100, 20,
                       "discussions on !%d" % iid)
    threads = [d for d in threads if isinstance(d, dict)]
    reviews, bot_threads = open_reviews(threads, own, bots, marks)
    people = [int(n["id"]) for notes in notes_of(threads) for n in notes if written_by(n, own, bots) == "person"]
    return Read(pid, status, jobs, tuple(reviews), bot_threads, max(people, default=0),
                tuple(plain_comments(threads, own, bots, since)), checks,
                max((int(n["id"]) for notes in notes_of(threads) for n in notes), default=0))


def at_head(gl: Any, project: Any, mr: Mapping[str, Any], hp: Mapping[str, Any]) -> bool:
    """Whether *hp*, GitLab's head pipeline of the MR, ran for its head: on the head itself, or on the merge of the head
    into the target branch (merged results, a merge train: ref refs/merge-requests/<iid>/merge or /train, on a merge
    commit one of whose parents is the head). An older GitLab keeps an older head's pipeline there for a while."""
    sha, head = hp.get("sha"), str(mr.get("sha") or "")
    if sha in (None, head):
        return True
    if hp.get("ref") not in ["refs/merge-requests/%d/%s" % (int(mr["iid"]), r) for r in ("merge", "train")]:
        return False
    rest = bk.m("rest")
    try:
        merge = gl.call("GET", "/projects/%s/repository/commits/%s" % (rest.q(project), rest.q(sha))) or {}
    except rest.HttpError as exc:
        if exc.status != 404:
            raise
        return False
    return head in (merge.get("parent_ids") or ())


def bridges_of(gl: Any, project: Any, pipeline_id: int) -> list[dict[str, Any]]:
    """The pipeline's failed trigger jobs (GitLab's bridges: pipelines/:id/jobs leaves them out); none when GitLab does
    not show them (403, 404)."""
    rest = bk.m("rest")
    try:
        rows = gl.call("GET", "/projects/%s/pipelines/%d/bridges" % (rest.q(project), pipeline_id),
                       {"scope[]": "failed", "per_page": 100})
    except rest.HttpError as exc:
        if exc.status not in (403, 404):
            raise
        return []
    return [b for b in rows or [] if isinstance(b, dict) and b.get("id")]


def pipeline_failures(gl: Any, project: Any, pipeline_id: int, logs: bool = True, depth: int = 0,
                      prefix: str = "") -> tuple[list[FailedJob], set[int]]:
    """(the failed jobs of the pipeline, each with the last lines of its log, then through each failed trigger job the
    failed jobs of the pipeline it started: a child pipeline, or another project's, named by the trigger job, MAX_DEPTH
    down; the ids of its failed jobs and trigger jobs, which GitLab lists among its commit statuses too). *logs* False:
    the ids alone. Jobs allowed to fail are left out, MAX_JOBS at most."""
    listed = [j for j in gl.pipeline_jobs(project, pipeline_id) if isinstance(j, dict) and j.get("id")]
    triggers = bridges_of(gl, project, pipeline_id)
    ids = {int(j["id"]) for j in listed + triggers}
    if not logs:
        return [], ids
    out = [FailedJob(prefix + str(j.get("name") or j["id"]), log_tail(gl.job_trace(project, int(j["id"]))))
           for j in listed if not j.get("allow_failure")][:MAX_JOBS]
    for b in triggers:
        if len(out) < MAX_JOBS and not b.get("allow_failure"):
            out += downstream_failures(gl, project, b, depth, prefix + str(b.get("name") or b["id"]))
    return out[:MAX_JOBS], ids


def downstream_failures(gl: Any, project: Any, trigger: Mapping[str, Any], depth: int, name: str) -> list[FailedJob]:
    """The failed jobs of the pipeline the failed *trigger* job started, each named *name* › its own; when there are
    none to show, the trigger job itself with why in place of its log."""
    rest = bk.m("rest")
    down = trigger.get("downstream_pipeline") if isinstance(trigger.get("downstream_pipeline"), dict) else {}
    if not down.get("id"):
        why = str(trigger.get("failure_reason") or "")
        return [FailedJob(name, "（它要触发的流水线没建成%s）" % ("：" + why if why else ""))]
    if depth >= MAX_DEPTH:
        return [FailedJob(name, "（它触发的流水线 #%d 没过：再往下的没读）" % int(down["id"]))]
    try:
        found, _ = pipeline_failures(gl, down.get("project_id") or project, int(down["id"]), True, depth + 1,
                                     name + " › ")
    except rest.HttpError as exc:
        if exc.status not in (403, 404):
            raise
        return [FailedJob(name, "（它触发的流水线 #%d 运行机读不到：HTTP %d）" % (int(down["id"]), exc.status))]
    return found or [FailedJob(name, "（它触发的流水线 #%d 没过，里面没有没过的任务）" % int(down["id"]))]


def failed_checks(gl: Any, logs: Logs, project: str, sha: str, pipeline_id: int, jobs: set[int],
                  known: tuple[FailedCheck, ...]) -> tuple[FailedCheck, ...]:
    """The pipeline's failed commit statuses that none of its GitLab *jobs* (their ids, trigger jobs too: GitLab lists
    both as statuses) stands for, the newest of each name, with the tail of each one's Jenkins log. *known*: the checks
    read before, whose logs are kept; one whose log was not read is asked of *logs* again, which says when it reads
    it again. No checks when GitLab does not show the statuses (403, 404)."""
    rest = bk.m("rest")
    try:
        rows = gl.call("GET", "/projects/%s/repository/commits/%s/statuses" % (rest.q(project), rest.q(sha)),
                       {"pipeline_id": pipeline_id, "per_page": 100})
    except rest.HttpError as exc:
        if exc.status not in (403, 404):
            raise
        return ()
    newest: dict[str, Mapping[str, Any]] = {}
    for s in rows or []:
        ours = isinstance(s, dict) and s.get("id") and s.get("pipeline_id") in (None, pipeline_id)
        if ours and int(s["id"]) not in jobs:
            name = str(s.get("name") or s["id"])
            if name not in newest or int(s["id"]) > int(newest[name]["id"]):
                newest[name] = s
    failed = sorted((s for s in newest.values() if s.get("status") == "failed" and not s.get("allow_failure")),
                    key=lambda s: int(s["id"]))[:MAX_JOBS]
    kept = {c.status_id: c for c in known if c.log is not None}
    out = []
    for s in failed:
        sid, name = int(s["id"]), str(s.get("name") or s["id"])
        if sid in kept:
            out.append(kept[sid])
            continue
        got = logs.console(str(s.get("target_url") or ""))
        out.append(FailedCheck(sid, name, got.page, log_tail(got.raw) if got.raw is not None else None, got.why,
                               got.later))
    return tuple(out)


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


def rules_said(jobs: tuple[FailedJob, ...] | list[FailedJob]) -> list[str]:
    """What brain-kit's push rules said (their FAIL lines) when one of a failed pipeline's failed *jobs* is
    `brain-ci push-rules` refusing it (RULES_RE ends its log); [] when none is."""
    for j in jobs:
        if RULES_RE.search(j.log):
            return [x[len("FAIL "):] for x in j.log.split("\n") if x.startswith("FAIL ")] or [j.log.split("\n")[-1]]
    return []


def runner_commits(gl: Any, project: str, iid: int, bots: set[str]) -> list[str]:
    """The short ids of the MR's commits the runner made (authored or committed as one of *bots*, its git emails),
    newest first, as GitLab lists them."""
    rows = gl.pages("/projects/%s/merge_requests/%d/commits" % (bk.m("rest").q(project), iid), {}, 100, 10,
                    "commits of !%d" % iid)
    return [str(c.get("short_id") or str(c["id"])[:8]) for c in rows if isinstance(c, dict) and c.get("id") and
            {str(c.get(k) or "").lower() for k in ("author_email", "committer_email")} & bots]


def item_of(project: str, mr: Mapping[str, Any], read: Read, answered: tuple[int, ...]) -> PatrolItem:
    target = str(mr.get("target_branch") or "")
    unsure = mr.get("detailed_merge_status") in UNSURE_MERGE
    problems: list[Problem] = []
    if mr.get("detailed_merge_status") == "conflict" or (mr.get("has_conflicts") and not unsure):
        problems.append(Conflict(target))
    if read.pipeline_status == "failed" and read.pipeline_id:
        problems.append(CiFailed(read.pipeline_id, read.jobs, read.checks))
    problems += [r for r in read.reviews + read.comments if r.note_id not in answered]
    return PatrolItem(project, int(mr.get("project_id") or mr.get("target_project_id") or 0), int(mr["iid"]),
                      str(mr.get("title") or ""), mr.get("web_url") or None, str(mr.get("source_branch") or ""), target,
                      str(mr.get("sha") or ""), tuple(problems))


def skip_of(kit: Mapping[str, Any], mr: Mapping[str, Any]) -> Skip | None:
    """The skip rules that the MR list alone can tell."""
    if snapshot.BRANCH in (mr.get("source_branch"), mr.get("target_branch")):
        return Skip("status", "团队状态的分支 %s 的 MR：它只放团队服务器写的状态，不巡检，也别合并" % snapshot.BRANCH)
    labels = {str(x) for x in mr.get("labels") or []}
    hand = [kit[k] for k in ("handoff_label", "running_label", "rejected_label") if kit.get(k) in labels]
    hand += [MERGE_LABEL] if MERGE_LABEL in labels else []      # accepted and done, still the hand-over's
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
    head_sha: str = ""                  # the last dispatch's head, pipeline, its failed checks and conflict
    pipeline_id: int | None = None
    status_ids: tuple[int, ...] = ()
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
                                        # | workspace (its clone was not ready, again: _unready)
    done_head: str = ""                 # the head of the dispatch whose turn ended last (its notes answered) ...
    done: tuple[str, ...] = ()          # ... and the kinds of problem (Problem.kind) that turn was given there
    answered: tuple[int, ...] = ()
    marks: tuple[int, ...] = ()         # Read.last_note at each dispatch: what its session could know (open_reviews)
    told: bool = False                  # it is blocked or paused, and the MR was told so
    ws_fp: str = ""                     # the fingerprint its clone was last not ready for ("": it was ready)
    ws_error: str = ""                  # ... and why
    rules: tuple[str, ...] = ()         # rules: the runner's commits brain-push-rules refused (short ids, newest first)
    rules_said: tuple[str, ...] = ()    # ... and what it said (its FAIL lines)
    claim: dict = field(default_factory=dict)
    seen: str = ""                      # the MR's updated_at and head when it was last read in detail
    read: Read | None = None
    read_at: float = 0.0

    @classmethod
    def of(cls, d: Mapping[str, Any]) -> "MrRow":
        d = dict(d)
        for k in ("sent_notes", "answered", "marks", "status_ids", "rules", "rules_said", "done"):
            d[k] = tuple(d.get(k) or ())
        d["read"] = Read.of(d["read"]) if isinstance(d.get("read"), dict) else None
        row = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if "done_head" not in d and d.get("done_fp") == fingerprint(row.head_sha, row.pipeline_id, row.conflict, (),
                                                                     row.status_ids):
            row = replace(row, done_head=row.head_sha, done=given(row))     # before 0.5.5: its turn's end as done_fp
        return row


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

    def respell(self, projects: list[str]) -> None:
        """The rows and start of a project kept under another spelling than *projects* has for it (GitLab's paths are
        not case-sensitive: 一键建运行机 writes GitLab's own) moved to that spelling, as the same project's."""
        named = {p.lower(): p for p in projects}
        for old in {r[0] for r in self.db.execute("SELECT project FROM mrs UNION SELECT project FROM projects")}:
            new = named.get(old.lower(), old)
            if new != old:
                for row in [r for r in self.rows() if r.project == old]:
                    self.put(replace(row, project=new))
                    self.drop(old, row.iid)
                self.db.execute("UPDATE OR REPLACE projects SET project=? WHERE project=?", (new, old))

    def keep_projects(self, keep: list[str]) -> None:
        """Forget when patrol started in every project but *keep*: it starts again there when it is on again."""
        self.db.execute("DELETE FROM projects WHERE project NOT IN (%s)" % ",".join("?" * len(keep)), tuple(keep))

    def close(self) -> None:
        self.db.close()


# ---- the state machine ---------------------------------------------------------------------------------------- #

def given(row: MrRow) -> tuple[str, ...]:
    """The kinds of problem its last dispatch gave the session besides people's notes: its failed CI, its conflict."""
    return tuple(k for k, on in ((CiFailed.kind, row.pipeline_id is not None), (Conflict.kind, row.conflict)) if on)


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
                           done_head=row.head_sha, done=given(row)), False
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
    """ok (nothing to fix) | working | blocked | rules (brain-push-rules refused the runner's commits: left alone) |
    done (a turn that ended at this head was given every kind of problem it has: people's notes not answered yet are
    never among them) | todo."""
    if row.state == WORKING:
        return "working"
    if not item.problems:
        return "ok"
    if row.state == RULES:
        return "rules"
    if row.state == PAUSED or (row.state == BLOCKED and row.fingerprint == item.fingerprint):
        return "blocked"
    if row.done_head == item.head_sha and {p.kind for p in item.problems} <= set(row.done):
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
            for c in p.checks:
                where = "Jenkins 构建 %s" % c.url if build_path(c.url) else "外部 CI 报给 GitLab 的，%s" % (c.url or "没有链接")
                out.append("   - %s（%s）：" % (c.name, where))
                if c.log is None:
                    out.append("     没读到日志：%s" % c.why)
                else:
                    out += (["     （%s）" % c.why] if c.why else []) + _quoted(c.log or "（日志是空的）", "     ")
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


class Locked(Exception):
    """A git lock in the clone younger than LOCK_STALE_S: the git of a turn just stopped may still be at it. Nothing was
    touched; the clone is prepared again next round."""


@dataclass(frozen=True)
class Workspaces:
    """A clone per MR under *root*, made once; before each dispatch fetched and reset to the source branch on origin.
    Its callers prepare a clone only while no turn works in it: a git lock a turn stopped mid-commit left there is
    removed once it is LOCK_STALE_S old."""
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
        unlock(os.path.join(path, ".git"))
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


def locked_note(exc: Locked) -> str:
    return "工作区里有 git 没做完留下的锁 %s：没有会话在用它，满 %d 分钟就删掉" % (exc, LOCK_STALE_S // 60)


def unlock(git_dir: str) -> None:
    """The clone's git locks (*.lock in .git and under .git/refs) removed when every one is LOCK_STALE_S old: none of
    them can be a live git's then, as no turn works in the clone. Locked while one is younger."""
    locks = [os.path.join(git_dir, n) for n in os.listdir(git_dir) if n.endswith(".lock")]
    for top, _, names in os.walk(os.path.join(git_dir, "refs")):
        locks += [os.path.join(top, n) for n in names if n.endswith(".lock")]
    now = time.time()
    for lock in locks:
        with contextlib.suppress(FileNotFoundError):
            age = now - os.stat(lock).st_mtime
            if age < LOCK_STALE_S:
                raise Locked("%s（%d 秒前留下的）" % (os.path.relpath(lock, os.path.dirname(git_dir)), max(0, age)))
    for lock in locks:
        with contextlib.suppress(FileNotFoundError):
            os.remove(lock)


def slot_name(project_id: int, iid: int, gen: int) -> str:
    return "patrol-%d-%d" % (project_id, iid) + ("-%d" % gen if gen > 1 else "")


# the first line of every blocked_text: a person's machine reads it as patrol having left the MR alone (runner_view.py)
STOPPED_RE = re.compile(r"^MR 巡检：(?:运行机(?:连着修了|派了|试了) \d+ 次|brain-push-rules 不让运行机)")


def blocked_text(row: MrRow, item: PatrolItem) -> str:
    what = "\n".join("- " + problem_text(p) for p in item.problems)
    if row.state == RULES:
        return rules_text(row, item)
    if row.state == PAUSED:
        return ("MR 巡检：运行机连着修了 %d 次，中间没有人推送或评论，这些还没修好：\n%s\n\n运行机先停下，不管这个 MR；有人推送"
                "或评论以后再看。" % (MAX_STREAK, what))
    if row.outcome == "refused":
        return ("MR 巡检：运行机派了 %d 次，它的会话都没收下，这些还没修：\n%s\n\n请管运行机的人看一下它的 KiroCrew。运行机先不管"
                "这个 MR；它有新的提交、新的流水线或新的评审意见以后再看。" % (MAX_ATTEMPTS, what))
    if row.outcome == "workspace":
        return ("MR 巡检：运行机试了 %d 次，都没能开始修：运行机上这个 MR 的工作区（它的克隆）准备不好。这些还没修：\n%s\n\n请管运行机"
                "的人看一下运行机页面上这个 MR 的说明。运行机先不管这个 MR；它有新的提交、新的流水线或新的评审意见以后再看。" % (MAX_ATTEMPTS, what))
    return ("MR 巡检：运行机试了 %d 次，没修好这些：\n%s\n\n运行机先不管这个 MR；它有新的提交、新的流水线或新的评审意见以后再看。"
            % (MAX_ATTEMPTS, what))


def rules_text(row: MrRow, item: PatrolItem) -> str:
    """What a person does once brain-push-rules refused the runner's commits on the MR's branch: those commits made
    theirs (rewritten with their name: a revert leaves them in the MR), or dropped."""
    br, first = item.source_branch, row.rules[-1]
    return "\n".join([
        "MR 巡检：brain-push-rules 不让运行机在分支 %s 上提交（机器人只能推 run 分支）。运行机的提交 %s 让这个 MR 的流水线过不了，"
        "这个 MR 合并不了。运行机不再修这个 MR。" % (br, "、".join(row.rules)), "",
        "要留下这些改动，把从 %s 起的提交改成你自己的（会把它们都记成你的），再推上去：" % first, "",
        "```", "git fetch origin", "git checkout -B %s origin/%s" % (br, br),
        "git rebase %s~1 --exec 'git commit --amend --no-edit --reset-author'" % first,
        "git push --force-with-lease origin %s" % br, "```", "",
        "不要这些改动：git rebase -i %s~1，删掉运行机的提交，再推上去。只 revert 不行：brain-push-rules 查 MR 里所有的提交。" % first,
        "", "brain-push-rules 说：", "", *["> " + x for x in row.rules_said]])


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
    """The work source mr-patrol, hosted by TeamRuntime: round() each bridge round on its worker thread (*shared* says
    how many brain-patrol sessions the other work sources have in a turn: sources.at_work), view() for the page from
    any thread (what the last round saw), held() for sources.holder, busy() for sources.at_work."""

    def __init__(self, *, gl: Any, sessions: Any, kit: Callable[[str], Mapping[str, Any]], projects: list[str],
                 ledger_path: str, workspaces: Workspaces | None, helper: Helper,
                 claims: Callable[[], list[sources.Claim]], jenkins: Jenkins = Jenkins(),
                 shared: Callable[[], int] = lambda: 0,
                 now: Callable[[], float] = time.time, out: Callable[[str], None] = print) -> None:
        self.gl, self.sessions, self.kit, self.projects = gl, sessions, kit, list(projects)
        self.logs = Logs(jenkins, now)
        self.ledger_path, self.workspaces, self.helper, self.claims = ledger_path, workspaces, helper, claims
        self.now, self.out, self.shared = now, out, shared
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
        return [r.claim for r in self.led.rows() if r.state in (WORKING, BLOCKED, PAUSED, RULES) and r.claim]

    def busy(self) -> int:
        """Its sessions in a turn now, as its ledger says (_busy), every project's: also one this round could not
        read."""
        if self._led is None and not os.path.exists(self.ledger_path):
            return 0
        return sum(1 for r in self.led.rows() if self._busy(r))

    def view(self) -> dict[str, Any]:
        return self.last

    # ---- one round ---------------------------------------------------------------------------------------- #

    def round(self) -> int:
        """Every open MR of the projects patrol is on, looked at; the ones with work dispatched, CAP at a time. -> how
        many open MRs were looked at. A project that cannot be read does not hold up the others: its error is raised at
        the end (BrainError)."""
        common = bk.m("common")
        now, self._turns = self.now(), {}
        self.logs.round()
        if self._led is not None or os.path.exists(self.ledger_path):
            self.led.respell(self.projects)
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
        self.last = view_of(projects, entries, now, self._hidden(entries))
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
            old = set(row.read.checks) if row.read is not None else set()
            row = replace(row, read=read_mr(self.gl, self.logs, e.project, mr, *self._who(kit), e.since,
                                            before=row.read, marks=row.marks), seen=_seen(mr), read_at=now)
            for c in row.read.checks:
                if c.log is None and not c.later and c not in old:
                    self.out("%s!%s: %s: no log: %s" % (e.project, mr["iid"], c.name, c.why))
            row = self._push_rules(e, row)
        item = item_of(e.project, mr, row.read, row.answered)
        row = self._watched(e, replace(row, project_id=item.project_id), item)
        if row.state == BLOCKED and row.fingerprint != item.fingerprint:
            row = replace(row, state=IDLE)
        if standing(row, item) == "todo" and row.streak >= MAX_STREAK:
            row = replace(row, state=PAUSED, told=False)
        row = self._told(e, row, item)
        led.put(row)
        e.row, e.item, e.standing = row, item, standing(row, item)
        if e.standing == "ok" and row.read.bot_notes:
            e.skip, e.standing = Skip("bots_only", "只有机器人写的评论，没有要修的"), "skipped"
        waits = [c.why for c in row.read.checks if c.later]
        if e.standing == "todo" and waits:
            e.standing = "waiting"
            e.note = BUILD_WAIT if BUILD_RUNNING in waits else "Jenkins 的日志这一轮没读完：下一轮读完再派"

    def _push_rules(self, e: Entry, row: MrRow) -> MrRow:
        """*row* in state rules for good when brain-push-rules refused the MR for commits of the runner's: any commit
        it pushes to a branch that is not a run branch is refused, so it does not try again (its turn is stopped)."""
        said = rules_said(row.read.jobs) if row.state != RULES and row.read.pipeline_status == "failed" else []
        mine = runner_commits(self.gl, e.project, row.iid, self._bot_emails(e.kit)) if said else []
        if not mine:
            return row
        if self._busy(row):
            self._stop(row)
        self.out("%s!%d: brain-push-rules refused the runner's commits %s: left to people" % (e.project, row.iid,
                                                                                             ", ".join(mine)))
        return replace(row, state=RULES, rules=tuple(mine), rules_said=tuple(said), told=False)

    def _told(self, e: Entry, row: MrRow, item: PatrolItem) -> MrRow:
        """*row*, its MR told once that it is blocked, paused or refused by brain-push-rules (blocked_text): told
        again next round when GitLab did not take it."""
        if row.state not in (BLOCKED, PAUSED, RULES) or row.told:
            return row
        try:
            self.gl.comment(e.project, item.iid, blocked_text(row, item))
        except bk.m("common").BrainError as exc:
            self.out("%s!%d: the blocked comment is not posted yet: %s" % (e.project, item.iid, _said(exc)))
            return row
        return replace(row, told=True)

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
        """Whether the MR is read in detail again: never read, changed since, being worked, a check's log left for this
        round, its pipeline not over (or none yet, just after a change), or not read for RECHECK_S."""
        r = row.read
        if r is None or row.seen != _seen(mr) or row.state == WORKING or now - row.read_at >= RECHECK_S:
            return True
        if any(c.later for c in r.checks):
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

    def _hidden(self, entries: list[Entry]) -> list[dict[str, str]]:
        """Its sessions in a turn now (_busy) whose MR no entry shows at work, each its project's with what holds it: so
        the page's count is every session it holds, as sources.at_work counts them."""
        if self._led is None and not os.path.exists(self.ledger_path):
            return []
        shown = {(e.project, int(e.mr["iid"])) for e in entries if e.standing == "working"}
        return [{"project": r.project, "text": ("%s!%d 的会话还在做上一轮（MR 有了新的提交）" if r.state != WORKING
                                                else "%s!%d 的会话还在做（这一轮没看成这个 MR）") % (r.project, r.iid)}
                for r in self.led.rows() if self._busy(r) and (r.project, r.iid) not in shown]

    def _busy(self, row: MrRow) -> bool:
        """Its session is at work: a turn patrol waits on, or one the MR head moved past that has not ended yet."""
        return row.state == WORKING or (row.outcome == "moved" and self._turn(row) == "running")

    def _dispatch(self, entries: list[Entry], now: float) -> None:
        """The MRs with work to give, the ones open before patrol started first, then by when each last changed, while
        fewer than CAP sessions are at work (every project's, also one this round could not read, and 测试环境报错's) or
        were handed work this round, whatever KiroCrew made of it."""
        common = bk.m("common")
        busy = self.shared() + self.busy()
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
            except Locked as exc:
                e.standing, e.note = "waiting", locked_note(exc)
                continue
            except (common.BrainError, OSError) as exc:     # git: tried again next round, an attempt when it repeats
                self.out("%s!%s: its workspace is not ready: %s" % (e.project, e.mr.get("iid"), _said(exc)))
                self._unready(e, _said(exc))
                continue
            busy += 1
            try:
                self._send(e, path, now)
            except common.BrainError as exc:                # its session not opened or set up: nothing was sent
                e.standing, e.note = "error", "没派出去：%s" % _said(exc)
                self.out("%s!%s: not dispatched: %s" % (e.project, e.mr.get("iid"), _said(exc)))

    def _unready(self, e: Entry, why: str) -> None:
        """*e*'s clone not ready: prepared again next round. When it was not ready for this fingerprint the round before
        either, a failed attempt, as a turn that failed: MAX_ATTEMPTS block the MR, with one comment."""
        item, row = e.item, replace(e.row, ws_error=why)
        if row.ws_fp != item.fingerprint:
            row = replace(row, ws_fp=item.fingerprint)
        else:
            row = _failed(replace(row, head_sha=item.head_sha, pipeline_id=item.pipeline_id, status_ids=item.status_ids,
                                  conflict=item.conflict, fingerprint=item.fingerprint,
                                  attempts=row.attempts + 1 if row.fingerprint == item.fingerprint else 1), "workspace")
            row = self._told(e, row, item)
        self.led.put(row)
        e.row, e.standing = row, standing(row, item)
        if e.standing != "blocked":
            e.standing, e.note = "error", ("工作区又没准备好，算一次没修好（第 %d 次）：%s" % (row.attempts, why)
                                           if row.outcome == "workspace" else "工作区没准备好：%s" % why)

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
        went = replace(row, state=WORKING, head_sha=item.head_sha, pipeline_id=item.pipeline_id,
                       status_ids=item.status_ids, conflict=item.conflict,
                       fingerprint=item.fingerprint, attempts=attempts, streak=row.streak + 1,
                       people_note=row.read.people_note, checked_head=item.head_sha, dispatched_at=now, send_id=sid,
                       ws_fp="", ws_error="",
                       sent_notes=item.note_ids, marks=row.marks + (row.read.last_note,), outcome="")
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
    if standing == "rules" and row is not None:
        return ("brain-push-rules 不让运行机在这个分支上提交（运行机的提交 %s）：在 MR 上说了怎么改成人的；运行机不再修这个 MR"
                % "、".join(row.rules))
    if standing == "blocked" and row is not None and row.state == PAUSED:
        return "连着派了 %d 次，中间没有人推送或评论，在 MR 上说了：等人推送或评论" % MAX_STREAK
    if standing == "blocked" and row is not None and row.outcome == "workspace":
        return "工作区一直没准备好（%s），在 MR 上说了：等它有新变化" % row.ws_error
    if standing == "blocked" and row is not None:
        tried = "派了 %d 次，会话都没收下" if row.outcome == "refused" else "试了 %d 次没修好"
        return (tried % row.attempts) + "，在 MR 上说了：等它有新变化"
    return "这些已经交给会话做过一轮：等 MR 有新变化" if standing == "done" else ""


def view_of(projects: list[dict[str, Any]], entries: list[Entry], at: float | None,
            hidden: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """GET /team/patrol (TeamPatrol in ui/src/types.ts): the projects and every open MR the last round saw; working,
    the sessions it holds: those at work and the *hidden* ones (Patrol._hidden: project, text)."""
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
    hidden = list(hidden or ())
    return {"on": any(p["on"] for p in projects), "cap": CAP,
            "working": sum(1 for i in items if i["state"] == "working") + len(hidden), "hidden": hidden,
            "last_at": at, "projects": projects, "items": items}
