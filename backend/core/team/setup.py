"""一键建运行机: the team runner in same-user mode, built by the app from one GitLab token typed once.

MIGRATE.md 第二、三部分 as one ordered table (STEPS) for the trial where the signer, the app and the agents are one OS user
(settings team.dev_same_user): no sudo, no second user, nothing under /etc. Each step looks at what is there and makes
only what is missing, so the table runs again safely: a second run makes nothing new, and a run that stopped part-way
converges when it runs again. The first failed step stops the run; a step that waits for a person (the kit.json MR)
does not.

Under one root (default ~/.brain/team-setup; files 0600, folders 0700):
  setup-state.json       the last run: each step's result and what the steps found (the project, the tokens' ids and
                         bot usernames, how the runner pushes, the MR), with times. Nothing secret.
  bridge-gitlab-token    project access token brain-bridge (api, Developer): the bridge's and the signer's GitLab
  bot-gitlab-token       project access token brain-bot (token push mode only): runnerd's clone and the runs' pushes
  identities.json, keys/, bridge.json, runnerd.json, signer.json, the spools, vouchers and ledgers, signer.pid / .log
Under ~/workplace/brain-team (KiroCrew lets agents work only under ~/workplace): runs/ and cache/. The brain-run agent:
~/.kiro/agents/brain-run.json. The person's token (SetupInput.admin_token) lives in memory for one run: it is never
written, logged or answered, and text from GitLab or a command goes through Ctx.redact before it is kept.
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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import bk
from ..constants import APP_ROOT, PYTHON_BIN
from . import signerproc

try:
    import fcntl
except ImportError:  # pragma: no cover - not macOS or Linux: nothing keeps two runs apart
    fcntl = None  # type: ignore[assignment]

ROLES = ("product", "builder", "security", "data")
ROLE_NAME = {"product": "产品", "builder": "开发", "security": "安全", "data": "数据"}
PUSH_MODES = ("auto", "ssh", "token")
LEVEL_NAME = {10: "Guest", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}
MAINTAINER, DEVELOPER, OWNER = 40, 30, 50
TOKEN_DAYS = 30
BRIDGE_SCOPES = ("api",)
# GET /user, which the reuse check and the bot's username and address come from, needs read_user as well
BOT_SCOPES = ("read_repository", "write_repository", "read_user")
SSH_BOT = ("brain-bot", "brain-bot@noreply.invalid")    # git_name, git_email of a runner pushing over this machine's SSH
REACH_WAIT_S = 20
KIT = ".brain/kit.json"
BRANCH = "brain/runner-setup"
MR_TITLE = "运行机：更新 .brain/kit.json"
MR_TEXT = ("一键建运行机开的 MR：运行机要 kit.json 写上 bridge_user（桥的账号）、bot_users 和 bot_emails（运行机推送用的账号和邮箱）、"
           "gitlab 和 default_branch。合并以前，签名服务不签交接。")
# (kit.json key, default name, colour): the labels the bridge moves, and brain reflect's
LABELS = (("handoff_label", "ai-handoff", "#1f75cb"), ("running_label", "ai-running", "#108548"),
          ("rejected_label", "ai-handoff-rejected", "#dd2b0e"), ("", "brain-reflect", "#6699cc"))
# an environment variable nobody sets: the bridge and the signer read their token from token_file only (the default
# names, BRIDGE_GITLAB_TOKEN and SIGNER_GITLAB_TOKEN, may hold something else)
NO_TOKEN_ENV = {"bridge": "BRAIN_TEAM_NO_TOKEN_ENV", "signer": "BRAIN_SIGNER_NO_TOKEN_ENV"}
WORK_PARENTS = ("workplace", "workplaces", "workspace", "workspaces")
USER_RE = re.compile(r"^[a-z0-9_.][a-z0-9_.-]{0,254}$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.][A-Za-z0-9_.-]*)+$")
CTRL_RE = re.compile(r"[\x00-\x20\x7f]")
HTTP_SAID = {401: "GitLab 不认这个 token：过期了，或者填错了",
             403: "这个 token 做不了这一步：要有 api 权限，你在项目里要是 Maintainer 或 Owner",
             404: "GitLab 上找不到要用的东西：看细节"}
SIGN_IN = "GitLab 回的是登录页：这个 GitLab 要单点登录，先放好它的登录辅助文件（~/.brain/local_transport.py），再跑一次"


class SetupRefused(ValueError):
    """The input cannot be used, said in plain Chinese for the page."""


class Busy(RuntimeError):
    """Another run holds the root."""


class StepFailed(Exception):
    """A step cannot go on: its plain words, and what GitLab or a command said."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message, self.detail = message, detail


