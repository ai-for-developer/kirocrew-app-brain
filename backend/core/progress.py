"""What teammates see of each other's runs (the org-level design, 2.3 items 1 and 2): this machine pushes the person's
own run branch to origin when Lean records a checkpoint on it, and fetches every repo with a remote every few minutes.
交付总览 on every machine then reads the same branches (「bob 在自己的机器上做」, 「等 bob 批准」, Lean's progress).

Push. ``push_due`` (pure) walks RULES, one row per reason not to push; a branch no row stops is pushed. That is the
person's own run branch, checked out in a worktree here, in a repo done with the team (a remote and GitLab) whose
auto_push is on; not merged, closed or handed over (``brain handoff`` pushes a hand-over, or leaves it to the person
after --no-push); on origin already and ahead of it; with a checkpoint (CHECKPOINT_KINDS) committed that this machine
has not pushed or tried yet. What goes is the commit judged, ``git push origin <sha>:refs/heads/<branch>``: never
forced, hooks on, no prompt, no label, no MR, no GitLab API (the bridge takes only open MRs with the hand-over label).
A push git refuses stays in the branch's mark with git's words and is not tried again before the next checkpoint;
待办 says what happened and what to do.

The marks (``pushes.json`` in the app's data dir, {repo path: {branch: Mark}}) have one writer, ``Sync.push_tick``,
which only the service's background loop runs; the page reads them from memory.

Fetch. ``repo.fetch()`` (「从远端取最新」's own) every ``fetch_every_min`` minutes per repo with a remote. A repo has one
git over the network at a time, its fetches and its pushes (``Fetches.lock``): the loop skips a repo whose fetch still
runs (a push due then goes the next round), the button waits for it. A failure is logged once until a fetch works
again (or fails another way); 设置 shows the last one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Union

from . import flowview, importer, lean_view
from .constants import GIT_BIN
from .repo import Repo, Source, parse_branch

TICK_S = 15.0           # a checkpoint is on origin long before a teammate's next fetch (every 5 minutes by default)
PUSH_TIMEOUT_S = 120

# the Lean ledger events (aidlc_lean.py) that pass a checkpoint. A release is the RELEASE approval and the release
# stage's STAGE_COMPLETED. Not APPROVAL_REFUSED: Lean refusing a typed line leaves the request pending.
CHECKPOINT_KINDS = frozenset({"STAGE_COMPLETED", "STAGE_SKIPPED", "PHASE_SKIPPED", "APPROVAL_REQUESTED", "APPROVED",
                              "REJECTED", "VERIFY"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def checkpoint_id(e: dict[str, Any]) -> str:
    """A ledger record's own hash (Lean chains every record), else where it is."""
    h = e.get("hash")
    return h if isinstance(h, str) and HASH_RE.match(h) else "%s#%s#%s" % (e.get("_file"), e.get("seq"), e.get("ts"))


# ---- the decision ------------------------------------------------------------------------------------------------ #

@dataclass(frozen=True)
class Mark:
    """One run branch, as this machine last left it with origin."""
    checkpoint: Optional[str] = None   # the newest checkpoint origin has from here, or that the last try carried
    sha: Optional[str] = None          # the tip origin was last seen or pushed at
    at: Optional[float] = None         # when this machine last pushed it
    error: Optional[dict] = None       # the last push git refused ({kind, text, todo, raw, at, sha}); None once it went


@dataclass(frozen=True)
class Ctx:
    """One repo, this tick."""
    me: Optional[str]
    prefix: str
    default_branch: str
    team_repo: bool         # a remote and GitLab: not done only on this machine
    auto_push: bool
    runner: bool            # this machine is the team runner


@dataclass(frozen=True)
class Branch:
    """A requirement's run branch, from repo.scan's facts. The two reads that cost git calls are asked for only when
    a rule gets to them (a teammate's branch costs none)."""
    name: str
    owner: Optional[str]
    worktree: Optional[str]
    local_sha: Optional[str]
    origin_sha: Optional[str]
    merged: bool
    closed: bool
    handed_over: bool
    checkpoint: Callable[[], Optional[str]]    # the newest checkpoint committed at local_sha
    behind: Callable[[], bool]                 # origin's tip has local_sha already


