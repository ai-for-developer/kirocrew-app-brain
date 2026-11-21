"""Team mode: brain-kit's bridge (poll → dispatch → watch → collect → reconcile) and runnerd, hosted by the app.

Everything is brain-kit's: bridge.json is read by its ``load_bridge_config``, every check (``Bridge.verify``), the
contract and answer vouchers, the questions on the MR and what counts as an answer (``decisions.check_answer``), the
admission limits, the ledger, runnerd's workspace, and signing through a signer object. The bridge runs with
brain-kit's ``sign.SpoolSigner``, the one bridge.json names (``"signer": {"type": "spool", "spool_dir",
"allowed_signers"}``), and no key: the separate signer (signer/brain_signer.py, another OS user) holds it, checks
every request again and answers through the spool; brain-kit's bridge itself rejects the hand-over on a refusal (or
marks the answer ``refused``) and tries again next round when the signer does not answer. Nothing of brain-kit is
patched or subclassed. What the app changes:
- sessions: ``kc`` is the app's KiroCrewAdapter (crewadapter.py), not brain-kit's HTTP client;
- vouchers: brain-kit writes the app's copy to bridge.json ``vouchers_dir``; ``publish_vouchers`` is forced off
  (brain-intake gets its copy from the signer, its only writer);
- bridge.json must not name a ``signing_key``; ``kirocrew.base/app/secret_file`` are not used (the app talks to its
  own gateway);
- poll: the app runs ``Bridge.poll``'s loop itself (every project, every MR with the hand-over label,
  ``Bridge.process``) to keep what process() said for each MR for the page: accepted, rejected, seen (that head and
  label event were checked before), and brain-kit 72b278e's unlabelled (the newest event on the label takes it off:
  `brain handoff` re-adding it right now) and waiting (this very hand-over is being signed elsewhere: looked at again
  next round). brain-kit's poll returns nothing (BRAINKIT_REQUESTS 27). A project whose kit.json work_sources leaves
  mr-handoff out (sources.py) is not polled, and one that cannot be read holds up none after it: the status' checks
  say which;
- patrol: a round ends with MR 巡检 (team/patrol.py, work source mr-patrol), on the same worker thread, then
  测试环境报错 (team/staging.py, work source staging-errors). MR 巡检 reads the logs of Jenkins builds with
  bridge.json's optional "jenkins" (team/jenkins.py), a block brain-kit leaves alone.

The ledger is brain-kit's ``Ledger``: one sqlite connection any thread may use, ``ledger.lock`` around several
statements. Rounds run one at a time on the runtime's own worker thread (a round may wait on GitLab, KiroCrew and the
signer for a while: it stays off the event loop and the default pool); the page reads the same Ledger from any thread:
its runs (brain-kit 9935b11: a run id reserved while its contract is signed is in state ``signing``), decisions and
hand-overs, and the vouchers the signer has not answered yet (``pending_vouchers``: the next round asks for the very
same voucher, so a late signature is used).
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sqlite3
import stat
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any

from .. import bk, deliveries, lean_view, problems, snapshot
from ..constants import APP_ROOT, PYTHON_BIN, VERSION
from ..repo import Git, Source, read_claim, read_facts
from ..scrub import scrub_tree
from . import patrol as patrol_mod
from . import relay as relay_mod
from . import signerproc, sources
from . import staging as staging_mod
from .crewadapter import KiroCrewAdapter
from .jenkins import Jenkins

REQ_RE = re.compile(r"^[a-z]+-[A-Za-z0-9-]{1,80}-[0-9a-f]{12}\.json$")
MAX_SPOOL_FILE = 64 * 1024
PENDING_WINDOW_S = 3600   # brain-kit's Ledger.pending_voucher(newer_than=3600): older ones are not asked for again
LEDGER_TABLES = ("runs", "decisions", "handoffs", "outbox", "notices")
# what the page shows of bridge.json and runnerd.json (brain-kit 0d52e97 reads each where it is used; a value that is
# not a positive number falls back to its default, sign.seconds): (key, default, unit, one plain line)
BRIDGE_KEYS = (
    ("interval", 60, "s", "桥每隔这么久跑一轮"),
    ("send_give_up_minutes", 60, "min", "接手消息送了 3 次都没进运行机的会话，过了这么久就算这次运行没能开始（回答和通知一直送，不放弃）"),
    ("runnerd_wait_minutes", 90, "min", "运行机这么久没准备好工作区、也没报错，就在 MR 上说一声（桥接着等）"),
    ("acceptance_ci_wait_minutes", 60, "min", "验收问题等流水线通过才发：流水线这么久还没跑完，就在 MR 上说一声"),
    ("edit_check_rounds", 3, "n", "查不到回复有没有被别人改过：连着这么多轮，就在 MR 上说一声（回答先等着，不算也不丢）"),
    ("edit_check_wait_minutes", 30, "min", "……或者从第一次查不到起过了这么久"),
)
RUNNERD_KEYS = (
    ("kit_dir", "app 用的这份 brain-kit", "path", "运行机用哪份 brain-kit 的 kit/：每建一个工作区前查一次，它带的 Lean 适配文件要是第 3 版"),
    ("agent_spec", "不查", "path", "brain-run 的设置：查它带着运行机那份 Lean 适配文件的三个拦截（调用工具前那个的匹配要是 *）"),
    ("hook_path", "/usr/bin:/bin", "path", "运行机上 Lean 的拦截找 git 和 ssh-keygen 用的 PATH"),
    ("git_timeout", 300, "s", "每次 fetch（和拉 brain-intake）最多等这么久"),
    ("clone_timeout", 3600, "s", "第一次克隆最多等这么久"),
    ("tries", 3, "n", "建工作区失败了，最多试这么多次"),
)


def load_config(path: str) -> dict[str, Any]:
    """bridge.json through brain-kit's own loader (required keys, the signer block), then the app's rules: a spool
    signer, no signing key, vouchers not published from here."""
    common = bk.m("common")
    cfg = bk.m("bridge.handoff").load_bridge_config(path)
    if cfg.get("signing_key"):
        raise common.BrainError("bridge.json 不能写 signing_key：私钥只给签名服务（signer/），桥用 \"signer\": "
                                "{\"type\": \"spool\", …} 请签名服务签")
    s = cfg.get("signer")
    if not isinstance(s, dict) or s.get("type") != "spool":
        raise common.BrainError("bridge.json 要有 \"signer\": {\"type\": \"spool\", \"spool_dir\": …, "
                                "\"allowed_signers\": …}：这个 app 只通过签名服务签名")
    cfg["publish_vouchers"] = False
    return cfg


def start_inputs(team: dict[str, Any]) -> list[str]:
    """What a TeamRuntime reads when it starts: bridge.json, runnerd.json, and the files bridge.json names (its GitLab
    token, the identities, the allowed signers, Jenkins' token); with it the signer.json 一键建运行机 wrote beside
    bridge.json."""
    try:
        with open(team["bridge_config"], encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (KeyError, OSError, ValueError):
        cfg = None
    cfg = cfg if isinstance(cfg, dict) else {}
    gitlab = cfg.get("gitlab") if isinstance(cfg.get("gitlab"), dict) else {}
    signer = cfg.get("signer") if isinstance(cfg.get("signer"), dict) else {}
    jenkins = cfg.get("jenkins") if isinstance(cfg.get("jenkins"), dict) else {}
    status = cfg.get("team_status") if isinstance(cfg.get("team_status"), dict) else {}
    named = [team.get("bridge_config"), team.get("runnerd_config"), gitlab.get("token_file"), cfg.get("identities"),
             signer.get("allowed_signers"), signerproc.config_for(team), jenkins.get("token_file"),
             status.get("token_file")]
    return [os.path.expanduser(str(p)) for p in named if p]


class TeamRuntime:
    """The hosted bridge + runnerd of one enable of the app. ``start`` builds brain-kit's Bridge with the spool
    signer; ``loop_once`` runs one round; ``stop`` closes the ledger after the round in progress."""

    def __init__(self, team: dict[str, Any], loop: Any, mgr: Any, log: Any, *, transport: Any = None,
                 kick: Any = None, sign_timeout: float | None = None, scrub: Any = None) -> None:
        self.team, self.loop, self.mgr, self.log = team, loop, mgr, log
        self.scrub = scrub or (lambda s: s)
        # kick (tests): run the signer while the SpoolSigner waits; sign_timeout: instead of bridge.json's
        self.transport, self.kick, self.sign_timeout = transport, kick, sign_timeout
        self.host: Any = None
        self.patrol: patrol_mod.Patrol | None = None
        self.staging: staging_mod.StagingWatch | None = None
        self.cfg: dict[str, Any] = {}
        self.runnerd_cfg: dict[str, Any] | None = None
        self.spool = ""
        self.allowed_signers = ""
        self.error = ""
        self.loop_error = ""
        self.last_loop_at: float | None = None
        self.last_summary: dict[str, int] = {}
        self.last_handovers: list[dict[str, Any]] = []   # what Bridge.process said for each labelled MR, last round
        self.poll_checks: list[dict[str, Any]] = []      # CheckRows: the projects the last poll did not read or poll
        self.poll_projects: list[dict[str, Any]] = []    # each project as the last poll left it (relay.project_states)
        self.relay: relay_mod.Relay | None = None        # 状态中转: bridge.json team_status
        self.deliveries: relay_mod.Deliveries | None = None
        self.lines: deque = deque(maxlen=200)
        self.same_user = False
        self.inputs = ""            # signerproc.digest of start_inputs, taken as start() read them
        self._lock = threading.Lock()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="brain-bridge")

    # ---- setup ---------------------------------------------------------------------------------------- #

    def changed(self) -> bool:
        """Whether a file it read when it started is different now: it holds the old one until it starts again."""
        return signerproc.digest(start_inputs(self.team)) != self.inputs

    def start(self) -> bool:
        common = bk.m("common")
        self.inputs = signerproc.digest(start_inputs(self.team))
        try:
            cfg = load_config(self.team["bridge_config"])
            s = cfg["signer"]
            spool, allowed = str(s["spool_dir"]), str(s["allowed_signers"])
            if not os.path.isfile(allowed):
                raise common.BrainError("找不到 bridge.json signer.allowed_signers：%s" % allowed)
            # the signer's answers come from another OS user: this one must not be able to write them
            writable = any(os.path.isdir(p) and os.access(p, os.W_OK)
                           for p in (os.path.join(spool, "signed"), os.path.join(spool, "refused")))
            self.same_user = writable or bool(self.team.get("dev_same_user"))
            if not self.team.get("dev_same_user"):
                if writable:
                    raise common.BrainError("签名服务的 signed/、refused/ 这个 app 的系统用户能写：签名服务要用自己的系统用户"
                                            "（brainbridge）跑。本机试用请在设置里打开「签名服务和 app 同一个用户」")
                owned = [p for p in (self.team["bridge_config"], self.team.get("runnerd_config") or "", allowed,
                                     cfg.get("identities") or "") if p and not foreign(p)]
                if owned:
                    raise common.BrainError("这些配置这个 app 的系统用户能改：%s。agent 和 app 是同一个用户，配置要归别的用户"
                                            "（root 或 brainbridge）所有、对 app 只读" % "、".join(os.path.basename(p) for p in owned))
            rcfg = None
            if self.team.get("runnerd_config"):
                rcfg = common.load_json(self.team["runnerd_config"])
                if os.path.realpath(str(rcfg.get("spool_dir") or "")) != os.path.realpath(cfg["spool_dir"]):
                    raise common.BrainError("runnerd.json 的 spool_dir 和 bridge.json 的不一样")
            rest = bk.m("rest")
            gcfg = cfg.get("gitlab") or {}
            tok = rest.read_token(gcfg.get("token_env", "BRIDGE_GITLAB_TOKEN"),
                                  default_file=gcfg.get("token_file", rest.DEFAULT_TOKEN_FILE))
            if not gcfg.get("base") or not tok:
                raise common.BrainError("bridge.json 的 gitlab.base 没写，或者读不到 GitLab token（%s / %s）"
                                        % (gcfg.get("token_env", "BRIDGE_GITLAB_TOKEN"), gcfg.get("token_file", rest.DEFAULT_TOKEN_FILE)))
            gl = rest.GitLab(gcfg["base"], tok, self.transport)
            jc = cfg.get("jira") or {}
            jira = None
            if jc.get("base"):
                jira = rest.Jira(jc["base"], os.environ.get(jc.get("token_env", "BRIDGE_JIRA_TOKEN"), ""), jc.get("api", "2"),
                                 self.transport, basic_user=os.environ.get(jc.get("user_env", ""), "") or None)
            sign = bk.m("sign")
            signer = sign.signer_from_config(cfg)          # brain-kit's SpoolSigner, from bridge.json's signer block
            if not isinstance(signer, sign.SpoolSigner):
                raise common.BrainError("bridge.json 的 signer 不是签名服务（spool）")
            if self.sign_timeout is not None:
                signer.timeout = float(self.sign_timeout)
            if self.kick is not None:
                kick = self.kick
                signer.sleep = lambda s: (kick(), time.sleep(s))
            kc = KiroCrewAdapter(self.loop, self.mgr, common.BrainError)
            self.host = bk.m("bridge.handoff").Bridge(cfg, gl, jira, kc, out=self._out, signer=signer)
            config = os.path.abspath(os.path.expanduser(self.team["bridge_config"]))
            self.patrol = patrol_mod.Patrol(
                gl=gl, sessions=kc, kit=self.host.kit, out=self._out,
                projects=[p["project"] if isinstance(p, dict) else p for p in cfg["projects"]],
                ledger_path=os.path.join(os.path.dirname(config), "patrol.sqlite"),
                workspaces=patrol_mod.Workspaces.of(rcfg) if rcfg and rcfg.get("runs_root") and rcfg.get("clone_url")
                else None,
                helper=patrol_mod.Helper(PYTHON_BIN, str(APP_ROOT / "backend" / "core" / "team" / "patrol_reply.py"),
                                         config, str(bk.root())),
                claims=lambda: sources.held(self), jenkins=Jenkins.of(cfg.get("jenkins"), self.transport),
                shared=lambda: sources.at_work(self, but=sources.PATROL))
            self.staging = staging_mod.StagingWatch(
                gl=gl, sessions=kc, kit=self.host.kit, out=self._out, projects=cfg["projects"],
                ledger_path=os.path.join(os.path.dirname(config), "staging.sqlite"),
                workspaces=staging_mod.beside(self.patrol.workspaces), helper=self.patrol.helper,
                claims=lambda: sources.held(self), shared=lambda: sources.at_work(self, but=sources.STAGING))
            self.relay, self.deliveries = self._relay(cfg, gl)
            self.cfg, self.runnerd_cfg, self.spool, self.allowed_signers = cfg, rcfg, spool, allowed
            self.error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc).split("\n", 1)[0][:400]
            self.host = self.patrol = self.staging = self.relay = self.deliveries = None
            return False

    def _relay(self, cfg: dict[str, Any], gl: Any) -> tuple[relay_mod.Relay | None, relay_mod.Deliveries | None]:
        """状态中转 when bridge.json names its project (team_status.project): written with the bridge's token, or the
        one in team_status.token_file (a project token of the status project's own, as 一键建运行机 makes it for a
        runner on project tokens), read when the first round writes."""
        ts = cfg.get("team_status") if isinstance(cfg.get("team_status"), dict) else {}
        project = str(ts.get("project") or "").strip()
        if not project:
            return None, None
        base, rest = str(cfg["gitlab"]["base"]), bk.m("rest")
        token_file = str(ts.get("token_file") or "")
        connect = (lambda: rest.GitLab(base, rest.read_token(relay_mod.NO_TOKEN_ENV, relay_mod.NO_TOKEN_ENV,
                                                             default_file=token_file), self.transport)) \
            if token_file else (lambda: gl)
        return relay_mod.Relay(connect, project), relay_mod.Deliveries(gl, base, self.host.kit)

    def stop(self) -> None:
        """Close the ledger after the round in progress (it runs on the executor), then let the executor go."""
        try:
            self.executor.submit(self._close).result(timeout=30)
        except Exception:  # noqa: BLE001
            pass
        self.executor.shutdown(wait=False)

    def _close(self) -> None:
        host, self.host = self.host, None
        patrol, self.patrol = self.patrol, None
        staging, self.staging = self.staging, None
        for held in (host.ledger if host is not None else None, patrol, staging):
            try:
                if held is not None:
                    held.close()
            except Exception:  # noqa: BLE001
                pass

    def _out(self, line: str) -> None:
        text = self.scrub(str(line)[:500])
        self.lines.append((time.time(), text))
        if self.log is not None:
            self.log.info("brain bridge: %s", text)

    # ---- one round ------------------------------------------------------------------------------------ #

    def loop_once(self) -> dict[str, int]:
        """poll, runnerd, dispatch, watch, collect, reconcile, patrol, staging; one bad step does not stop the others."""
        host = self.host
        if host is None:
            raise RuntimeError(self.error or "运行机模式没有启动")
        common = bk.m("common")
        summary: dict[str, int] = {}
        errors = []
        with self._lock:
            for step in ("poll", "runnerd", "dispatch", "watch", "collect", "reconcile", "patrol", "staging"):
                try:
                    if step == "runnerd":
                        if self.runnerd_cfg:
                            summary["prepared"] = int(bk.m("bridge.runnerd").once(self.runnerd_cfg, out=self._out) or 0)
                        continue
                    if step == "poll":
                        summary["poll"] = self._poll(host)
                        continue
                    if step == "patrol":
                        if self.patrol is not None:
                            summary["patrol"] = self.patrol.round()
                        continue
                    if step == "staging":
                        if self.staging is not None:
                            summary["staging"] = self.staging.round()
                        continue
                    n = getattr(host, step)()
                    if isinstance(n, int):
                        summary[step] = n
                except (common.BrainError, OSError, ValueError, sqlite3.Error) as exc:
                    errors.append("%s: %s" % (step, str(exc).split("\n", 1)[0][:200]))
                    self._out("%s failed this round: %s" % (step, exc))
            self.last_loop_at = time.time()
            self.loop_error = "；".join(errors)
            if self.relay is not None:      # 状态中转: what goes wrong is its own to say, the round is done
                summary["relay"] = int(self.relay.round(self.snapshot) in ("wrote", "heartbeat"))
            self.last_summary = summary
        return summary

    def snapshot(self, now: float) -> dict[str, Any]:
        """The TeamSnapshot this round relays (snapshot.py): the server and each project as the round left it; the
        runner at work as 运行机 shows it (its runs, runner_rows, still open; MR 巡检's and 测试环境报错's views, as
        they are); 全队交付 from GitLab (relay.Deliveries). Scrubbed as the pages are."""
        projects = [p["project"] if isinstance(p, dict) else str(p) for p in self.host.cfg["projects"]]
        patrol, staging = self.patrol_view(), self.staging_view()
        roles = relay_mod.identity_roles(self.cfg.get("identities"))
        deployed = {p["project"]: str(p.get("deployed_sha") or "") for p in staging.get("projects") or [] if p.get("on")}
        rows, said = self.deliveries.build(projects, deployed=deployed, roles=roles)
        asked = {(r["project"], r["key"]): r["waits"] for r in rows}
        handovers = []
        for row, _facts, _decs, run in self.runner_rows():
            if run["state"] in ("merged", "closed"):
                continue
            seen = asked.get((str(run["project"]), row["key"]))
            gate = row["waits"]["do"] in deliveries.GATES
            on = seen["on"] if seen and gate and seen["do"] in deliveries.GATES else relay_mod.on_names(row, None, roles)
            handovers.append({"project": str(run["project"]), "iid": int(run["iid"]),
                              "mr_url": self.mr_url(run["project"], run["iid"]), "key": row["key"], "title": row["title"],
                              "run_id": str(run["run_id"]), "state": str(run["state"]), "owner": run.get("owner") or None,
                              "waits": relay_mod.waits_of(row, on)})
        snap = {"schema": snapshot.SCHEMA, "generated_at": now,
                "server": {"version": VERSION, "team_mode": True, "last_round_at": self.last_loop_at,
                           "projects": relay_mod.project_states(self.poll_projects, patrol, staging, said)},
                "runner": {"handovers": handovers, "patrol": patrol["items"], "staging": staging["items"],
                           "cap": int(patrol["cap"]), "working": int(patrol["working"]) + int(staging["working"])},
                "delivery": rows}
        return scrub_tree(snap, self.scrub)

    def _poll(self, host: Any) -> int:
        """brain-kit's Bridge.poll, MR by MR, keeping each MR's result (Bridge.process) for the page. A project whose
        kit.json or labelled MRs cannot be read, or one MR that fails, does not stop the others, as in brain-kit: a
        project's error is raised at the end, an MR's is logged the same way. The projects not read and those whose
        kit.json leaves mr-handoff out are the status' checks. -> how many MRs were looked at."""
        common = bk.m("common")
        seen: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        errors: list[str] = []
        polled: list[dict[str, Any]] = []
        try:
            for proj in host.cfg["projects"]:
                project = proj["project"] if isinstance(proj, dict) else proj
                state = {"project": str(project), "on": False, "sources": [], "error": ""}
                polled.append(state)
                try:
                    kit = host.kit(project)
                    state["sources"] = list(sources.enabled(kit))
                    if sources.HANDOFF not in sources.enabled(kit):
                        checks.append(problems.mk(
                            "WARN", "%s 的 kit.json 里 work_sources 没写 %s：运行机不接这个项目的交接" % (project, sources.HANDOFF),
                            "要接交接，在 work_sources 里写回 %s（写全，比如 %s），合并到默认分支" % (
                                sources.HANDOFF, json.dumps(list(sources.SOURCES)))))
                        continue
                    mrs = host.gl.mrs_with_label(project, kit["handoff_label"])
                except (common.BrainError, OSError, ValueError) as exc:     # read again next round
                    said = self.scrub(" ".join(str(exc).split())[:300])
                    errors.append("%s: %s" % (project, said))
                    state["error"] = "这一轮没读成，它的交接没看：%s" % said
                    checks.append(problems.mk(
                        "FAIL", "%s 上一轮没读成：它的交接没看，别的项目照常" % project,
                        "下一轮会自动再试；一直这样就看细节：多半是这个项目默认分支上的 .brain/kit.json 读不了，或者桥的账号看不到这个项目",
                        raw=said))
                    continue
                state["on"] = True
                for mr in mrs:
                    row = {"project": str(project), "iid": int(mr.get("iid") or 0), "sha": str(mr.get("sha") or "")[:40],
                           "result": "error", "error": "", "at": time.time()}
                    try:
                        row["result"] = str(host.process(project, kit, mr) or "")
                    except common.BrainError as e:  # retried next round, as brain-kit's poll does
                        host.out("error on %s!%s: %s" % (project, mr.get("iid"), e))
                        row["error"] = self.scrub(str(e).split("\n", 1)[0][:300])
                    seen.append(row)
        finally:
            self.last_handovers, self.poll_checks, self.poll_projects = seen, checks, polled
        if errors:
            raise common.BrainError("not done this round: " + "; ".join(errors))
        return len(seen)

    def patrol_view(self) -> dict[str, Any]:
        """GET /team/patrol: what MR 巡检's last round saw (patrol.view_of)."""
        p = self.patrol
        return p.view() if p is not None else patrol_mod.view_of([], [], None)

    def staging_view(self) -> dict[str, Any]:
        """GET /team/staging: what 测试环境报错's last round saw (staging.view_of)."""
        s = self.staging
        return s.view() if s is not None else staging_mod.view_of([], [], {}, None)

    def handovers(self) -> list[dict[str, Any]]:
        """The labelled MRs of the last round and what brain-kit's bridge said about each (see _poll)."""
        out = []
        for r in list(self.last_handovers):
            out.append(dict(r, mr_url=self.mr_url(r["project"], r["iid"])))
        return out

    def runner_rows(self) -> list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
        """(row, facts, decisions, run) per requirement the bridge has a run of, its latest run: the row as 交付总览 and
        需求现状 say it (deliveries.team_classify over the ledger and the records in the run's workspace, its run ids,
        MR and last update). Its credits and the first line of its 变更说明 are the service's."""
        if self.host is None:
            return []
        led = self.ledger()
        latest: dict[str, dict[str, Any]] = {}
        all_ids: dict[str, list[str]] = {}
        for r in led["runs"]:
            all_ids.setdefault(r["req"], []).append(r["run_id"])
            latest[r["req"]] = r
        notes = deliveries.notices_by_run(led.get("notices") or [], led.get("outbox") or [])
        out = []
        for key, r in sorted(latest.items()):
            facts = {"key": key, "claim": None, "where": "runner", "branch": None, "owner": r.get("owner"),
                     "worktree": None, "stages": [], "events": [], "approvals": [], "run": None, "park": None,
                     "project": r.get("project")}
            path = r.get("path")
            if path and os.path.isdir(path):
                g = Git(path)
                got = read_facts(Source(g, path=path), key, "runs")
                # runnerd's clone fetches every branch: the claim says the requirement's title, as a person's row does
                facts.update(got, worktree=path, stages=lean_view.stages(got["events"]),
                             claim=read_claim(g, "refs/remotes/origin/claim/" + key, "refs/heads/claim/" + key))
                facts["branch"] = (got["run"] or {}).get("branch")
            row = deliveries.base_row("runner", facts)
            row["where"] = "runner"
            decs = [d for d in led["decisions"] if d["run_id"] in all_ids[key]]
            deliveries.team_classify(row, r, decs, facts.get("park"), notes=notes.get(r["run_id"], []))
            row["run_ids"] = all_ids[key]
            row["mr"] = {"iid": int(r["iid"]), "url": self.mr_url(r["project"], r["iid"]), "state": None}
            row["updated_at"] = lean_view.epoch(r.get("updated"))
            out.append((row, facts, decs, r))
        return out

    # ---- what the page reads -------------------------------------------------------------------------- #

    def ledger(self) -> dict[str, Any]:
        """{runs, decisions, handoffs, outbox, notices}: read from the bridge's own Ledger (any thread), under its
        lock so the tables are one moment's. outbox and notices are brain-kit 0d52e97's (QA rounds 3 and 4: every
        message into a run's session, and what the bridge told an MR once); [] with an older brain-kit."""
        out: dict[str, Any] = {k: [] for k in LEDGER_TABLES}
        host = self.host
        if host is None:
            return out
        led = host.ledger
        try:
            with led.lock:
                tables = {r[0] for r in led.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "runs" in tables:
                    out["runs"] = [dict(r) for r in led.db.execute(
                        "SELECT run_id, project, req, iid, sha, voucher_id, state, slot, path, paths, owner, last_sha, "
                        "created, updated FROM runs ORDER BY created")]
                if "decisions" in tables:
                    out["decisions"] = [dict(r) for r in led.db.execute(
                        "SELECT id, run_id, project, iid, sha, kind, lean_request, state, answer, answerer, voucher_id, "
                        "created, updated FROM decisions ORDER BY created")]
                if "handoffs" in tables:
                    out["handoffs"] = [dict(r) for r in led.db.execute(
                        "SELECT project, iid, sha, state, run_id, voucher_id, reason, created, updated FROM handoffs "
                        "ORDER BY updated")]
                if "outbox" in tables:     # the messages themselves are not read (they are the run's)
                    out["outbox"] = [dict(r) for r in led.db.execute(
                        "SELECT send_id, run_id, slot, what, state, tries, created, updated FROM outbox ORDER BY created")]
                if "notices" in tables:
                    out["notices"] = [dict(r) for r in led.db.execute(
                        "SELECT key, run_id, project, iid, state, count, created, updated FROM notices ORDER BY created")]
        except sqlite3.Error:  # closed by stop() meanwhile
            return {k: [] for k in LEDGER_TABLES}
        return out

    def hook_rows(self) -> list[dict[str, Any]]:
        """The runner's Lean hooks as brain-runnerd checks them before each workspace (brain-kit 0d52e97's
        cli.runner_hook_problems for runnerd.json's kit_dir and agent_spec), as CheckRows; [] with an older brain-kit
        or without runnerd.json."""
        fn = getattr(bk.m("cli"), "runner_hook_problems", None) if bk.ok() else None
        if fn is None or not self.runnerd_cfg:
            return []
        try:
            probs = fn(self.runnerd_cfg.get("kit_dir") or None, self.runnerd_cfg.get("agent_spec") or None)
        except Exception as exc:  # noqa: BLE001 - said on the page, nothing stops
            return [problems.fallback("WARN", str(exc)[:300], todo="看下面的原话")]
        if not probs:
            return [problems.mk("OK", "运行机上 Lean 的拦截没问题：brain-kit 带的 %s是第 %s 版%s" % (
                problems.ADAPTER, getattr(bk.m("cli"), "CREW_HOOK_VERSION", "?"),
                "，brain-run 的三个拦截都对" if self.runnerd_cfg.get("agent_spec") else "（brain-run 的设置没让查）"))]
        return [problems.row("FAIL", str(x)) for x in probs][:20]

    def config_view(self) -> list[dict[str, Any]]:
        """The bridge.json and runnerd.json settings the page shows (TeamConfigRow): each key brain-kit reads, its value
        (or its default, said so) and one plain line. Only these keys: no token, no secret, no path of a key."""
        sec = getattr(bk.m("sign"), "seconds", None) if bk.ok() else None
        rows = []
        for file, cfg, specs in (("bridge.json", self.cfg or {}, BRIDGE_KEYS), ("runnerd.json", self.runnerd_cfg, RUNNERD_KEYS)):
            if cfg is None:
                continue
            for key, default, unit, text in specs:
                raw = cfg.get(key)
                if unit in ("min", "s", "n"):
                    v = sec(raw, None) if sec is not None else (raw if isinstance(raw, (int, float)) and not
                                                                isinstance(raw, bool) and raw > 0 else None)
                    shown = v if v is not None else default
                    value = "%g" % shown if isinstance(shown, (int, float)) else str(shown)
                    value += {"min": " 分钟", "s": " 秒", "n": ""}[unit] if isinstance(shown, (int, float)) else ""
                    is_default = v is None
                else:
                    is_default = not (isinstance(raw, str) and raw.strip())
                    value = str(raw).strip()[:300] if not is_default else str(default)
                rows.append({"file": file, "key": key, "value": value, "default": is_default, "text": text})
        return rows

    def pending(self, window_s: float = PENDING_WINDOW_S) -> list[dict[str, Any]]:
        """Vouchers the bridge asked the signer for in an earlier round and got no answer to (brain-kit's
        pending_vouchers): the next attempt asks for that very voucher again, so a late signature is used. Only those
        still within brain-kit's reuse window (Ledger.pending_voucher's newer_than)."""
        host = self.host
        if host is None:
            return []
        led = host.ledger
        common = bk.m("common")
        since = common.iso(common.utcnow() - timedelta(seconds=window_s))
        out = []
        try:
            with led.lock:
                if not led.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_vouchers'"
                                      ).fetchone():
                    return []
                rows = led.db.execute("SELECT ref, voucher, created FROM pending_vouchers WHERE created>=? "
                                      "ORDER BY created", (since,)).fetchall()
        except sqlite3.Error:
            return []
        for r in rows:
            try:
                v = json.loads(r["voucher"] or "{}")
            except ValueError:
                v = {}
            v = v if isinstance(v, dict) else {}
            out.append({"ref": str(r["ref"])[:200], "kind": str(v.get("kind") or ""), "run_id": str(v.get("run_id") or ""),
                        "since": r["created"] if isinstance(r["created"], str) else None})
        return out

    def counts(self, rows: dict[str, Any]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {"runs": {}, "decisions": {}, "handoffs": {}, "outbox": {}}
        for k in out:
            for r in rows.get(k) or []:
                out[k][str(r.get("state"))] = out[k].get(str(r.get("state")), 0) + 1
        return out

    def mr_url(self, project: str, iid: Any) -> str | None:
        base = str(((self.cfg or {}).get("gitlab") or {}).get("base") or "").rstrip("/")
        if not base or not project or not iid:
            return None
        return "%s/%s/-/merge_requests/%d" % (base, project, int(iid))

    def status(self) -> dict[str, Any]:
        sig = self.signer_view()
        why = problems.team_start(self.error)   # the start error in words; as it was (file names, keys) for 细节
        return {"enabled": bool(self.team.get("enabled")), "ok": self.host is not None and not self.error,
                "error": why["text"], "error_todo": why["todo"], "error_raw": why["raw"],
                "last_loop_at": self.last_loop_at, "loop_error": self.loop_error, "checks": list(self.poll_checks),
                "same_user": self.same_user,
                "signer": {"pending": len(sig["pending"]), "refused": len(sig["refused"])},
                "relay": self.relay.view() if self.relay is not None else None}

    def signer_view(self) -> dict[str, Any]:
        return spool_view(self.spool) if self.host is not None and self.spool else {"pending": [], "refused": []}

    def interval(self) -> float:
        try:
            return max(5.0, float((self.cfg or {}).get("interval") or 60))
        except (TypeError, ValueError):
            return 60.0


def _read_small(path: str) -> Any:
    """JSON of a small regular file, opened without following a symlink or blocking on a FIFO (the agents' OS user can
    put anything into requests/); None otherwise."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SPOOL_FILE:
            return None
        data = os.read(fd, MAX_SPOOL_FILE + 1)
    finally:
        os.close(fd)
    try:
        return json.loads(data.decode("utf-8")) if len(data) <= MAX_SPOOL_FILE else None
    except (ValueError, UnicodeDecodeError):
        return None


def spool_view(spool: str, keep: int = 20) -> dict[str, Any]:
    """The signer spool (brain-kit's SpoolSigner layout) for the page: requests the bridge is waiting on (it takes a
    request back once answered or given up), and the signer's latest refusals with their reasons."""
    pending, refused = [], []
    req_dir, ref_dir = os.path.join(spool, "requests"), os.path.join(spool, "refused")
    for name in sorted(os.listdir(req_dir)) if os.path.isdir(req_dir) else []:
        if not REQ_RE.match(name):
            continue
        r = _read_small(os.path.join(req_dir, name))
        r = r if isinstance(r, dict) else {}
        v = r.get("voucher") if isinstance(r.get("voucher"), dict) else {}
        pending.append({"req_id": name[:-5], "kind": str(r.get("kind") or ""), "run_id": str(v.get("run_id") or ""),
                        "since": r.get("created") if isinstance(r.get("created"), str) else None})
    names = []
    for name in os.listdir(ref_dir) if os.path.isdir(ref_dir) else []:
        try:
            st = os.lstat(os.path.join(ref_dir, name))
        except OSError:
            continue
        if REQ_RE.match(name) and stat.S_ISREG(st.st_mode):
            names.append((st.st_mtime, name))
    for _, name in sorted(names)[-keep:]:
        r = _read_small(os.path.join(ref_dir, name))
        reasons = r.get("reasons") if isinstance(r, dict) and isinstance(r.get("reasons"), list) else []
        refused.append({"req_id": name[:-5], "kind": name.split("-", 1)[0],
                        "reasons": [str(x)[:300] for x in reasons][:10]})
    return {"pending": pending, "refused": refused}


def foreign(path: str) -> bool:
    """*path* and its folder are owned by another OS user and this one cannot write them (the agents run as this
    user: a config they can change is no check at all)."""
    try:
        for p in (path, os.path.dirname(os.path.abspath(path))):
            st = os.stat(p)
            if st.st_uid == os.getuid() or os.access(p, os.W_OK):
                return False
    except OSError:
        return False
    return True


def run_paths(row: dict[str, Any]) -> list[str]:
    try:
        return [p for p in json.loads(row.get("paths") or "[]") if isinstance(p, str)]
    except ValueError:
        return []