@dataclass(frozen=True)
class SetupInput:
    gitlab_base: str
    project: str                                  # group/repo
    admin_token: str = field(repr=False)          # the person's one-time token (api): this run's memory only
    roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)   # role -> usernames; none: the token's owner
    push: str = "auto"                            # auto | ssh | token

    @classmethod
    def parse(cls, raw: Any) -> "SetupInput":
        """The page's (or the command line's) fields, checked; SetupRefused says what is wrong."""
        if not isinstance(raw, Mapping):
            raise SetupRefused("页面发来的内容不对：刷新页面再试")
        base = str(raw.get("gitlab_base") or "").strip().rstrip("/")
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ("https", "http") or not u.hostname or "@" in u.netloc or u.query or u.fragment \
                or CTRL_RE.search(base):
            raise SetupRefused("GitLab 地址要写成 https://gitlab.example.com 这样")
        project = str(raw.get("project") or "").strip()
        if project.startswith(base + "/"):            # the project's page, pasted
            project = project[len(base) + 1:]
        elif "://" in project:
            project = urllib.parse.urlsplit(project).path
        project = project.split("/-/", 1)[0].strip("/")
        project = project[:-4] if project.endswith(".git") else project
        if not PROJECT_RE.match(project):
            raise SetupRefused("项目要写成「组/仓库」，比如 grp/pay")
        token = raw.get("admin_token")
        token = token.strip() if isinstance(token, str) else ""
        if not token or CTRL_RE.search(token) or len(token) > 1000:
            raise SetupRefused("填一个一次性的 GitLab 个人 token（要有 api 权限）")
        got = raw.get("roles") or {}
        if not isinstance(got, Mapping):
            raise SetupRefused("角色的格式不对：刷新页面再试")
        roles: dict[str, tuple[str, ...]] = {}
        for role, names in got.items():
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
        push = raw.get("push") or "auto"
        if push not in PUSH_MODES:
            raise SetupRefused("推送方式只能是 auto、ssh 或 token")
        return cls(base, project, token, roles, push)

    def public(self) -> dict[str, Any]:
        """What setup-state.json keeps of the input: all of it but the token."""
        return {"gitlab_base": self.gitlab_base, "project": self.project, "push": self.push,
                "roles": {r: list(self.roles.get(r) or ()) for r in ROLES}}


@dataclass(frozen=True)
class StepResult:
    id: str
    status: str               # done | waiting | failed | skipped
    message: str              # short plain Chinese
    link: str | None = None
    detail: str = ""          # what GitLab or a command said (the page folds it under 细节)


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

    @property
    def state(self) -> Path:
        return self.root / "setup-state.json"

    @property
    def identities(self) -> Path:
        return self.root / "identities.json"

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def key(self) -> Path:
        return self.keys / "bridge_ed25519"

    @property
    def allowed(self) -> Path:
        return self.keys / "allowed_signers"

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    @property
    def signer_spool(self) -> Path:
        return self.root / "signer-spool"

    @property
    def vouchers(self) -> Path:
        return self.root / "vouchers"

    @property
    def signer_vouchers(self) -> Path:
        return self.root / "signer-vouchers"

    @property
    def bridge_json(self) -> Path:
        return self.root / "bridge.json"

    @property
    def runnerd_json(self) -> Path:
        return self.root / "runnerd.json"

    @property
    def signer_json(self) -> Path:
        return self.root / "signer.json"

    @property
    def runs(self) -> Path:
        return self.work / "runs"

    @property
    def cache(self) -> Path:
        return self.work / "cache"