@dataclass(frozen=True)
class Push:
    sha: str
    checkpoint: str


class Skip(Enum):
    RUNNER = "this machine is the team runner: brain-run pushes its own commits"
    LOCAL_ONLY = "the repo is done only on this machine (no remote, or no GitLab)"
    OFF = "auto_push is off for this repo"
    NOT_RUN_BRANCH = "not a run branch (the default branch and claim/* are never pushed)"
    NOT_MINE = "not the person's own run, or this machine does not know the person's name here"
    MERGED = "merged"
    CLOSED = "closed with brain intent close"
    NOT_HERE = "not checked out in a worktree on this machine"
    HANDED_OVER = "run.json records a hand-over: brain handoff pushes it"
    NOT_ON_ORIGIN = "origin has no such branch (brain start --no-push, or deleted there): only a branch origin has moves"
    UP_TO_DATE = "origin has this tip"
    NO_CHECKPOINT = "no checkpoint committed on the branch"
    NOTHING_NEW = "no checkpoint committed since the last push or try"
    BEHIND = "origin is ahead of this tip"


def is_run_branch(name: str, ctx: Ctx) -> bool:
    return parse_branch(name, ctx.prefix) is not None and name != ctx.default_branch and not name.startswith("claim/")


Rule = Callable[[Branch, Optional[Mark], Ctx], bool]
RULES: tuple[tuple[Skip, Rule], ...] = (    # in order: the first that holds is the answer
    (Skip.RUNNER, lambda b, m, c: c.runner),
    (Skip.LOCAL_ONLY, lambda b, m, c: not c.team_repo),
    (Skip.OFF, lambda b, m, c: not c.auto_push),
    (Skip.NOT_RUN_BRANCH, lambda b, m, c: not is_run_branch(b.name, c)),
    (Skip.NOT_MINE, lambda b, m, c: not c.me or b.owner != c.me),
    (Skip.MERGED, lambda b, m, c: b.merged),
    (Skip.CLOSED, lambda b, m, c: b.closed),
    (Skip.NOT_HERE, lambda b, m, c: not b.worktree or not b.local_sha),
    (Skip.HANDED_OVER, lambda b, m, c: b.handed_over),
    (Skip.NOT_ON_ORIGIN, lambda b, m, c: not b.origin_sha),
    (Skip.UP_TO_DATE, lambda b, m, c: b.local_sha == b.origin_sha),
    (Skip.NO_CHECKPOINT, lambda b, m, c: b.checkpoint() is None),
    (Skip.NOTHING_NEW, lambda b, m, c: m is not None and m.checkpoint == b.checkpoint()),
    (Skip.BEHIND, lambda b, m, c: b.behind()),
)


def push_due(b: Branch, mark: Optional[Mark], ctx: Ctx) -> Union[Push, Skip]:
    for reason, holds in RULES:
        if holds(b, mark, ctx):
            return reason
    return Push(str(b.local_sha), str(b.checkpoint()))


# ---- the push ---------------------------------------------------------------------------------------------------- #

PUSH = "git -C {wt} push origin {branch}"
PUSH_WHY = {
    "rejected": ("推进度没成功：远端的这个分支上有这台机器没有的提交（你在别处推过，或者有人推到了你的分支上）",
                 "在这个需求的工作目录里把远端的提交取下来合进来（细节里有命令），下一个关卡 app 会接着推；app 不会强推。",
                 "git -C {wt} pull --no-rebase origin {branch}"),
    "auth": ("推进度没成功：远端不让推（没有权限，或者登录过期了）",
             "在终端里对这个分支推一次（细节里有命令），按提示登录；以后过关卡 app 会接着推。", PUSH),
    "unreachable": ("推进度没成功：连不上远端",
                    "检查网络或 VPN。下一个关卡 app 会再推；等不及就在终端里推一次（细节里有命令）。", PUSH),
    "hook": ("推进度没成功：git 的检查不让这次推送过（远端的或你本机的，细节里是它的原话）",
             "照细节里说的改好，下一个关卡 app 会再推。", PUSH),
    "notfound": ("推进度没成功：远端的地址不对，或者远端仓库没有了",
                 "在终端里看看这个仓库的远端地址（细节里有命令）。", "git -C {wt} remote -v"),
    "timeout": ("推进度没成功：等了 2 分钟远端也没回",
                "网络慢或远端没响应：下一个关卡 app 会再推；等不及就在终端里推一次（细节里有命令）。", PUSH),
    "other": ("推进度没成功：原因在细节里", "在终端里对这个分支推一次（细节里有命令），看 git 怎么说。", PUSH),
}


