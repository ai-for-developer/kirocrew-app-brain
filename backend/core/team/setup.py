"""一键建运行机: the team runner in same-user mode, built by the app from GitLab tokens typed once.

MIGRATE.md 第二、三部分 as one ordered table (STEPS) for the trial where the signer, the app and the agents are one OS user
(settings team.dev_same_user): no sudo, no second user, nothing under /etc. Each step looks at what is there and makes
only what is missing, so the table runs again safely: a second run makes nothing new, and a run that stopped part-way
converges when it runs again. The first failed step stops the run; a step that waits for a person (the kit.json MRs)
does not.

Where the runner's GitLab accounts come from (SetupInput.mode):
  project-token  one project. The person's one-time token (Maintainer or Owner) makes the project access tokens
                 brain-bridge, and brain-bot when the runner pushes with a token, keeps them for a year and gives them a
                 new value in their last 14 days. A project token sees its own project only.
  bot-account    one project or several. A GitLab user of the bridge's own (brain-bridge, Developer in every project)
                 and its personal token (api); the runner pushes over this machine's SSH, or as a second user (brain-bot,
                 Developer) with its personal token (write_repository, read_user). Nothing is made or rotated: the tokens
                 are the person's, kept under the root, and a later run that leaves one out uses the one kept while it
                 still signs in as that account. The one-time token is optional: without it the labels and the kit.json
                 MRs are made as brain-bridge.
A step about the projects (finding them, the labels, the members, the kit.json MR) does its work for each of them, every
one even after another failed, and keeps each one's result (StepResult.projects) when there are two or more. bridge.json
and signer.json list them all, and identities.json holds the members of all of them; runnerd clones each from one
address. A run with a project added or taken out writes nothing in the projects that stay.

Under one root (default ~/.brain/team-setup; files 0600, folders 0700):
  setup-state.json       the last run: each step's result and what the steps found (the projects, the project tokens'
                         ids and bot usernames, the accounts of the personal tokens kept, how the runner pushes, the MRs
                         that wait), with times. Nothing secret.
  bridge-gitlab-token    the bridge's and the signer's GitLab token (api): project access token brain-bridge
                         (Developer), or brain-bridge's personal token
  bot-gitlab-token       runnerd's clone and the runs' pushes, when the runner pushes with a token: project access token
                         brain-bot, or the bot user's personal token
  jenkins-token          the token of the Jenkins read-only user typed (SetupInput.jenkins): MR 巡检 reads logs with it
  identities.json, keys/, bridge.json, runnerd.json, signer.json, the spools, vouchers and ledgers, signer.pid / .log
  status.git             a bare repo of its own, where the team server makes a project's brain/status the first time
状态中转 (step status): the team server writes each project's status into the project itself, on its branch brain/status
(team/relay.py), so GitLab lets whoever can read the project read its status, and nobody else; the bridge's account
pushes it, and the step checks that no protected branch rule keeps it from that.
Under ~/workplace/brain-team (KiroCrew lets agents work only under ~/workplace): runs/ and cache/, and MR 巡检's clones
in patrol/. The agents: ~/.kiro/agents/brain-run.json, and brain-patrol.json beside it when 巡检所有开着的 MR is on (the
kit.json MRs then also switch work source mr-patrol on). 从打了标签的 issue 接活 switches work source issue-label on in
the same MRs and makes its label (team/issues.py: issues.LABEL). The person's one-time token (SetupInput.admin_token)
lives in memory for one run: it is never written, logged or answered, and text from GitLab or a command goes through
Ctx.redact before it is kept.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import bk, kitmr, snapshot
from ..constants import APP_ROOT, PYTHON_BIN
from . import issues as issues_mod
from . import patrol as patrol_mod
from . import signerproc, sources

try:
    import fcntl
except ImportError:  # pragma: no cover - not macOS or Linux: nothing keeps two runs apart
    fcntl = None  # type: ignore[assignment]

ROLES = ("product", "builder", "security", "data")
ROLE_NAME = {"product": "产品", "builder": "开发", "security": "安全", "data": "数据"}
PUSH_MODES = ("auto", "ssh", "token")
PROJECT_TOKEN, BOT_ACCOUNT = "project-token", "bot-account"
MODES = (PROJECT_TOKEN, BOT_ACCOUNT)
LEVEL_NAME, MAINTAINER, DEVELOPER, OWNER = sources.LEVEL_NAME, sources.MAINTAINER, sources.DEVELOPER, sources.OWNER
# the role the account that makes the labels and the kit.json MRs needs in each project: a project token is made by a
# Maintainer
MAKER_LEVEL = {PROJECT_TOKEN: MAINTAINER, BOT_ACCOUNT: DEVELOPER}
MAKER_LOW = {PROJECT_TOKEN: "%s在 %s 是 %s：要 Maintainer 或 Owner 才能建项目令牌和标签",
             BOT_ACCOUNT: "%s在 %s 是 %s：要 Developer 以上才能建标签、开 kit.json 的 MR"}
# a year less a day from today in UTC: GitLab refuses an expires_at more than 365 days past its own today, which is
# UTC's (this machine's may be a day ahead), or a day behind it on a GitLab set to a zone west of UTC
TOKEN_DAYS = 364
RENEW_DAYS = 14         # a token with this many days or fewer to go gets a new value (rotated), its account the same
TOKEN_SAID = {"kept": "用上次建的项目令牌 {name}：账号 {user}，{end} 到期",
              "made": "建了项目令牌 {name}：账号 {user}，{end} 到期",
              "ending": "项目令牌 {name} 快到期了，换了新的值：账号还是 {user}，{end} 到期",
              "lost": "这台机器上项目令牌 {name} 的值丢了或者不对，换了新的值：账号还是 {user}，{end} 到期"}
BRIDGE_SCOPES = ("api",)
# GET /user, which the reuse check and the bot's username and address come from, takes read_api (GitLab gives a project
# token no read_user: it answers 400 to one asked with it)
BOT_SCOPES = ("read_repository", "write_repository", "read_api")
# bot-account mode: what the personal token of each account is called on the page, and the scopes it needs, any one of
# them (git over HTTPS takes api as well as write_repository)
ACCOUNT_NAME = {"bridge": "桥的账号的个人令牌", "bot": "机器人账号的个人令牌"}
ACCOUNT_SCOPES = {"bridge": ("api",), "bot": ("write_repository", "api")}
ACCOUNT_NEEDS = {"bridge": "没有 api 权限：桥要用它在 MR 上贴问题、改标签。在 GitLab 上给 %s 换一个有 api 的令牌",
                 "bot": "没有 write_repository 权限：运行机要用它推代码。在 GitLab 上给 %s 换一个有 write_repository 的令牌"}
SSH_BOT = ("brain-bot", "brain-bot@noreply.invalid")    # git_name, git_email of a runner pushing over this machine's SSH
REACH_WAIT_S = 20
BRANCH = "brain/runner-setup"
STATUS_BRANCH = snapshot.BRANCH     # where the team server writes each project's status (状态中转, team/relay.py)
MR_TITLE = "运行机：更新 .brain/kit.json"
MR_TEXT = ("一键建运行机开的 MR：运行机要 kit.json 写上 bridge_user（桥的账号）、bot_users 和 bot_emails（运行机推送用的账号和邮箱）、"
           "gitlab 和 default_branch。合并以前，签名服务不签交接。")
MR_PATROL = {True: "work_sources 加上 mr-patrol：合并以后，运行机巡检这个项目所有开着的 MR，流水线没过、评审意见没回、有冲突的，它自己去修。",
             False: "work_sources 去掉 mr-patrol：合并以后，运行机不再巡检这个项目开着的 MR。"}
MR_ISSUES = {True: "work_sources 加上 issue-label：合并以后，项目里 Developer 以上的人给 issue 打上标签 %s，运行机就认领需求、开分支和 "
                   "MR，在 MR 上问问题；MR 合并时 issue 跟着关。" % issues_mod.LABEL,
             False: "work_sources 去掉 issue-label：合并以后，运行机不再接打了标签的 issue。"}
# the work sources 一键建运行机 switches on or off: (kind, its SetupInput field, what the kit.json MR says of it on and off,
# what starts or stops once that MR is merged). On, a kind goes into kit.json work_sources with the hand-over before it.
SWITCHES = ((sources.PATROL, "patrol", MR_PATROL, "巡检"), (sources.ISSUE, "issues", MR_ISSUES, "从 issue 接活"))
# (kit.json key, default name, colour): the labels the bridge moves, and brain reflect's
LABELS = (("handoff_label", "ai-handoff", "#1f75cb"), ("running_label", "ai-running", "#108548"),
          ("rejected_label", "ai-handoff-rejected", "#dd2b0e"), ("", "brain-reflect", "#6699cc"))
ISSUE_LABEL = ("issue_label", issues_mod.LABEL, "#9e5fd0")      # the issue label, made when 从 issue 接活 is on
# an environment variable nobody sets: the bridge and the signer read their token from token_file only (the default
# names, BRIDGE_GITLAB_TOKEN and SIGNER_GITLAB_TOKEN, may hold something else)
NO_TOKEN_ENV = {"bridge": "BRAIN_TEAM_NO_TOKEN_ENV", "signer": "BRAIN_SIGNER_NO_TOKEN_ENV"}
WORK_PARENTS = ("workplace", "workplaces", "workspace", "workspaces")
USER_RE = re.compile(r"^[a-z0-9_.][a-z0-9_.-]{0,254}$")
BOT_USER_RE = re.compile(r"^(?:project|group)_\d+_bot(?:_[0-9a-f]+)?\d*$")   # GitLab's users of project and group tokens
PROJECT_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.][A-Za-z0-9_.-]*)+$")
CTRL_RE = re.compile(r"[\x00-\x20\x7f]")
HTTP_SAID = {400: "GitLab 不接受这一步的请求（HTTP 400）：再跑一次也一样，看细节里 GitLab 说的原因",
             401: "GitLab 不认这个 token：过期了，或者填错了",
             404: "GitLab 上找不到要用的东西：看细节"}
FORBIDDEN = {PROJECT_TOKEN: "这个 token 做不了这一步：要有 api 权限，你在项目里要是 Maintainer 或 Owner",
             BOT_ACCOUNT: "这个 token 做不了这一步：要有 api 权限，它的账号在项目里要是 Developer 以上"}
SIGN_IN = "GitLab 回的是登录页：这个 GitLab 要单点登录，先放好它的登录辅助文件（~/.brain/local_transport.py），再跑一次"
# a step's result from those of its projects: the first of these one of them has
WORST = ("failed", "waiting", "done", "skipped")


class SetupRefused(ValueError):
    """The input cannot be used, said in plain Chinese for the page."""


class Busy(RuntimeError):
    """Another run holds the root."""


class StepFailed(Exception):
    """A step cannot go on: its plain words, and what GitLab or a command said."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message, self.detail = message, detail