@dataclass
class Ctx:
    """One run: the input, the paths, a GitLab client for any token, what turns team mode on, the state that is kept
    (setup-state.json, nothing secret) and what the steps found on the way."""
    inp: SetupInput
    paths: Paths
    gitlab: Callable[[str], Any]                                # token -> brain-kit's rest.GitLab for inp.gitlab_base
    team_mode: Callable[[dict, bool], dict] | None = None       # (settings team, restart) -> TeamStatus; None: not here
    signer: Callable[[Paths], tuple[int, bool]] | None = None   # -> (pid, started now); None: signerproc.ensure
    reach: Callable[[str], tuple[bool, str]] | None = None      # clone URL -> (git reaches it, what git said)
    state: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    project: dict = field(default_factory=dict)       # GitLab's answer for the project, this run
    kit: dict = field(default_factory=dict)           # .brain/kit.json on the default branch, and its text
    kit_text: str = ""
    owner: str = ""                                   # the token's GitLab username
    rewrote: list = field(default_factory=list)       # files team mode reads at its start that this run changed
    _admin: Any = field(default=None, repr=False)
    _secrets: set = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self._secrets.add(self.inp.admin_token)

    @property
    def gl(self) -> Any:
        """GitLab with the person's token, made on first use (a broken sign-in helper fails the step that needs it)."""
        if self._admin is None:
            self._admin = self.gitlab(self.inp.admin_token)
        return self._admin

    @property
    def pid(self) -> int:
        return int(self.state["project"]["id"])

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
            r = _attempt(ctx, step)
            r = replace(r, id=step.id, message=ctx.redact(r.message), detail=ctx.redact(r.detail)[-4000:])
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


def _attempt(ctx: Ctx, step: Step) -> StepResult:
    try:
        return step.run(ctx)
    except StepFailed as exc:
        return failed(exc.message, detail=exc.detail)
    except Exception as exc:  # noqa: BLE001 - GitLab, git, a file: said on the page, and the run stops here
        message, raw = _said(exc)
        return failed(message, detail=raw)


def _gitlab_status(exc: BaseException) -> int | None:
    """The HTTP status of brain-kit's rest.HttpError (by its attribute: the module may have been loaded again since)."""
    status = getattr(exc, "status", None)
    return status if type(status) is int else None


def _said(exc: BaseException) -> tuple[str, str]:
    raw = str(exc).strip() or type(exc).__name__
    status = _gitlab_status(exc)
    if "sign-in" in raw:
        return SIGN_IN, raw
    if status is not None:
        return HTTP_SAID.get(status, "GitLab 出错了（HTTP %d）：等一会儿再跑一次" % status), raw
    if any(c.__name__ == "BrainError" for c in type(exc).__mro__):
        return "连不上 GitLab，或者它回的不是要的东西：看细节，好了再跑一次", raw
    if isinstance(exc, OSError):
        return "读写文件出错了：看细节", raw
    return "这一步出错了：看细节", raw


def view(state: Mapping[str, Any], paths: Paths, running: bool | None = None) -> dict[str, Any]:
    """GET /team/setup (TeamSetup in ui/src/types.ts): every step of the table with the last run's result, the one in
    progress, or todo; that run's input without the token. *running*: whether a run holds the root now (a run cut off by
    a restart left running in its file)."""
    on = bool(state.get("running")) if running is None else bool(running)
    got = {s.get("id"): s for s in state.get("steps") or [] if isinstance(s, dict)}
    steps = []
    for st in STEPS:
        r = got.get(st.id) or {}
        status = str(r.get("status") or ("running" if on and state.get("current") == st.id else "todo"))
        steps.append({"id": st.id, "title": st.title, "status": status, "message": str(r.get("message") or ""),
                      "link": r.get("link") or None, "detail": str(r.get("detail") or "")})
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    roles = inp.get("roles") if isinstance(inp.get("roles"), dict) else {}
    return {"running": on, "started_at": _time(state.get("started_at")),
            "ended_at": None if on else _time(state.get("ended_at")),
            "input": {"gitlab_base": str(inp.get("gitlab_base") or ""), "project": str(inp.get("project") or ""),
                      "roles": {r: [str(n) for n in roles.get(r) or []] for r in ROLES},
                      "push": str(inp.get("push") or "auto")},
            "steps": steps, "root": str(paths.root)}


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