def push_branch(repo: Repo, branch: str, sha: str, worktree: str, now: float) -> Optional[dict[str, Any]]:
    """``git push origin <sha>:refs/heads/<branch>`` (blocking): None when origin took it, else what the mark keeps."""
    p = importer.run_quiet([GIT_BIN, "-C", repo.path, "push", "origin", "%s:refs/heads/%s" % (sha, branch)],
                           repo.path, PUSH_TIMEOUT_S, importer.quiet_env(repo.path))
    repo.invalidate()
    return None if p.returncode == 0 else refusal(p.returncode, importer.tail(p, 2000), worktree, branch, sha, now)


def refusal(code: int, said: str, worktree: str, branch: str, sha: str, now: float) -> dict[str, Any]:
    """A push git refused (its exit *code*, its words *said*), as the mark keeps it and 待办 shows it."""
    low = said.lower()
    kind = "timeout" if code == 124 else next(
        (k for k, words in importer.FAILS if k in PUSH_WHY and any(w in low for w in words)), "other")
    text, todo, cmd = PUSH_WHY[kind]
    raw = said + "\n\n做法：" + cmd.format(wt=shlex.quote(worktree), branch=shlex.quote(branch))
    return {"kind": kind, "text": text, "todo": todo, "raw": raw.strip(), "at": now, "sha": sha}


# ---- the marks: pushes.json -------------------------------------------------------------------------------------- #

def _str(x: Any) -> Optional[str]:
    return x if isinstance(x, str) else None


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _mark(raw: Any) -> Optional[Mark]:
    """A mark as pushes.json holds it, in the shape push_branch makes its error (None: not a mark)."""
    if not isinstance(raw, dict):
        return None
    e = raw.get("error")
    err = {"kind": _str(e.get("kind")) or "other", "text": e["text"], "todo": _str(e.get("todo")) or "",
           "raw": _str(e.get("raw")) or "", "at": _num(e.get("at")), "sha": _str(e.get("sha"))} \
        if isinstance(e, dict) and isinstance(e.get("text"), str) else None
    return Mark(checkpoint=_str(raw.get("checkpoint")), sha=_str(raw.get("sha")), at=_num(raw.get("at")), error=err)