def project_path(base: str, text: str) -> str:
    """group/repo from what was typed for a project: the path, or its page on *base* (or any address) pasted."""
    project = text.strip()
    if project.startswith(base + "/"):
        project = project[len(base) + 1:]
    elif "://" in project:
        project = urllib.parse.urlsplit(project).path
    project = project.split("/-/", 1)[0].strip("/")
    return project[:-4] if project.endswith(".git") else project


def typed_token(raw: Mapping[str, Any], key: str) -> str | None:
    """The token typed in *key* ('' when none); None when it cannot be one."""
    tok = raw.get(key)
    tok = tok.strip() if isinstance(tok, str) else ""
    return None if CTRL_RE.search(tok) or len(tok) > 1000 else tok


@dataclass(frozen=True)
class SetupInput:
    gitlab_base: str
    projects: tuple[str, ...]                     # group/repo, each once, in the order given; project-token: one
    admin_token: str = field(repr=False)          # the person's one-time token (api): this run's memory only; '' when
                                                  # bot-account mode goes without it
    roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)   # role -> usernames; none: the token's owner
    push: str = "auto"                            # auto | ssh | token
    patrol: bool = True                           # 巡检所有开着的 MR
    issues: bool = False                          # 从打了标签的 issue 接活
    mode: str = PROJECT_TOKEN
    bridge_token: str = field(default="", repr=False)   # bot-account: brain-bridge's personal token ('': the one kept)
    bot_token: str = field(default="", repr=False)      # bot-account: the bot user's personal token ('': the one kept)
    jenkins: JenkinsInput | None = None

    @classmethod
    def parse(cls, raw: Any) -> "SetupInput":
        """The page's (or the command line's) fields, checked; SetupRefused says what is wrong. The projects come as a
        list (or text, one per line) in "projects", else the one in "project"."""
        if not isinstance(raw, Mapping):
            raise SetupRefused("页面发来的内容不对：刷新页面再试")
        base = str(raw.get("gitlab_base") or "").strip().rstrip("/")
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ("https", "http") or not u.hostname or "@" in u.netloc or u.query or u.fragment \
                or CTRL_RE.search(base):
            raise SetupRefused("GitLab 地址要写成 https://gitlab.example.com 这样")
        mode = raw.get("mode") or PROJECT_TOKEN
        if mode not in MODES:
            raise SetupRefused("账号只能是 %s（项目令牌）或 %s（机器人账号）" % MODES)
        typed = raw.get("projects") if raw.get("projects") is not None else [raw.get("project") or ""]
        if isinstance(typed, str):
            typed = re.split(r"[\s,，、]+", typed)
        if not isinstance(typed, (list, tuple)) or not all(isinstance(t, str) for t in typed):
            raise SetupRefused("项目的格式不对：刷新页面再试")
        got: dict[str, str] = {}
        for t in typed:
            if t.strip():
                path = project_path(base, t)
                if not PROJECT_RE.match(path):
                    raise SetupRefused("「%s」要写成「组/仓库」，比如 grp/pay" % t.strip()[:200])
                got.setdefault(path.lower(), path)        # GitLab's paths are not case-sensitive
        if not got:
            raise SetupRefused("项目要写成「组/仓库」，比如 grp/pay")
        if mode == PROJECT_TOKEN and len(got) > 1:
            raise SetupRefused("项目令牌只看得到自己那个项目：一台运行机管几个项目，账号选「机器人账号」")
        token = typed_token(raw, "admin_token")
        if token is None or (not token and mode == PROJECT_TOKEN):
            raise SetupRefused("填一个一次性的 GitLab 个人 token（要有 api 权限）")
        bridge, bot = (typed_token(raw, k) if mode == BOT_ACCOUNT else "" for k in ("bridge_token", "bot_token"))
        for key, tok in (("bridge", bridge), ("bot", bot)):
            if tok is None:
                raise SetupRefused("%s不像一个 token：重新粘贴一次" % ACCOUNT_NAME[key])
        if bridge and bridge == bot:
            raise SetupRefused("桥的账号和机器人账号要是两个不同的 GitLab 用户：两个令牌填成一样的了")
        got_roles = raw.get("roles") or {}
        if not isinstance(got_roles, Mapping):
            raise SetupRefused("角色的格式不对：刷新页面再试")
        roles: dict[str, tuple[str, ...]] = {}
        for role, names in got_roles.items():
            if role not in ROLES:
                raise SetupRefused("角色只有 %s" % "、".join(ROLES))
            if isinstance(names, str):
                names = re.split(r"[,，、\s]+", names)
            if not isinstance(names, (list, tuple)) or not all(isinstance(n, str) for n in names):
                raise SetupRefused("「%s」要写 GitLab 用户名" % ROLE_NAME[role])
            clean = [n.strip().lstrip("@").lower() for n in names if n.strip()]
            bad = [n for n in clean if not USER_RE.match(n)]
            if bad:
                raise SetupRefused("这些不像 GitLab 用户名：%s" % "、".join(bad))
            roles[role] = tuple(dict.fromkeys(clean))
        if not token and not (roles.get("product") and roles.get("builder")):
            raise SetupRefused("没填一次性 token 时，「产品」和「开发」要填上 GitLab 用户名：空着的角色没有 token 的主人来顶")
        push = raw.get("push") or "auto"
        if push not in PUSH_MODES:
            raise SetupRefused("推送方式只能是 auto、ssh 或 token")
        patrol = raw.get("patrol", True)
        if not isinstance(patrol, bool):
            raise SetupRefused("「巡检所有开着的 MR」的值不对：刷新页面再试")
        issues = raw.get("issues", False)
        if not isinstance(issues, bool):
            raise SetupRefused("「从打了标签的 issue 接活」的值不对：刷新页面再试")
        return cls(base, tuple(got.values()), token, roles, push, patrol, issues, mode, bridge or "", bot or "",
                   JenkinsInput.parse(raw.get("jenkins")))

    def public(self) -> dict[str, Any]:
        """What setup-state.json keeps of the input: all of it but the tokens."""
        return {"gitlab_base": self.gitlab_base, "mode": self.mode, "projects": list(self.projects), "push": self.push,
                "roles": {r: list(self.roles.get(r) or ()) for r in ROLES}, "patrol": self.patrol,
                "issues": self.issues,
                "jenkins": {"base": self.jenkins.base, "user": self.jenkins.user} if self.jenkins else None}


@dataclass(frozen=True)
class Row:
    """One project's part of a step about the projects."""
    project: str
    status: str
    message: str
    link: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class StepResult:
    id: str
    status: str               # done | waiting | failed | skipped
    message: str              # short plain Chinese
    link: str | None = None
    detail: str = ""          # what GitLab or a command said (the page folds it under 细节)
    projects: tuple[Row, ...] = ()   # a step about two projects or more: each one's own result


def done(message: str, link: str | None = None, detail: str = "") -> StepResult:
    return StepResult("", "done", message, link, detail)


def waiting(message: str, link: str | None = None, detail: str = "") -> StepResult:
    return StepResult("", "waiting", message, link, detail)


def failed(message: str, link: str | None = None, detail: str = "") -> StepResult:
    return StepResult("", "failed", message, link, detail)


def skipped(message: str) -> StepResult:
    return StepResult("", "skipped", message)


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    run: Callable[["Ctx"], StepResult]


@dataclass(frozen=True)
class Proj:
    """A project of the runner as GitLab has it this run (the first step finds it), with its .brain/kit.json on the
    default branch."""
    id: int
    path: str                 # GitLab's path_with_namespace
    default_branch: str
    web_url: str | None
    ssh_url: str
    http_url: str
    kit: dict
    kit_text: str

    def record(self) -> dict[str, Any]:
        """What setup-state.json keeps of it."""
        return {"id": self.id, "path": self.path, "default_branch": self.default_branch, "web_url": self.web_url}


def _under(base: str, *parts: str) -> Any:
    """A path of Paths: *parts* under its *base* folder (root or work)."""
    return property(lambda self: getattr(self, base).joinpath(*parts))


@dataclass(frozen=True)
class Paths:
    """Where the runner's files go: one root for the configs, tokens, keys, spools and ledgers; the work folder for the
    runs and the clone cache (KiroCrew lets agents work only under ~/workplace or ~/workspace)."""
    root: Path
    work: Path
    agent_spec: Path
    kit_dir: Path                 # the brain-kit the app runs on: runnerd's kit_dir, the signer's brainkit
    app_dir: Path = APP_ROOT      # signer/brain_signer.py is in it
    python: str = PYTHON_BIN

    @classmethod
    def default(cls, kit_dir: Any, root: Any = None, work: Any = None, agent_spec: Any = None) -> "Paths":
        home = Path.home()

        def at(given: Any, usual: Path) -> Path:
            return Path(os.path.abspath(os.path.expanduser(str(given)))) if given else usual
        return cls(at(root, home / ".brain" / "team-setup"), at(work, home / "workplace" / "brain-team"),
                   at(agent_spec, home / ".kiro" / "agents" / "brain-run.json"), Path(str(kit_dir)))

    def token(self, key: str) -> Path:
        return self.root / ("%s-gitlab-token" % key)

    state = _under("root", "setup-state.json")
    identities = _under("root", "identities.json")
    keys = _under("root", "keys")
    key = _under("root", "keys", "bridge_ed25519")
    allowed = _under("root", "keys", "allowed_signers")
    spool = _under("root", "spool")
    signer_spool = _under("root", "signer-spool")
    vouchers = _under("root", "vouchers")
    signer_vouchers = _under("root", "signer-vouchers")
    bridge_json = _under("root", "bridge.json")
    runnerd_json = _under("root", "runnerd.json")
    signer_json = _under("root", "signer.json")
    jenkins_token = _under("root", "jenkins-token")
    runs = _under("work", "runs")
    cache = _under("work", "cache")
    patrol_spec = property(lambda self: self.agent_spec.with_name(patrol_mod.AGENT + ".json"))