# ---- 1 the project ---------------------------------------------------------------------------------------------- #

def step_project(ctx: Ctx) -> StepResult:
    """The project as GitLab has it (id, default branch, web address, clone addresses), the token owner's role in it
    (Maintainer or Owner: they make project tokens and labels), and its .brain/kit.json on the default branch."""
    gl = ctx.gl
    me = gl.current_user() or {}
    try:
        p = gl.project(ctx.inp.project) or {}
    except Exception as exc:  # noqa: BLE001 - a 404 said with the project's name; anything else as every step says it
        if _gitlab_status(exc) == 404:
            raise StepFailed("GitLab 上找不到 %s：项目名写错了，或者这个 token 看不到它" % ctx.inp.project, str(exc))
        raise
    path, branch = str(p.get("path_with_namespace") or ctx.inp.project), str(p.get("default_branch") or "")
    owner = str(me.get("username") or "").lower()
    level = access_level(gl, p, me)
    if level < MAINTAINER:
        return failed("你（%s）在 %s 是 %s：要 Maintainer 或 Owner 才能建项目令牌和标签"
                      % (owner, path, LEVEL_NAME.get(level, "没有权限的人")))
    if not branch:
        return failed("%s 还没有默认分支：先推一个分支上去" % path)
    raw = gl.file_raw(p["id"], KIT, branch)
    if raw is None:
        return failed("%s 的默认分支 %s 上没有 .brain/kit.json：先在个人电脑上导入这个仓库" % (path, branch))
    try:
        text = raw.decode("utf-8-sig")
        kit = json.loads(text)
    except ValueError as exc:
        return failed("默认分支上的 .brain/kit.json 读不懂：先在个人电脑上导入这个仓库，让它修好", detail=str(exc))
    if not isinstance(kit, dict):
        return failed("默认分支上的 .brain/kit.json 读不懂：先在个人电脑上导入这个仓库，让它修好")
    old = ctx.state.get("project") if isinstance(ctx.state.get("project"), dict) else {}
    if old.get("id") not in (None, p["id"]):
        ctx.state.pop("tokens", None)          # another project's tokens are not this one's
        ctx.state.pop("kit_mr", None)
    ctx.project, ctx.kit, ctx.kit_text, ctx.owner = p, kit, text, owner
    ctx.state["project"] = {"id": int(p["id"]), "path": path, "default_branch": branch, "web_url": p.get("web_url")}
    ctx.state["owner"] = owner
    note = "；它用 Jira 跟踪需求：团队模式还配不了 Jira，交接会被拒" if kit.get("tracker") == "jira" else ""
    return done("%s，默认分支 %s；你（%s）是 %s%s" % (path, branch, owner, LEVEL_NAME.get(level, level), note),
                link=p.get("web_url") or None)


def access_level(gl: Any, project: Mapping[str, Any], me: Mapping[str, Any]) -> int:
    """The token owner's access level in *project*: GitLab's permissions on it (the project's or its group's), else
    the member list; an instance admin counts as Owner."""
    if me.get("is_admin"):
        return OWNER
    perms = project.get("permissions") or {}
    level = max(int(((perms.get(k) or {}).get("access_level")) or 0) for k in ("project_access", "group_access"))
    if level or not me.get("id"):
        return level
    try:
        got = gl.call("GET", "/projects/%d/members/all/%d" % (int(project["id"]), int(me["id"]))) or {}
    except Exception as exc:  # noqa: BLE001 - not a member: no level
        if _gitlab_status(exc) != 404:
            raise
        return 0
    return int(got.get("access_level") or 0)


# ---- 2 the bridge's token --------------------------------------------------------------------------------------- #

def step_bridge_token(ctx: Ctx) -> StepResult:
    """brain-bridge: the project access token the bridge and the signer use (Developer, api, 30 days)."""
    rec, made = project_token(ctx, "bridge", "brain-bridge", BRIDGE_SCOPES)
    return done("%s项目令牌 brain-bridge：账号 %s，%s 到期" % ("建了" if made else "用上次建的", rec["username"],
                                                       rec.get("expires_at") or "?"))


