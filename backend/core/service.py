"""The app's single service object per enable: settings, brain-kit, the repos, the sessions, team mode, and every
view the routes answer. Built by ``register_routes`` (no I/O), started by ``on_startup``, stopped by ``on_shutdown``.

Repo reads, brain-kit calls and git run in worker threads (``asyncio.to_thread``); the session manager is only touched
on the event loop (a snapshot of it goes into the thread).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import (acts, bk, brainview, crewcheck, deliveries, deploys_view, detail, evidence, family, fastboard, fastrow,
               flowview, identity, importer, inplace, installer, intake, kitmr, landing, lean_view, mrdesc_view, mrinfo,
               picker, progress, roster, runner_view)
from . import outputs as outputs_mod
from . import problems, stages
from . import board as board_mod
from . import settings as settings_mod
from . import todo as todo_mod
from . import turns as turns_mod
from .constants import APP_NAME, APP_ROOT, KEY_RE, LOCAL_FOLDER, PYTHON_BIN, REPO_ID_RE, TICK_S, VERSION
from .gateway import Gateway, GatewayError
from .gateway import said as gateway_said
from .httpkit import AppError
from .repo import Git, Repo, files_stamp, git_stamp
from .scrub import basic_scrub, scrub_tree
from .repoids import RepoIds, from_records
from .sessions import SessionManager, requirement_credits
from . import store as store_mod
from .store import Store
from .team import issues as issues_mod
from .team import patrol as patrol_mod
from .team import setup as setup_mod
from .team import signerproc
from .team import staging as staging_mod
from .team.crewadapter import KiroCrewAdapter

_REGISTRY: dict[str, "Service"] = {}
EVAL_TTL_S = 120.0
REVIEW_TITLE = " · 审查"     # a review session's title ends so (开审查会话): it is never the live dev session
REVIEW_OPEN = "这个需求的审查会话还开着，审查还没写好：打开它接着审，没有另开一个"
MR_EVERY_S = 30.0
CREW_EVERY_S = 300.0
FOLD_TTL_S = 10.0     # the 产出 drawer reads the board's last fold; the board polls every 2 s while it is open
FAILED_TRACE_S = 15.0  # a trace brain-kit could not make (git timed out under load) is made again this soon
RUNNER_ELSEWHERE = "交接以后，运行机上的会话这台机器读不到，没算进来"
RUNNER_STARTED = "它在运行机上开工，运行机上的会话这台机器读不到，没算进来"
LOCAL_ELSEWHERE = "交接以前，开发者机器上的会话这台机器读不到，没算进来"
RUNNER_TOOK = ("running", "parked", "acceptance", "merged", "closed")   # a hand-over's states once the runner has it


def scope_of(rid: str, key: str) -> str:
    return "%s-%s" % (rid, key)


def trace_failed(tl: dict[str, Any]) -> bool:
    """brain-kit could not make the trace, or a 小需求's inside a 大需求's: it catches that one and leaves its numbers
    None (only started 小需求 are children, and a made trace always has numbers)."""
    kids = (((tl.get("trace") or {}).get("family") or {}).get("children")) or []
    return bool(tl["error"]) or any(c.get("numbers") is None for c in kids)


RECORD_SCOPE_RE = re.compile(r"^(r[1-9][0-9]{0,2})-([A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6})$")   # a local scope_of


def me_line(repos: list[dict[str, Any]], token: bool, env: str) -> dict[str, Any]:
    first = next((x["me"] for x in repos if x["me"] and x["me"]["name"]), None)
    said = first["text"] if first else "%s（%s）" % (env, identity.FROM["env"]) if env else ""
    return {"user": first["name"] if first else env or None, "token": token,
            "how": "GitLab 令牌（%s）" % ("有" if token else "没有") + ("；" + said if said else "")}


def last_record(info: Any, tail: dict[str, Any] | None) -> float | None:
    """When a session last did something, by its records' own times (the app's message, the agent's last reply or
    call), until the whole transcript is read: never the moment this app noticed a change."""
    ats = [info.sent_at, (tail or {}).get("said_at"), ((tail or {}).get("call") or {}).get("at")]
    ats = [a for a in ats if isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0]
    return max(ats) if ats else None


def session_row(mgr: SessionManager, info: Any) -> dict[str, Any]:
    """A session this app opened, as the pages list it (and service._live picks from)."""
    st = mgr.slot_stats(info.slot) or {}
    tail = mgr.slot_tail(info.slot)
    return {
        "slot": info.slot, "title": info.title, "agent": info.agent, "kind": info.kind or "local",
        "credits": mgr.slot_credits(info.slot),   # the very number credits_by_key adds up
        "turns": int(st.get("turns") or info.turns or 0), "busy": bool(st.get("busy")),
        "awaiting": bool(st.get("awaiting")), "last_activity": st.get("last_activity") or last_record(info, tail),
        "opened_at": st.get("opened_at") or info.started, "project": info.cwd or None,
        "url": "/chat?sid=%s" % info.slot, "error": info.error, "missing": bool(st.get("missing")),
        "id": info.id, "tail": tail, "whole": st.get("whole"),
        "subagents": int(st.get("subagents") or 0), "unreported": int(st.get("unreported") or 0),
        "opened": info.started is not None, "review": str(info.title or "").endswith(REVIEW_TITLE)}


def live_said(live: dict[str, Any]) -> str:
    """What 开会话 says when it hands back the requirement's session instead of opening one ('' when it simply is)."""
    if live["error"]:
        return "这个需求已经有一个会话，只是它的第一条消息没发进去：打开它，在里面说一句接着做；要换一个新的，点「重开一个会话」"
    return "" if live["opened"] else "这个需求的会话刚开，第一条消息还在发：打开它看一眼"