@dataclass
class Ctx:
    """One run: the input, the paths, a GitLab client for any token, what turns team mode on, the state that is kept
    (setup-state.json, nothing secret) and what the steps found on the way."""
    inp: SetupInput
    paths: Paths
    gitlab: Callable[[str], Any]                                # token -> brain-kit's rest.GitLab for inp.gitlab_base
    team_mode: Callable[[dict], dict] | None = None             # settings team -> TeamStatus; None: not here
    signer: Callable[[Paths], tuple[int, bool]] | None = None   # -> (pid, started now); None: signerproc.ensure
    reach: Callable[[str], tuple[bool, str]] | None = None      # clone URL -> (git reaches it, what git said)
    state: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    found: list = field(default_factory=list)         # the projects (Proj) the first step found, in the input's order
    owner: str = ""                                   # the username of the account that makes the labels and MRs
    _maker: Any = field(default=None, repr=False)
    _accounts: dict = field(default_factory=dict, repr=False)   # bot-account: key -> (token, GitLab's user), this run
    _secrets: set = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        for s in (self.inp.admin_token, self.inp.bridge_token, self.inp.bot_token):
            self.remember(s)
        if self.inp.jenkins is not None:
            self.remember(self.inp.jenkins.token)

    @property
    def gl(self) -> Any:
        """GitLab as the account that finds the projects and makes the labels and the kit.json MRs: the person's one-time
        token, else (bot-account) brain-bridge's. Made on first use: a broken sign-in helper, or no token for the
        bridge, fails the step that needs it."""
        if self._maker is None:
            self._maker = self.gitlab(self.inp.admin_token or account(self, "bridge")[0])
        return self._maker

    def remember(self, secret: str) -> None:
        if secret:
            self._secrets.add(secret)

    def redact(self, text: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            if len(s) >= 6:
                text = text.replace(s, "[已隐去]")
        return text


# ---- one run at a time, the state, the run ---------------------------------------------------------------------- #

class Lock:
    """One run at a time per root, the app's and the command line's: an flock on <root>/.setup.lock until release()."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(root / ".setup.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise Busy("已经有一次在建了：等它建完再跑") from None
        self.fd: int | None = fd

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "Lock":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def busy(root: Path) -> bool:
    """Whether a run holds *root* now, this process's or another's (the lock is taken and given back at once)."""
    if fcntl is None:
        return False
    try:
        fd = os.open(root / ".setup.lock", os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def dumps(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def write_file(path: Path, text: str, mode: int = 0o600) -> bool:
    """*text* into *path*, atomically and with *mode*, unless it holds exactly that already. -> whether it was written."""
    try:
        if path.read_text(encoding="utf-8") == text:
            os.chmod(path, mode)
            return False
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def load_state(paths: Paths) -> dict[str, Any]:
    doc = parse_json(read_secret(paths.state))
    return doc if isinstance(doc, dict) else {}


def save_state(ctx: Ctx) -> None:
    ctx.state["updated_at"] = time.time()
    write_file(ctx.paths.state, ctx.redact(dumps(ctx.state)))


def begin(ctx: Ctx) -> None:
    """A new run's state on disk at once, so the page shows its steps as not done yet: the last run's findings stay,
    its step results go. The caller holds the root's Lock."""
    ctx.state = load_state(ctx.paths)
    ctx.state.update(schema=1, input=ctx.inp.public(), running=True, current=None, started_at=time.time(),
                     ended_at=None, steps=[])
    save_state(ctx)


def run(ctx: Ctx, on_step: Callable[[Step, StepResult], None] | None = None) -> dict[str, Any]:
    """The table, in order, on the caller's Lock (after begin(), or it begins here); *on_step* hears each result as it
    comes. -> view() of what it leaves."""
    if not ctx.state.get("running"):
        begin(ctx)
    try:
        for step in STEPS:
            ctx.state["current"] = step.id
            save_state(ctx)
            r = _attempt(ctx, lambda: step.run(ctx))
            r = replace(_redacted(ctx, r), id=step.id,
                        projects=tuple(_redacted(ctx, x) for x in r.projects))
            ctx.results.append(r)
            ctx.state["steps"].append(asdict(r))
            if on_step is not None:
                on_step(step, r)
            if r.status == "failed":
                break
    finally:
        ctx.state.update(running=False, current=None, ended_at=time.time())
        save_state(ctx)
    return view(ctx.state, ctx.paths)


def _redacted(ctx: Ctx, r: Any) -> Any:
    return replace(r, message=ctx.redact(r.message), detail=ctx.redact(r.detail)[-4000:])


def _attempt(ctx: Ctx, work: Callable[[], StepResult]) -> StepResult:
    try:
        return work()
    except StepFailed as exc:
        return failed(exc.message, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001 - GitLab, git, a file: said on the page, and the run stops here
        message, raw = _said(exc, ctx.inp.mode)
        return failed(message, detail=raw)


def each(ctx: Ctx, items: list[tuple[str, Any]], one: Callable[[Any], StepResult]) -> list[Row]:
    """*one* for each project of *items* (its name, what *one* takes), every one tried even after another failed: their
    results as rows."""
    rows = []
    for name, item in items:
        r = _attempt(ctx, lambda: one(item))
        rows.append(Row(name, r.status, r.message, r.link, r.detail))
    return rows


def of_found(ctx: Ctx) -> list[tuple[str, Proj]]:
    return [(p.path, p) for p in ctx.found]


def summed(rows: list[Row], done_said: str, waiting_said: str = "") -> StepResult:
    """A step whose result is its projects' (the labels, the kit.json MRs): with one project, that one's result; with
    more, the first of failed, waiting, done that one of them has, in words, over every row."""
    if len(rows) == 1:
        r = rows[0]
        return StepResult("", r.status, r.message, r.link, r.detail)
    status = next(s for s in WORST if any(r.status == s for r in rows))
    bad = [r.project for r in rows if r.status == "failed"]
    said = {"failed": "%d 个项目里 %d 个没成：%s" % (len(rows), len(bad), "、".join(bad)),
            "waiting": waiting_said, "done": done_said, "skipped": done_said}[status]
    return StepResult("", status, said, projects=tuple(rows))


def _gitlab_status(exc: BaseException) -> int | None:
    """The HTTP status of brain-kit's rest.HttpError (by its attribute: the module may have been loaded again since)."""
    status = getattr(exc, "status", None)
    return status if type(status) is int else None


def _said(exc: BaseException, mode: str = PROJECT_TOKEN) -> tuple[str, str]:
    raw = str(exc).strip() or type(exc).__name__
    status = _gitlab_status(exc)
    if "sign-in" in raw:
        return SIGN_IN, raw
    if status == 403:
        return FORBIDDEN[mode], raw
    if status is not None:
        return HTTP_SAID.get(status, "GitLab 出错了（HTTP %d）：等一会儿再跑一次" % status), raw
    if any(c.__name__ == "BrainError" for c in type(exc).__mro__):
        return "连不上 GitLab，或者它回的不是要的东西：看细节，好了再跑一次", raw
    if isinstance(exc, OSError):
        return "读写文件出错了：看细节", raw
    return "这一步出错了：看细节", raw


def view(state: Mapping[str, Any], paths: Paths, running: bool | None = None) -> dict[str, Any]:
    """GET /team/setup (TeamSetup in ui/src/types.ts): every step of the table with the last run's result (and its
    projects' when there were two or more), the one in progress, or todo; that run's input without the tokens; the
    accounts of the personal tokens kept here (bot-account). *running*: whether a run holds the root now. A run cut off
    part-way (the app or the command stopped) left running in its file with no one holding the root: cut_off is the
    step it had not finished."""
    on = bool(state.get("running")) if running is None else bool(running)
    got = {s.get("id"): s for s in state.get("steps") or [] if isinstance(s, dict)}
    steps = []
    for st in STEPS:
        r = got.get(st.id) or {}
        status = str(r.get("status") or ("running" if on and state.get("current") == st.id else "todo"))
        steps.append({"id": st.id, "title": st.title, **_shown(r, status),
                      "projects": [{"project": str(x.get("project") or ""), **_shown(x, str(x.get("status") or "todo"))}
                                   for x in r.get("projects") or [] if isinstance(x, dict)]})
    cut = bool(state.get("running")) and not on
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    roles = inp.get("roles") if isinstance(inp.get("roles"), dict) else {}
    projects = inp.get("projects")
    if not isinstance(projects, list):          # a run before bot-account mode kept its one project as "project"
        projects = [inp["project"]] if inp.get("project") else []
    kept = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    jenkins = inp.get("jenkins") if isinstance(inp.get("jenkins"), dict) else {}
    return {"running": on, "started_at": _time(state.get("started_at")),
            "ended_at": None if on else _time(state.get("ended_at")),
            "cut_off": next((s["id"] for s in steps if s["status"] == "todo"), None) if cut else None,
            "input": {"gitlab_base": str(inp.get("gitlab_base") or ""),
                      "mode": inp.get("mode") if inp.get("mode") in MODES else PROJECT_TOKEN,
                      "projects": [str(p) for p in projects],
                      "roles": {r: [str(n) for n in roles.get(r) or []] for r in ROLES},
                      "push": str(inp.get("push") or "auto"), "patrol": inp.get("patrol") is not False,
                      "issues": inp.get("issues") is True,
                      "jenkins": {"base": str(jenkins.get("base") or ""), "user": str(jenkins.get("user") or "")}},
            "accounts": {k: str((kept.get(k) if isinstance(kept.get(k), dict) else {}).get("username") or "")
                         for k in ("bridge", "bot")},
            "steps": steps, "root": str(paths.root)}


def _shown(r: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {"status": status, "message": str(r.get("message") or ""), "link": r.get("link") or None,
            "detail": str(r.get("detail") or "")}


def _time(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def tilde(path: Path) -> str:
    home = str(Path.home())
    p = str(path)
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def tool(name: str) -> str:
    path = os.pathsep.join([os.environ.get("PATH", ""), "/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"])
    return shutil.which(name, path=path) or name


def run_tool(cmd: list[str], what: str, cwd: Path | None = None, timeout: float = 120) -> str:
    """*cmd*'s output; StepFailed (「<what>没成」 and what it said) when it fails."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           cwd=str(cwd) if cwd else None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StepFailed("%s没成" % what, str(exc)) from None
    if p.returncode != 0:
        raise StepFailed("%s没成" % what, ((p.stderr or "") + (p.stdout or "")).strip())
    return p.stdout


# ---- 1 the projects --------------------------------------------------------------------------------------------- #

def step_project(ctx: Ctx) -> StepResult:
    """Each project as GitLab has it (id, default branch, web address, clone addresses), the role in it of the account
    that makes the labels and MRs (project-token: Maintainer or Owner, as they make the project tokens too; bot-account:
    Developer or more), and its .brain/kit.json on the default branch."""
    gl = ctx.gl
    me = gl.current_user() or {}
    ctx.owner = str(me.get("username") or "").lower()
    ctx.state["owner"] = ctx.owner
    ctx.found = []
    rows = each(ctx, [(n, n) for n in ctx.inp.projects], lambda name: find_project(ctx, me, name))
    ctx.state.pop("project", None)          # a run before bot-account mode kept its one project there
    ctx.state["projects"] = [p.record() for p in ctx.found]
    if len(rows) == 1 and rows[0].status == "done":
        return done("%s，%s" % (ctx.found[0].path, rows[0].message), link=rows[0].link)
    return summed(rows, "%d 个项目都找到了" % len(rows))


def find_project(ctx: Ctx, me: Mapping[str, Any], name: str) -> StepResult:
    gl, mode = ctx.gl, ctx.inp.mode
    try:
        p = gl.project(name) or {}
    except Exception as exc:  # noqa: BLE001 - a 404 said with the project's name; anything else as every step says it
        if _gitlab_status(exc) == 404:
            raise StepFailed("GitLab 上找不到 %s：项目名写错了，或者这个 token 看不到它" % name, str(exc))
        raise
    twin = next((f for f in ctx.found if f.id == int(p["id"])), None)
    if twin is not None:
        return failed("%s 和 %s 是同一个项目：只留一个" % (name, twin.path))
    path, branch = str(p.get("path_with_namespace") or name), str(p.get("default_branch") or "")
    who = ("你（%s）" if ctx.inp.admin_token else "桥的账号（%s）") % ctx.owner
    level = access_level(gl, p, me)
    if level < MAKER_LEVEL[mode]:
        return failed(MAKER_LOW[mode] % (who, path, LEVEL_NAME.get(level, "没有权限的人")))
    if not branch:
        return failed("%s 还没有默认分支：先推一个分支上去" % path)
    raw = gl.file_raw(p["id"], kitmr.KIT, branch)
    if raw is None:
        return failed("%s 的默认分支 %s 上没有 .brain/kit.json：先在个人电脑上导入这个仓库" % (path, branch))
    try:
        text = raw.decode("utf-8-sig")
        kit = json.loads(text)
    except ValueError as exc:
        return failed("默认分支上的 .brain/kit.json 读不懂：先在个人电脑上导入这个仓库，让它修好", detail=str(exc))
    if not isinstance(kit, dict):
        return failed("默认分支上的 .brain/kit.json 读不懂：先在个人电脑上导入这个仓库，让它修好")
    ctx.found.append(Proj(int(p["id"]), path, branch, p.get("web_url") or None, str(p.get("ssh_url_to_repo") or ""),
                          str(p.get("http_url_to_repo") or ""), kit, text))
    note = "；它用 Jira 跟踪需求：团队模式还配不了 Jira，交接会被拒" if kit.get("tracker") == "jira" else ""
    return done("默认分支 %s；%s是 %s%s" % (branch, who, LEVEL_NAME.get(level, level), note), link=p.get("web_url") or None)


def access_level(gl: Any, project: Mapping[str, Any], me: Mapping[str, Any]) -> int:
    """The token owner's access level in *project*: GitLab's permissions on it (the project's or its group's), else
    the member list; an instance admin counts as Owner."""
    if me.get("is_admin"):
        return OWNER
    perms = project.get("permissions") or {}
    level = max(int(((perms.get(k) or {}).get("access_level")) or 0) for k in ("project_access", "group_access"))
    if level or not me.get("id"):
        return level
    return member_level(gl, int(project["id"]), int(me["id"]))


def member_level(gl: Any, pid: int, uid: int) -> int:
    """*uid*'s access level in project *pid*, its own or its groups' (0: not a member)."""
    try:
        got = gl.call("GET", "/projects/%d/members/all/%d" % (pid, uid)) or {}
    except Exception as exc:  # noqa: BLE001 - not a member: no level
        if _gitlab_status(exc) != 404:
            raise
        return 0
    return int(got.get("access_level") or 0)


# ---- 2 the bridge's token --------------------------------------------------------------------------------------- #

def step_bridge_token(ctx: Ctx) -> StepResult:
    """The bridge's and the signer's GitLab account. project-token: the project access token brain-bridge (Developer,
    api, a year). bot-account: brain-bridge's personal token, given or kept: it takes api, and its account is
    Developer or more in every project and is not the person's own."""
    if ctx.inp.mode == PROJECT_TOKEN:
        rec, how = project_token(ctx, ctx.found[0], "bridge", "brain-bridge", BRIDGE_SCOPES)
        ctx.state["bridge_user"] = rec["username"]
        return done(token_said(how, rec))
    tok, me = account(ctx, "bridge")
    name = str(me.get("username") or "").lower()
    if ctx.inp.admin_token and name == ctx.owner:
        return failed("%s就是你自己（%s）：桥要一个专门的 GitLab 用户（比如 brain-bridge），它发的评论都算桥的，你的回答就不算数了"
                      % (ACCOUNT_NAME["bridge"], name))
    rec, problem = check_account(ctx, "bridge", tok, me)
    if problem:
        return failed(problem)
    ctx.state["bridge_user"] = name
    return done("桥的账号是 %s：%s" % (name, account_said(ctx, "bridge", rec)))


def account(ctx: Ctx, key: str) -> tuple[str, dict]:
    """bot-account: (the personal token of the bridge's or the bot's account, GitLab's user for it), once per run: the
    token given this run, else the one an earlier run kept under the root while it still signs in as the account kept
    with it. StepFailed when there is neither, or GitLab does not take the one given."""
    if key not in ctx._accounts:
        given = ctx.inp.bridge_token if key == "bridge" else ctx.inp.bot_token
        kept = (ctx.state.get("accounts") or {}).get(key)
        kept = kept if isinstance(kept, dict) else None
        tok = given or (read_secret(ctx.paths.token(key)) if kept else "")
        ctx.remember(tok)
        me, status = None, None
        try:
            me = (ctx.gitlab(tok).current_user() or {}) if tok else None
        except Exception as exc:  # noqa: BLE001 - one GitLab does not take, said below; anything else as every step says it
            status = _gitlab_status(exc)
            if status not in (401, 403):
                raise
        if given and status == 403:     # GET /user takes api, read_api or read_user
            raise StepFailed("%s读不出它是哪个 GitLab 用户：给它加上 read_user 权限" % ACCOUNT_NAME[key])
        if given and me is None:
            raise StepFailed("GitLab 不认%s：过期了、撤掉了，或者填错了" % ACCOUNT_NAME[key])
        if not given and (me is None or kept is None or me.get("id") != kept.get("user_id")):
            raise StepFailed("填上%s%s" % (ACCOUNT_NAME[key], "：这台机器上存的那个不能用了（过期了、撤掉了，或者换成了别的）"
                                           if kept else ""))
        ctx._accounts[key] = (tok, me)
    return ctx._accounts[key]


def check_account(ctx: Ctx, key: str, tok: str, me: Mapping[str, Any]) -> tuple[dict, str]:
    """(what setup-state.json keeps of the account, what is wrong with it or ''): its token takes one of
    ACCOUNT_SCOPES[key] (when this GitLab says what it takes) and its account is Developer or more in every project.
    Kept, its value in its own file, only when nothing is wrong."""
    info = pat_info(ctx, tok)
    scopes = info.get("scopes") if isinstance(info.get("scopes"), list) else None
    name = str(me.get("username") or "").lower()
    if scopes is not None and not set(scopes) & set(ACCOUNT_SCOPES[key]):
        return {}, "%s（%s 的）%s" % (ACCOUNT_NAME[key], name, ACCOUNT_NEEDS[key] % name)
    levels = {p.path: member_level(ctx.gl, p.id, int(me["id"])) for p in ctx.found}
    low = ["%s 是 %s" % (path, LEVEL_NAME[lv]) if lv in LEVEL_NAME else "%s 不是成员" % path
           for path, lv in levels.items() if lv < DEVELOPER]
    if low:
        return {}, "%s %s 在 %s：每个项目里都要是 Developer 以上，在 GitLab 上把它加进去" % (
            "桥的账号" if key == "bridge" else "机器人账号", name, "、".join(low))
    write_file(ctx.paths.token(key), tok + "\n")
    rec = dict(bot_fields(me), user_id=me.get("id"), expires_at=info.get("expires_at"), scopes=scopes)
    ctx.state.setdefault("accounts", {})[key] = rec
    return rec, ""


def account_said(ctx: Ctx, key: str, rec: Mapping[str, Any]) -> str:
    given = ctx.inp.bridge_token if key == "bridge" else ctx.inp.bot_token
    end = rec.get("expires_at")
    said = "用%s个人令牌，%s" % ("你填的" if given else "这台机器上存的", "%s 到期" % end if end else "GitLab 没说它哪天到期")
    if end and ends_soon(end):
        said += "；它快到期了：在 GitLab 上给 %s 换一个新令牌，再跑一次填上" % rec.get("username")
    return said


def pat_info(ctx: Ctx, tok: str) -> dict:
    """GitLab's record of personal token *tok* (GET /personal_access_tokens/self: its scopes, its last day); {} when
    this GitLab does not say (an older one, or a token whose scopes do not reach the API)."""
    try:
        got = ctx.gitlab(tok).call("GET", "/personal_access_tokens/self")
    except Exception as exc:  # noqa: BLE001
        if _gitlab_status(exc) in (403, 404):
            return {}
        raise
    return got if isinstance(got, dict) else {}


def tokens_of(ctx: Ctx, p: Proj) -> dict:
    """The project tokens this setup made for project *p* on this GitLab (bridge, bot), kept under
    "<GitLab>/projects/<id>": another GitLab's project of the same id has its own."""
    return ctx.state.setdefault("tokens", {}).setdefault("%s/projects/%d" % (ctx.inp.gitlab_base, p.id), {})


def project_token(ctx: Ctx, p: Proj, key: str, name: str, scopes: tuple[str, ...]) -> tuple[dict, str]:
    """(the token's record, how: a TOKEN_SAID key). The one this setup made on this GitLab for project *p* while GitLab
    has it active: kept while its file signs in as its bot user and it has more than RENEW_DAYS to go, else rotated (a
    new value for the same bot user, so kit.json stays as it is). A new one (Developer, *scopes*) only when there is
    none, or GitLab has it revoked, expired or gone. A token's value goes only into its own file. Nothing is revoked
    here: rotating a token revokes its old value, and a token this setup did not make is never rotated."""
    saved = tokens_of(ctx, p).get(key)
    live = active_token(ctx, p, saved) if isinstance(saved, dict) else None
    if live is None:
        return keep(ctx, p, key, name, ctx.gl.call("POST", "/projects/%d/access_tokens" % p.id, body={
            "name": name, "scopes": list(scopes), "access_level": DEVELOPER, "expires_at": until()})), "made"
    tok = read_secret(ctx.paths.token(key))
    ctx.remember(tok)
    me = whoami(ctx, tok) if tok else None
    signs_in = me is not None and me.get("id") == saved["user_id"]
    if signs_in and not ends_soon(live.get("expires_at")):
        saved.update(bot_fields(me), expires_at=live.get("expires_at"))
        return saved, "kept"
    got = ctx.gl.call("POST", "/projects/%d/access_tokens/%d/rotate" % (p.id, int(saved["id"])),
                      body={"expires_at": until()})
    return keep(ctx, p, key, name, got), "ending" if signs_in else "lost"


def active_token(ctx: Ctx, p: Proj, saved: Mapping[str, Any]) -> dict | None:
    """GitLab's record of the token *saved* names while it is active and still that token (its name, its bot user);
    None when it is revoked, expired or gone."""
    try:
        got = ctx.gl.call("GET", "/projects/%d/access_tokens/%d" % (p.id, int(saved["id"])))
    except Exception as exc:  # noqa: BLE001 - gone: made again; anything else as every step says it
        if _gitlab_status(exc) == 404:
            return None
        raise
    if not isinstance(got, dict) or got.get("active") is not True or got.get("revoked") \
            or (got.get("user_id"), got.get("name")) != (saved.get("user_id"), saved.get("name")):
        return None
    return got


def keep(ctx: Ctx, p: Proj, key: str, name: str, got: Any) -> dict:
    """A token GitLab just made or rotated: its value into its own file, its id into the state at once (before anything
    else can go wrong), then its bot user."""
    got = got if isinstance(got, dict) else {}
    tok = str(got.get("token") or "")
    if not tok or not got.get("user_id") or got.get("id") is None:
        raise StepFailed("GitLab 回的项目令牌 %s 里没有令牌的值：在 GitLab 上撤掉它，再跑一次" % name)
    ctx.remember(tok)
    write_file(ctx.paths.token(key), tok + "\n")
    (ctx.state.get("accounts") or {}).pop(key, None)       # the file no longer holds that personal token
    rec = {"id": got["id"], "user_id": got["user_id"], "name": name, "scopes": list(got.get("scopes") or ()),
           "expires_at": got.get("expires_at"), "username": "", "email": "", "display": ""}
    tokens_of(ctx, p)[key] = rec
    save_state(ctx)
    me = whoami(ctx, tok)
    if me is None:
        raise StepFailed("项目令牌 %s 的新值登不上 GitLab：再跑一次" % name)
    rec.update(bot_fields(me))
    return rec


def token_said(how: str, rec: Mapping[str, Any]) -> str:
    return TOKEN_SAID[how].format(name=rec["name"], user=rec["username"], end=rec.get("expires_at") or "?")


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def until() -> str:
    """A new token's last day (it ends at midnight UTC): TOKEN_DAYS days from today in UTC."""
    return (utc_today() + timedelta(days=TOKEN_DAYS)).isoformat()


def ends_soon(expires_at: Any) -> bool:
    """Whether a token GitLab says expires on *expires_at* (YYYY-MM-DD) has RENEW_DAYS days or fewer to go; one
    without a date never expires."""
    try:
        end = date.fromisoformat(str(expires_at)[:10])
    except ValueError:
        return False
    return end - utc_today() <= timedelta(days=RENEW_DAYS)


def whoami(ctx: Ctx, tok: str) -> dict | None:
    """GitLab's user for *tok*; None when GitLab does not take it (revoked, expired, not a token)."""
    try:
        return ctx.gitlab(tok).current_user() or {}
    except Exception as exc:  # noqa: BLE001
        if _gitlab_status(exc) in (401, 403):
            return None
        raise


def bot_fields(me: Mapping[str, Any]) -> dict[str, str]:
    return {"username": str(me.get("username") or "").lower(), "email": str(me.get("email") or me.get("commit_email") or ""),
            "display": str(me.get("name") or "")}


# ---- 3 how the runner pushes ------------------------------------------------------------------------------------ #

def step_push(ctx: Ctx) -> StepResult:
    """How runnerd clones and the runs push: one clone address for every project (runnerd's {project}). auto: this
    machine's SSH when `git ls-remote` reaches every project (the commits are brain-bot's, each kit.json's bot_users
    kept), else a token over HTTPS (its own account and address) once git reaches every project with it too: the
    project token brain-bot (project-token), the bot user's personal token (bot-account); ssh or token: that one only."""
    host = urllib.parse.urlsplit(ctx.inp.gitlab_base).hostname or ""
    reach = ctx.reach or (lambda url: git_reaches(ctx, url))
    why = ""
    if ctx.inp.push != "token":
        template, why = common_template(ctx, "ssh_url")
        missed, said = unreached(ctx, reach, lambda p: p.ssh_url) if template else ([], "")
        why = "\n".join(x for x in (why, said) if x)
        if template and not missed:
            ctx.state["push"] = {"mode": "ssh", "clone_url": template, "git_name": SSH_BOT[0], "git_email": SSH_BOT[1],
                                 "bot_user": ""}
            return done("这台机器的 SSH 连得上 GitLab：运行机用 SSH 拉取和推送")
        if ctx.inp.push == "ssh":
            if len(ctx.found) == 1:
                return failed("这台机器的 SSH 连不上 %s：先让 git ls-remote %s 能用，再跑一次" % (host, ctx.found[0].ssh_url),
                              detail=why)
            return failed("这台机器的 SSH 连不上 %s 上的 %s：先让 git ls-remote 能用，再跑一次"
                          % (host, "、".join(missed) or "这些项目"), detail=why)
    template, no = common_template(ctx, "http_url")
    if not template:
        return failed(no, detail=why)
    if ctx.inp.mode == PROJECT_TOKEN:
        rec, how = project_token(ctx, ctx.found[0], "bot", "brain-bot", BOT_SCOPES)
        tok, who, said = read_secret(ctx.paths.token("bot")), "项目令牌 brain-bot", token_said(how, rec)
    else:
        try:
            tok, me = account(ctx, "bot")
        except StepFailed as exc:
            return failed(exc.message + ("，或者让这台机器的 SSH 能用" if ctx.inp.push == "auto" else ""), detail=why)
        if me.get("id") == account(ctx, "bridge")[1].get("id"):
            return failed("机器人账号和桥的账号是同一个 GitLab 用户 %s：要两个不同的用户，运行机不能替桥说话" % me.get("username"))
        if ctx.inp.admin_token and str(me.get("username") or "").lower() == ctx.owner:
            return failed("%s就是你自己（%s）：运行机推的提交会算成机器人的，你自己的交接就都不算了。推代码要一个专门的 GitLab 用户"
                          "（比如 brain-bot）" % (ACCOUNT_NAME["bot"], ctx.owner))
        rec, problem = check_account(ctx, "bot", tok, me)
        if problem:
            return failed(problem, detail=why)
        who, said = "机器人账号 %s" % rec["username"], "推送的账号是 %s：%s" % (rec["username"], account_said(ctx, "bot", rec))
    missed, git_said = unreached(ctx, reach, lambda p: with_token(template, tok).format(project=p.path))
    if missed:
        where = host if len(ctx.found) == 1 else "%s 上的 %s" % (host, "、".join(missed))
        how = " SSH 和%s 都连不上" % who if ctx.inp.push == "auto" else "%s 连不上" % who
        return failed("这台机器的 git 用%s %s：运行机拉不到代码" % (how, where), detail="\n".join(x for x in (why, git_said) if x))
    email = rec.get("email") or "%s@noreply.%s" % (rec["username"], host)
    ctx.state["push"] = {"mode": "token", "clone_url": template, "git_name": rec.get("display") or "brain-bot",
                         "git_email": email, "bot_user": rec["username"]}
    return done("%s运行机用%s拉取和推送。%s"
                % ("这台机器的 SSH 连不上 GitLab，" if ctx.inp.push == "auto" else "",
                   "项目令牌" if ctx.inp.mode == PROJECT_TOKEN else "机器人账号的个人令牌", said), detail=why)


def common_template(ctx: Ctx, attr: str) -> tuple[str, str]:
    """(runnerd's clone template made from GitLab's *attr* address of every project, or ''; why there is none)."""
    kind = "SSH" if attr == "ssh_url" else "HTTPS"
    got = {p.path: clone_template(getattr(p, attr), p.path) for p in ctx.found}
    missing = [path for path, t in got.items() if not t]
    if missing:
        return "", "GitLab 没给%s的 %s 地址" % ("这个项目" if len(got) == 1 else " %s " % "、".join(missing), kind)
    if len(set(got.values())) > 1:
        return "", "这些项目的 %s 地址不在同一个地方：运行机只有一个拉取地址，{project} 换成项目" % kind
    return next(iter(got.values())), ""


def unreached(ctx: Ctx, reach: Callable[[str], tuple[bool, str]], url: Callable[[Proj], str]) -> tuple[list[str], str]:
    """The projects git does not reach at *url*, and what git said for them."""
    missed, said = [], []
    for p in ctx.found:
        ok, why = reach(url(p))
        if not ok:
            missed.append(p.path)
            said.append(why if len(ctx.found) == 1 else "%s: %s" % (p.path, why))
    return missed, "\n".join(x for x in said if x)


def clone_template(url: str, path: str) -> str:
    """*url*, GitLab's clone address of *path*, with the project as runnerd's {project}; '' when it is not one."""
    end = "%s.git" % path
    if not url.endswith(end) or "{" in url or "}" in url:
        return ""
    return url[: -len(end)] + "{project}.git"


def with_token(template: str, token: str) -> str:
    """An https clone template that signs in with *token* (GitLab takes any user name with an access token)."""
    u = urllib.parse.urlsplit(template)
    host = u.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((u.scheme, "oauth2:%s@%s" % (urllib.parse.quote(token, safe=""), host), u.path,
                                    "", ""))


def git_reaches(ctx: Ctx, url: str) -> tuple[bool, str]:
    """Whether this machine's git reaches *url* the way runnerd will (brain-kit's git_net: no prompt), within
    REACH_WAIT_S. -> (it does, what git said)."""
    common = bk.m("common")
    try:
        p = common.git_net(str(ctx.paths.root), "ls-remote", url, "HEAD", timeout=REACH_WAIT_S, check=False)
    except common.NetworkError as exc:
        return False, str(exc)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-2000:]


def bot_users(ctx: Ctx, p: Proj) -> list[str]:
    """kit.json bot_users for project *p*: the account the runner pushes as with a token; over SSH the ones *p*'s
    kit.json names, else brain-bot."""
    push = ctx.state["push"]
    if push["mode"] == "token":
        return [push["bot_user"]]
    return [str(u).lower() for u in p.kit.get("bot_users") or [] if str(u).strip()] or [SSH_BOT[0]]


# ---- 4 labels ----------------------------------------------------------------------------------------------------- #

def step_labels(ctx: Ctx) -> StepResult:
    """The labels the bridge moves (each kit.json's names), brain reflect's, and the issue label when 从 issue 接活 is
    on, made where a project has none."""
    rows = each(ctx, of_found(ctx), lambda p: project_labels(ctx, p))
    return summed(rows, "%d 个项目的标签都好了" % len(rows))


def project_labels(ctx: Ctx, p: Proj) -> StepResult:
    want: list[tuple[str, str]] = []
    for key, default, color in LABELS + ((ISSUE_LABEL,) if ctx.inp.issues else ()):
        name = str(p.kit.get(key) or default) if key else default
        if name.lower() not in {w.lower() for w, _ in want}:
            want.append((name, color))
    have = {str(row.get("name") or "").lower()
            for row in ctx.gl.pages("/projects/%d/labels" % p.id, {}, 100, 20, "labels")}
    made = []
    for name, color in want:
        if name.lower() in have:
            continue
        try:
            ctx.gl.call("POST", "/projects/%d/labels" % p.id, body={"name": name, "color": color})
        except Exception as exc:  # noqa: BLE001 - one made meanwhile is there
            if _gitlab_status(exc) != 409:
                raise
            continue
        made.append(name)
    if made:
        return done("建了 %d 个标签：%s" % (len(made), "、".join(made)))
    return done("标签都在：%s" % "、".join(n for n, _ in want))


# ---- 5 who is who ------------------------------------------------------------------------------------------------- #

def step_identities(ctx: Ctx) -> StepResult:
    """identities.json in brain-authz's shape (brain-kit's examples/identities.json): in users every member with
    Developer or more of any of the projects (each of them may hand over), the bots left out (project and group token
    users, the bridge's account, the accounts the runner pushes as); the roles as typed, each one a member of a project
    and not the bridge's account. A role left empty is the one-time token's owner; with no one-time token (bot-account),
    安全 and 数据 left empty are the 开发 people."""
    bridge = str(ctx.state.get("bridge_user") or "")
    bots = {bridge} | {u for p in ctx.found for u in bot_users(ctx, p)}
    seen: dict[str, dict[str, int]] = {}      # project path -> member -> access level, people only

    def members(p: Proj) -> StepResult:
        got = seen.setdefault(p.path, {})
        for m in ctx.gl.pages("/projects/%d/members/all" % p.id, {}, 100, 50, "members"):
            name = str(m.get("username") or "").lower()
            if name and name not in bots and not BOT_USER_RE.match(name):
                got[name] = max(got.get(name, 0), int(m.get("access_level") or 0))
        return done("")
    rows = each(ctx, of_found(ctx), members)
    if any(r.status == "failed" for r in rows):
        return summed(rows, "")
    level: dict[str, int] = {}
    for got in seen.values():
        for name, lv in got.items():
            level[name] = max(level.get(name, 0), lv)
    fallback = [ctx.owner] if ctx.inp.admin_token else list(ctx.inp.roles.get("builder") or ())
    roles = {r: list(ctx.inp.roles.get(r) or ()) or fallback for r in ROLES}
    named = list(dict.fromkeys(n for r in ROLES for n in roles[r]))
    if bridge in named:
        return failed("%s 是桥的账号，不能当角色：桥的账号发的评论都算桥的，它的回答不算数" % bridge)
    unknown = [n for n in named if n not in level]
    if unknown:
        where = " %s 的" % ctx.found[0].path if len(ctx.found) == 1 else "这几个项目里任何一个的"
        return failed("这些人不是%s成员：%s。先在 GitLab 上把他们加进项目，或者改填别人" % (where, "、".join(unknown)))
    users = sorted(n for n, lv in level.items() if lv >= DEVELOPER)
    wrote = write_file(ctx.paths.identities, dumps({"users": {n: {} for n in users}, "roles": roles}))
    said = done("%s：%d 个 Developer 以上的人能交接；%s" % ("写好了身份表" if wrote else "身份表没变", len(users),
                                                   "；".join("%s %s" % (ROLE_NAME[r], "、".join(roles[r])) for r in ROLES)))
    if len(ctx.found) == 1:
        return said
    per = []
    for p in ctx.found:
        away = [n for n in named if n not in seen[p.path]]
        per.append(Row(p.path, "done", "%d 个 Developer 以上的人%s" % (
            sum(1 for lv in seen[p.path].values() if lv >= DEVELOPER),
            "；%s 不是这个项目的成员，答不了这里的问题" % "、".join(away) if away else "")))
    return replace(said, projects=tuple(per))


# ---- 6 状态中转 ------------------------------------------------------------------------------------------------- #

def step_status(ctx: Ctx) -> StepResult:
    """状态中转 (team/relay.py): the team server writes each project's status into the project itself, on its branch
    brain/status, so GitLab lets whoever can read the project read its status, and nobody else. The bridge's account
    pushes it (it is a Developer or more in every project); here each project's protected branches are checked for a
    rule that keeps it from pushing brain/status, which waits for a person to lift. Nothing is made: the team server's
    first write makes the branch."""
    ctx.state.pop("status_project", None)       # an earlier run's status project of its own: left as it is, unused
    rows = each(ctx, of_found(ctx), lambda p: project_status(ctx, p))
    return summed(rows, "%d 个项目的 brain/status 桥的账号都能推" % len(rows),
                  "有的项目的 brain/status 受保护，桥的账号推不了：看每个项目那一行")


def project_status(ctx: Ctx, p: Proj) -> StepResult:
    name, uid, level = bridge_in(ctx, p)
    try:
        rules = ctx.gl.call("GET", "/projects/%d/protected_branches" % p.id, {"per_page": 100}) or []
    except Exception as exc:  # noqa: BLE001 - a token that may not read the rules: said; anything else as every step says it
        if _gitlab_status(exc) not in (401, 403):
            raise
        return done("看不了 %s 的受保护分支（这个 token 不够）：团队服务器第一次写 %s 时就知道能不能推，推不了会在它的运行机页上说"
                    % (p.path, STATUS_BRANCH))
    held = [r for r in rules if isinstance(r, dict) and branch_matches(str(r.get("name") or ""), STATUS_BRANCH)]
    if held and not any(may_push(a, uid, level) for r in held for a in r.get("push_access_levels") or []
                        if isinstance(a, dict)):
        return waiting("受保护分支 %s 管着 %s，%s 推不了：在 GitLab 上放开它（Developer 能推），团队服务器才写得了这个项目的状态"
                       % ("、".join(str(r["name"]) for r in held), STATUS_BRANCH, name))
    there = kitmr.branch_exists(ctx.gl, p.id, STATUS_BRANCH)
    return done("%s %s，%s 能推" % (STATUS_BRANCH, "已经在了" if there else "还没有：团队服务器第一次写的时候建", name))


def bridge_in(ctx: Ctx, p: Proj) -> tuple[str, int | None, int]:
    """(the bridge's GitLab user, its id, its level in *p*): a project token's is Developer."""
    if ctx.inp.mode == PROJECT_TOKEN:
        rec = tokens_of(ctx, ctx.found[0]).get("bridge") or {}
        return ctx.state["bridge_user"], rec.get("user_id"), DEVELOPER
    _tok, me = account(ctx, "bridge")
    return ctx.state["bridge_user"], me.get("id"), member_level(ctx.gl, p.id, int(me["id"]))


def branch_matches(rule: str, branch: str) -> bool:
    """GitLab's protected branch rule *rule* (a name, or one with * for any run of characters) covers *branch*."""
    return re.fullmatch(re.escape(rule).replace(r"\*", ".*"), branch) is not None


def may_push(entry: Mapping[str, Any], uid: int | None, level: int) -> bool:
    """One push_access_levels entry of a protected branch lets the user *uid* at *level* push: it names that user, or
    (naming no user or group) a level it reaches; No one (0) lets nobody."""
    if entry.get("user_id") is not None:
        return uid is not None and int(entry["user_id"]) == int(uid)
    if entry.get("group_id") is not None or entry.get("deploy_key_id") is not None:
        return False
    return 0 < int(entry.get("access_level") or 0) <= level


# ---- 7 kit.json, through an MR per project ----------------------------------------------------------------------- #

def step_kit_mr(ctx: Ctx) -> StepResult:
    """Each project's kit.json on its default branch names the bridge's account, the bot's, GitLab and the default
    branch (MIGRATE 第二部分 4). Where it does not: the change on branch brain/runner-setup and an MR to the default
    branch (kitmr.kit_mr), which a person merges: waiting until then. The MRs that wait are kept in the state
    (kit_mr), with whether each only switches work sources and which ones (SWITCHES)."""
    ctx.state["kit_mr"] = {}
    rows = each(ctx, of_found(ctx), lambda p: project_kit_mr(ctx, p))
    if not ctx.state["kit_mr"]:
        ctx.state.pop("kit_mr")
    n = sum(1 for r in rows if r.status == "waiting")
    return summed(rows, "%d 个项目的 .brain/kit.json 都写好了运行机要的几项" % len(rows),
                  "%d 个项目的 kit.json MR 等你在 GitLab 上合并" % n)


def project_kit_mr(ctx: Ctx, p: Proj) -> StepResult:
    want = wanted_kit(ctx, p)
    if want == p.kit:
        return done("默认分支上的 .brain/kit.json 已经写好了运行机要的几项")
    changed = kit_changes(p, want)
    only, turns = sources_only(p, want), switched(p, want)
    lines = [said[on] for (_, _, said, _), on in turns]
    text = ("一键建运行机开的 MR：" + "\n\n".join(lines) if only else
            MR_TEXT + "".join("\n\n" + x for x in lines))
    mr, opened = kitmr.kit_mr(ctx.gl, p.id, p.default_branch, BRANCH, want, p.kit_text, title=MR_TITLE, text=text,
                              message="运行机：kit.json 写上 %s" % "、".join(changed))
    ctx.state["kit_mr"][p.path] = {"iid": mr.get("iid"), "web_url": mr.get("web_url"), "sources_only": only,
                                   "switches": [sw[0] for sw, _ in turns]}
    return waiting("%s MR !%s：请在 GitLab 上合并这个 MR" % ("开了" if opened else "还没合并", mr.get("iid")),
                   link=mr.get("web_url") or None)


def kit_changes(p: Proj, want: dict[str, Any]) -> list[str]:
    """The keys of *p*'s kit.json the MR changes."""
    return [k for k in want if want[k] != p.kit.get(k)]


def sources_only(p: Proj, want: dict[str, Any]) -> bool:
    """Whether *p*'s kit.json MR only switches work sources on or off (SWITCHES): everything the hand-over needs is on
    the default branch already."""
    kinds = set(sources.enabled(want)) ^ set(sources.enabled(p.kit))
    return kit_changes(p, want) == ["work_sources"] and sources.HANDOFF not in kinds


def switched(p: Proj, want: dict[str, Any]) -> list[tuple[tuple, bool]]:
    """The SWITCHES rows whose kind *p*'s kit.json MR turns on or off, each with whether it turns it on."""
    before, after = set(sources.enabled(p.kit)), set(sources.enabled(want))
    return [(sw, sw[0] in after) for sw in SWITCHES if (sw[0] in before) != (sw[0] in after)]


def wanted_kit(ctx: Ctx, p: Proj) -> dict[str, Any]:
    """*p*'s kit.json as it is on the default branch, with what the runner needs set."""
    want = json.loads(json.dumps(p.kit))
    want["bridge_user"] = ctx.state["bridge_user"]
    want["bot_users"] = bot_users(ctx, p)
    want["bot_emails"] = [ctx.state["push"]["git_email"]]
    gitlab = want.get("gitlab") if isinstance(want.get("gitlab"), dict) else {}
    want["gitlab"] = dict(gitlab, base=ctx.inp.gitlab_base, project=p.path)
    want["default_branch"] = p.default_branch
    raw = want.get("work_sources")
    kinds = [k for k in raw if isinstance(k, str)] if isinstance(raw, list) else []
    named = isinstance(raw, list)
    for kind, flag, _, _ in SWITCHES:
        if getattr(ctx.inp, flag):
            kinds += [k for k in (sources.HANDOFF, kind) if k not in kinds]
            named = True
        elif kind in kinds:
            kinds = [k for k in kinds if k != kind]
    if named:
        want["work_sources"] = kinds
    return want


# ---- 8 folders ---------------------------------------------------------------------------------------------------- #

def step_dirs(ctx: Ctx) -> StepResult:
    """The spools (runnerd's requests, ready, failed; the signer's requests, signed, refused), the vouchers, the keys'
    folder, the runs and the clone cache. The runs go under ~/workplace or ~/workspace: KiroCrew's sessions work there."""
    ps = ctx.paths
    home, runs = os.path.realpath(str(Path.home())), os.path.realpath(str(ps.runs))
    if not any(runs == os.path.join(home, w) or runs.startswith(os.path.join(home, w) + os.sep) for w in WORK_PARENTS):
        return failed("运行机的工作区 %s 要在 ~/workplace 或 ~/workspace 下面：KiroCrew 只让 AI 在那里干活" % ps.runs)
    want = ([ps.spool / d for d in ("requests", "ready", "failed")]
            + [ps.signer_spool / d for d in ("requests", "signed", "refused")]
            + [ps.vouchers, ps.signer_vouchers, ps.keys, ps.runs, ps.cache])
    made = [d for d in want if not d.is_dir()]
    for d in want:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return done("建了 %d 个目录" % len(made) if made else "目录都在")


# ---- 9 the signing key ------------------------------------------------------------------------------------------- #

def step_signing_key(ctx: Ctx) -> StepResult:
    """The signer's ed25519 key, made once, and allowed_signers from it: the line MIGRATE 第三部分 5 builds."""
    ps = ctx.paths
    keygen = tool("ssh-keygen")
    made = not ps.key.is_file()
    if made:
        with tempfile.TemporaryDirectory(dir=ps.keys) as tmp:
            k = Path(tmp) / ps.key.name
            run_tool([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(k), "-C", "brain-bridge"], "生成签名密钥")
            os.replace(k, ps.key)
    pub = run_tool([keygen, "-y", "-f", str(ps.key)], "读签名密钥的公钥").split()
    if len(pub) < 2:
        raise StepFailed("签名密钥 %s 读不出公钥：它坏了的话，删掉它再跑一次" % ps.key)
    wrote = write_file(ps.allowed, 'brain-bridge namespaces="brain-voucher" %s %s\n' % (pub[0], pub[1]), mode=0o644)
    return done("生成了签名密钥" if made else "签名密钥已经有了" if not wrote else "签名密钥已经有了，重写了公钥名单")


# ---- 10 the three configs ---------------------------------------------------------------------------------------- #

def step_configs(ctx: Ctx) -> StepResult:
    """bridge.json, runnerd.json and signer.json in the shapes of MIGRATE 第三部分 7, for one OS user and every project:
    no MR filter, no brain-intake, vouchers not published, the signer on the bridge's token with its account as
    bridge_users, one pseudonym_salt for both (made once, kept on every run). A file is written only when its content
    changes."""
    ps = ctx.paths
    salt = old_salt(ps) or secrets.token_hex(16)
    ctx.remember(salt)
    docs = ((ps.bridge_json, bridge_doc(ctx, salt)), (ps.runnerd_json, runnerd_doc(ctx)),
            (ps.signer_json, signer_doc(ctx, salt)))
    wrote = [path.name for path, doc in docs if write_file(path, dumps(doc))]
    return done("写了 %s" % "、".join(wrote) if wrote else "三份配置都没变")


def old_salt(ps: Paths) -> str:
    for path in (ps.bridge_json, ps.signer_json):
        doc = parse_json(read_secret(path))
        salt = doc.get("pseudonym_salt") if isinstance(doc, dict) else None
        if isinstance(salt, str) and salt.strip():
            return salt
    return ""


def kept_entries(path: Path, wanted: list[str]) -> list[Any]:
    """bridge.json's project entries for these paths: an entry written as an object (a person's `staging` block) is
    kept as it is, the others are the plain path."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("projects") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    old = {r["project"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("project"), str)}
    return [old.get(w, w) for w in wanted]


def bridge_doc(ctx: Ctx, salt: str) -> dict[str, Any]:
    ps = ctx.paths
    return {
        "projects": kept_entries(ps.bridge_json, [p.path for p in ctx.found]),
        "gitlab": {"base": ctx.inp.gitlab_base, "token_env": NO_TOKEN_ENV["bridge"], "token_file": str(ps.token("bridge"))},
        "identities": str(ps.identities),
        "ledger": str(ps.root / "bridge-ledger.sqlite"),
        "spool_dir": str(ps.spool),
        "vouchers_dir": str(ps.vouchers),
        "runner_runs_root": str(ps.runs),
        "publish_vouchers": False,
        "max_inflight": 1,
        "max_per_repo": 1,
        "pseudonym_salt": salt,
        "kirocrew": {"agent": "brain-run", "memory_mode": "incognito"},
        "signer": {"type": "spool", "spool_dir": str(ps.signer_spool), "allowed_signers": str(ps.allowed)},
        **jenkins_doc(ctx),
    }


@dataclass(frozen=True)
class JenkinsInput:
    """Jenkins, for MR 巡检 to read a failed build's log there (team/jenkins.py) as a read-only user."""
    base: str
    user: str
    token: str = field(repr=False)          # "": the one this machine saved before (Paths.jenkins_token)

    @classmethod
    def parse(cls, raw: Any) -> "JenkinsInput | None":
        """None when no address is typed."""
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise SetupRefused("Jenkins 那几项的格式不对：刷新页面再试")
        base = str(raw.get("base") or "").strip().rstrip("/")
        if not base:
            return None
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ("https", "http") or not u.hostname or "@" in u.netloc or u.query or u.fragment \
                or CTRL_RE.search(base):
            raise SetupRefused("Jenkins 地址要写成 https://jenkins.example.com 这样")
        user = str(raw.get("user") or "").strip()
        if not user or CTRL_RE.search(user) or ":" in user or len(user) > 255:
            raise SetupRefused("填上 Jenkins 只读用户的用户名")
        token = raw.get("token")
        token = token.strip() if isinstance(token, str) else ""
        if CTRL_RE.search(token) or len(token) > 1000:
            raise SetupRefused("Jenkins 的 token 不对：它是一串没有空格的字符")
        return cls(base, user, token)


def jenkins_doc(ctx: Ctx) -> dict[str, Any]:
    """bridge.json's "jenkins" when Jenkins is typed (team/jenkins.py): its address, its read-only user and the file of
    its token, written with the token typed this run, else the one saved there before."""
    j, path = ctx.inp.jenkins, ctx.paths.jenkins_token
    if j is None:
        return {}
    if j.token:
        write_file(path, j.token + "\n")
    elif not read_secret(path):
        raise StepFailed("Jenkins 的 token 没填，这台机器上也没存过：填上它再跑一次")
    return {"jenkins": {"base": j.base, "user": j.user, "token_file": str(path)}}


def runnerd_doc(ctx: Ctx) -> dict[str, Any]:
    ps, push = ctx.paths, ctx.state["push"]
    clone = push["clone_url"]
    if push["mode"] == "token":
        tok = read_secret(ps.token("bot"))
        if not tok:
            raise StepFailed("找不到运行机推送用的令牌 %s：再跑一次" % tilde(ps.token("bot")))
        ctx.remember(tok)
        clone = with_token(clone, tok)
    return {"spool_dir": str(ps.spool), "cache_dir": str(ps.cache), "runs_root": str(ps.runs),
            "allowed_signers": str(ps.allowed), "clone_url": clone, "kit_dir": str(ps.kit_dir),
            "agent_spec": str(ps.agent_spec), "git_name": push["git_name"], "git_email": push["git_email"]}


def signer_doc(ctx: Ctx, salt: str) -> dict[str, Any]:
    ps = ctx.paths
    return {
        "brainkit": str(ps.kit_dir),
        "spool": str(ps.signer_spool),
        "signing_key": str(ps.key),
        "projects": [p.path for p in ctx.found],
        "gitlab": {"base": ctx.inp.gitlab_base, "token_env": NO_TOKEN_ENV["signer"], "token_file": str(ps.token("bridge"))},
        "identities": str(ps.identities),
        "vouchers_dir": str(ps.signer_vouchers),
        "publish_vouchers": False,
        "ledger": str(ps.root / "signer-ledger.sqlite"),
        "pseudonym_salt": salt,
        "bridge_users": [ctx.state["bridge_user"]],
    }


# ---- 11 the brain-run agent ------------------------------------------------------------------------------------- #

def step_agent(ctx: Ctx) -> StepResult:
    """The runner's agent, written by brain-kit itself: `brain-runnerd --config runnerd.json setup`."""
    ps = ctx.paths
    cmd = [ps.python, "-I", str(ps.kit_dir / "bin" / "brain-runnerd"), "--config", str(ps.runnerd_json), "setup"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, cwd=str(ps.root))
    except (OSError, subprocess.SubprocessError) as exc:
        raise StepFailed("brain-runnerd 跑不起来", str(exc)) from None
    out = (p.stdout or "").strip()
    first = out.splitlines()[0] if out else ""
    probs = [line for line in out.splitlines() if line.startswith("problem: ")]
    if p.returncode != 0 or probs or not (first.startswith("wrote ") or first.endswith(" is up to date")):
        return failed("brain-run 的设置没写好，或者没过 brain-kit 的检查", detail=(out + "\n" + (p.stderr or "")).strip())
    return done("%s brain-run 的设置：%s" % ("写好了" if first.startswith("wrote ") else "已经是新的", tilde(ps.agent_spec)),
                detail=out)


# ---- 12 the brain-patrol agent ---------------------------------------------------------------------------------- #

def step_patrol_agent(ctx: Ctx) -> StepResult:
    """brain-patrol, the agent of MR 巡检's sessions (team/patrol.py agent_spec): brain-run's spec, its own short prompt,
    no Lean hooks. Beside brain-run.json; written only when its content changes."""
    ps = ctx.paths
    if not ctx.inp.patrol:
        return skipped("没勾「巡检所有开着的 MR」：不写 brain-patrol")
    wrote = write_file(ps.patrol_spec, dumps(patrol_mod.agent_spec(str(ps.kit_dir))), mode=0o644)
    return done("%s brain-patrol 的设置：%s" % ("写好了" if wrote else "已经是新的", tilde(ps.patrol_spec)))


# ---- 13 the signer ------------------------------------------------------------------------------------------------ #

def step_signer(ctx: Ctx) -> StepResult:
    """The signer, run by the app itself in same-user mode (signerproc): started when it is not running, or when it
    runs with an older signer.json."""
    ensure = ctx.signer or (lambda ps: signerproc.ensure(ps.signer_json, ps.app_dir, ps.python))
    try:
        pid, started = ensure(ctx.paths)
    except signerproc.SignerFailed as exc:
        return failed(exc.text, detail=exc.detail)
    return done("签名服务%s（进程 %d）" % ("开起来了" if started else "在跑", pid))


# ---- 14 team mode ------------------------------------------------------------------------------------------------- #

def step_team_mode(ctx: Ctx) -> StepResult:
    """Team mode on with the two configs and the same-user trial, through the app's settings (its TeamRuntime starts,
    and the signer with it), and what it says then: which kit.json MRs still hold up the hand-overs, or MR 巡检. The app
    starts it again when a file it read at its start has changed since, whichever run changed it."""
    ps = ctx.paths
    if ctx.team_mode is None:
        return skipped("这次没打开团队模式：在设置 → 团队模式里填上 %s 和 %s，勾上本机试用"
                       % (tilde(ps.bridge_json), tilde(ps.runnerd_json)))
    want = {"enabled": True, "bridge_config": str(ps.bridge_json), "runnerd_config": str(ps.runnerd_json),
            "dev_same_user": True}
    st = ctx.team_mode(want) or {}
    if not st.get("ok"):
        return failed(str(st.get("error") or "团队模式没开起来"), detail=str(st.get("error_raw") or ""))
    return done(str(st.get("said") or "团队模式开了") + mrs_said(ctx))


def mrs_said(ctx: Ctx) -> str:
    """What the kit.json MRs that wait hold up until they are merged: the hand-overs of their projects, or (MRs that
    only switch work sources) what their switches start or stop."""
    wait = ctx.state.get("kit_mr") or {}
    handoff = [path for path, mr in wait.items() if not mr.get("sources_only")]
    if not wait:
        return ""
    if handoff:
        return ("；可是上面那个 MR 合并以前，交接都会被拒" if len(ctx.found) == 1 else
                "；可是 %s 的 kit.json MR 合并以前，这些项目的交接都会被拒" % "、".join(handoff))
    kinds = {k for mr in wait.values() for k in mr.get("switches") or ()}
    turns = "，".join("%s才%s" % (what, "开始" if getattr(ctx.inp, flag) else "停")
                      for kind, flag, _, what in SWITCHES if kind in kinds)
    if len(ctx.found) == 1:
        return "；上面那个 MR 合并以后，" + turns
    return "；%s 的 kit.json MR 合并以后，那里的%s" % ("、".join(wait), turns)


STEPS: tuple[Step, ...] = (
    Step("project", "找到项目", step_project),
    Step("bridge_token", "桥的令牌", step_bridge_token),
    Step("push", "运行机怎么推送", step_push),
    Step("labels", "MR 标签", step_labels),
    Step("identities", "身份表", step_identities),
    Step("status", "状态中转", step_status),
    Step("kit_mr", "kit.json 的 MR", step_kit_mr),
    Step("dirs", "目录", step_dirs),
    Step("signing_key", "签名密钥", step_signing_key),
    Step("configs", "三份配置", step_configs),
    Step("agent", "brain-run agent", step_agent),
    Step("patrol_agent", "brain-patrol agent", step_patrol_agent),
    Step("signer", "签名服务", step_signer),
    Step("team_mode", "打开团队模式", step_team_mode),
)