def project_token(ctx: Ctx, key: str, name: str, scopes: tuple[str, ...]) -> tuple[dict, bool]:
    """(the token's record, made now). The saved one when its file still signs in as the bot user GitLab made for it;
    else a new one (Developer, *scopes*, TOKEN_DAYS days) whose value goes only into its own file, and the one it
    replaces revoked (only ever a token this setup made)."""
    pid, path = ctx.pid, ctx.paths.token(key)
    tokens = ctx.state.setdefault("tokens", {})
    saved = tokens.get(key) if isinstance(tokens.get(key), dict) else None
    saved = saved if saved and saved.get("project_id") == pid else None
    if saved:
        tok = read_secret(path)
        ctx.remember(tok)
        me = whoami(ctx, tok) if tok else None
        if me is not None and me.get("id") == saved.get("user_id"):
            saved.update(bot_fields(me))
            return saved, False
    made = ctx.gl.call("POST", "/projects/%d/access_tokens" % pid, body={
        "name": name, "scopes": list(scopes), "access_level": DEVELOPER,
        "expires_at": (date.today() + timedelta(days=TOKEN_DAYS)).isoformat()}) or {}
    tok = str(made.get("token") or "")
    if not tok or not made.get("user_id"):
        raise StepFailed("GitLab 建了项目令牌 %s，可是没把令牌给回来：在 GitLab 上撤掉它，再跑一次" % name)
    ctx.remember(tok)
    write_file(path, tok + "\n")
    rec = {"id": made.get("id"), "user_id": made["user_id"], "name": name, "scopes": list(scopes), "project_id": pid,
           "expires_at": made.get("expires_at"), "username": "", "email": "", "display": ""}
    tokens[key] = rec
    save_state(ctx)                 # the new token's id is on disk before anything else can go wrong
    ctx.rewrote.append(path.name)
    if saved and saved.get("id") not in (None, rec["id"]):
        revoke(ctx, saved["id"])
    me = whoami(ctx, tok)
    if me is None:
        raise StepFailed("新建的项目令牌 %s 登不上 GitLab：再跑一次" % name)
    rec.update(bot_fields(me))
    return rec, True


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


def revoke(ctx: Ctx, token_id: Any) -> None:
    """Revoke a project token this setup made and replaced. Best effort: one GitLab will not revoke expires anyway."""
    try:
        ctx.gl.call("DELETE", "/projects/%d/access_tokens/%d" % (ctx.pid, int(token_id)))
    except Exception as exc:  # noqa: BLE001
        if _gitlab_status(exc) is None:
            raise


# ---- 3 how the runner pushes ------------------------------------------------------------------------------------ #

def step_push(ctx: Ctx) -> StepResult:
    """How runnerd clones and the runs push. auto: this machine's SSH when `git ls-remote` reaches the project (the
    commits are brain-bot's, kit.json's bot_users kept), else a project token brain-bot over HTTPS (its own account and
    address), once git reaches the project with it too; ssh or token: that one only."""
    p, path = ctx.project, ctx.state["project"]["path"]
    host = urllib.parse.urlsplit(ctx.inp.gitlab_base).hostname or ""
    reach = ctx.reach or (lambda url: git_reaches(ctx, url))
    why = ""
    if ctx.inp.push != "token":
        url = str(p.get("ssh_url_to_repo") or "")
        template = clone_template(url, path)
        ok, why = reach(url) if template else (False, "GitLab 没给这个项目的 SSH 地址")
        if ok:
            bots = [str(u).lower() for u in ctx.kit.get("bot_users") or [] if str(u).strip()] or [SSH_BOT[0]]
            ctx.state["push"] = {"mode": "ssh", "clone_url": template, "git_name": SSH_BOT[0], "git_email": SSH_BOT[1],
                                 "bot_users": bots}
            return done("这台机器的 SSH 连得上 GitLab：运行机用 SSH 拉取和推送")
        if ctx.inp.push == "ssh":
            return failed("这台机器的 SSH 连不上 %s：先让 git ls-remote %s 能用，再跑一次" % (host, url), detail=why)
    template = clone_template(str(p.get("http_url_to_repo") or ""), path)
    if not template:
        return failed("GitLab 没给这个项目的 HTTPS 地址", detail=why)
    rec, made = project_token(ctx, "bot", "brain-bot", BOT_SCOPES)
    ok, said = reach(with_token(template, read_secret(ctx.paths.token("bot"))).format(project=path))
    if not ok:
        return failed("这台机器的 git 用 SSH 和项目令牌 brain-bot 都连不上 %s：运行机拉不到代码" % host,
                      detail="\n".join(x for x in (why, said) if x))
    email = rec.get("email") or "%s@noreply.%s" % (rec["username"], host)
    ctx.state["push"] = {"mode": "token", "clone_url": template, "git_name": rec.get("display") or "brain-bot",
                         "git_email": email, "bot_users": [rec["username"]]}
    return done("%s运行机用项目令牌 brain-bot 拉取和推送：账号 %s%s"
                % ("这台机器的 SSH 连不上 GitLab，" if ctx.inp.push == "auto" else "", rec["username"],
                   "（新建的）" if made else ""), detail=why)