class Marks:
    """{repo path: {branch: Mark}} in pushes.json; changed only by Sync.push_tick, read by the page from memory."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._all: dict[str, dict[str, Mark]] = {}
        self._dirty = False
        try:
            got = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        for top, branches in (got.items() if isinstance(got, dict) else []):
            if isinstance(branches, dict):
                kept = {b: m for b, m in ((b, _mark(x)) for b, x in branches.items()) if m is not None}
                if kept:
                    self._all[str(top)] = kept

    def get(self, top: str, branch: Optional[str]) -> Optional[Mark]:
        with self._lock:
            return self._all.get(top, {}).get(branch or "")

    def of(self, top: str) -> dict[str, Mark]:
        with self._lock:
            return dict(self._all.get(top, {}))

    def set(self, top: str, marks: dict[str, Mark]) -> None:
        with self._lock:
            if self._all.get(top, {}) != marks:
                self._dirty = True
                if marks:
                    self._all[top] = dict(marks)
                else:
                    self._all.pop(top, None)

    def retain(self, tops: set[str]) -> None:
        with self._lock:
            for top in [t for t in self._all if t not in tops]:
                del self._all[top]
                self._dirty = True

    def save(self) -> None:
        """Written whole to a temp file, then renamed into place (only when something changed)."""
        with self._lock:
            if not self._dirty:
                return
            data = {top: {b: asdict(m) for b, m in sorted(ms.items())} for top, ms in sorted(self._all.items())}
            self._dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".pushes.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


# ---- the fetches ------------------------------------------------------------------------------------------------- #

class Fetches:
    """repo.fetch() of each repo, one at a time per repo: in the background every few minutes, and on the button."""

    def __init__(self, log: Any, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time) -> None:
        self.log, self.clock, self.now = log, clock, now
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._started: dict[str, float] = {}
        self._last: dict[str, dict[str, Any]] = {}

    def take_turn(self, repo: Any, every_s: float) -> bool:
        """The repo is due for its fetch now (it has a remote; every *every_s*, 0: never); its next turn counts from now."""
        if every_s <= 0 or not repo.has_origin():
            return False
        with self._guard:
            t = self._started.get(repo.path)
            if t is not None and self.clock() - t < every_s:
                return False
            self._started[repo.path] = self.clock()
        return True

    def lock(self, top: str) -> threading.Lock:
        """The repo's one git-over-the-network at a time: its fetches, and its pushes (Sync.push_tick)."""
        with self._guard:
            return self._locks.setdefault(top, threading.Lock())

    def fetch(self, repo: Any, wait: bool = True) -> Optional[dict[str, Any]]:
        """repo.fetch()'s answer; None, without waiting, when this repo's fetch still runs and *wait* is False."""
        lock = self.lock(repo.path)
        if not lock.acquire(blocking=wait):
            return None
        try:
            with self._guard:
                self._started[repo.path] = self.clock()
            got = dict(repo.fetch())
        finally:
            lock.release()
        got["raw"] = importer.hide_credentials(str(got.get("raw") or ""))   # a remote URL may carry user:token@
        last = {"at": self.now(), "ok": bool(got.get("ok")), "text": str(got.get("text") or ""),
                "raw": str(got.get("raw") or "")}
        with self._guard:
            prev = self._last.get(repo.path)
            self._last[repo.path] = last
        if not last["ok"] and (prev is None or prev["ok"] or prev["text"] != last["text"]):
            self.log.warning("brain: fetching %s failed: %s", repo.path, last["text"])
        return got

    def last(self, top: str) -> Optional[dict[str, Any]]:
        with self._guard:
            got = self._last.get(top)
        return dict(got) if got else None


# ---- the loop's work --------------------------------------------------------------------------------------------- #

NO_NAME = ("app 还不知道你在这个仓库是谁，分不出哪些分支是你的，所以还没推过：在「仓库」里这个仓库的「你是谁」填上你的 "
           "GitLab 用户名，点「保存」；也可以在终端里写一次（把最后一个词换成你的 GitLab 用户名）")
ON_RUNNER = "这台机器是团队运行机：运行机上的提交由 brain-run 自己推，这里不推"


