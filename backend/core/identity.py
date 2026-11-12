"""Who brain-kit will say the person is in a repo, by brain-kit's own rule (cli._me):

  a GitLab token and kit.json gitlab.base   GitLab's username for the token (``Who`` asks GitLab's current user with
                                            brain-kit's own client, as cli._me does, in the background)
  $BRAIN_USER, else git config brain.user   that name (it must pass brain-kit's USER_RE, lowercased); with a token,
                                            only while GitLab has not said who it is (slow, down, or the token refused)
  no GitLab (gitlab.base empty)             the machine's login name, when brain-kit falls back to it (brain-kit after
                                            48b6b97: the app looks for cli._login_name); an older brain-kit asks
  GitLab and none of the above              brain-kit refuses ("who are you?"): the page asks for the GitLab username

The token is read as brain-kit reads it (rest.read_token) and goes only to GitLab: what GitLab said is kept under a
hash of it, and the token is not logged or returned.

``set_name`` writes ``git config brain.user <name>`` in the repo (its own .git/config, which its worktrees share): the
name checked with brain-kit's USER_RE, git run without a shell, with a time limit. No token is asked for or stored.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import bk
from .constants import GIT_BIN
from .repo import Git

USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")   # brain-kit 48b6b97 common.USER_RE, while brain-kit is not loaded
SET_TIMEOUT_S = 15
ASK_AGAIN_S = 600.0     # what GitLab said about the token is asked again after this long (a username can change)
RETRY_S = 60.0          # and this soon after GitLab did not say
RULE = "只能用小写字母、数字和 . _ -（字母或数字开头，最多 64 个）"
LABEL_GITLAB = "你的 GitLab 用户名"
LABEL_NAME = "你的名字"
WHY_GITLAB = "这个仓库用 GitLab：认领和交接都按这个名字认人，要和你在 GitLab 上的用户名一样"
WHY_OLD = "要一个名字，认领需求时用：这个仓库不用 GitLab，用登录名就行"
WHY_LOGIN = "这台机器的登录名不能当名字用：写一个"
WHY_LOCAL = "这个仓库不用 GitLab：名字只在认领需求时用"
FROM = {"token": "GitLab 按你的令牌认出来的", "env": "这台机器的环境变量 BRAIN_USER", "git": "git 设置里的 brain.user",
        "login": "这台机器的登录名：这个仓库不用 GitLab"}
ASKING = "正在问 GitLab 你是谁"
UNREACHABLE = "连不上 GitLab"
REFUSED = "令牌过期了或被收回了"


@dataclass(frozen=True)
class Asked:
    """What GitLab said about this machine's token: its username (lowercased, brain-kit's USER_RE), else why not in
    plain Chinese ('' while it has not answered yet)."""
    name: Optional[str] = None
    error: str = ""


def user_re() -> "re.Pattern[str]":
    try:
        rx = bk.m("common").USER_RE
        return rx if hasattr(rx, "match") else USER_RE
    except Exception:  # noqa: BLE001 - brain-kit not loaded: the same rule, copied
        return USER_RE


def valid(name: Any) -> bool:
    return isinstance(name, str) and bool(user_re().match(name))


def login_name() -> str:
    """The login name, lowercased, when it is a usable name (brain-kit's cli._login_name); '' otherwise."""
    try:
        name = getpass.getuser().strip().lower()
    except Exception:  # noqa: BLE001 - no login name (no USER, no passwd entry)
        return ""
    return name if valid(name) else ""


def falls_back() -> bool:
    """The loaded brain-kit uses the login name in a repo without GitLab."""
    try:
        return callable(getattr(bk.m("cli"), "_login_name", None))
    except Exception:  # noqa: BLE001 - brain-kit not loaded
        return False


def token() -> str:
    """This machine's GitLab token as brain-kit reads it (rest.read_token: $GITLAB_TOKEN, else ~/.brain/gitlab-token);
    '' without one, without brain-kit, or when others can read the file (brain-kit refuses it)."""
    try:
        return str(bk.m("rest").read_token() or "")
    except Exception:  # noqa: BLE001 - brain-kit not loaded, or a token file others can read
        return ""


def ask(base: str, tok: str, transport: Any = None) -> Asked:
    """GitLab's current user for *tok* at *base*, with brain-kit's own client. Blocking; never raises."""
    try:
        user = bk.m("rest").GitLab(base, tok, transport).current_user()
    except Exception as exc:  # noqa: BLE001 - unreachable, timed out, an HTTP error: GitLab did not say
        status = getattr(exc, "status", None)
        if status in (401, 403):
            return Asked(error=REFUSED)
        return Asked(error="GitLab 出错了，HTTP %d" % status if isinstance(status, int) else UNREACHABLE)
    name = str(user.get("username") or "").strip().lower() if isinstance(user, dict) else ""
    return Asked(name=name) if valid(name) else Asked(error="GitLab 说的用户名「%s」不能当名字用" % name[:64])


class Who:
    """Who the person is in each repo (``of``), for one service. GitLab's answer about the token is asked in a background
    thread, never while a page waits, and kept per (GitLab base, token hash): asked again after ASK_AGAIN_S, or after
    RETRY_S when GitLab did not say; the last answer stands while the next ask runs."""

    def __init__(self, transport: Any = None, *, log: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.transport, self.log, self.clock = transport, log, clock
        self._said: dict[tuple[str, str], tuple[float, Asked]] = {}
        self._asking: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def of(self, top: str, base: str) -> dict[str, Any]:
        return of(top, base, self.asked(base))

    def asked(self, base: str) -> Asked | None:
        """What GitLab at *base* said about this machine's token (None: no GitLab, or no token: brain-kit does not ask
        then); the ask starts in the background when nothing fresh is kept."""
        tok = token() if base else ""
        if not tok:
            return None
        key = (base.rstrip("/"), hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16])
        with self._lock:
            got = self._said.get(key)
            stale = got is None or self.clock() - got[0] >= (ASK_AGAIN_S if got[1].name else RETRY_S)
            if stale and key not in self._asking:
                self._asking.add(key)
                threading.Thread(target=self._ask, args=(key, base, tok), daemon=True, name="brain-who").start()
        return got[1] if got else Asked()

    def _ask(self, key: tuple[str, str], base: str, tok: str) -> None:
        said = ask(base, tok, self.transport)
        with self._lock:
            old = self._said.get(key)
            self._said[key] = (self.clock(), said)
            self._asking.discard(key)
        if said.error and self.log is not None and (old is None or old[1].error != said.error):
            self.log.warning("brain: GitLab at %s did not say who the token is: %s", key[0], said.error)


def of(top: str, gitlab_base: str, asked: Asked | None = None) -> dict[str, Any]:
    """Who brain-kit will say the person is in the repo at *top* (see the module doc), given what GitLab said about the
    token (*asked*; None: no token). Reads git config; never asks GitLab.

    name     the name the app goes by here (None: it cannot tell, or not yet)
    source   token | env | git | login | none
    text     one line for the page: the name and where it came from; ask: the page shows the input (label, why,
             suggest: the login name); error: what is wrong with the name that is set"""
    base = str(gitlab_base or "")
    asked = asked if base else None
    out: dict[str, Any] = {"name": None, "source": "none", "gitlab": base, "text": "", "ask": False, "label": "",
                           "why": "", "suggest": "", "error": ""}
    if asked and asked.name:
        out.update(name=asked.name, source="token", text="%s（%s）" % (asked.name, FROM["token"]))
        return out
    env = os.environ.get("BRAIN_USER", "").strip()
    conf = "" if env else Git(top).out("config", "brain.user").strip()
    who = env or conf
    not_said = "GitLab 没认出你（%s）" % asked.error if asked and asked.error else ASKING if asked else ""
    if who and valid(who.lower()):
        src = "env" if env else "git"
        out.update(name=who.lower(), source=src, text="%s（%s）" % (who.lower(), FROM[src]) +
                   ("。%s，先按这个名字认你" % not_said if not_said else ""))
        return out
    if env:     # git config does not win over it: nothing to fix here
        out.update(source="env", error="你在环境变量 BRAIN_USER 里设的名字「%s」不能用：%s。改掉它" % (env[:64], RULE))
        return out
    if asked and not asked.error:       # GitLab will say
        out.update(source="token", text=ASKING)
        return out
    login = login_name()
    if not who and not base and falls_back() and login:
        out.update(name=login, source="login", text="%s（%s）" % (login, FROM["login"]))
        return out
    if who:
        out["error"] = "你在这个仓库的名字「%s」不能用：%s" % (who[:64], RULE)
    if asked:
        why = "%s：写上你在 GitLab 上的用户名，app 先按它认你" % not_said
    elif base:
        why = WHY_GITLAB
    else:
        why = WHY_LOCAL if who else WHY_LOGIN if falls_back() else WHY_OLD
    out.update(ask=True, suggest=login, label=LABEL_GITLAB if base else LABEL_NAME, why=why)
    return out


class NameRefused(ValueError):
    pass


def set_name(top: str, name: Any) -> str:
    """``git config brain.user <name>`` in the repo at *top* (a checked repo top level): the name lowercased, as
    brain-kit reads it. Raises NameRefused (a bad name, git said no)."""
    n = name.strip().lower() if isinstance(name, str) else ""
    if not valid(n):
        raise NameRefused("名字%s" % RULE)
    from .importer import last_lines, run_quiet, tail       # here: importer imports this module
    p = run_quiet([GIT_BIN, "-C", top, "config", "--local", "brain.user", n], top, SET_TIMEOUT_S)
    if p.returncode != 0:
        raise NameRefused("名字没存上，git 说：%s" % (last_lines(tail(p), 3) or "退出码 %d" % p.returncode))
    return n