def clone_template(url: str, path: str) -> str:
    """*url*, GitLab's clone address of *path*, with the project as runnerd's {project}; '' when it is not one."""
    end = "%s.git" % path
    if not url.endswith(end) or "{" in url or "}" in url:
        return ""
    return url[: -len(end)] + "{project}.git"


def with_token(template: str, token: str) -> str:
    """An https clone template that signs in with *token* (GitLab takes any user name with a project token)."""
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


# ---- 4 labels ----------------------------------------------------------------------------------------------------- #

def step_labels(ctx: Ctx) -> StepResult:
    """The labels the bridge moves (kit.json's names) and brain reflect's, made where the project has none."""
    want: list[tuple[str, str]] = []
    for key, default, color in LABELS:
        name = str(ctx.kit.get(key) or default) if key else default
        if name.lower() not in {w.lower() for w, _ in want}:
            want.append((name, color))
    have = {str(row.get("name") or "").lower()
            for row in ctx.gl.pages("/projects/%d/labels" % ctx.pid, {}, 100, 20, "labels")}
    made = []
    for name, color in want:
        if name.lower() in have:
            continue
        try:
            ctx.gl.call("POST", "/projects/%d/labels" % ctx.pid, body={"name": name, "color": color})
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
    """identities.json in brain-authz's shape (brain-kit's examples/identities.json): every person named in a role, and
    the roles; a role left empty is the token's owner. Each one must be a member of the project."""
    roles = {r: list(ctx.inp.roles.get(r) or ()) or [ctx.owner] for r in ROLES}
    names = list(dict.fromkeys(n for r in ROLES for n in roles[r]))
    members = {str(m.get("username") or "").lower()
               for m in ctx.gl.pages("/projects/%d/members/all" % ctx.pid, {}, 100, 50, "members")}
    unknown = [n for n in names if n not in members]
    if unknown:
        return failed("这些人不是 %s 的成员：%s。先在 GitLab 上把他们加进项目，或者改填别人"
                      % (ctx.state["project"]["path"], "、".join(unknown)))
    wrote = write_file(ctx.paths.identities, dumps({"users": {n: {} for n in names}, "roles": roles}))
    said = "；".join("%s %s" % (ROLE_NAME[r], "、".join(roles[r])) for r in ROLES)
    return done(("写好了身份表：" if wrote else "身份表没变：") + said)


# ---- 6 kit.json, through an MR ------------------------------------------------------------------------------------- #