class Sync:
    """This machine's pushes and fetches (one per service)."""

    def __init__(self, data_dir: Path, log: Any, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time) -> None:
        self.log = log
        self.marks = Marks(Path(data_dir) / "pushes.json")
        self.fetches = Fetches(log, clock=clock, now=now)
        self._ticking = threading.Lock()      # the marks' one writer: one push tick at a time
        self._checkpoints: dict[tuple, Optional[str]] = {}   # a commit never changes: kept by (repo, sha, run's own)
        self._behind: dict[tuple, bool] = {}

    def _checkpoint(self, repo: Repo, sha: Optional[str], run: dict[str, Any], flow: Any) -> Optional[str]:
        """The newest checkpoint of the run's own ledger records at commit *sha* (flowview.belongs: a Lean run's by its
        intent, a fast run's by its branch, whose VERIFY is its checkpoint)."""
        own = run.get("intent") if flow.intent else run.get("branch")
        if not sha or not isinstance(own, str):
            return None
        k = (repo.path, sha, flow.value, own)
        if k not in self._checkpoints:
            if len(self._checkpoints) > 2048:
                self._checkpoints.clear()
            src = Source(repo.git, ref=sha)
            evs = [e for e in lean_view.events_where(src.read, src.ls, flowview.belongs(flow, run))
                   if e.get("event") in CHECKPOINT_KINDS]
            self._checkpoints[k] = checkpoint_id(evs[-1]) if evs else None
        return self._checkpoints[k]

    def _is_behind(self, repo: Repo, local: Optional[str], origin: Optional[str]) -> bool:
        k = (repo.path, local, origin)
        if k not in self._behind:
            if len(self._behind) > 2048:
                self._behind.clear()
            self._behind[k] = bool(local and origin) and repo.git.ok("merge-base", "--is-ancestor", str(local),
                                                                     str(origin))
        return self._behind[k]

    def branch(self, repo: Repo, f: dict[str, Any]) -> Optional[Branch]:
        name = f.get("branch")
        if not name:
            return None
        run = f.get("run") or {}
        local, origin = f.get("local_sha"), f.get("origin_sha")
        return Branch(name=name, owner=f.get("owner"), worktree=f.get("worktree"), local_sha=local, origin_sha=origin,
                      merged=bool(f.get("merged")), closed=bool((f.get("claim") or {}).get("closed")),
                      handed_over=bool(run.get("handoff")),
                      checkpoint=lambda: self._checkpoint(repo, local, run, f["flow"]),
                      behind=lambda: self._is_behind(repo, local, origin))

    def push_tick(self, repos: list[tuple[Repo, Ctx]], now: float, listed: set[str]) -> None:
        """Every loaded repo's run branches against their marks: push what is due, keep what git said (blocking). The
        marks of a repo no longer *listed* (settings.repos) go."""
        with self._ticking:
            for repo, ctx in repos:
                try:
                    facts = repo.scan()
                except Exception:  # noqa: BLE001 - the page says what is wrong with a repo it cannot read
                    continue
                old = self.marks.of(repo.path)
                new: dict[str, Mark] = {}
                for f in facts:
                    b = self.branch(repo, f)
                    m = self._step(repo, b, old.get(b.name), ctx, now) if b else None
                    if b and m is not None:
                        new[b.name] = m
                self.marks.set(repo.path, new)
            self.marks.retain(listed)
            self.marks.save()

    def _step(self, repo: Repo, b: Branch, old: Optional[Mark], ctx: Ctx, now: float) -> Optional[Mark]:
        got = push_due(b, old, ctx)
        if isinstance(got, Push):
            lock = self.fetches.lock(repo.path)
            if not lock.acquire(blocking=False):
                return old      # its fetch runs (both move refs/remotes/origin/*): still due next round
            try:
                err = push_branch(repo, b.name, got.sha, str(b.worktree), now)
            finally:
                lock.release()
            if err is None:
                self.log.info("brain: pushed %s at %s to origin", b.name, got.sha[:12])
                return Mark(got.checkpoint, got.sha, now, None)
            self.log.warning("brain: origin refused %s at %s (%s)", b.name, got.sha[:12], err["kind"])
            return Mark(got.checkpoint, old.sha if old else None, old.at if old else None, err)
        if got is Skip.UP_TO_DATE and (old is None or old.sha != b.local_sha or old.error):
            # origin has this tip (pushed from here or by hand): what it carries is there, an old refusal is moot
            return Mark(b.checkpoint(), b.local_sha, old.at if old else None, None)
        return old

    def pushed_at(self, top: str, branch: Optional[str]) -> Optional[float]:
        m = self.marks.get(top, branch)
        return m.at if m else None

    def push_error(self, top: str, f: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The refused push 待办 shows for this requirement: while origin still lacks the branch's tip."""
        m = self.marks.get(top, f.get("branch"))
        if not m or not m.error or not f.get("local_sha") or f.get("local_sha") == f.get("origin_sha"):
            return None
        return m.error

    def repo_view(self, repo: Repo, ctx: Ctx) -> dict[str, Any]:
        return repo_view(repo.path, ctx, self.fetches.last(repo.path))


def repo_view(top: str, ctx: Ctx, fetch: Optional[dict[str, Any]]) -> dict[str, Any]:
    """/status's RepoSync: the push setting (None: done only on this machine), why it cannot push, the last fetch."""
    why, cmd = "", ""
    if ctx.team_repo and ctx.auto_push and ctx.runner:
        why = ON_RUNNER
    elif ctx.team_repo and ctx.auto_push and not ctx.me:
        why, cmd = NO_NAME, "git -C %s config brain.user 你的GitLab用户名" % shlex.quote(top)
    return {"auto_push": ctx.auto_push if ctx.team_repo else None, "push_why": why, "push_cmd": cmd, "fetch": fetch}