class Service:
    def __init__(self, ctx: Any, *, sessions: Any = None, transport: Any = None, now: Callable[[], float] = time.time,
                 signer_kick: Callable[[], Any] | None = None, sign_timeout: float | None = None) -> None:
        self.ctx = ctx
        self.name = getattr(ctx, "name", APP_NAME) or APP_NAME
        self.log = getattr(ctx, "logger", None) or logging.getLogger(APP_NAME)
        self.data_dir = Path(getattr(ctx, "data_dir", ".") or ".")
        self.now = now
        self.transport = transport  # GitLab / Jira transport (tests); None: brain-kit's own
        # tests: run the signer while brain-kit's SpoolSigner waits, and wait less than bridge.json says
        self.signer_kick, self.sign_timeout = signer_kick, sign_timeout
        self.started = False
        self.settings = settings_mod.load(self.data_dir / "settings.json")
        self.repo_ids = RepoIds(self.data_dir / "repo_ids.json")     # each listed repo's id, kept (UI-19)
        self.sessions = sessions or SessionManager(Gateway(self.name, APP_ROOT / ".app_secret",
                                                           token_source=getattr(ctx, "app_token", None)), app=self.name,
                                                   state_file=self.data_dir / "sessions.json", log=self.log,
                                                   scrub=self.scrub)
        self.store = Store(self.data_dir / "events")
        self.mr = mrinfo.MrCache()
        self.runners = runner_view.Runners()   # 运行机 on a personal install: the team runner as GitLab shows it
        self.deploys = deploys_view.Clients(transport)   # one GitLab client per GitLab for the deploys after a merge
        self.repos: dict[str, Repo] = {}
        self.repo_errors: dict[str, tuple[str, str]] = {}
        # listed repos whose brain-kit is on its way (the install branch, merged but not pulled): (path, state)
        self.repo_pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self.imports = importer.Records(self.data_dir / "imports.json")   # what 导入仓库 installed where
        self.upgrades = importer.Records(self.data_dir / "upgrades.json")  # the kit fix's branches (apart from installs)
        self.jobs = importer.Jobs(now)                                      # clone / install in the background
        self.crew: dict[str, Any] = {"rows": [], "fix": {"agent": False, "engine": False}, "checked_at": None}
        self.team: Any = None
        self._team_loop: asyncio.Task | None = None     # the rounds of self.team, started and stopped with it
        self._team_lock = asyncio.Lock()                # team mode started again (or stopped) one at a time
        self._setup_task: asyncio.Task | None = None    # 一键建运行机's run (team/setup.py), in a worker thread
        self._tasks: list[asyncio.Task] = []
        self._mr_wanted: dict[tuple, tuple[str, str]] = {}   # (base, project, branch) -> (scope, kit bridge_user)
        self._mr_seen: set[tuple] = set()      # (project, branch, hand-over commit) whose MR was read at first sight
        self._fast_git: dict[tuple, tuple[int, tuple[str, ...]]] = {}   # fastrow.fast_facts' git half
        self._fast_views: dict[tuple, tuple[bool, str, bool | None]] = {}   # _fast_view's git half
        self._eval: dict[str, tuple] = {}
        self._eval_running: set[str] = set()
        self._trace: dict[str, tuple] = {}      # scope -> (what it was made from, detail.timeline, when)
        self.desc = mrdesc_view.DescCache()     # each requirement's 变更说明, by the commits it is made from
        self._subj: dict[tuple, tuple] = {}     # the board's commit subjects and changed files, by the commits asked for
        self._units: dict[tuple, list] = {}     # the units unit-of-work.md plans, by the commit it is read at
        self._reflects: dict[tuple, tuple] = {}  # (repo, key, default branch) -> (git_stamp, _reflect_refs)
        self._folds: dict[tuple, tuple] = {}    # (repo, key) -> (when, outputs.Fold): the board's last fold, for 产出
        self._proofs: dict[tuple, Any] = {}     # 证据: bind()'s JSON by what it was read from (_evidence), None: unreadable
        self.who = identity.Who(transport, log=self.log)
        self.families = family.Families()      # brain-kit's listing per repo (大需求 and their 小需求), made in the background
        self.sync = progress.Sync(self.data_dir, self.log)     # pushes at Lean's checkpoints, fetches every few minutes
        self._opening: dict[str, asyncio.Lock] = {}     # 开会话 and 重开, one at a time per requirement
        self._handing: dict[str, asyncio.Future] = {}   # 交接给运行机, one at a time per requirement
        self._acting: dict[str, asyncio.Future] = {}    # 开 MR / 转完整流程, the same
        self._big_file = self.data_dir / "big.json"
        self._big = self._load_big()           # scopes opened here as 「大需求：先拆成小需求」
        self.folders_stuck: list[dict[str, Any]] = []   # 设置 rows: the folders KiroCrew would not move (sessions.regroup)

    # ---- lifecycle ------------------------------------------------------------------------------------ #

    def scrub(self, text: str) -> str:
        """KiroCrew's own outbound redaction (``ctx.scrub``) when there is one, else a few obvious patterns."""
        if not text:
            return ""
        sdk = getattr(self.ctx, "scrub", None)
        if sdk is not None:
            try:
                return sdk.outbound(text).text
            except Exception:  # noqa: BLE001
                pass
        return basic_scrub(text)

    async def start(self) -> None:
        if self.started:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self.sessions.load)
        await asyncio.to_thread(self.reload_sync)
        await self._start_team()
        self.started = True
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._forever()), loop.create_task(self._slow()),
                       loop.create_task(self._sync_forever()), loop.create_task(self._regroup_soon())]
        self.log.info("brain: started, brain-kit %s, %d repos, team %s", bk.info().get("version"), len(self.repos),
                      "on" if self.team is not None else "off")

    async def stop(self) -> None:
        self.started = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        async with self._team_lock:
            await self._stop_team()
        if hasattr(self.sessions.gw, "close"):
            await self.sessions.gw.close()
        bk.unload()

    def reload_sync(self) -> None:
        """brain-kit from settings.brainkit, then the repos (their kit.json through brain-kit). A repo whose checkout
        has no kit.json yet but whose brain-kit is on its way (the install branch 导入仓库 made, or merged and not
        pulled yet) is pending, with what to do; one without any is an error that says where to install it."""
        bk.load(self.settings["brainkit"])
        self.repos, self.repo_errors, self.repo_pending = {}, {}, {}
        records = self.imports.all()
        token = None
        for path, rid in self.repo_ids.of(self.settings["repos"]).items():
            try:
                if not os.path.isdir(path):
                    raise RuntimeError("找不到这个目录")
                if not os.path.isfile(os.path.join(path, importer.KIT)):
                    token = self._token_present() if token is None else token
                    pend = importer.pending_state(path, records.get(path), token=token)
                    if pend:
                        self.repo_pending[rid] = (path, pend)
                        continue
                    raise RuntimeError("还没装 brain-kit：在「导入仓库」里检查它，装上就能用")
                if not bk.ok():
                    raise RuntimeError("brain-kit 没装载")
                try:
                    cfg = bk.m("config").load(path)
                except Exception as exc:  # noqa: BLE001 - brain-kit's own words: in the 检查 of 导入仓库
                    raise RuntimeError("brain-kit 读不懂这个仓库的设置：在「导入仓库」里检查它，那里会说哪里不对") from exc
                self.repos[rid] = Repo(rid, path, cfg)
            except Exception as exc:  # noqa: BLE001
                self.repo_errors[rid] = (path, str(exc).split("\n", 1)[0][:300])
        for r in self.repos.values():   # start asking now: the first page then usually finds GitLab's answer
            self.who.want(self._gitlab_of(r)[0])
        self._eval.clear()
        self._trace.clear()
        self.crew = self.crew_sync()

    def team_paths(self) -> list[str]:
        """The listed repos handed to the team runner (see runner_repo): their runner-only KiroCrew checks FAIL; for
        the others they are a reminder. A few local git reads per repo (repo.scan, cached for a few seconds)."""
        out = []
        for r in self.repos.values():
            try:
                facts = [] if r.cfg.get("bridge_user") else r.scan()
            except Exception:  # noqa: BLE001 - an unreadable repo is said by the checks themselves
                facts = []
            if runner_repo(r.cfg, facts):
                out.append(r.path)
        return out

    def crew_sync(self) -> dict[str, Any]:
        """The KiroCrew checks of the listed repos (blocking: call it in a thread off the event loop)."""
        return crewcheck.checks([r.path for r in self.repos.values()], team_paths=self.team_paths())

    async def _start_team(self) -> None:
        """Team mode from the settings: brain-kit's bridge and runnerd in a TeamRuntime with its loop of rounds, and,
        for the same-user trial 一键建运行机 made, the signer beside its bridge.json as a child process
        (team/signerproc.py). Team mode that ran is stopped first, its loop with it, and two saves at once start it one
        after the other: one loop runs at most."""
        async with self._team_lock:
            team = self.settings["team"]
            await self._stop_team()
            if not team.get("enabled") or not bk.ok():
                return
            from .team.bridgehost import TeamRuntime
            loop = asyncio.get_running_loop()
            rt = TeamRuntime(team, loop, self.sessions, self.log, transport=self.transport, kick=self.signer_kick,
                             sign_timeout=self.sign_timeout, scrub=self.scrub)
            await loop.run_in_executor(rt.executor, rt.start)
            signer = signerproc.config_for(team) if rt.host is not None else None
            if signer is not None:
                try:
                    await asyncio.to_thread(signerproc.ensure, signer, APP_ROOT, PYTHON_BIN)
                except signerproc.SignerFailed as exc:
                    rt.error = exc.team_error()
            self.team = rt
            self._team_loop = loop.create_task(self._team_forever(rt))

    async def _stop_team(self) -> None:
        """Team mode stops: its loop, its TeamRuntime after the round in progress, and the signer started with it."""
        task, self._team_loop = self._team_loop, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rt, self.team = self.team, None
        if rt is None:
            return
        await asyncio.to_thread(rt.stop)
        signer = signerproc.config_for(rt.team)
        if signer is not None:
            await asyncio.to_thread(signerproc.stop, signer)

    async def _in_team(self, fn: Callable[[], Any]) -> Any:
        """Run on the team runtime's own worker: one round at a time, off the event loop and the default pool (a round
        waits on GitLab, KiroCrew and the signer). The ledger itself may be read from any thread."""
        return await asyncio.get_running_loop().run_in_executor(self.team.executor, fn)

    async def _forever(self) -> None:
        while True:
            try:
                await self.sessions.poll()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.log.exception("brain: polling the sessions failed")
            await asyncio.sleep(TICK_S)

    async def _regroup_soon(self) -> None:
        """regroup_folders once KiroCrew answers (it may not yet while it is starting this app)."""
        while not await self.regroup_folders():
            await asyncio.sleep(MR_EVERY_S)

    async def regroup_folders(self) -> bool:
        """The requirement folders made before repo folders, moved into 「Brain 需求」 › repo (sessions.regroup); the
        ones that stay are said in 设置 (status: a KiroCrew row). False: KiroCrew could not be read, try again."""
        try:
            self.folders_stuck = await self.sessions.regroup(self._repo_of)
        except GatewayError as exc:
            self.log.info("brain: reading KiroCrew's folders: %s", exc)
            return False
        except Exception:  # noqa: BLE001 - the sidebar's layout never stops the app
            self.log.exception("brain: moving the requirement folders into their repo folders failed")
        await self.sessions.save()
        return True

    def _repo_of(self, parent: str) -> str:
        """The repo folder of a requirement this app opened a session for (*parent*: <app>:<repo id>-<KEY>): the listed
        repo's name as the app shows it, else (a repo no longer listed) the folder under work_root its worktree was
        made in (<work_root>/<repo>/<slug>); "" when neither."""
        app, _, scope = parent.partition(":")
        m = RECORD_SCOPE_RE.match(scope)
        if app != self.name or not m:
            return ""
        if m.group(1) in self.repos:
            return self.repos[m.group(1)].name
        top = os.path.realpath(os.path.expanduser(self.settings["work_root"]))
        for i in self.sessions.roots(key=scope):
            repo_dir = os.path.dirname(os.path.realpath(i.cwd)) if i.cwd else ""
            if repo_dir and os.path.dirname(repo_dir) == top:
                return os.path.basename(repo_dir)
        return ""

    async def refresh_mrs(self) -> None:
        """The MRs of handed-over runs and of fast runs until their MR is merged (fastrow.polls_mr; person mode), each
        at most every mrinfo.TTL_S. Only the notes of the account the repo's kit.json names as bridge_user are the
        bridge's (when it names one)."""
        for (base, project, branch), (_, bridge_user) in list(self._mr_wanted.items()):
            if self.mr.due(project, branch):
                await asyncio.to_thread(self.mr.refresh, base, project, branch, transport=self.transport,
                                        bridge_user=bridge_user or None)

    async def _slow(self) -> None:
        """MRs of handed-over runs (person mode), the listed repos whose brain-kit is on its way, and the KiroCrew
        checks now and then."""
        last_crew = time.monotonic()
        while True:
            try:
                await asyncio.sleep(MR_EVERY_S)
                await self.refresh_mrs()
                cleared = await asyncio.to_thread(self._sweep)
                if (set(cleared) & set(self.settings["repos"])) or (
                        self.repo_pending and await asyncio.to_thread(self._pending_changed)):
                    await asyncio.to_thread(self.reload_sync)
                if time.monotonic() - last_crew > CREW_EVERY_S:
                    last_crew = time.monotonic()
                    await asyncio.to_thread(crewcheck.refresh_engine)
                    self.crew = await asyncio.to_thread(self.crew_sync)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.log.exception("brain: background refresh failed")

    async def _sync_forever(self) -> None:
        """Teammates see each other's runs: the repos due for a fetch, then the checkpoints to push (progress.py)."""
        while True:
            try:
                await asyncio.sleep(progress.TICK_S)
                await asyncio.to_thread(self.sync_tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.log.exception("brain: pushing progress or fetching failed")

    def sync_tick(self) -> None:
        """One round of the loop (blocking): every run branch whose new checkpoint is due pushed
        (progress.Sync.push_tick), then each repo with a remote fetched when due, in its own thread."""
        repos = list(self.repos.items())
        # pruned by the listed paths, not by self.repos: a settings save refills that dict while a tick may read it
        self.sync.push_tick([(repo, self._push_ctx(repo)) for _, repo in repos], self.now(), set(self.settings["repos"]))
        every = self.settings["fetch_every_min"] * 60
        for rid, repo in repos:
            if self.sync.fetches.take_turn(repo, every):
                threading.Thread(target=self._fetch, args=(rid, repo, False), daemon=True, name="brain-fetch").start()

    def _fetch(self, rid: str, repo: Repo, wait: bool) -> dict[str, Any] | None:
        """repo.fetch() through progress.Fetches (one at a time per repo); None when one runs and *wait* is False."""
        got = self.sync.fetches.fetch(repo, wait)
        if got and got.get("ok") and bk.has("split"):
            self.families.want(rid, repo.path, now=True)    # the 大需求 too: who claimed which small one
        return got

    def _push_ctx(self, repo: Repo) -> progress.Ctx:
        return progress.Ctx(me=self.me_in(repo), prefix=str(repo.cfg["branch_prefix"]),
                            default_branch=str(repo.cfg["default_branch"]), team_repo=not self._local_only(repo),
                            auto_push=self.settings["auto_push"].get(repo.path, True),
                            runner=bool(self.settings["team"].get("enabled")))

    def _pending_changed(self) -> bool:
        """A pending repo's checkout has kit.json now (pulled after the merge), or its state moved (merged, pushed, an
        MR, a commit): the list is read again. A few local git calls per pending repo; no network."""
        records = self.imports.all()
        token = self._token_present()
        for path, pend in list(self.repo_pending.values()):
            if os.path.isfile(os.path.join(path, importer.KIT)):
                return True
            now = importer.pending_state(path, records.get(path), token=token)
            keys = ("state", "branch", "commit", "pushed", "mr_url", "can_open_mr", "word", "next", "merge", "pull")
            if now is None or any(now.get(k) != pend.get(k) for k in keys):
                return True
        return False

    def _sweep(self) -> list[str]:
        """The install and upgrade records nothing waits for any more, cleared (landing.sweep): the repo tops."""
        return landing.sweep(self.imports, "install") + landing.sweep(self.upgrades, "upgrade")

    async def _team_forever(self, rt: Any) -> None:
        """The rounds of *rt*, one interval apart, until _stop_team cancels them."""
        while True:
            try:
                if rt.host is not None:
                    await self._in_team(lambda: self._team_round(rt))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.log.exception("brain: the bridge loop failed")
            await asyncio.sleep(rt.interval())

    def _team_round(self, rt: Any) -> dict[str, int]:
        """One round of team mode on its worker (blocking): the signer 一键建运行机 set up started again when its process
        is gone, then the bridge's round."""
        signer = signerproc.config_for(rt.team)
        if signer is not None and signerproc.running(signer) is None:
            try:
                pid, _ = signerproc.ensure(signer, APP_ROOT, PYTHON_BIN)
            except signerproc.SignerFailed as exc:
                rt.error = exc.team_error()
                self.log.warning("brain: the signer was gone and does not start again: %s", rt.error)
            else:
                if rt.error.startswith(signerproc.FAILED):
                    rt.error = ""
                self.log.info("brain: the signer was gone: started it again (pid %d)", pid)
        return rt.loop_once()

    # ---- small helpers -------------------------------------------------------------------------------- #

    def _load_big(self) -> set[str]:
        try:
            got = json.loads(self._big_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {s for s in got if isinstance(s, str) and re.match(r"^r[1-9][0-9]{0,2}-[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}$", s)} \
            if isinstance(got, list) else set()

    def _mark_big(self, scope: str) -> None:
        """Blocking: a requirement opened as 「大需求：先拆成小需求」 (its family is 'big' until it is split)."""
        self._big.add(scope)
        tmp = self._big_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self._big)), encoding="utf-8")
        os.replace(tmp, self._big_file)

    def _families_of(self, repo: Repo, facts: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """{key: ReqFamily} of the repo's family members (family.families): brain-kit's listing when it has answered
        (asked for again in the background, at most every FAMILY_TTL_S), else this machine's claims. Only for a repo
        with split requirements, or one opened here as a big one. Blocking (a few stat calls)."""
        facts = repo.scan() if facts is None else facts
        big = {f["key"] for f in facts if scope_of(repo.id, f["key"]) in self._big or family.units_here(f)}
        here = family.has_family(facts)
        if not here and not big:
            return {}
        if here and bk.has("split"):
            self.families.want(repo.id, repo.path)
        docs, at, _err = self.families.get(repo.id)
        return family.families(facts, docs, me=self.me_in(repo), cfg=repo.cfg, can_split=bk.has("split"), big=big, at=at)

    def _repo(self, rid: str) -> Repo:
        if not REPO_ID_RE.match(rid or "") or rid not in self.repos:
            raise AppError("没有这个仓库", "no_repo", 404)
        return self.repos[rid]

    def me(self, which: str) -> str | None:
        """This machine's person on 交付总览's *which*: the name in that repo; on 全部 and the runner's view, the first
        repo's that has one, the name /status's 你是谁 says (me_line)."""
        repos = [self.repos[which]] if which in self.repos else self.repos.values()
        return next((name for name in map(self.me_in, repos) if name), None)

    def me_in(self, repo: Repo) -> str | None:
        return self._me_view(repo)["name"]

    def _me_view(self, repo: Repo) -> dict[str, Any]:
        return self.who.of(repo.path, self._gitlab_of(repo)[0])

    def _sessions_snapshot(self) -> dict[str, Any]:
        """{scope: [session rows]} and credits per scope, read on the event loop."""
        by: dict[str, list[dict[str, Any]]] = {}
        for info in self.sessions.roots():
            if info.key and info.slot:
                by.setdefault(info.key, []).append(session_row(self.sessions, info))
        return {"by": by, "credits": self.sessions.credits_by_key()}

    @staticmethod
    def _req_credits(snap: dict[str, Any], keys: list[str], elsewhere: str | None = None
                     ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A requirement's sessions here, every one of *keys*' (its repo scope, its runs on the team runner here), and
        its 成本 over them (sessions.requirement_credits)."""
        rows = [s for k in keys for s in snap["by"].get(k, [])]
        return rows, dict(requirement_credits(rows, elsewhere), note=detail.NO_TOKENS)

    def _local_credits(self, snap: dict[str, Any], f: dict[str, Any], scope: str, state: str | None = None
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A requirement of a repo here: its sessions on this machine, then its runs' when the team runner is here too
        (run id RUN-<KEY>-<n>); taken by a runner on another machine (*state*, RUNNER_TOOK), the runner's are said to be
        left out."""
        runs = sorted((k for k in snap["by"] if re.fullmatch(r"RUN-%s-[1-9][0-9]*" % re.escape(f["key"]), k)),
                      key=lambda k: int(k.rsplit("-", 1)[1]))
        run = f.get("run") or {}
        took = deliveries.on_runner(run) and state in RUNNER_TOOK
        gone = None if not took or runs or self._team_on() else RUNNER_ELSEWHERE if run.get("handoff") else RUNNER_STARTED
        return self._req_credits(snap, [scope] + runs, gone)

    def _team_credits(self, snap: dict[str, Any], key: str, run_ids: list[str], project: Any
                      ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """A requirement on the team runner here: the sessions of its repo's scope on this machine, then its runs'; with
        none of the first here, the sessions before the hand-over are said to be left out."""
        home = self._home_of(project)
        mine = [scope_of(home.id, key)] if home is not None else []
        here = any(snap["by"].get(k) for k in mine)
        return self._req_credits(snap, mine + list(run_ids), None if here else LOCAL_ELSEWHERE)

    def _home_of(self, project: Any) -> Repo | None:
        """The repo here of a GitLab project (the first listed with it), None when this machine does not list it."""
        return next((r for r in self.repos.values() if project and self._gitlab_of(r)[1] == project), None)

    @staticmethod
    def _live(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The requirement's live brain-dev session on this machine, the one 打开会话 opens, 重开一个会话 replaces and
        an answer goes into: the newest that KiroCrew still has, also while its first message is being sent or when it
        did not go in (the session is there, in the worktree: one session per worktree). Never a review session:
        开审查会话 opens one beside it."""
        live = [r for r in rows if r["kind"] == "local" and not r["missing"] and not r["review"]]
        return live[-1] if live else None

    def _live_of(self, scope: str) -> dict[str, Any] | None:
        return self._live([session_row(self.sessions, i) for i in self.sessions.roots(key=scope) if i.slot])

    def _gitlab_of(self, repo: Repo) -> tuple[str, str]:
        g = repo.cfg.get("gitlab") or {}
        return str(g.get("base") or ""), str(g.get("project") or "")

    def _token_present(self) -> bool:
        return bool(identity.token())

    # ---- 交付总览 ------------------------------------------------------------------------------------- #

    def _local_only(self, repo: Repo) -> bool:
        """Its requirements are done only on this machine: no GitLab, or no remote (RepoInfo.remote), to hand them over
        through."""
        base, project = self._gitlab_of(repo)
        return not (base and project) or not repo.has_origin()

    def _row(self, repo: Repo, f: dict[str, Any], snap: dict[str, Any],
             fams: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        row = deliveries.base_row(repo.id, f, local_only=self._local_only(repo))
        scope = scope_of(repo.id, f["key"])
        sess = snap["by"].get(scope, [])
        live = self._live(sess)
        base, project = self._gitlab_of(repo)
        run = f.get("run") or {}
        flow = f["flow"]
        own_mr = bool(run) and not f.get("flow_error") and flowview.reads_own_mr(flow)
        mine = deliveries.owns(self.me_in(repo), f.get("owner") or (f.get("claim") or {}).get("owner"))
        mr = None
        runner = deliveries.on_runner(run)
        if (runner or own_mr) and f.get("branch") and base and project:
            wanted = (base, project, f["branch"])
            mr = self.mr.get(project, f["branch"])
            if runner or fastrow.polls_mr(f, mr):
                self._mr_wanted[wanted] = (scope, str(repo.cfg.get("bridge_user") or ""))
            else:
                self._mr_wanted.pop(wanted, None)
            seen = (project, f["branch"], f.get("tip"))
            if (mr or {}).get("sha") != f.get("tip") and seen not in self._mr_seen and repo.is_handover_tip(f):
                # a hand-over just made: read its MR now, so a label that did not go on is said at once
                self._mr_seen.add(seen)
                mr = self.mr.refresh(base, project, f["branch"], transport=self.transport,
                                     bridge_user=str(repo.cfg.get("bridge_user") or "") or None)
        elif f.get("branch") and base and project:     # a fast run switched to the full flow reads no MR of its own
            self._mr_wanted.pop((base, project, f["branch"]), None)
        since = deliveries.runner_commits_from(run, mr, repo.cfg)
        bot = repo.bot_commits_since(since, f.get("origin_sha")) if since else 0
        merged = bool(f.get("merged") or (mr or {}).get("state") == "merged")
        fast = self._fast_of(repo, f, git=mine and not merged and (mr or {}).get("state") != "closed")
        deliveries.classify(row, f, mr=mr, slot=live, cfg=repo.cfg, bot_commits=bot,
                            at_handover=bool(mr) and repo.is_handover_tip(f), me=self.me_in(repo),
                            reflected=merged and f["key"] in repo.reflected(), now=self.now(),
                            pushed=not run.get("handoff") or row["local_only"] or repo.on_remote(f), fast=fast)
        fams = self._families_of(repo) if fams is None else fams
        row["family"] = fams.get(f["key"])
        family.classify(row, row["family"], f, self.me_in(repo) if row["family"] else None)
        counted, cost = self._local_credits(snap, f, scope, row["state"])
        row["credits"], row["credits_whole"], row["sessions"] = cost["total"], cost["whole"], len(counted)
        row["updated_at"] = deliveries.updated_at(f, live)
        row["session_url"] = live["url"] if live else None
        row["progress_pushed_at"] = self.sync.pushed_at(repo.path, f.get("branch"))
        job = self._desc_job(repo, f, row["credits"])
        row["summary"] = mrdesc_view.plain_line(self.desc.first(*job)) if job else None
        if mr:
            row["mr"] = {"iid": mr["iid"], "url": mr.get("url") or mrinfo.mr_url(base, project, mr["iid"]),
                         "state": mr.get("state")}
            row["open_questions"] = [q["id"] for q in mr.get("questions") or [] if q.get("state") == "open"]
            row["run_ids"] = sorted({q["run_id"] for q in mr.get("questions") or [] if q.get("run_id")})
        return row

    def _fast_of(self, repo: Repo, f: dict[str, Any], *, git: bool = True) -> fastrow.FastFacts | None:
        """The FastFacts of a run without a Lean intent (a fast run): its row and its board read them; *git*: its
        commits and risk paths too (a row that never reads them skips that git work). Blocking."""
        flow = f.get("flow")
        if not f.get("run") or f.get("flow_error") or flow is None or flow.intent:
            return None
        base, project = self._gitlab_of(repo)
        branch = str(f.get("branch") or "")
        mr = self.mr.get(project, branch) if base and project and branch else None
        read_at = self.mr.stale_since(project, branch) if mr else None
        pushed = bool(f.get("origin_sha")) and f.get("origin_sha") == f.get("local_sha")
        by_hand = not mr and pushed and not self._token_present() and any(
            e["label"] == acts.BY_HAND for e in self.store.events(scope_of(repo.id, f["key"])))
        return fastrow.fast_facts(repo, f, self._fast_git, git=git, by_hand=by_hand,
                                  stale_s=max(0.0, time.time() - read_at) if read_at else None)

    # ---- 变更说明 (brain mr-desc) ------------------------------------------------------------------------- #

    @staticmethod
    def _desc_credits(credits: Any) -> float | None:
        return round(float(credits), 2) if isinstance(credits, (int, float)) and not isinstance(credits, bool) else None

    def _desc_job(self, repo: Repo, f: dict[str, Any], credits: Any) -> tuple[tuple, Callable[[], dict]] | None:
        """(signature, maker) of a requirement's 变更说明 on this machine: from the commit its facts are read at (the
        worktree's or branch's tip, the default branch once merged); None before it has a run."""
        if not f.get("run") or not f.get("key"):
            return None
        main = repo.main_sha()
        sha = f.get("tip") if f.get("where") in ("worktree", "branch") else (main if f.get("where") == "main" else None)
        if not sha:
            return None
        c = self._desc_credits(credits)
        key, cfg, path = f["key"], repo.cfg, repo.path
        cr = {"runs": {key: {"credits": c}}} if c is not None else None
        return (path, sha, main, key, c), (lambda: mrdesc_view.describe(path, cfg, key, sha, cr))

    def _desc_runner_job(self, path: str, cfg: dict[str, Any], key: str, credits: Any,
                         run_ids: list[str]) -> tuple[tuple, Callable[[], dict]] | None:
        """The same for a run on the team runner: its workspace's HEAD (the branch as the runner has it)."""
        git = Git(path)
        sha = mrdesc_view.target(git, "HEAD")
        if not sha:
            return None
        d = cfg.get("default_branch") or "main"
        main = mrdesc_view.target(git, "refs/remotes/origin/" + d) or mrdesc_view.target(git, "refs/heads/" + d)
        c = self._desc_credits(credits)
        cr = {"runs": {x: {"credits": c} for x in run_ids[-1:]}} if c is not None and run_ids else None
        return (path, sha, main, key, c), (lambda: mrdesc_view.describe(path, cfg, key, sha, cr))

    def _team_rows(self, snap: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]]:
        """(row, facts, decisions) per requirement the team runner has (its latest run, TeamRuntime.runner_rows), with
        the credits of its sessions here and its 变更说明's first line."""
        if self.team is None or self.team.host is None:
            return []
        out = []
        for row, facts, decs, _run in self.team.runner_rows():
            counted, cost = self._team_credits(snap, row["key"], row["run_ids"], facts.get("project"))
            row["credits"], row["credits_whole"], row["sessions"] = cost["total"], cost["whole"], len(counted)
            job = self._runner_desc(facts, row["key"], row) if facts.get("run") else None
            row["summary"] = mrdesc_view.plain_line(self.desc.first(*job)) if job else None
            out.append((row, facts, decs))
        return out

    def _team_split(self, mine: list[tuple[str, dict[str, Any], dict[str, Any]]], snap: dict[str, Any]
                    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]], set[tuple[str, str]]]:
        """Team mode, one row per requirement: the runner's requirements listed as its own (_team_rows'), each with
        the listed repo of its GitLab project (row ``home``, None when this machine does not list it), and the
        (repo id, key) of this machine's rows (*mine*: repo id, facts, row) they stand for (deliveries.runner_takes)."""
        team = self._team_rows(snap)
        rid_of: dict[str, str] = {}
        for rid, repo in self.repos.items():
            if self._gitlab_of(repo)[1]:
                rid_of.setdefault(self._gitlab_of(repo)[1], rid)
        by ={(rid, f["key"]): (f, row) for rid, f, row in mine}
        keep, taken = [], set()
        for t in team:
            rid = rid_of.get(str(t[1].get("project") or ""))
            f, row = by.get((rid, t[0]["key"]), (None, None)) if rid else (None, None)
            if deliveries.runner_takes(f, row, t[0]):
                t[0]["home"] = rid
                keep.append(t)
                if f is not None:
                    taken.add((rid, t[0]["key"]))
        return keep, taken

    def _team_on(self) -> bool:
        return self.team is not None and self.team.host is not None

    def _runner_desc(self, facts: dict[str, Any], key: str, row: dict[str, Any]):
        path = facts.get("worktree")
        if not path:
            return None
        try:
            cfg = bk.m("config").load(path)
        except Exception:  # noqa: BLE001
            cfg = dict(bk.m("config").DEFAULTS)
        return self._desc_runner_job(path, cfg, key, row.get("credits"), row.get("run_ids") or [])

    def _overview_sync(self, which: str, snap: dict[str, Any]) -> dict[str, Any]:
        rows, errors, per_row = [], [], []   # each row's questions asked and decisions, added up over the rows listed
        team = self._team_on()
        mine = []
        for rid, repo in self.repos.items():
            if which not in ("all", rid) and not team:   # team mode: every repo's rows, to list each requirement once
                continue
            try:
                facts = repo.scan()
            except Exception as exc:  # noqa: BLE001 - git's or brain-kit's own words go under 细节
                if which in ("all", rid):
                    errors.append({"repo": rid, "name": repo.name, "path": repo.path,
                                   "error": "读不了这个仓库里的需求：在「导入仓库」里检查它，那里会说哪里不对",
                                   "raw": str(exc)[:2000]})
                continue
            fams = self._families_of(repo, facts)
            mine += [(rid, f, self._row(repo, f, snap, fams)) for f in facts]
        runner, taken = self._team_split(mine, snap) if team else ([], set())
        for rid, f, row in mine:
            if which not in ("all", rid) or (rid, f["key"]) in taken:
                continue
            repo = self.repos[rid]
            base, project = self._gitlab_of(repo)
            rows.append(row)
            mr = self.mr.get(project, f["branch"]) if base and project and f.get("branch") else None
            intent = (f.get("run") or {}).get("intent")
            per_row.append((row, deliveries.questions_asked(f, mr), [dict(d, key=f["key"]) for d in detail.decisions(
                f.get("decisions_text"), "aidlc-docs/%s/decisions.md" % intent if intent else "decisions.md",
                repo.cfg.get("bot_users") or [])]))
        for rid, (path, err) in self.repo_errors.items():
            if which in ("all", rid):   # the app's own words say what to do; anything else is under 细节
                said = re.search(r"[\u4e00-\u9fff]", err)
                errors.append({"repo": rid, "name": os.path.basename(path), "path": path,   # /status's name for it
                               "error": err if said else "读不了这个仓库：在「导入仓库」里检查它，那里会说哪里不对",
                               "raw": path if said else "%s：%s" % (path, err)})
        for row, f, decs in runner:   # a repo's view has what the runner does for it, as 全部 and the runner's do
            if which in ("all", "runner", row["home"]):
                rows.append(row)
                per_row.append((row, deliveries.ledger_questions(decs),
                                [dict(d, key=row["key"]) for d in detail.decisions(f.get("decisions_text"), "decisions.md", [])]))
        overlaps = deliveries.overlaps(rows)     # over every row: a teammate's may share a path with one of mine
        listed = len(rows)
        own = None      # a person's: (repo id, key) of the rows listed, which the numbers count
        if self.settings["role"] == settings_mod.PERSONAL:
            rows = deliveries.own_rows(rows, {rid: self.me_in(r) for rid, r in self.repos.items()})
            keys = {r["key"] for r in rows}
            overlaps = [o for o in overlaps if keys & set(o["keys"])]
            own = {(r["home"] if r["repo"] == "runner" else r["repo"], r["key"]) for r in rows}
        kept = {id(r) for r in rows}
        q_counts = {k: sum(q[k] for row, q, _ in per_row if id(row) in kept) for k in ("asked", "answered", "delivered")}
        parts: list[tuple[str, dict[str, Any]]] = []
        view, funnel = self._eval_view(which, snap, parts)
        if funnel is None:
            funnel = deliveries.funnel_app([d for row, _, ds in per_row if id(row) in kept for d in ds], q_counts)
        else:
            funnel = dict(deliveries.own_funnel(funnel, parts, own) if own is not None else funnel, questions=q_counts)
        scope = [r for rid, r in self.repos.items() if which in ("all", rid)]
        cto = deliveries.cto_numbers(parts, rows, available=bool(view.get("available")), now=self.now(),
                                     names={rid: r.name for rid, r in self.repos.items()}, repos=len(scope),
                                     local_repos=sum(1 for r in scope if self._local_only(r)), mine=own)
        return {"at": self.now(), "rows": rows, "others": listed - len(rows), "overlaps": overlaps, "funnel": funnel,
                "eval": view, "cto": cto, "counts": deliveries.counts(rows), "errors": errors,
                "roster": roster.roster(rows, me=self.me(which), now=self.now(), settings=self.settings)}

    def _eval_view(self, which: str, snap: dict[str, Any],
                   parts: list[tuple[str, dict[str, Any]]] | None = None) -> tuple[dict[str, Any], dict | None]:
        """Eval from the cache (computed in the background, at most every EVAL_TTL_S per repo and main sha). *parts*
        gets each repo's part of the 交付总览's numbers (deliveries.cto_part)."""
        ids = [rid for rid in self.repos if which in ("all", rid)]
        if not bk.has("evaluate"):
            return deliveries.evaluate("", {}, None, None)
        views, funnels, stale, got_parts = [], [], [], []
        computing = False    # a repo's numbers for its default branch as it is now are still being worked out
        for rid in ids:
            repo = self.repos[rid]
            sha = repo.git.out("rev-parse", "-q", "--verify", (repo.main_ref() or "HEAD") + "^{commit}")
            got = self._eval.get(rid)
            computing = computing or not got or got[0] != sha
            if not got or got[0] != sha or time.monotonic() - got[1] > EVAL_TTL_S:
                stale.append((rid, sha))
            if got:
                v = dict(got[2])
                part = v.pop("_cto", None)
                if part is not None:
                    got_parts.append((rid, part))
                v["_name"] = repo.name
                views.append(v)
                if got[3]:
                    funnels.append(got[3])
        if parts is not None:
            parts.extend(got_parts)
        for rid, sha in stale:
            if rid not in self._eval_running:
                self._eval_running.add(rid)
                credits = {"runs": {k.split("-", 1)[1]: {"credits": v} for k, v in snap["credits"].items()
                                    if k.startswith(rid + "-")}}
                threading_start(self._eval_compute, rid, sha, credits)
        if not views:
            return {"available": False, "error": "正在算交付评估…" if ids else "没有仓库", "raw": "", "ref": None,
                    "metrics": [], "queue": [], "skipped": [], "notes": [], "note_rows": [], "computing": bool(ids),
                    "families": []}, None
        ref = deliveries.branch_words([(v.pop("_name"), v.get("ref")) for v in views])
        if len(views) == 1:
            return dict(views[0], ref=ref, computing=computing), (dict(funnels[0]) if funnels else None)
        merged = {"available": any(v["available"] for v in views), "computing": computing,
                  "error": "；".join(dict.fromkeys(v["error"] for v in views if v["error"])),
                  "raw": "\n".join(v.get("raw") or "" for v in views if v.get("raw")), "ref": ref, "metrics": [], "queue": [q for v in views for q in v["queue"]],
                  "skipped": [x for v in views for x in v.get("skipped") or []],
                  "notes": [x for v in views for x in v.get("notes") or []],
                  "note_rows": [x for v in views for x in v.get("note_rows") or []],
                  "families": [x for v in views for x in v.get("families") or []]}
        # every repo's numbers, in the order they come (the 部署 ones only where kit.json turned the deploys on)
        for mid in dict.fromkeys(x["id"] for v in views for x in v["metrics"]):
            m = next(x for v in views for x in v["metrics"] if x["id"] == mid)
            vals = [next((x["value"] for x in v["metrics"] if x["id"] == m["id"]), None) for v in views]
            pairs = [x for x in vals if isinstance(x, list)]
            nums = [x for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if m["id"] == "credits_per_success":   # an average: the repos' credits over their correct deliveries
                spent = [p["credits_total"] for _, p in got_parts if p.get("credits_total") is not None]
                good = sum(p.get("correct") or 0 for _, p in got_parts)
                value: Any = round(sum(spent) / good, 4) if spent and good else None
            elif m["id"] == "lead_time":   # a median: over every repo's requirements
                hours = [h for _, p in got_parts for h in p.get("lead_hours") or []]
                value = round(statistics.median(hours), 2) if hours else None
            elif pairs:
                value = [sum(p[0] for p in pairs), sum(p[1] for p in pairs)]
            else:
                value = sum(nums) if nums else None
            merged["metrics"].append({**m, "value": value})
        funnel = None
        if funnels:
            funnel = dict(funnels[0])
            funnel["rows"] = [{**r, "count": sum(next((x["count"] for x in f["rows"] if x["id"] == r["id"]), 0)
                                                 for f in funnels)} for r in funnels[0]["rows"]]
            funnel["total"] = sum(f["total"] for f in funnels)
            funnel["unclassified"] = [x for f in funnels for x in f.get("unclassified") or []]
        return merged, funnel

    def _eval_compute(self, rid: str, sha: str, credits: dict[str, Any]) -> None:
        try:
            repo = self.repos.get(rid)
            if repo is None:
                return
            view, funnel = deliveries.evaluate(repo.path, repo.cfg, repo.main_ref(), credits,
                                               deploys=self.deploys.source(repo.path, repo.cfg, repo.main_ref()))
            view["families"] = [dict(x, repo=rid) for x in view.get("families") or []]
            self._eval[rid] = (sha, time.monotonic(), view, funnel)
        except Exception as exc:  # noqa: BLE001
            self._eval[rid] = (sha, time.monotonic(), {"available": False, "error": "交付评估没算成：原话在细节里",
                                                        "raw": str(exc)[:2000], "ref": None, "metrics": [],
                                                        "queue": [], "skipped": [], "notes": [], "note_rows": [],
                                                        "families": []}, None)
        finally:
            self._eval_running.discard(rid)

    async def overview(self, which: str = "all") -> dict[str, Any]:
        if which != "all" and not REPO_ID_RE.match(which or ""):
            raise AppError("repo 只能是 all、r1、r2 … 或 runner", "invalid_repo")
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(self._overview_sync, which, snap), self.scrub)

    async def refresh(self, rid: str) -> dict[str, Any]:
        """「从远端取最新」: after the background fetch of this repo, when one runs."""
        repo = self._repo(rid)
        return await asyncio.to_thread(self._fetch, rid, repo, True)

    # ---- 需求现状 ------------------------------------------------------------------------------------- #

    def _facts(self, repo: Repo, key: str) -> dict[str, Any]:
        if not KEY_RE.match(key or ""):
            raise AppError("需求号不对", "invalid_key")
        f = next((x for x in repo.scan() if x["key"] == key), None)
        if f is None:
            raise AppError("这个仓库里没有 %s" % key, "not_found", 404)
        return f

    def _vouchers_dir(self, repo: Repo) -> str | None:
        s = os.environ.get("BRAIN_INTAKE_DIR") or identity.git_config(repo.path).get("brain.intake") or "~/.brain/intake"
        d = os.path.join(os.path.abspath(os.path.expanduser(s)), "vouchers")
        return d if os.path.isdir(d) else None

    def _detail_sync(self, rid: str, key: str, snap: dict[str, Any], parts: dict[str, Any] | None = None) -> dict[str, Any]:
        """需求现状. *parts*, when given, gets what the board is built from as well (facts, the raw trace, the MR)."""
        if rid == "runner":
            return self._team_detail(key, snap, parts)
        repo = self._repo(rid)
        f = self._facts(repo, key)
        row = self._row(repo, f, snap)
        scope = scope_of(rid, key)
        run = f.get("run") or {}
        me = self.me_in(repo)
        own = bool(me) and me == (f.get("owner") or "")
        base, project = self._gitlab_of(repo)
        mr = self.mr.get(project, f["branch"]) if (base and project and f.get("branch")) else None
        can_post = own and bool(base and project) and self._token_present()
        questions = []
        handed = deliveries.on_runner(run)
        pq = detail.park_question(f.get("park"), answerable=own and not handed and bool(
            self._live(snap["by"].get(scope, []))), via="session", handed=handed,
            mr_url=(row.get("mr") or {}).get("url"))
        posted = str(f.get("park_sha") or "")[:12]
        if pq and not (handed and posted and any(q.get("park") == posted for q in (mr or {}).get("questions") or [])):
            questions.append(pq)    # once the bridge posted it, its D-n on the MR is the same question
        questions += detail.mr_questions(mr, own=own, can_post=can_post)
        tl = self._timeline(repo, f, snap, scope)
        counted, cost = self._local_credits(snap, f, scope, row["state"])
        if parts is not None:
            parts.update(repo=repo, facts=f, trace=tl.get("trace"), mr=mr, scope=scope, decisions=[], me=me,
                         cfg=repo.cfg, path=repo.path, local_only=row["local_only"])
        return {
            "row": row, "where_label": self._where_label(f),
            "run": {"owner": run.get("owner"), "branch": run.get("branch"), "intent": run.get("intent"),
                    "brain_commit": run.get("brain_commit"), "origin": run.get("origin"),
                    "created_at": run.get("created_at"),
                    "materials": [str(m.get("package")) for m in run.get("materials") or [] if isinstance(m, dict)],
                    "handoff": ({"requested_at": run["handoff"].get("requested_at"), "by": run["handoff"].get("by")}
                                if isinstance(run.get("handoff"), dict) else None)} if f.get("run") else None,
            "claim": self._claim_view(f.get("claim")),
            "lean": {"stages": f.get("stages") or lean_view.stages([]),
                     "phases": lean_view.phases(f.get("stages") or lean_view.stages([])),
                     "approvals": [{k: a[k] for k in ("kind", "state", "sha8", "at", "line", "path")}
                                   for a in f.get("approvals") or []],
                     **lean_view.notes(f.get("state_text"))},
            "decisions": detail.decisions(f.get("decisions_text"),
                                          flowview.decisions_path(f["flow"], run, repo.cfg["runs_dir"])
                                          or "aidlc-docs/%s/decisions.md" % run.get("intent"),
                                          repo.cfg.get("bot_users") or []),
            "questions": questions,
            "timeline": {"source": tl["source"], "items": tl["items"], "error": tl["error"],
                         "missing": board_mod.missing_sources(tl["missing"], local_only=row["local_only"],
                                                              handed=handed, ci_read=board_mod.pipelines_read(mr)),
                         "other_runs": tl.get("other_runs") or []},
            "sessions": [{k: s[k] for k in ("slot", "title", "agent", "kind", "credits", "turns", "busy", "awaiting",
                                             "last_activity", "opened_at", "project", "url", "error", "missing")}
                         for s in counted],
            "live_slot": (self._live(snap["by"].get(scope, [])) or {}).get("slot"),
            "credits": cost,
            "brain": detail.brain_block(tl.get("brain"), None, f.get("decisions_text")),
            "mr_desc": self._desc_now(self._desc_job(repo, f, cost["total"])),
            "deploy": deploys_view.requirement(tl.get("trace"), repo.cfg),
        }

    def _desc_now(self, job: tuple[tuple, Callable[[], dict]] | None) -> dict[str, Any]:
        """The 变更说明 for the detail page: from the cache, or made now (blocking: called in a thread)."""
        if job is None:
            return mrdesc_view.empty("还没开工：开工以后才有变更说明")
        return self.desc.get_or_make(*job)

    def _timeline(self, repo: Repo, f: dict[str, Any], snap: dict[str, Any], scope: str) -> dict[str, Any]:
        """The requirement's trace, made again only when something it is made from changed (_trace_stamp), or when
        brain-kit could not make it (trace_failed) FAILED_TRACE_S ago."""
        behind = f.get("where") == "worktree" and f.get("ref")      # origin's run branch is ahead of the worktree
        src_repo, ref = (f["worktree"], None) if f.get("worktree") and not behind else (repo.path, f.get("ref"))
        c = self._local_credits(snap, f, scope)[1]["total"]
        vouchers = self._vouchers_dir(repo)
        sig = (f.get("tip"), f.get("worktree"), len(f.get("events") or []), f.get("park_sha"), bool(f.get("run")),
               src_repo, ref, c, vouchers, self._trace_stamp(repo.cfg, src_repo, ref, vouchers))
        got = self._trace.get(scope)
        if got is not None and got[0] == sig and not (trace_failed(got[1]) and time.monotonic() - got[2] > FAILED_TRACE_S):
            tl = dict(got[1])
        else:
            tl = detail.timeline(src_repo, repo.cfg, f["key"], ref=ref, events=f.get("events") or [],
                                 credits={"runs": {f["key"]: {"credits": c}}} if c is not None else None,
                                 vouchers_dir=vouchers, has_run=bool(f.get("run")),
                                 deploys=self.deploys.source(src_repo, repo.cfg) if f.get("run") else None)
            self._trace[scope] = (sig, dict(tl), time.monotonic())
        tl["items"] = sorted(tl["items"] + self.store.events(scope), key=lambda x: (x["at"] or "", x["kind"]))
        return tl

    def _trace_stamp(self, cfg: dict[str, Any], src: str, ref: str | None, vouchers: str | None) -> tuple:
        """What brain-kit's trace reads besides commits, which never change: the branches, the run's records when they
        are read in the worktree (trace and usage read runs_dir, aidlc-docs and brain_dir there), every run's vouchers
        (a big one's trace reads each small one's too), and the minute: it judges the 14 days after a merge at the time
        it is made, and reads some git calls without a check (a failed one reads as not there, with no error)."""
        files = files_stamp(src, [cfg["runs_dir"], lean_view.DOCS, cfg["brain_dir"]]) if ref is None else ()
        try:
            runs = [d for d in sorted(os.listdir(vouchers)) if d.startswith("RUN-")] if vouchers else []
        except OSError:
            runs = []
        return (git_stamp(src, ("refs/heads/", "refs/remotes/origin/")), files,
                files_stamp(vouchers, runs) if vouchers else (), int(self.now() // 60))

    def _team_detail(self, key: str, snap: dict[str, Any], parts: dict[str, Any] | None = None) -> dict[str, Any]:
        hit = next(((r, f, d) for r, f, d in self._team_rows(snap) if r["key"] == key), None)
        if hit is None:
            raise AppError("运行机上没有 %s" % key, "not_found", 404)
        row, f, decs = hit
        run = f.get("run") or {}
        path = f.get("worktree")
        tl = {"source": "app", "items": lean_view.stage_events(f.get("events") or []), "missing": [], "error": "",
              "brain": None, "other_runs": []}
        deploy = None
        if path:
            try:
                cfg = bk.m("config").load(path)
            except Exception:  # noqa: BLE001
                cfg = dict(bk.m("config").DEFAULTS)
            c = sum(snap["credits"].get(x, 0.0) for x in row["run_ids"]) or None
            tl = detail.timeline(path, cfg, key, ref=None, events=f.get("events") or [],
                                 credits={"runs": {x: {"credits": snap["credits"].get(x)} for x in row["run_ids"]
                                                   if snap["credits"].get(x) is not None}} if c else None,
                                 ledger_path=self.team.cfg.get("ledger"), vouchers_dir=self.team.cfg.get("vouchers_dir"),
                                 allowed_signers=self.team.allowed_signers or None,
                                 deploys=self.deploys.source(path, cfg))
            deploy = deploys_view.requirement(tl.get("trace"), cfg)
        for rid in row["run_ids"]:
            tl["items"] += self.store.events(rid)
        tl["items"].sort(key=lambda x: (x["at"] or "", x["kind"]))
        sess = [s for x in row["run_ids"] for s in snap["by"].get(x, [])]
        counted, cost = self._team_credits(snap, key, row["run_ids"], f.get("project"))
        if parts is not None:
            try:
                cfg_t = bk.m("config").load(path) if path else dict(bk.m("config").DEFAULTS)
            except Exception:  # noqa: BLE001
                cfg_t = dict(bk.m("config").DEFAULTS)
            home = self._home_of(f.get("project"))
            parts.update(repo=None, facts=f, trace=tl.get("trace"), mr=None, scope=None, decisions=decs,
                         me=self.me_in(home) if home is not None else None,   # who reads it, as in its repo
                         cfg=cfg_t, path=path, local_only=False, sessions=sess,
                         app_events=[e for rid in row["run_ids"] for e in self.store.events(rid)])
        return {
            "row": row, "where_label": "运行机工作区 %s" % (path or "（还没建）"),
            "run": {"owner": run.get("owner"), "branch": run.get("branch"), "intent": run.get("intent"),
                    "brain_commit": run.get("brain_commit"), "origin": run.get("origin"), "created_at": run.get("created_at"),
                    "materials": [str(m.get("package")) for m in run.get("materials") or [] if isinstance(m, dict)],
                    "handoff": ({"requested_at": run["handoff"].get("requested_at"), "by": run["handoff"].get("by")}
                                if isinstance(run.get("handoff"), dict) else None)} if run else None,
            "claim": None,
            "lean": {"stages": f.get("stages") or lean_view.stages([]),
                     "phases": lean_view.phases(f.get("stages") or lean_view.stages([])),
                     "approvals": [dict({k: a[k] for k in ("kind", "state", "sha8", "at", "path")}, line=None)
                                   for a in f.get("approvals") or []],
                     **lean_view.notes(f.get("state_text"))},
            "decisions": detail.decisions(f.get("decisions_text"), "decisions.md", []),
            "questions": detail.ledger_questions(decs, row["mr"]["url"] if row.get("mr") else None),
            "timeline": {"source": tl["source"], "items": tl["items"], "error": tl["error"],
                         "missing": board_mod.missing_sources(tl["missing"], local_only=False, handed=True),
                         "other_runs": tl.get("other_runs") or []},
            "sessions": [{k: s[k] for k in ("slot", "title", "agent", "kind", "credits", "turns", "busy", "awaiting",
                                             "last_activity", "opened_at", "project", "url", "error")} for s in counted],
            "credits": cost,
            "brain": detail.brain_block(tl.get("brain"), None, f.get("decisions_text")),
            # no run read yet: the runner has not started on it, nothing is waiting for 开工
            "mr_desc": self._desc_now(self._runner_desc(f, key, row)) if run else mrdesc_view.empty(board_mod.RUNNER_NO_DESC),
            "deploy": deploy,
        }

    @staticmethod
    def _claim_view(c: Any) -> dict[str, Any] | None:
        if not isinstance(c, dict):
            return None
        closed = c.get("closed")
        return {"owner": c.get("owner"), "title": str(c.get("title") or "")[:200], "created_at": c.get("created_at"),
                "source": str(c.get("source") or "product"),
                "closed": (str((closed or {}).get("reason") or "已关闭")[:200] if closed else None)}

    @staticmethod
    def _where_label(f: dict[str, Any]) -> str:
        return {"worktree": "这台机器的工作区 %s" % f.get("worktree"), "branch": "分支 %s（没在这台机器上检出）" % (f.get("ref") or ""),
                "main": "默认分支（已合并）", "claim": "只有认领，还没开工", "runner": "运行机"}.get(f.get("where"), "")

    async def requirement(self, rid: str, key: str) -> dict[str, Any]:
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(self._detail_sync, rid, key, snap), self.scrub)

    # ---- 运行过程 ------------------------------------------------------------------------------------- #

    def _commits(self, git: Git, shas: list[str]) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
        """The commits' subjects and the files each changed (one git call, board_mod.GIT_LOG; kept by the commits
        asked for, unless git failed)."""
        shas = [x for x in shas if re.match(r"^[0-9a-f]{40}$", x or "")][-board_mod.MAX_BELT:]
        if not shas:
            return {}, {}
        sig = (git.repo, tuple(shas))
        got = self._subj.get(sig)
        if got is None:
            p = git.run(*board_mod.GIT_LOG, *shas, timeout=20)
            if p.returncode != 0:
                return {}, {}
            got = (board_mod.subjects_from_log(p.stdout), board_mod.changes_from_log(p.stdout))
            if len(self._subj) > 200:
                self._subj.clear()
            self._subj[sig] = got
        return got

    @staticmethod
    def _docs(git: Git, intent: Any, changes: dict[str, list[dict[str, Any]]]) -> board_mod.DocsByCommit:
        rd = outputs_mod.GitReader(git)
        out = {}
        for sha, cs in changes.items():
            got = {c["path"]: rd.digest(sha, c["path"]) for c in cs if stages.is_doc(c["path"], intent)}
            out[sha] = {p: h for p, h in got.items() if h}
        return out

    @staticmethod
    def _reflect_refs(git: Git, key: str, default_branch: str) -> list[tuple[str, float, float | None]] | None:
        """brain reflect's branches for *key* (brain/<KEY>-reflect-<n>, local or origin's): when each was made, and
        when it was merged into the default branch (the merge commit on its first-parent line; its own time when the
        branch is on that line, fast-forwarded), or None while it waits. None: a git call failed or timed out (any
        exit but 0, or 1 for no such ref or not merged)."""
        def run(*args: str) -> subprocess.CompletedProcess:
            p = git.run(*args)
            if p.returncode not in (0, 1):
                raise subprocess.CalledProcessError(p.returncode, args)
            return p

        def time_of(rev: str) -> float | None:
            s = run("log", "-1", "--format=%ct", rev).stdout.strip()
            return float(s) if s.isdigit() else None

        out, seen = [], set()
        rx = re.compile(r"^refs/(?:heads|remotes/origin)/(brain/%s-reflect-[1-9][0-9]*)$" % re.escape(key))
        try:
            refs = run("for-each-ref", "--format=%(refname)", "refs/heads/brain/", "refs/remotes/origin/brain/")
            main = next((r for r in ("refs/remotes/origin/" + default_branch, "refs/heads/" + default_branch)
                         if run("rev-parse", "-q", "--verify", r + "^{commit}").returncode == 0), None)
            for ref in sorted(refs.stdout.split()):
                m = rx.match(ref)
                if m and m.group(1) not in seen:
                    at = time_of(ref)
                    if at:
                        seen.add(m.group(1))
                        merged = None
                        if main and run("merge-base", "--is-ancestor", ref, main).returncode == 0:
                            line = run("rev-list", "--first-parent", "--ancestry-path", "--reverse",
                                       "%s..%s" % (ref, main)).stdout.split()
                            ff = not line or run("rev-parse", line[0] + "^1").stdout == run("rev-parse", ref).stdout
                            merged = at if ff else time_of(line[0])
                        out.append((m.group(1), at, merged))
        except subprocess.CalledProcessError:
            return None
        return out

    def _reflects_of(self, git: Git, key: str, default_branch: str) -> list[tuple[str, float, float | None]]:
        """_reflect_refs, read with git again only when a brain/ branch or the default branch moves, or a git call
        failed the last time."""
        stamp = git_stamp(git.repo, ("refs/heads/brain/", "refs/remotes/origin/brain/", "refs/heads/" + default_branch,
                                     "refs/remotes/origin/" + default_branch))
        sig = (git.repo, key, default_branch)
        got = self._reflects.get(sig)
        if got is None or got[0] != stamp:
            refs = self._reflect_refs(git, key, default_branch)
            if refs is None:
                return []
            if len(self._reflects) > 200:
                self._reflects.clear()
            got = self._reflects[sig] = (stamp, refs)
        return got[1]

    def _planned_units(self, git: Git, f: dict[str, Any]) -> list[stages.UnitPlan]:
        """The units unit-of-work.md plans, as Lean reads its first table (brain-kit's split.parse_units), at the run
        branch's newest commit (on the team runner: its workspace's HEAD). Only a regular file of that commit is read,
        so never a symlink and never the worktree's copy. None when it is not committed there or brain-kit cannot read
        it: the fold then has the units Lean's records name."""
        intent = (f.get("run") or {}).get("intent")
        sha = f.get("tip") or (f.get("ref") if f.get("where") == "main" else None) or (
            mrdesc_view.target(git, "HEAD") if f.get("where") == "runner" else None)
        if not sha or not isinstance(intent, str) or not lean_view.INTENT_RE.match(intent) or not bk.has("split"):
            return []
        sig = (git.repo, sha, intent)
        if sig not in self._units:
            raw = mrdesc_view.committed_file(git, sha, stages.doc_path(stages.BY_ID["units"], None, intent))
            try:
                got = bk.m("split").parse_units(raw.decode("utf-8", "replace"))[0] if raw else []
            except Exception:  # noqa: BLE001 - brain-kit could not read it: nothing planned
                got = []
            if len(self._units) > 200:
                self._units.clear()
            self._units[sig] = [stages.UnitPlan(u["name"], tuple(u["paths"])) for u in got]
        return self._units[sig]

    def _board_sync(self, rid: str, key: str, snap: dict[str, Any]) -> dict[str, Any]:
        parts: dict[str, Any] = {}
        d = self._detail_sync(rid, key, snap, parts)
        f, t, cfg = parts.get("facts") or {}, parts.get("trace"), parts.get("cfg") or {}
        repo = parts.get("repo")
        git = repo.git if repo is not None else (Git(parts["path"]) if parts.get("path") else None)
        commits = ((t or {}).get("actions") or {}).get("commits") or []
        subjects, changes = self._commits(git, [str(c.get("sha") or "") for c in commits]) if git is not None else ({}, {})
        sessions = parts["sessions"] if parts.get("sessions") is not None else snap["by"].get(parts.get("scope") or "", [])
        app_events = parts["app_events"] if parts.get("app_events") is not None else (
            self.store.events(parts["scope"]) if parts.get("scope") else [])
        units = self._planned_units(git, f) if git is not None else []
        kept: dict[str, Any] = {}
        live_slot = (self._live(sessions) or {}).get("slot")
        fast = self._fast_of(repo, f) if repo is not None else None
        if fast is not None:
            g = fastboard.board(
                f, d["row"], t, mr=parts.get("mr"), app_events=app_events, subjects=subjects, changes=changes,
                default_branch=str(cfg.get("default_branch") or "main"), local_only=bool(parts.get("local_only")),
                kit_rows=board_mod.kit_cells(self.crew.get("rows") or [], parts.get("path") or ""),
                decisions=d["decisions"], me=parts.get("me"), fast=fast, fold=kept)
        else:
            g = board_mod.build(
                f, d["row"], t, mr=parts.get("mr"), sessions=sessions, app_events=app_events,
                team_decisions=parts.get("decisions") or [], subjects=subjects, changes=changes,
                docs=self._docs(git, (f.get("run") or {}).get("intent"), changes) if git is not None else {},
                reflect_refs=self._reflects_of(git, key, str(cfg.get("default_branch") or "main"))
                if git is not None else [],
                default_branch=str(cfg.get("default_branch") or "main"), local_only=bool(parts.get("local_only")),
                kit_rows=board_mod.kit_cells(self.crew.get("rows") or [], parts.get("path") or ""),
                decisions=d["decisions"], brain=d["brain"], me=parts.get("me"), now=self.now(), units=units, fold=kept,
                live_slot=live_slot)
        if len(self._folds) > 50:
            self._folds.clear()
        self._folds[(rid, key)] = (time.monotonic(), outputs_mod.Fold(
            reader=outputs_mod.GitReader(git) if git is not None else None, intent=(f.get("run") or {}).get("intent"),
            branch=d["row"].get("branch"), nodes=tuple(kept["nodes"]), commits=tuple(kept["commits"]),
            changes=changes, units=tuple(units),
            worktree=f.get("worktree") if rid != "runner" and f.get("where") == "worktree" else None,
            runs=tuple(kept["runs"]), turns=tuple(kept["turns"])))
        return {"at": self.now(), "row": d["row"], "branch": d["row"].get("branch"),
                "base": str(cfg.get("default_branch") or "main"), "started_at": g["started_at"], "ended_at": g["ended_at"],
                "live": g["live"], "person": g["person"], "phases": g["phases"], "nodes": g["nodes"], "runs": g["runs"],
                "relays": g["relays"], "other_runs": g["other_runs"],
                "events": g["events"], "lean_steps": g["lean_steps"], "belt": g["belt"], "checks": g["checks"],
                "accept": g["accept"], "brain": d["brain"], "decisions": d["decisions"], "questions": d["questions"],
                "mr_desc": d["mr_desc"], "proof": self._evidence(parts, f, kept["nodes"]),
                "sessions": d["sessions"], "live_slot": live_slot, "credits": d["credits"], "source": d["timeline"]["source"],
                "missing": g["missing"], "error": d["timeline"]["error"], "rollup": board_mod.rollup(t),
                "steps": g.get("steps"),
                "time": fastboard.time_spent([self.sessions.slot_stats(s["slot"]) for s in d["sessions"]
                                              if s["kind"] == "local"], now=self.now(), until=g["ended_at"])
                if fast is not None else None}

    def _evidence(self, parts: dict[str, Any], f: dict[str, Any], nodes: list[stages.Node]) -> dict[str, Any]:
        """证据 (evidence.row): bind()'s JSON at the commit under review (evidence.py says which), read again only when
        that commit, the requirement's verifies (the only records bind() reads) or the brain-kit changed."""
        if not bk.has("proof"):
            return evidence.empty(evidence.NO_MODULE)
        path, repo, intent = parts.get("path"), parts.get("repo"), (f.get("run") or {}).get("intent")
        if repo is not None:
            sha = f.get("tip") or (repo.main_sha() if f.get("where") == "main" else None)
        else:
            sha = mrdesc_view.target(Git(path), "HEAD") if path else None
        if not (path and sha and isinstance(intent, str) and lean_view.INTENT_RE.match(intent)):
            return evidence.empty(evidence.NO_RUN)
        events, clone, units = evidence.scope(f)
        verifies = [e for e in events if e.get("event") == "VERIFY"]
        last = verifies[-1] if verifies else {}
        sig = (path, sha, intent, clone, len(verifies), last.get("_file"), last.get("seq"), last.get("ts"),
               bk.info()["path"])
        if sig not in self._proofs:
            try:
                got = evidence.read(path, sha, intent, events, clone, units)
            except Exception as exc:  # noqa: BLE001 - a brain-kit that cannot read them: said on the card, not raised
                self.log.info("brain proof %s at %s: %s", f.get("key"), sha[:12], str(exc).split("\n", 1)[0][:300])
                got = None
            if len(self._proofs) > 200:
                self._proofs.clear()
            self._proofs[sig] = got
        got = self._proofs[sig]
        return evidence.row(got, nodes, sha) if got is not None else evidence.empty(evidence.NOT_READ)

    async def board(self, rid: str, key: str) -> dict[str, Any]:
        """运行过程: polled every 2 s; the trace, the facts and the 变更说明 come from their caches."""
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(self._board_sync, rid, key, snap), self.scrub)

    def _fold(self, rid: str, key: str, snap: dict[str, Any]) -> outputs_mod.Fold:
        """The board's last fold of the requirement, folded again (by the board's own code) when it is older than
        FOLD_TTL_S: the drawer never folds on its own."""
        got = self._folds.get((rid, key))
        if got is None or time.monotonic() - got[0] > FOLD_TTL_S:
            self._board_sync(rid, key, snap)
            got = self._folds[(rid, key)]
        return got[1]

    async def outputs(self, rid: str, key: str, q: outputs_mod.NodeQuery) -> dict[str, Any]:
        """产出: one round of one node, its files (outputs.node_output)."""
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(lambda: outputs_mod.node_output(self._fold(rid, key, snap), q)),
                          self.scrub)

    async def outputs_commits(self, rid: str, key: str) -> dict[str, Any]:
        """产出 of a fast run: the run branch's commits, each with its files, and all of them added up
        (outputs.commits_output)."""
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(lambda: outputs_mod.commits_output(self._fold(rid, key, snap))),
                          self.scrub)

    async def outputs_file(self, rid: str, key: str, q: outputs_mod.FileQuery) -> dict[str, Any]:
        """产出: one file of it, its text or its change (outputs.file_view)."""
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(lambda: outputs_mod.file_view(self._fold(rid, key, snap), q)), self.scrub)

    async def outputs_turns(self, rid: str, key: str, q: outputs_mod.NodeQuery) -> dict[str, Any]:
        """产出's session tabs: the turns one round of one node was done in (turns.turn_views), each read from its
        session's transcript on this machine."""
        snap = self._sessions_snapshot()
        fold = await asyncio.to_thread(self._fold, rid, key, snap)
        slots = []
        for s in turns_mod.sessions_to_read(fold, q):
            try:
                rows, whole = await self.sessions.kept_transcript(str(s["slot"]))
            except GatewayError as exc:
                raise AppError("读不了这一轮的会话：%s" % gateway_said(exc), "transcript_unreadable", 502) from None
            root = self.sessions.by_slot(str(s["slot"]))
            slots.append(turns_mod.Slot(str(s["slot"]), str(s.get("url") or ""), root.cwd if root else "",
                                        bool(s.get("busy")), rows, whole, self.sessions.sent(str(s["slot"]))))
        return scrub_tree(turns_mod.turn_views(fold, q, slots, key, self.now(), self.scrub), self.scrub)

    # ---- sessions and answers ------------------------------------------------------------------------- #

    async def open_session(self, rid: str, key: str, trust: bool = True, kickoff: bool = True,
                           review: bool = False) -> dict[str, Any]:
        """The requirement's brain-dev session on this machine: the live one, else a new one. *review*: its review
        session still on its review (_review_of), else a new one (开审查会话: the session that wrote the code may not
        review it), told Lean's review line."""
        repo = self._repo(rid)
        f = await asyncio.to_thread(self._facts, repo, key)
        run = f.get("run") or {}
        if not run or deliveries.on_runner(run) or f.get("merged"):
            raise AppError("只有本地开发中的需求能在这里开会话", "not_local", 409)
        if not f.get("worktree"):
            raise AppError("这台机器上没有检出 %s 的分支：先在开需求里开工" % f.get("branch"), "no_worktree", 409)
        scope = scope_of(repo.id, key)
        async with self._opening.setdefault(scope, asyncio.Lock()):
            mine = self._review_of(scope, (f.get("review") or {}).get("at")) if review else None
            if mine:
                return {"ok": True, "slot": mine["slot"], "url": mine["url"], "said": REVIEW_OPEN}
            return await self._open_local(repo, key, f["branch"], f["worktree"], run, trust=trust, kickoff=kickoff,
                                          review=review)

    def _review_of(self, scope: str, review_at: float | None) -> dict[str, Any] | None:
        """The requirement's review session whose review is not written yet, the one 开审查会话 hands back: the newest
        that KiroCrew still has, whose first message went in, opened after review.md last changed (a review.md written
        since is its review: the code changed after it, and the next review is a new session's)."""
        rows = [session_row(self.sessions, i) for i in self.sessions.roots(key=scope) if i.slot]
        mine = [r for r in rows if r["review"] and r["kind"] == "local" and not r["missing"] and not r["error"]
                and (review_at is None or not r["opened_at"] or r["opened_at"] > review_at)]
        return mine[-1] if mine else None

    async def stop_session(self, rid: str, key: str) -> dict[str, Any]:
        """停止 on the board, once the person confirmed: the turn this requirement's own sessions on this machine are
        working on (its 本机会话 and 审查会话), never another requirement's or the runner's. The session stays in KiroCrew:
        打开会话 opens it to carry on."""
        repo = self._repo(rid)
        scope = scope_of(repo.id, key)
        slots = [i.slot for i in self.sessions.roots(key=scope) if (i.kind or "local") == "local" and i.slot
                 and (self.sessions.slot_stats(i.slot) or {}).get("busy")]
        if not slots:
            raise AppError("%s 在这台机器上没有在做的会话，没有要停的：刷新页面看看" % key, "nothing_to_stop", 409)
        for slot in slots:
            cancelled = [await self.sessions.cancel(i.id) for i in self.sessions.all_agents if i.slot == slot and not i.done]
            if not any(cancelled):   # a turn the app does not follow (typed in KiroCrew after a stop): stop it there
                try:
                    await self.sessions.gw.stop(slot)
                except Exception as exc:  # noqa: BLE001
                    raise AppError("KiroCrew 没停下这个会话（%s）：在 KiroCrew 的会话里点停止" % exc, "stop_failed", 502)
        await asyncio.to_thread(self.store.append, scope, "app", "停止了本机会话", None)
        await self.sessions.save()
        return {"ok": True, "stopped": slots}

    async def _open_local(self, repo: Repo, key: str, branch: str, wt: str, run: dict[str, Any], *, trust: bool,
                          kickoff: bool, review: bool = False, task: str | None = None, fresh: bool = False
                          ) -> dict[str, Any]:
        got = flowview.read(run)
        flow, broken = got["flow"], got["flow_error"]
        why = (broken or flowview.action_problem(flow, "review")) if review else ""
        if why:
            raise AppError(why, "not_this_flow", 409)
        self.crew = await asyncio.to_thread(self.crew_sync)
        why = await asyncio.to_thread(crewcheck.session_problem, self.crew["rows"], wt)
        if why:
            raise AppError(why, "agent_not_ready", 409)
        scope = scope_of(repo.id, key)
        if review:
            task = intake.review_task(key, branch, run.get("intent"))
            if not task:
                raise AppError("这个需求的记录里找不到它在 Lean 里的名字：开不了审查会话", "no_intent", 409)
            title, said, cause = key + REVIEW_TITLE, "开了审查会话", "审查会话的开工消息，只有编号"
        else:
            live = self._live_of(scope)
            if live and not fresh:     # also with a mode brain-kit does not know: its row says to fix it there
                return {"ok": True, "slot": live["slot"], "url": live["url"], "said": live_said(live)}
            if broken:                 # a new session's first message and its clone's Lean setup follow the flow
                raise AppError(broken, "not_this_flow", 409)
            if task is None:     # a small requirement's start brings its own first message (family.kickoff)
                task = intake.kickoff(key, branch, run.get("intent"),
                                      [str(m.get("package")) for m in run.get("materials") or [] if isinstance(m, dict)],
                                      flow.value) if kickoff else ""
                if kickoff and scope_of(repo.id, key) in self._big:
                    task += "\n" + family.BIG_KICKOFF
            title, said, cause = "%s · 本地开发" % key, "开了本机会话", "开工消息，只有编号"
            if fresh:
                task += "\n" + intake.resume_line(flow.value, flowview.decisions_path(flow, run, repo.cfg["runs_dir"]))
                title, said = "%s · 本地开发（重开）" % key, "重开了本机会话"
            try:
                await asyncio.to_thread(bk.m("flows").prepare_clone, flow, wt)
            except bk.brain_error() as exc:
                raise AppError("这个工作区里选不了「不开 Lean intent」（%s）：先更新 kit 文件" % str(exc)[:200],
                               "lean_none", 409)
        slug = branch.rsplit("/", 1)[-1][len(key) + 1:] or key.lower()
        info = await self.sessions.open_session(
            task, parent_session_key="%s:%s" % (self.name, scope), cwd=wt, title=title,
            folder="%s · %s" % (key, slug), agent=crewcheck.crew_agent(), root_folder=LOCAL_FOLDER, project_dir=wt,
            repo_folder=repo.name, strict_agent=True, resend_ok=True, nudge_ok=False, trust_ok=bool(trust), kind="local",
            key=scope, shown={"by": "app", "cause": cause, "text": task} if task else None)   # task: ids only, by its builder
        if info.error or not info.slot:
            raise AppError(info.error or "会话没开成", "session_failed", 502)
        await asyncio.to_thread(self.store.append, scope, "app", said, store_mod.TRUSTED if trust else None)
        await self.sessions.save()
        return {"ok": True, "slot": info.slot, "url": "/chat?sid=%s" % info.slot, "said": ""}

    async def reopen_session(self, rid: str, key: str, replace: Any, trust: bool = True) -> dict[str, Any]:
        """重开一个会话: a new session for the requirement in its worktree, whatever the live one is doing (E2E-12: one
        KiroCrew left stuck after an interrupted turn). The old one stays, its record readable; still busy, it is
        stopped first, so two sessions never work in one worktree. *replace*: the slot the page showed as live; when
        another one is live by now (the same click twice, another tab) that one is the answer and none is opened,
        unless its first message did not go in (that click failed: this one replaces it)."""
        repo = self._repo(rid)
        f = await asyncio.to_thread(self._facts, repo, key)
        run = f.get("run") or {}
        if not run or deliveries.on_runner(run) or f.get("merged"):
            raise AppError("只有本地开发中的需求能在这里开会话", "not_local", 409)
        if not f.get("worktree"):
            raise AppError("这台机器上没有检出 %s 的分支：先在开需求里开工" % f.get("branch"), "no_worktree", 409)
        scope = scope_of(repo.id, key)
        async with self._opening.setdefault(scope, asyncio.Lock()):
            live = self._live_of(scope)
            cur = (live or {}).get("slot")
            if cur and cur != (replace if isinstance(replace, str) and replace else None) and not live["error"]:
                return {"ok": True, "slot": cur, "url": "/chat?sid=%s" % cur, "reopened": False, "said": live_said(live)}
            if cur:
                turns = [i.id for i in self.sessions.all_agents if i.slot == cur and not i.done]
                for t in turns:
                    await self.sessions.cancel(t)     # stops the slot; the turn counts as stopped by the person
                if not turns and (self.sessions.slot_stats(cur) or {}).get("busy"):
                    try:
                        await self.sessions.gw.stop(cur)
                    except Exception as exc:  # noqa: BLE001 - a stuck slot may not stop: the new one opens anyway
                        self.log.info("brain: stopping session %s before reopening: %s", cur, exc)
            got = await self._open_local(repo, key, f["branch"], f["worktree"], run, trust=trust, kickoff=True,
                                         fresh=True)
        return dict(got, reopened=True)

    async def answer(self, rid: str, key: str, choice: Any, note: Any, did: Any = None) -> dict[str, Any]:
        """A clarify question of the person's own run: into its local session, or as their own MR comment. Never a Lean
        approval: a question with a lean_request is not answerable here. *choice*: one of the question's options (A–H),
        or approve / reject for a question without options (brain-kit counts nothing else for one)."""
        if not isinstance(choice, str) or not re.fullmatch(r"(?i)[A-H]|approve|reject", choice.strip()):
            raise AppError("回答选一个选项（A–H）；没有选项的问题答 approve 或 reject", "invalid_choice")
        choice = choice.strip()
        choice = choice.lower() if choice.lower() in ("approve", "reject") else choice.upper()
        note = " ".join(str(note or "").split())[:300]
        repo = self._repo(rid)
        f = await asyncio.to_thread(self._facts, repo, key)
        me = await asyncio.to_thread(self.me_in, repo)
        if not me or me != f.get("owner"):
            raise AppError("只有需求负责人本人能在这里回答（按你在这个仓库的名字认人）", "not_owner", 403)
        run = f.get("run") or {}
        scope = scope_of(rid, key)
        park = f.get("park")
        if not deliveries.on_runner(run):
            if not deliveries.park_waiting(park) or park.get("kind") != "clarify" or park.get("lean_request"):
                raise AppError("这个需求没有等你回答的澄清问题", "no_question", 409)
            if choice not in answer_choices([str(o.get("id")) for o in park.get("options") or [] if isinstance(o, dict)]):
                raise AppError("没有选项 %s" % choice, "invalid_choice")
            live = self._live_of(scope)
            if not live:
                raise AppError("先开这个需求的 brain-dev 会话", "no_session", 409)
            text = local_answer(choice, note)
            shown = {"by": "app", "cause": "澄清问题的回答", "text": local_answer(choice, NOTE_HIDDEN if note else "")}
            turn = await self.sessions.continue_conversation_async(live["id"], text, shown=shown, parent_session_key=self.sessions.get(
                live["id"]).parent_session_key)
            if turn.error:
                raise AppError(turn.error, "send_failed", 502)
            await asyncio.to_thread(self.store.append, scope, "app", "在 app 里回答了澄清问题：%s" % choice, None)
            return {"ok": True, "via": "session", "text": "回答已送进会话"}
        base, project = self._gitlab_of(repo)
        mr = self.mr.get(project, f.get("branch") or "") if base and project else None
        open_q = [q for q in (mr or {}).get("questions") or [] if q.get("state") == "open" and q.get("kind") == "clarify"
                  and not q.get("lean_request") and (not did or q.get("id") == did)]
        if not mr or not open_q:
            raise AppError("MR 上没有等你回答的澄清问题", "no_question", 409)
        q = open_q[-1]
        if choice not in answer_choices([o["id"] for o in q.get("options") or []]):
            raise AppError("没有选项 %s" % choice, "invalid_choice")
        try:
            await asyncio.to_thread(self.mr.post_answer, base, project, mr["iid"], q["id"], choice, note,
                                    transport=self.transport)
        except Exception as exc:  # noqa: BLE001 - GitLab's own words go to the log
            self.log.warning("brain: posting the answer to %s on MR !%s failed: %s", q["id"], mr["iid"], exc)
            raise AppError("没发到 MR 上：GitLab 没收下。看看网络和你的 GitLab 令牌，再点一次；或者直接去 MR 上回答",
                           "post_failed", 502)
        await asyncio.to_thread(self.store.append, scope, "app", "在 MR 上回答了 %s：%s" % (q["id"], choice), None)
        return {"ok": True, "via": "mr", "text": "已经以你的 GitLab 身份发在 MR 上：桥核对后送回运行机"}

    # ---- 开需求 --------------------------------------------------------------------------------------- #

    async def intake_next(self, rid: str) -> dict[str, Any]:
        repo = self._repo(rid)
        return await asyncio.to_thread(lambda: intake.next_key(repo, local_only=self._local_only(repo)))

    async def intake_check(self, body: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo(str(body.get("repo") or ""))
        me = await asyncio.to_thread(self.me_in, repo)
        return scrub_tree(await asyncio.to_thread(intake.check, repo, body, me), self.scrub)

    async def pick(self, kind: Any) -> dict[str, Any]:
        """选文件… / 选文件夹…: this machine's own dialog (picker.pick), in a worker thread until it closes."""
        return {"paths": await asyncio.to_thread(picker.pick, kind)}

    async def intake_start(self, body: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo(str(body.get("repo") or ""))
        try:
            out = await asyncio.to_thread(lambda: intake.start(repo, body, self.settings["work_root"],
                                                               local_only=self._local_only(repo)))
        except intake.IntakeError as exc:
            raise AppError(str(exc), "invalid_intake")
        out = scrub_tree(out, self.scrub)
        if not out["ok"]:
            out["problem"] = out.get("problem") or problems.who_row(out["output"], repo=repo.path)
            if (out["problem"] or {}).get("fix") == "me":
                out["me"] = await asyncio.to_thread(self._me_view, repo)
            return out
        scope = scope_of(repo.id, out["key"])
        await asyncio.to_thread(self.store.append, scope, "app", "在 app 里开工",
                                "工作区 %s，分支 %s" % (out["worktree"], out["branch"]))
        if body.get("big") is True:      # 「大需求：先拆成小需求」: Lean's design splits it into units, then 拆成小需求
            await asyncio.to_thread(self._mark_big, scope)
            await asyncio.to_thread(self.store.append, scope, "app", "大需求：先拆成小需求", None)
        if body.get("open_session", True):
            f = await asyncio.to_thread(self._facts, repo, out["key"])
            try:
                got = await self._open_local(repo, out["key"], out["branch"], out["worktree"], f.get("run") or {},
                                             trust=body.get("trust", True) is not False, kickoff=True)
                out.update(slot=got["slot"], url=got["url"])
            except AppError as exc:
                out["session_error"] = str(exc)
        return out

    # ---- 大需求拆成小需求 -------------------------------------------------------------------------------- #

    def _member(self, rid: str, key: str) -> tuple[Repo, dict[str, Any], dict[str, Any] | None, str | None]:
        """The repo, the requirement's facts, its family (family.families) and who the person is there. Blocking."""
        if rid == "runner":
            raise AppError("运行机上的需求不在这里拆、认领或开工", "no_repo", 404)
        repo = self._repo(rid)
        f = self._facts(repo, key)
        return repo, f, self._families_of(repo).get(key), self.me_in(repo)

    def _after_family(self, repo: Repo) -> None:
        """After an act: this machine's refs read again, and brain-kit's listing made now (the answer and the next
        poll have it). Blocking."""
        repo.invalidate()
        self.families.make_now(repo.id, repo.path)

    async def family_split(self, rid: str, key: str, apply: bool) -> dict[str, Any]:
        """拆成小需求: the preview (apply false: `brain split --dry-run --json`, nothing written), then the split, in the
        big requirement's worktree on this machine; only its owner."""
        if not bk.has("split"):
            raise AppError(family.NO_SPLIT, "no_split", 409)
        repo, f, fam, me = await asyncio.to_thread(self._member, rid, key)
        if (f.get("claim") or {}).get("parent") or (f.get("run") or {}).get("parent"):
            raise AppError("%s 自己就是拆出来的小需求：拆大需求" % key, "is_child", 409)
        act = family.split_act(f, me, repo.cfg.get("tracker") == "none", True)
        if act is None:
            raise AppError("只有需求的负责人本人能拆它（它在这台机器上开工）", "not_owner", 403)
        if not act["ok"] and act["why"] != family.NOT_DESIGNED:
            raise AppError(act["why"], "not_ready", 409)       # without the DESIGN approval brain-kit says why itself
        out = await asyncio.to_thread(family.split, f["worktree"], key, apply)
        if apply and out["ok"]:
            await asyncio.to_thread(self._after_family, repo)
            filed = [a["key"] for a in out["actions"] if a["key"] and a["kind"] in ("new", "update", "same")]
            await asyncio.to_thread(self.store.append, scope_of(rid, key), "app", "拆成小需求：%s" % "、".join(filed[:20]),
                                    out.get("branch"))
        return scrub_tree(out, self.scrub)

    async def family_claim(self, rid: str, key: str, release: bool) -> dict[str, Any]:
        """认领 / 放弃认领 a small requirement `brain split` filed (never any other key: brain-kit would file a new claim
        for it)."""
        if not bk.has("split"):
            raise AppError(family.NO_SPLIT, "no_split", 409)
        repo, f, fam, me = await asyncio.to_thread(self._member, rid, key)
        if not fam or fam["role"] != "child":
            raise AppError("只有拆出来的小需求能在这里认领", "not_child", 409)
        if not me:
            raise AppError("先在设置里填上你在这个仓库里的名字", "no_me", 409)
        if release:
            if fam["owner"] != me:
                raise AppError("%s 不是你认领的" % key if fam["owner"] else "%s 没人认领" % key, "not_owner", 409)
            if fam["state"] != "claimed":
                raise AppError("%s 已经开工了：不能放回" % key, "started", 409)
        elif fam["owner"] and fam["owner"] != me:
            return {"ok": False, "text": "晚了一步：%s 已经被 %s 认领了" % (key, fam["owner"]), "owner": fam["owner"],
                    "problem": problems.mk("FAIL", "晚了一步：%s 已经被 %s 认领了" % (key, fam["owner"]), "换一个待认领的小需求"),
                    "output": ""}
        out = await asyncio.to_thread(family.claim, repo.path, key, release, me)
        await asyncio.to_thread(self._after_family, repo)
        if out["ok"]:
            await asyncio.to_thread(self.store.append, scope_of(rid, key), "app", "放弃认领" if release else "认领了", None)
        return scrub_tree(out, self.scrub)

    async def family_start(self, rid: str, key: str, on: Any, open_session: bool, trust: bool) -> dict[str, Any]:
        """开工 on a small requirement (it claims it on the way): `brain start --key` into a worktree under work_root,
        optionally on its one unmerged dependency's branch (--on); then its brain-dev session there."""
        if not bk.has("split"):
            raise AppError(family.NO_SPLIT, "no_split", 409)
        if on is not None and (not isinstance(on, str) or not KEY_RE.match(on)):
            raise AppError("on 要写它依赖的那个小需求的号", "invalid_on")
        repo, f, fam, me = await asyncio.to_thread(self._member, rid, key)
        if not fam or fam["role"] != "child":
            raise AppError("只有拆出来的小需求能在这里这样开工", "not_child", 409)
        if not me:
            raise AppError("先在设置里填上你在这个仓库里的名字", "no_me", 409)
        if fam["owner"] and fam["owner"] != me:
            raise AppError("%s 是 %s 认领的" % (key, fam["owner"]), "not_owner", 409)
        if fam["state"] not in ("unclaimed", "claimed"):
            raise AppError("%s 已经开工了" % key if fam["state"] == "in_progress" else "%s %s" % (key, fam["state_label"]),
                           "started", 409)
        if on and on not in [d["key"] for d in fam["depends_on"]]:
            raise AppError("%s 不依赖 %s" % (key, on), "invalid_on")
        try:
            wt = family.worktree_for(self.settings["work_root"], repo.name, key, fam.get("unit"))
        except ValueError as exc:
            raise AppError(str(exc), "no_worktree", 409)
        await asyncio.to_thread(os.makedirs, os.path.dirname(wt), exist_ok=True)
        out = await asyncio.to_thread(family.start, repo.path, key, wt, on)
        await asyncio.to_thread(self._after_family, repo)
        if not out["ok"]:
            return scrub_tree(out, self.scrub)
        scope = scope_of(rid, key)
        await asyncio.to_thread(self.store.append, scope, "app", "开工（小需求，单元 %s）" % (fam.get("unit") or "?"),
                                "工作区 %s，分支 %s%s" % (out["worktree"], out["branch"], "，从 %s 的分支" % on if on else ""))
        if open_session and out.get("worktree") and out.get("branch"):
            f2 = await asyncio.to_thread(self._facts, repo, key)
            run = f2.get("run") or {}
            task = family.kickoff(key, out["branch"], run.get("intent"), fam["parent"], fam.get("unit") or "")
            try:
                got = await self._open_local(repo, key, out["branch"], f2.get("worktree") or out["worktree"], run,
                                             trust=trust, kickoff=True, task=task)
                out.update(slot=got["slot"], url=got["url"])
            except AppError as exc:
                out["session_error"] = str(exc)
        return scrub_tree(out, self.scrub)

    async def family_sync(self, rid: str, key: str) -> dict[str, Any]:
        """同步: `brain sync` in a small requirement's worktree (its big one's branch merged in)."""
        if not bk.has("split"):
            raise AppError(family.NO_SPLIT, "no_split", 409)
        repo, f, fam, me = await asyncio.to_thread(self._member, rid, key)
        if not fam or fam["role"] != "child":
            raise AppError("只有拆出来的小需求能同步大需求的分支", "not_child", 409)
        if not me or fam["owner"] != me:
            raise AppError("只有认领它的人能同步它", "not_owner", 403)
        if not f.get("worktree"):
            raise AppError("这台机器上没有它的工作区", "no_worktree", 409)
        out = await asyncio.to_thread(family.sync, f["worktree"])
        await asyncio.to_thread(self._after_family, repo)
        await asyncio.to_thread(self.store.append, scope_of(rid, key), "app", "同步大需求的分支：%s" % out["text"][:80], None)
        return scrub_tree(out, self.scrub)

    # ---- 交接给运行机 --------------------------------------------------------------------------------- #

    async def handoff(self, rid: str, key: str) -> dict[str, Any]:
        """交接给运行机: `brain handoff` in the requirement's worktree on this machine (handover.run), its answer in plain
        words (HandoffResult). Refused in words before anything runs when the app can tell (handover.why_not). One at a
        time per requirement: a second click while it runs gets the same answer."""
        from . import handover
        repo = self._repo(rid)
        scope = scope_of(rid, key)
        running = self._handing.get(scope)
        if running is None:
            async def go() -> dict[str, Any]:
                f = await asyncio.to_thread(self._facts, repo, key)
                base, project = self._gitlab_of(repo)
                branch, bridge = f.get("branch") or "", str(repo.cfg.get("bridge_user") or "") or None

                def mr_now() -> None:     # GitLab's MR now: whether a hand-over made before still waits for its label
                    try:
                        self.mr.refresh(base, project, branch, transport=self.transport, bridge_user=bridge)
                    except Exception as exc:  # noqa: BLE001 - a token file others can read: brain-kit says so itself
                        self.log.info("brain: reading the MR of %s: %s", branch, exc)

                if deliveries.on_runner(f.get("run")) and branch and base and project and self.mr.due(project, branch):
                    await asyncio.to_thread(mr_now)
                why = f.get("flow_error") or flowview.action_problem(f["flow"], "handoff")
                if why:
                    raise AppError(why, "not_this_flow", 409)
                snap = self._sessions_snapshot()
                row = await asyncio.to_thread(self._row, repo, f, snap)
                pushed = await asyncio.to_thread(repo.on_remote, f)
                busy = any(s["busy"] for s in snap["by"].get(scope, []) if s["kind"] == "local" and not s["missing"])
                why = handover.why_not(f, row, me=await asyncio.to_thread(self.me_in, repo),
                                       local_only=row["local_only"], busy=busy, pushed=pushed)
                if why:
                    raise AppError(*why)
                lock = self.data_dir / "handoff" / ("%s.lock" % scope.replace(":", "-"))
                try:
                    with handover.guard(lock) as held:
                        out = await asyncio.to_thread(handover.run, f["worktree"], base=base, project=project,
                                                      hold=held, lock=lock)
                except handover.Busy:
                    ran = await asyncio.to_thread(handover.stop_stale, lock)
                    if ran:
                        raise AppError("上一次点的交接跑了 %d 分钟没做完（Brain 交付重启前开始的），停掉了：再点一次「%s」"
                                       % (ran, handover.BUTTON), "handing_stopped", 409) from None
                    raise AppError("上一次点的交接还在跑（Brain 交付重启前开始的）：等它做完，再刷新这一页看结果；过了 %d 分钟"
                                   "还没做完，再点一次就会停掉它" % (handover.TIMEOUT_S // 60), "handing", 409) from None
                await asyncio.to_thread(repo.invalidate)
                if branch and base and project:
                    await asyncio.to_thread(mr_now)
                await asyncio.to_thread(self.store.append, scope, "app", "交接：%s" % out["text"], None)
                return scrub_tree(out, self.scrub)

            running = self._handing[scope] = asyncio.ensure_future(go())
            running.add_done_callback(lambda _t: self._handing.pop(scope, None))
        return await asyncio.shield(running)

    async def act(self, rid: str, key: str, action: str) -> dict[str, Any]:
        """开 MR (mr) and 转完整流程 (full) of a fast run: `brain mr|full --json` in its worktree on this machine
        (acts.run), refused in words first when the app can tell (acts.why_not). One at a time per requirement: a
        second click while it runs gets the same answer. After 转完整流程 an idle live session is told, in one line
        with the intent's name only, to go on under Lean (told); a busy one is left alone."""
        if action not in acts.COMMAND:
            raise AppError("没有这个动作", "unknown_action", 404)
        repo = self._repo(rid)
        scope = scope_of(rid, key)
        running = self._acting.get(scope)
        if running is None:
            async def go() -> dict[str, Any]:
                f = await asyncio.to_thread(self._facts, repo, key)
                snap = self._sessions_snapshot()
                row = await asyncio.to_thread(self._row, repo, f, snap)
                busy = any(s["busy"] for s in snap["by"].get(scope, []) if s["kind"] == "local" and not s["missing"])
                why = acts.why_not(f, row, action, me=await asyncio.to_thread(self.me_in, repo), busy=busy)
                if why:
                    raise AppError(*why)
                out = await asyncio.to_thread(acts.run, f["worktree"], action)
                await asyncio.to_thread(repo.invalidate)
                base, project = self._gitlab_of(repo)
                if out["ok"] and base and project and f.get("branch"):
                    self._mr_wanted[(base, project, f["branch"])] = (scope, str(repo.cfg.get("bridge_user") or ""))
                    try:
                        await asyncio.to_thread(self.mr.refresh, base, project, f["branch"], transport=self.transport)
                    except Exception as exc:  # noqa: BLE001 - a token file others can read: brain-kit says so itself
                        self.log.info("brain: reading the MR of %s: %s", f["branch"], exc)
                told = False
                live = self._live_of(scope)
                line = intake.switched_line(out["intent"]) if action == "full" and out["ok"] else ""
                if line and live and not live.get("busy"):
                    turn = await self.sessions.continue_conversation_async(
                        live["id"], line, shown={"by": "app", "cause": "转完整流程", "text": line},
                        parent_session_key=self.sessions.get(live["id"]).parent_session_key)
                    told = not turn.error
                by_hand = action == "mr" and out["ok"] and out["pushed"] and not out["mr"]
                await asyncio.to_thread(self.store.append, scope, "app", acts.BY_HAND if by_hand else "%s：%s" % (
                    acts.NAME[action], out["text"] or ("做好了" if out["ok"] else "没做成")), out["text"] if by_hand else None)
                return scrub_tree(dict(out, told=told), self.scrub)

            running = self._acting[scope] = asyncio.ensure_future(go())
            running.add_done_callback(lambda _t: self._acting.pop(scope, None))
        return await asyncio.shield(running)

    # ---- KiroCrew checks ------------------------------------------------------------------------------ #

    async def crew_checks(self) -> dict[str, Any]:
        self.crew = await asyncio.to_thread(self.crew_sync)
        return self.crew

    async def crew_fix(self, what: Any) -> dict[str, Any]:
        if what not in ("agent", "engine"):
            raise AppError("what 只能是 agent 或 engine", "invalid_fix")
        try:
            text = await asyncio.to_thread(crewcheck.fix_agent if what == "agent" else crewcheck.fix_engine)
            ok = True
        except Exception as exc:  # noqa: BLE001
            text, ok = str(exc)[:300], False
        self.crew = await asyncio.to_thread(self.crew_sync)
        return {"ok": ok, "text": text, "rows": self.crew["rows"]}

    # ---- Brain ---------------------------------------------------------------------------------------- #

    def _reflectable(self, repo: Repo) -> list[dict[str, Any]]:
        out = []
        for f in repo.scan():
            t = f.get("decisions_text") or ""
            if "brain-proposal" in t or "brain-gap" in t:
                row = deliveries.base_row(repo.id, f)
                deliveries.classify(row, f, mr=None, slot=None, cfg=repo.cfg, me=self.me_in(repo), now=self.now())
                out.append({"key": f["key"], "title": row["title"], "state": row["state"]})
        return out

    async def brain(self, rid: str) -> dict[str, Any]:
        repo = self._repo(rid)
        return await asyncio.to_thread(lambda: brainview.view(repo, self._reflectable(repo)))

    async def reflect_preview(self, rid: str, key: str) -> dict[str, Any]:
        repo = self._repo(rid)
        f = await asyncio.to_thread(self._facts, repo, key)
        return scrub_tree(await asyncio.to_thread(brainview.preview, repo, f, self._vouchers_dir(repo)), self.scrub)

    async def reflect_apply(self, rid: str, key: str, push: bool) -> dict[str, Any]:
        repo = self._repo(rid)
        f = await asyncio.to_thread(self._facts, repo, key)
        out = scrub_tree(await asyncio.to_thread(brainview.apply, repo, f, push), self.scrub)
        await asyncio.to_thread(self.store.append, scope_of(rid, key), "app",
                                "回写 Brain%s：%s" % (" 并推送" if push else "", "成功" if out["ok"] else "失败"),
                                out.get("branch"))
        return out

    # ---- 待办 ----------------------------------------------------------------------------------------- #

    def _todo_sync(self, snap: dict[str, Any]) -> dict[str, Any]:
        entries, mine = [], []
        token = self._token_present()
        for rid, repo in self.repos.items():
            try:
                facts = repo.scan()
            except Exception:  # noqa: BLE001
                continue
            fams = self._families_of(repo, facts)
            mine += [(rid, f, self._row(repo, f, snap, fams)) for f in facts]
        runner, taken = self._team_split(mine, snap) if self._team_on() else ([], set())
        for rid, f, row in mine:
            repo = self.repos[rid]
            base, project = self._gitlab_of(repo)
            me = self.me_in(repo)
            if (rid, f["key"]) in taken:
                continue    # listed as the runner's
            if (row["state"] == "closed" and not (f.get("claim") or {}).get("closed")) or \
                    (row["state"] in ("merged", "merged_into_parent") and row["waits"]["who"] != "you"):
                # a merge waits for nobody, unless its write-back waits for you; a requirement closed by its claim may
                # have left its run branch or MR behind (todo.closed_leftover)
                continue
            live = self._live(snap["by"].get(scope_of(rid, f["key"]), []))
            own = bool(me) and me == f.get("owner")
            entries.append({"row": row, "facts": f, "own": own, "has_session": bool(live),
                            "can_post": own and bool(base and project) and token,
                            "mr": self.mr.get(project, f.get("branch") or "") if base and project else None,
                            "session": {"slot": live["slot"], "title": live["title"], "url": live["url"]} if live else None,
                            "push_error": self.sync.push_error(repo.path, f)})
        out = todo_mod.build(entries, team=False, crew_rows=self.crew["rows"])
        if self._team_on():
            team_entries = [{"row": row, "decisions": decs, "mr_url": (row.get("mr") or {}).get("url")}
                            for row, _, decs in runner]
            t = todo_mod.build(team_entries, team=True, crew_rows=[])
            out["items"] += t["items"]
            out["team"] = True
        return out

    async def todo(self) -> dict[str, Any]:
        snap = self._sessions_snapshot()
        return scrub_tree(await asyncio.to_thread(self._todo_sync, snap), self.scrub)

    # ---- status, settings ----------------------------------------------------------------------------- #

    def _team_status(self) -> dict[str, Any]:
        if self.team is None:
            on = bool(self.settings["team"].get("enabled"))
            why = problems.team_start("brain-kit 没装载" if on else "")
            return {"enabled": on, "ok": False, "error": why["text"], "error_todo": why["todo"], "error_raw": why["raw"],
                    "last_loop_at": None, "loop_error": "", "checks": [], "same_user": False,
                    "signer": {"pending": 0, "refused": 0}, "relay": None}
        return self.team.status()

    async def status(self) -> dict[str, Any]:
        if self.folders_stuck:   # one folder list: a folder dragged in since, or KiroCrew answering again
            await self.regroup_folders()
        info = bk.info()
        repos = []
        for path, rid in self.repo_ids.of(self.settings["repos"]).items():
            r = self.repos.get(rid)
            if r is not None:
                repos.append({"id": rid, "path": r.path, "name": r.name, "ok": True, "error": "",
                              "kit": importer.kit_view(r.cfg), "pending": None, "me": None,
                              "remote": await asyncio.to_thread(r.has_origin)})
            elif rid in self.repo_pending:
                p, pend = self.repo_pending[rid]
                repos.append({"id": rid, "path": p, "name": os.path.basename(p), "ok": False, "error": "", "kit": None,
                              "pending": pend, "me": None, "remote": await asyncio.to_thread(Git(p).has_origin)})
            else:
                p, err = self.repo_errors.get(rid, (path, "没装载"))
                repos.append({"id": rid, "path": p, "name": os.path.basename(p), "ok": False, "error": err, "kit": None,
                              "pending": None, "me": None,
                              "remote": os.path.isdir(p) and await asyncio.to_thread(Git(p).has_origin)})
        todo = await self.todo()
        token = await asyncio.to_thread(self._token_present)
        loaded = {x["id"]: self.repos[x["id"]] for x in repos if x["ok"] and x["id"] in self.repos}
        mes = await asyncio.to_thread(lambda: {rid: self._me_view(r) for rid, r in loaded.items()})
        fasts = await asyncio.to_thread(lambda: {rid: self._fast_view(r) for rid, r in loaded.items()})
        syncs = await asyncio.to_thread(lambda: {rid: scrub_tree(self.sync.repo_view(r, self._push_ctx(r)), self.scrub)
                                                 for rid, r in loaded.items()})
        for x in repos:
            x["me"] = mes.get(x["id"])
            x["sync"] = syncs.get(x["id"])
            x["fast"] = fasts.get(x["id"])
        return {"app": {"name": self.name, "version": VERSION, "team": self.team is not None,
                        "role": self.settings["role"]},
                "brainkit": {"path": info["path"], "ok": info["ok"], "version": info["version"], "error": info["error"],
                             "modules": info["modules"]},
                "repos": repos, "crew": dict(self.crew, rows=list(self.crew["rows"]) + self.folders_stuck),
                "me": me_line(repos, token, os.environ.get("BRAIN_USER", "").strip().lower()),
                "team": self._team_status(), "counts": {"todo": len(todo["items"])},
                "repo_ids_problem": self.repo_ids.problem}

    def _fast_view(self, repo: Repo) -> dict[str, Any] | None:
        """设置's 「允许快速模式」 of a repo with a remote (RepoInfo.fast; None without one): the default branch's
        fast_mode as this machine last fetched it, why brain-kit offers no fast run there whatever it says, and the
        switch's change not merged yet, read at its open MR's head (else at brain/fast-mode as last fetched). The git
        half is kept while the default branch and that head stay put. Blocking."""
        if not repo.has_origin():
            return None
        bsha = repo.git.out("rev-parse", "-q", "--verify", "refs/remotes/origin/" + kitmr.FAST_BRANCH) or None
        base, project = self._gitlab_of(repo)
        mr = None
        if base and project and (bsha or (base, project, kitmr.FAST_BRANCH) in self._mr_wanted):
            self._mr_wanted[(base, project, kitmr.FAST_BRANCH)] = ("", "")
            mr = self.mr.get(project, kitmr.FAST_BRANCH)
        # an open MR's head as GitLab says it, newer than the last fetch right after a flip; not here yet: unknown
        head = bsha
        if (mr or {}).get("state") == "opened":
            head = mr["sha"] if mrinfo.SHA40_RE.match(str(mr.get("sha") or "")) else None
        k = (repo.path, repo.main_sha(), head)
        got = self._fast_views.get(k)
        if got is None:
            if len(self._fast_views) > 256:
                self._fast_views.clear()
            on, why = kitmr.fast_policy(repo.path, repo.main_ref())
            got = (on, why, kitmr.branch_fast(repo.git, head) if head else None)
            if got[2] is not None or not head:
                self._fast_views[k] = got
        on, why, branch_on = got
        return {"on": on, "why": why, "pending": kitmr.fast_pending(on, branch_on, mr)}

    async def fast_mode(self, body: Any) -> dict[str, Any]:
        """「允许快速模式」 flipped, once the person confirmed: the default branch's kit.json fast_mode through an MR
        (kitmr.fast_mr), and the switch as it is now."""
        body = body if isinstance(body, dict) else {}
        on = body.get("on")
        if not isinstance(on, bool):
            raise AppError("「允许快速模式」只能开或关", "invalid_fast_mode")
        repo = self._repo(str(body.get("repo") or ""))
        view = await asyncio.to_thread(self._fast_view, repo)
        if view is None:
            raise AppError("这个仓库没有远端：团队规则在默认分支上，这里改不了", "no_remote", 409)
        if view["why"]:
            raise AppError(view["why"], "fast_refused", 409)
        base, project = self._gitlab_of(repo)
        try:
            out = await asyncio.to_thread(kitmr.fast_mr, repo.path, str(repo.cfg["default_branch"]), on, base, project,
                                          transport=self.transport)
        except Exception as exc:  # noqa: BLE001 - GitLab down, no rights, a sign-in page: said in words
            raise AppError("GitLab 没开成这个 MR（%s）：看看网络和你的 GitLab 令牌，再点一次" % str(exc).split("\n", 1)[0][:200],
                           "gitlab_failed", 502)
        if out["mr"]:
            self._mr_wanted[(base, project, kitmr.FAST_BRANCH)] = ("", "")
            await asyncio.to_thread(self.mr.refresh, base, project, kitmr.FAST_BRANCH, transport=self.transport)
        out["fast"] = await asyncio.to_thread(self._fast_view, repo)
        return scrub_tree(out, self.scrub)

    def settings_view(self) -> dict[str, Any]:
        return {**self.settings, "team": dict(self.settings["team"]), "auto_push": dict(self.settings["auto_push"])}

    async def save_settings(self, body: Any) -> dict[str, Any]:
        """Check, save atomically, reload. A key the body leaves out keeps its value (the 设置 form sends no repos and no
        role: the list is changed by 导入仓库 and 移出列表 only, the role by its own switch). A team server made
        personal again stops being the team runner: its team mode goes off."""
        if isinstance(body, dict):
            body = {**self.settings_view(), **body}
            if body.get("role") == settings_mod.PERSONAL and self.settings["role"] != settings_mod.PERSONAL \
                    and isinstance(body.get("team"), dict):
                body["team"] = dict(body["team"], enabled=False)
        clean, probs = settings_mod.check(body)
        if probs:
            raise AppError("；".join(probs), "invalid_settings")
        await asyncio.to_thread(settings_mod.save, self.data_dir / "settings.json", clean)
        self.settings = clean
        await self._stop_team()
        await asyncio.to_thread(bk.unload)
        await asyncio.to_thread(self.reload_sync)
        if self.started:
            await self._start_team()
        return {"ok": True, "settings": self.settings_view(), "status": await self.status()}

    # ---- 导入仓库 ------------------------------------------------------------------------------------- #

    @staticmethod
    def _fail(exc: "importer.ImportFail") -> AppError:
        return AppError(str(exc), exc.code, exc.status)

    async def import_defaults(self) -> dict[str, Any]:
        token = await asyncio.to_thread(self._token_present)
        found = await asyncio.to_thread(importer.found_repos, list(self.settings["repos"]), self.settings["work_root"])
        return {"clone_parent": importer.default_parent(self.settings["work_root"]), "work_root": self.settings["work_root"],
                "branch": importer.DEFAULT_BRANCH, "token": token, "brainkit_ok": bk.ok(), "found": found}

    async def import_inspect(self, body: dict[str, Any]) -> dict[str, Any]:
        """检查: read only (see importer.inspect)."""
        token = await asyncio.to_thread(self._token_present)
        listed = self.repo_ids.of(self.settings["repos"])
        got = await asyncio.to_thread(importer.inspect, body.get("path"), listed=listed,
                                      records=self.imports, token=token, who=self.who, team_of=runner_repo_at)
        return scrub_tree(got, self.scrub)

    def _start_job(self, kind: str, target: str, steps: tuple, work: Callable[[Callable[..., None]], Any],
                   after: Callable[[Any], Any] | None = None) -> str:
        """A clone or an install in a worker thread (never on the event loop); its steps and result in self.jobs."""
        try:
            jid = self.jobs.start(kind, target, steps)
        except importer.ImportFail as exc:
            raise self._fail(exc)

        def step(sid: str, state: str, detail: str = "") -> None:
            self.jobs.step(jid, sid, state, detail)

        async def go() -> None:
            try:
                res = await asyncio.to_thread(work, step)
                if after is not None:
                    try:
                        await after(res)
                    except Exception:  # noqa: BLE001 - the job itself is done
                        self.log.exception("brain: after the %s job", kind)
                self.jobs.finish(jid, result=res)
            except importer.ImportFail as exc:
                self.jobs.finish(jid, result=exc.result, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - said on the page; the traceback goes to the log only
                self.log.exception("brain: %s job failed", kind)
                self.jobs.finish(jid, error="出错了：%s" % str(exc).split("\n", 1)[0][:300])

        self.jobs.tasks[jid] = asyncio.get_running_loop().create_task(go())
        return jid

    def import_job(self, jid: str) -> dict[str, Any]:
        got = self.jobs.view(jid)
        if got is None:
            raise AppError("没有这个任务（app 重开过？重新检查一下）", "no_job", 404)
        return scrub_tree(got, self.scrub)

    async def wait_job(self, jid: str) -> dict[str, Any]:
        """The job's end (tests, and any caller that wants it)."""
        task = self.jobs.tasks.get(jid)
        if task is not None:
            await task
        return self.import_job(jid)

    async def import_clone(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            plan = await asyncio.to_thread(importer.clone_plan, body.get("url"), body.get("parent"), body.get("name"),
                                           self.settings["work_root"])
        except importer.ImportFail as exc:
            raise self._fail(exc)
        jid = self._start_job("clone", plan["dest"], importer.CLONE_STEPS, lambda step: importer.clone(plan, step))
        return {"job": self.import_job(jid)}

    async def _import_top(self, raw: Any) -> str:
        """The repo top level a request names (checked like 检查 does)."""
        def find() -> str:
            real = importer.clean_dir(raw)
            try:
                return importer.locate(real)["top"]
            except importer.NotARepo as exc:
                raise importer.ImportFail("%s：%s" % (exc, exc.todo), "not_a_repo")
        try:
            return await asyncio.to_thread(find)
        except importer.ImportFail as exc:
            raise self._fail(exc)

    async def _mode(self, top: str, body: dict[str, Any], purpose: str) -> str:
        """The mode a request asks for ("inplace" | "branch"); none given: the recommended one (importer.choices). In
        place when it cannot be done is refused here, before any job starts, with the one line why."""
        mode = body.get("mode")
        if mode is not None and mode not in ("inplace", "branch"):
            raise AppError("mode 只能是 inplace（直接装在当前分支）或 branch（装到新分支）", "invalid_mode")
        if mode == "branch":
            return "branch"
        cfg = await asyncio.to_thread(installer._kit_of, top) if purpose == "upgrade" else None
        got = await asyncio.to_thread(lambda: importer.choices(top, purpose=purpose, cfg=cfg))
        if mode is None:
            return str(got["recommend"])
        opt = got["options"][0]
        if not opt["ok"]:
            raise AppError("不能直接装：%s。可以装到新分支" % opt["why"], "inplace_refused", 409)
        return "inplace"

    async def me_name(self, body: dict[str, Any]) -> dict[str, Any]:
        """「你的 GitLab 用户名」 (or 「你的名字」): git config brain.user <name> in the repo at body.path (identity.set_name:
        brain-kit's USER_RE, no shell). Answers who brain-kit says the person is there now, and the status."""
        top = await self._import_top(body.get("path"))
        try:
            name = await asyncio.to_thread(identity.set_name, top, body.get("name"))
        except identity.NameRefused as exc:
            raise AppError(str(exc), "invalid_name")
        rid = importer.listed_id(top, self.repo_ids.of(self.settings["repos"]))
        repo = self.repos.get(rid or "")
        if repo is not None:
            me = await asyncio.to_thread(self._me_view, repo)
        else:
            base = await asyncio.to_thread(lambda: ((installer._kit_of(top) or {}).get("gitlab") or {}).get("base") or "")
            me = await asyncio.to_thread(self.who.of, top, base)
        self.log.info("brain: git config brain.user set in %s", top)
        return {"ok": True, "text": "你在这个仓库的名字设成了 %s" % name, "raw": "git config brain.user %s" % name,
                "me": me, "status": await self.status()}

    async def import_choices(self, body: dict[str, Any]) -> dict[str, Any]:
        """Where an install (or a kit update, purpose "upgrade") goes: in place or a new branch (importer.choices)."""
        top = await self._import_top(body.get("path"))
        purpose = body.get("purpose", "install")
        if purpose not in installer.PURPOSES:
            raise AppError("purpose 只能是 install 或 upgrade", "invalid_purpose")
        cfg = await asyncio.to_thread(installer._kit_of, top) if purpose == "upgrade" else None
        return scrub_tree(await asyncio.to_thread(lambda: importer.choices(top, purpose=purpose, cfg=cfg)), self.scrub)

    async def import_install(self, body: dict[str, Any]) -> dict[str, Any]:
        """安装: in place on the checkout's branch (the default, inplace.install) or on a new branch for an MR
        (installer.install), in the background; the page follows the job."""
        top = await self._import_top(body.get("path"))
        try:
            kit = installer.check_kit(body.get("kit"))
            mode = await self._mode(top, body, "install")
            branch = await asyncio.to_thread(installer.check_branch, body.get("branch")) if mode == "branch" else ""
        except importer.ImportFail as exc:
            raise self._fail(exc)
        work_root = self.settings["work_root"]

        async def after(res: Any) -> None:
            if top in self.settings["repos"]:   # a listed repo that was not installed: usable now, or pending
                await asyncio.to_thread(self.reload_sync)
            elif ((res or {}).get("commit") or {}).get("ok"):   # listed even when the page was closed (SETUP-18)
                await self.import_add({"path": top})

        if mode == "inplace":
            jid = self._start_job("install", top, inplace.INSTALL_STEPS,
                                  lambda step: inplace.install(top, kit, self.imports, step), after)
        else:
            jid = self._start_job("install", top, installer.INSTALL_STEPS,
                                  lambda step: installer.install(top, kit, branch, work_root, self.imports, step), after)
        return {"job": self.import_job(jid)}

    def _purpose(self, body: dict[str, Any]) -> tuple[str, "importer.Records"]:
        """purpose "install" (the default: imports.json) or "upgrade" (the kit fix: upgrades.json)."""
        purpose = body.get("purpose", "install")
        if purpose not in installer.PURPOSES:
            raise AppError("purpose 只能是 install 或 upgrade", "invalid_purpose")
        return purpose, (self.upgrades if purpose == "upgrade" else self.imports)

    async def _after_import(self, top: str, purpose: str) -> None:
        """After a commit or a push: a listed repo whose install moved is read again (the KiroCrew checks with it);
        else the KiroCrew checks again, so a row's state is fresh."""
        if purpose == "install" and top in self.settings["repos"]:
            await asyncio.to_thread(self.reload_sync)
        else:
            self.crew = await asyncio.to_thread(self.crew_sync)

    async def import_upgrade(self, body: dict[str, Any]) -> dict[str, Any]:
        """The 'kit' fix of a KiroCrew check row: brain install again, in place on the checkout's branch (the default,
        inplace.install with purpose "upgrade") or on a new branch (installer.upgrade), in the background; the page
        follows the job, then commits again, pushes, or publishes with purpose "upgrade"."""
        top = await self._import_top(body.get("path"))
        try:
            mode = await self._mode(top, body, "upgrade")
            branch = (await asyncio.to_thread(installer.check_branch, body.get("branch"), installer.UPGRADE_BRANCH)
                      if mode == "branch" else "")
        except importer.ImportFail as exc:
            raise self._fail(exc)
        work_root = self.settings["work_root"]
        token = await asyncio.to_thread(self._token_present)

        async def after(_res: Any) -> None:
            self.crew = await asyncio.to_thread(self.crew_sync)

        if mode == "inplace":
            jid = self._start_job("upgrade", top, inplace.UPGRADE_STEPS,
                                  lambda step: inplace.install(top, None, self.upgrades, step, purpose="upgrade",
                                                               token=token), after)
        else:
            jid = self._start_job("upgrade", top, installer.UPGRADE_STEPS,
                                  lambda step: installer.upgrade(top, branch, work_root, self.upgrades, step, token=token),
                                  after)
        return {"job": self.import_job(jid)}

    async def import_commit(self, body: dict[str, Any]) -> dict[str, Any]:
        """再提交一次: the install's (or upgrade's) commit after a hook said no (the hooks run again)."""
        top = await self._import_top(body.get("path"))
        purpose, records = self._purpose(body)
        rec = await asyncio.to_thread(records.get, top)
        mod = inplace if (rec or {}).get("mode") == "inplace" else installer
        try:
            got = await asyncio.to_thread(lambda: mod.commit(top, records, purpose=purpose))
        except importer.ImportFail as exc:
            raise self._fail(exc)
        if got["ok"]:
            await self._after_import(top, purpose)
        return scrub_tree(got, self.scrub)

    async def import_merge(self, body: dict[str, Any]) -> dict[str, Any]:
        """「合并到 <default>」: a branch install fast-forwarded into the checkout (landing.merge); a repo with a remote
        only with confirm true."""
        top = await self._import_top(body.get("path"))
        purpose, records = self._purpose(body)
        try:
            got = await asyncio.to_thread(lambda: landing.merge(top, records, confirm=body.get("confirm") is True))
        except importer.ImportFail as exc:
            raise self._fail(exc)
        await self._after_land(top)
        return scrub_tree(got, self.scrub)

    async def import_pull(self, body: dict[str, Any]) -> dict[str, Any]:
        """「拉取」: git pull --ff-only of the default branch, after the MR was merged (landing.pull)."""
        top = await self._import_top(body.get("path"))
        try:
            got = await asyncio.to_thread(landing.pull, top, self.imports)
        except importer.ImportFail as exc:
            raise self._fail(exc)
        if got["ok"]:
            await self._after_land(top)
        return scrub_tree(got, self.scrub)

    async def import_push(self, body: dict[str, Any]) -> dict[str, Any]:
        """推送 after an in-place install or a local merge: the checkout's branch to origin, only as confirmed."""
        top = await self._import_top(body.get("path"))
        confirm = {k: body.get(k) for k in ("branch", "remote_url")}
        try:
            got = await asyncio.to_thread(inplace.push, top, confirm)
        except importer.ImportFail as exc:
            raise self._fail(exc)
        return scrub_tree(got, self.scrub)

    async def _after_land(self, top: str) -> None:
        """After a merge or a pull: the list read again when the repo is listed, else the KiroCrew checks."""
        if top in self.settings["repos"]:
            await asyncio.to_thread(self.reload_sync)
        else:
            self.crew = await asyncio.to_thread(self.crew_sync)

    async def import_publish(self, body: dict[str, Any]) -> dict[str, Any]:
        """推送并开 MR: only with the branch, remote and target the person confirmed on the page."""
        top = await self._import_top(body.get("path"))
        purpose, records = self._purpose(body)
        confirm = {k: body.get(k) for k in ("branch", "target", "remote_url")}
        try:
            got = await asyncio.to_thread(lambda: installer.publish(top, records, confirm, transport=self.transport,
                                                                    purpose=purpose))
        except importer.ImportFail as exc:
            raise self._fail(exc)
        await self._after_import(top, purpose)
        return scrub_tree(got, self.scrub)

    async def import_add(self, body: dict[str, Any]) -> dict[str, Any]:
        """加入列表: through save_settings (checked, saved atomically, reloaded). A repo whose brain-kit is only on the
        install branch is added too and shows as pending; one without brain-kit anywhere is refused."""
        top = await self._import_top(body.get("path"))
        rid = importer.listed_id(top, self.repo_ids.of(self.settings["repos"]))
        if rid:
            return {"ok": True, "id": rid, "text": "已经在列表里了", "status": await self.status()}
        token = await asyncio.to_thread(self._token_present)

        def state() -> tuple[bool, dict[str, Any] | None, str]:
            if os.path.isfile(os.path.join(top, importer.KIT)):
                if not bk.ok():
                    return False, None, "brain-kit 没装载：先在设置里写对它的位置"
                try:
                    bk.m("config").load(top)
                except Exception as exc:  # noqa: BLE001
                    return False, None, "这个仓库的 brain-kit 设置读不懂：在「导入仓库」里检查它，那里会说哪里不对"
                return True, None, ""
            pend = importer.pending_state(top, self.imports.get(top), token=token)
            return (True, pend, "") if pend else (False, None, "这个仓库还没装 brain-kit：先检查它，装上再加入")

        ok, pend, why = await asyncio.to_thread(state)
        if not ok:
            raise AppError(why, "not_installed", 409)
        if len(self.settings["repos"]) >= settings_mod.MAX_REPOS:
            raise AppError("最多 %d 个仓库：先移出一个" % settings_mod.MAX_REPOS, "too_many", 409)
        saved = await self.save_settings({**self.settings_view(), "repos": list(self.settings["repos"]) + [top]})
        rid = importer.listed_id(top, self.repo_ids.of(self.settings["repos"])) or ""
        text = "加入了（%s）" % rid + ("：%s" % pend["text"] if pend else "")
        return {"ok": True, "id": rid, "text": text, "status": saved["status"]}

    async def repo_ids_rebuild(self) -> dict[str, Any]:
        """按记录重建编号: a damaged repo_ids.json without a good copy, rebuilt from the ids the records on disk carry
        (repoids.from_records); the repos are loaded with them."""
        if not self.repo_ids.problem:
            raise AppError("仓库编号的记录是好的：不用重建", "ids_fine", 409)
        listed = list(self.settings["repos"])
        found = await asyncio.to_thread(self._recorded_ids)
        keys_of = await asyncio.to_thread(lambda: {p: self._keys_at(p) for p in listed})
        ids, n, fresh = from_records(listed, keys_of, found)
        await asyncio.to_thread(self.repo_ids.rebuild, ids, n)
        await asyncio.to_thread(self.reload_sync)
        kept = "、".join(os.path.basename(os.path.normpath(p)) for p in listed if p not in fresh)
        new = "、".join(os.path.basename(os.path.normpath(p)) for p in fresh)
        text = "按记录重建了编号" + ("：%s 接上了原来的记录" % kept if kept else "")
        if new:
            text += "；%s 在这里没有能认准的记录，给了新编号（brain-kit 的记录都在，只是这里以前的会话和时间线不显示）" % new
        return {"ok": True, "text": text, "status": await self.status()}

    def _recorded_ids(self) -> dict[str, set[str]]:
        """{repo id: requirement keys} of the records this app keeps by scope: events, hand-over locks, sessions,
        big.json. Blocking."""
        scopes = [f.stem for d in ("events", "handoff") if (self.data_dir / d).is_dir()
                  for f in (self.data_dir / d).iterdir() if f.is_file()]
        scopes += [i.key for i in self.sessions.roots() if i.key] + sorted(self._big)
        found: dict[str, set[str]] = {}
        for s in scopes:
            m = RECORD_SCOPE_RE.match(str(s))
            if m:
                found.setdefault(m.group(1), set()).add(m.group(2))
        return found

    @staticmethod
    def _keys_at(path: str) -> set[str] | None:
        """The requirement keys of the repo at *path* (claims, run branches, merged runs); None: it cannot be read."""
        try:
            return {f["key"] for f in Repo("r0", path, bk.m("config").load(path)).scan()}
        except Exception:  # noqa: BLE001 - a repo that cannot be read keeps no id (from_records)
            return None

    async def repo_remove(self, body: dict[str, Any]) -> dict[str, Any]:
        """移出列表: the app's list only. No file, branch or worktree is touched; a folder that is gone can be removed."""
        raw = body.get("path")
        if not isinstance(raw, str) or not raw.strip() or importer.CTRL_RE.search(raw):
            raise AppError("写要移出的仓库目录", "invalid_path")
        p = os.path.expanduser(raw.strip())
        if not os.path.isabs(p):
            raise AppError("仓库目录要写绝对路径", "invalid_path")
        real = os.path.realpath(p)
        repos = list(self.settings["repos"])
        hit = next((r for r in repos if r == p or r == real or os.path.realpath(r) == real), None)
        if hit is None:
            raise AppError("列表里没有这个仓库：%s" % raw.strip(), "not_listed", 404)
        saved = await self.save_settings({**self.settings_view(), "repos": [r for r in repos if r != hit]})
        return {"ok": True, "text": "从列表里移出了 %s（文件、分支都没动）" % os.path.basename(hit), "status": saved["status"]}

    # ---- team mode ------------------------------------------------------------------------------------ #

    async def team_view(self) -> dict[str, Any]:
        if self.team is None:
            return {"status": self._team_status(), "ledger": {"runs": {}, "decisions": {}, "handoffs": {}, "outbox": {}},
                    "runs": [], "handovers": [], "signer": {"pending": [], "refused": [], "waiting": []}, "config": [],
                    "hooks": []}
        led = await asyncio.to_thread(self.team.ledger)
        signer = await asyncio.to_thread(self.team.signer_view)
        signer["waiting"] = await asyncio.to_thread(self.team.pending)
        # the signer's reasons and a round's errors in short Chinese (problems.reason), their own words beside them
        signer["refused"] = [dict(x, reasons=[problems.reason(r)["text"] for r in x.get("reasons") or []],
                                  raw=[str(r) for r in x.get("reasons") or []]) for x in signer.get("refused") or []]
        handovers = []
        for h in self.team.handovers():
            row = {k: h[k] for k in ("project", "iid", "sha", "result", "error", "at", "mr_url")}
            got = problems.reason(row["error"]) if row["error"] else None
            row.update(error=got["text"] if got else "", raw=(got["raw"] or "") if got else "")
            handovers.append(row)
        notes = deliveries.notices_by_run(led.get("notices") or [], led.get("outbox") or [])
        return {"status": self.team.status(), "ledger": self.team.counts(led),
                "runs": [{"run_id": r["run_id"], "req": r["req"], "project": r["project"], "iid": int(r["iid"]),
                          "state": r["state"], "slot": r.get("slot"), "mr_url": self.team.mr_url(r["project"], r["iid"]),
                          "updated": r.get("updated"), "notes": notes.get(r["run_id"], [])} for r in led["runs"]],
                "handovers": handovers, "signer": signer, "config": self.team.config_view(),
                "hooks": await asyncio.to_thread(self.team.hook_rows)}

    async def runner_status(self) -> dict[str, Any]:
        """GET /team/status: each listed repo's team runner as GitLab shows it (runner_view.py), read with this person's
        own token: 运行机 on a personal install."""
        repos = list(self.repos.values())

        def view() -> dict[str, Any]:
            rows = [runner_view.Listed(r.id, r.name, r.cfg, *self._gitlab_of(r), self.me_in(r)) for r in repos]
            return self.runners.view(rows, now=self.now(), transport=self.transport)
        return scrub_tree(await asyncio.to_thread(view), self.scrub)

    async def team_patrol(self) -> dict[str, Any]:
        """GET /team/patrol: MR 巡检's last round, every open MR it looked at (team/patrol.py)."""
        if self.team is None:
            return patrol_mod.view_of([], [], None)
        return scrub_tree(self.team.patrol_view(), self.scrub)

    async def team_staging(self) -> dict[str, Any]:
        """GET /team/staging: 测试环境报错's last round, every error signature it holds (team/staging.py)."""
        if self.team is None:
            return staging_mod.view_of([], [], {}, None)
        return scrub_tree(self.team.staging_view(), self.scrub)

    async def team_issues(self) -> dict[str, Any]:
        """GET /team/issues: the issues labelled for Brain that the last round looked at (team/issues.py)."""
        if self.team is None:
            return issues_mod.view_of([], [], None)
        return scrub_tree(self.team.issues_view(), self.scrub)

    async def team_loop(self) -> dict[str, Any]:
        rt = self.team
        if rt is None or rt.host is None:
            raise AppError((problems.team_start(rt.error)["text"] if rt else "") or "运行机模式没打开", "team_off", 409)
        try:
            summary = await self._in_team(lambda: self._team_round(rt))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "summary": {}, "error": str(exc)[:300]}
        return {"ok": not rt.loop_error, "summary": summary, "error": rt.loop_error}

    # ---- 一键建运行机 ---------------------------------------------------------------------------------- #

    def _setup_paths(self) -> "setup_mod.Paths":
        return setup_mod.Paths.default(kit_dir=bk.root())

    async def team_setup(self, body: Any) -> dict[str, Any]:
        """POST /team/setup: team/setup.py's steps on a worker thread (they wait on GitLab, git and brain-kit for longer
        than a request may take); the page polls GET /team/setup. The person's token stays in this run's memory. Only
        on the team server: a personal install never becomes the team runner by itself."""
        if self.settings["role"] != settings_mod.TEAM_SERVER:
            raise AppError("这台机器是个人电脑，不建运行机：团队运行机装在团队的服务器上。这台就是那台服务器的话，先在设置最下面勾上"
                           "「%s」" % settings_mod.ROLE_SWITCH, "not_team_server", 409)
        try:
            inp = setup_mod.SetupInput.parse(body)
        except setup_mod.SetupRefused as exc:
            raise AppError(str(exc), "invalid_setup")
        if not bk.ok():
            raise AppError("brain-kit 没装载：先在设置里写对 brain-kit 的位置", "no_brainkit", 409)
        paths = self._setup_paths()
        loop = asyncio.get_running_loop()
        rest, transport = bk.m("rest"), self.transport      # brain-kit before the root's lock: a failure holds nothing

        def team_mode(want: dict[str, Any]) -> dict[str, Any]:
            return asyncio.run_coroutine_threadsafe(self._setup_team_mode(want), loop).result(600)

        ctx = setup_mod.Ctx(inp, paths, gitlab=lambda tok: rest.GitLab(inp.gitlab_base, tok, transport),
                             team_mode=team_mode, stop=KiroCrewAdapter(loop, self.sessions, bk.brain_error()).stop)
        try:
            lock = await asyncio.to_thread(setup_mod.Lock, paths.root)
        except setup_mod.Busy as exc:
            raise AppError(str(exc), "setup_busy", 409)

        async def go() -> None:
            try:
                await asyncio.to_thread(setup_mod.run, ctx)
            except Exception:  # noqa: BLE001 - each step's failure is in the state; this is a bug: the log has it
                self.log.exception("brain: 一键建运行机 stopped")
            finally:
                lock.release()

        try:            # until the run's task holds it, the lock is this request's to give back
            await asyncio.to_thread(setup_mod.begin, ctx)
            self._setup_task = loop.create_task(go())
        except BaseException:
            lock.release()
            raise
        return await self.team_setup_view()

    async def wait_setup(self) -> dict[str, Any]:
        """The end of the run in progress (tests, and any caller that wants it)."""
        if self._setup_task is not None:
            await self._setup_task
        return await self.team_setup_view()

    async def team_setup_view(self) -> dict[str, Any]:
        """GET /team/setup: the steps and the last run's results (setup-state.json, nothing secret)."""
        paths = self._setup_paths()
        state = await asyncio.to_thread(setup_mod.load_state, paths)
        running = await asyncio.to_thread(setup_mod.busy, paths.root)
        return scrub_tree(setup_mod.view(state, paths, running=running), self.scrub)

    async def _setup_team_mode(self, want: dict[str, Any]) -> dict[str, Any]:
        """一键建运行机's last step: team mode on with its configs through the settings (its TeamRuntime starts, and the
        signer with it). Started again unless it runs well with these settings and every file it read at its start is
        still the same (TeamRuntime.changed): a token or config an earlier run replaced and stopped before this step
        is read then too."""
        rt = self.team
        fine = rt is not None and rt.host is not None and not rt.error and not await asyncio.to_thread(rt.changed)
        if not fine or self.settings["team"] != want:
            await self.save_settings({"team": want})
        return self._team_status()



def runner_repo(cfg: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    """A repo handed to the team runner: its kit.json names bridge_user, or one of its requirements has a hand-over
    (run.json handoff). The checks that matter only to the runner (the hook adapter's version, Lean counting runs/**
    as code) FAIL for it; for a local-only repo they are a reminder."""
    if str(cfg.get("bridge_user") or "").strip():
        return True
    return any((f.get("run") or {}).get("handoff") for f in facts if isinstance(f, dict))


def runner_repo_at(path: str, cfg: dict[str, Any]) -> bool:
    """runner_repo for a repo that is not listed (导入仓库's 检查): its requirements read here (blocking)."""
    if str(cfg.get("bridge_user") or "").strip():
        return True
    try:
        return runner_repo(cfg, Repo("import", path, cfg).scan())
    except Exception:  # noqa: BLE001 - not known: the checks say it as brain-kit does (FAIL)
        return True


def answer_choices(options: list[str]) -> set[str]:
    """What counts as an answer (brain-kit's check_answer): one of the options; approve / reject when there are none."""
    return set(options) if options else {"approve", "reject"}


NOTE_HIDDEN = "（你写的补充，没在这里显示）"   # the 产出 drawer's copy of an answer: the person's note never travels


def local_answer(choice: str, note: str) -> str:
    """The person's answer to their local agent's clarify question: the choice and a neutralized one-line note (a line
    that reads like an approval or an instruction is quoted, as brain-kit's bridge does)."""
    n = note
    try:
        neutral = bk.m("bridge.decisions")._neutral_note
        n = neutral(note)
    except Exception:  # noqa: BLE001
        if re.match(r"(?i)^\s*(approve|approved|批准|同意|reject|驳回)\b", n):
            n = "> " + n
    lines = ["（本地）澄清问题已答：%s。" % choice]
    if n:
        lines.append("说明：%s" % n)
    lines.append("把 park.json 的 status 改成 answered，按这个回答继续。")
    return "\n".join(lines)


def threading_start(fn: Callable[..., Any], *args: Any) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="brain-eval").start()


def get_or_build(ctx: Any) -> Service:
    name = getattr(ctx, "name", APP_NAME) or APP_NAME
    svc = _REGISTRY.get(name)
    if svc is None:
        svc = Service(ctx)
        _REGISTRY[name] = svc
    elif ctx is not svc.ctx:
        svc.ctx = ctx
    return svc


def current(name: str = APP_NAME) -> Service | None:
    return _REGISTRY.get(name)


def discard(name: str = APP_NAME) -> Service | None:
    return _REGISTRY.pop(name, None)