def step_kit_mr(ctx: Ctx) -> StepResult:
    """kit.json on the default branch names the bridge's account, the bot's, GitLab and the default branch (MIGRATE
    第二部分 4). When it does not: the change on branch brain/runner-setup (the file's own formatting kept) and an MR to
    the default branch, which a person merges: waiting until then."""
    want = wanted_kit(ctx)
    if want == ctx.kit:
        ctx.state.pop("kit_mr", None)
        return done("默认分支上的 .brain/kit.json 已经写好了运行机要的几项")
    gl, pid, default = ctx.gl, ctx.pid, ctx.state["project"]["default_branch"]
    if not branch_exists(gl, pid, BRANCH):
        gl.call("POST", "/projects/%d/repository/branches" % pid, body={"branch": BRANCH, "ref": default})
    raw = gl.file_raw(pid, KIT, BRANCH)
    there = raw.decode("utf-8-sig") if raw is not None else ""
    if parse_json(there) != want:
        gl.call("POST", "/projects/%d/repository/commits" % pid, body={
            "branch": BRANCH, "commit_message": "运行机：kit.json 写上 bridge_user、bot_users、bot_emails、gitlab、default_branch",
            "actions": [{"action": "update" if raw is not None else "create", "file_path": KIT,
                         "content": kit_text(want, there or ctx.kit_text)}]})
    mr = gl.mr_for_branch(pid, BRANCH)
    opened = mr is None
    if opened:
        mr = gl.call("POST", "/projects/%d/merge_requests" % pid, body={
            "source_branch": BRANCH, "target_branch": default, "title": MR_TITLE, "description": MR_TEXT,
            "remove_source_branch": True}) or {}
    ctx.state["kit_mr"] = {"iid": mr.get("iid"), "web_url": mr.get("web_url")}
    return waiting("%s MR !%s：请在 GitLab 上合并这个 MR" % ("开了" if opened else "还没合并", mr.get("iid")),
                   link=mr.get("web_url") or None)


def wanted_kit(ctx: Ctx) -> dict[str, Any]:
    """kit.json as it is on the default branch, with what the runner needs set."""
    push, project = ctx.state["push"], ctx.state["project"]
    want = json.loads(json.dumps(ctx.kit))
    want["bridge_user"] = ctx.state["tokens"]["bridge"]["username"]
    want["bot_users"] = list(push["bot_users"])
    want["bot_emails"] = [push["git_email"]]
    gitlab = want.get("gitlab") if isinstance(want.get("gitlab"), dict) else {}
    want["gitlab"] = dict(gitlab, base=ctx.inp.gitlab_base, project=project["path"])
    want["default_branch"] = project["default_branch"]
    return want


def kit_text(doc: Any, like: str) -> str:
    """*doc* as JSON written the way *like*, the text it replaces, is: its indent, its key order (sorted when its keys
    were), whether it escaped non-ASCII, its final newline. So the MR shows only the keys that changed."""
    m = re.search(r"\n([ \t]+)\S", like)
    indent: Any = (m.group(1) if "\t" in m.group(1) else len(m.group(1))) if m else None
    old = parse_json(like)
    keys = list(old) if isinstance(old, dict) else []
    escaped = bool(re.search(r"\\u[0-9a-fA-F]{4}", like)) and not re.search(r"[^\x00-\x7f]", like)
    text = json.dumps(doc, indent=indent, sort_keys=len(keys) > 1 and keys == sorted(keys), ensure_ascii=escaped)
    return text + ("\n" if like.endswith("\n") or not like else "")


def branch_exists(gl: Any, pid: int, name: str) -> bool:
    try:
        gl.call("GET", "/projects/%d/repository/branches/%s" % (pid, urllib.parse.quote(name, safe="")))
    except Exception as exc:  # noqa: BLE001
        if _gitlab_status(exc) == 404:
            return False
        raise
    return True


# ---- 7 folders ---------------------------------------------------------------------------------------------------- #

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


# ---- 8 the signing key ------------------------------------------------------------------------------------------- #

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
    if wrote:
        ctx.rewrote.append(ps.allowed.name)
    return done("生成了签名密钥" if made else "签名密钥已经有了" if not wrote else "签名密钥已经有了，重写了公钥名单")


# ---- 9 the three configs ----------------------------------------------------------------------------------------- #

def step_configs(ctx: Ctx) -> StepResult:
    """bridge.json, runnerd.json and signer.json in the shapes of MIGRATE 第三部分 7, for one OS user: no MR filter, no
    brain-intake, vouchers not published, the signer on the bridge's token with its account as bridge_users, one
    pseudonym_salt for both (made once, kept on every run). A file is written only when its content changes."""
    ps = ctx.paths
    salt = old_salt(ps) or secrets.token_hex(16)
    ctx.remember(salt)
    docs = ((ps.bridge_json, bridge_doc(ctx, salt)), (ps.runnerd_json, runnerd_doc(ctx)),
            (ps.signer_json, signer_doc(ctx, salt)))
    wrote = [path.name for path, doc in docs if write_file(path, dumps(doc))]
    ctx.rewrote += wrote
    return done("写了 %s" % "、".join(wrote) if wrote else "三份配置都没变")


def old_salt(ps: Paths) -> str:
    for path in (ps.bridge_json, ps.signer_json):
        doc = parse_json(read_secret(path))
        salt = doc.get("pseudonym_salt") if isinstance(doc, dict) else None
        if isinstance(salt, str) and salt.strip():
            return salt
    return ""


def bridge_doc(ctx: Ctx, salt: str) -> dict[str, Any]:
    ps = ctx.paths
    return {
        "projects": [ctx.state["project"]["path"]],
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
    }


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
        "projects": [ctx.state["project"]["path"]],
        "gitlab": {"base": ctx.inp.gitlab_base, "token_env": NO_TOKEN_ENV["signer"], "token_file": str(ps.token("bridge"))},
        "identities": str(ps.identities),
        "vouchers_dir": str(ps.signer_vouchers),
        "publish_vouchers": False,
        "ledger": str(ps.root / "signer-ledger.sqlite"),
        "pseudonym_salt": salt,
        "bridge_users": [ctx.state["tokens"]["bridge"]["username"]],
    }


# ---- 10 the brain-run agent ------------------------------------------------------------------------------------- #

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


# ---- 11 the signer ------------------------------------------------------------------------------------------------ #

def step_signer(ctx: Ctx) -> StepResult:
    """The signer, run by the app itself in same-user mode (signerproc): started when it is not running, or when it
    runs with an older signer.json."""
    ensure = ctx.signer or (lambda ps: signerproc.ensure(ps.signer_json, ps.app_dir, ps.python))
    try:
        pid, started = ensure(ctx.paths)
    except signerproc.SignerFailed as exc:
        return failed(exc.text, detail=exc.detail)
    return done("签名服务%s（进程 %d）" % ("开起来了" if started else "在跑", pid))


# ---- 12 team mode ------------------------------------------------------------------------------------------------- #

def step_team_mode(ctx: Ctx) -> StepResult:
    """Team mode on with the two configs and the same-user trial, through the app's settings (its TeamRuntime starts,
    and the signer with it), and what it says then. Started again only when a file it reads at its start changed."""
    ps = ctx.paths
    if ctx.team_mode is None:
        return skipped("这次没打开团队模式：在设置 → 团队模式里填上 %s 和 %s，勾上本机试用"
                       % (tilde(ps.bridge_json), tilde(ps.runnerd_json)))
    want = {"enabled": True, "bridge_config": str(ps.bridge_json), "runnerd_config": str(ps.runnerd_json),
            "dev_same_user": True}
    st = ctx.team_mode(want, bool(ctx.rewrote)) or {}
    if not st.get("ok"):
        return failed(str(st.get("error") or "团队模式没开起来"), detail=str(st.get("error_raw") or ""))
    said = str(st.get("said") or "团队模式开了")
    if any(r.id == "kit_mr" and r.status == "waiting" for r in ctx.results):
        said += "；可是上面那个 MR 合并以前，交接都会被拒"
    return done(said)


STEPS: tuple[Step, ...] = (
    Step("project", "找到项目", step_project),
    Step("bridge_token", "桥的令牌", step_bridge_token),
    Step("push", "运行机怎么推送", step_push),
    Step("labels", "MR 标签", step_labels),
    Step("identities", "身份表", step_identities),
    Step("kit_mr", "kit.json 的 MR", step_kit_mr),
    Step("dirs", "目录", step_dirs),
    Step("signing_key", "签名密钥", step_signing_key),
    Step("configs", "三份配置", step_configs),
    Step("agent", "brain-run agent", step_agent),
    Step("signer", "签名服务", step_signer),
    Step("team_mode", "打开团队模式", step_team_mode),
)
