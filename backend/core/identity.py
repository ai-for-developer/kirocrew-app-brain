"""Who brain-kit will say the person is in a repo, by brain-kit's own rule (cli._me), without asking GitLab:

  a GitLab token and kit.json gitlab.base   the GitLab username (GitLab says it; the app does not ask)
  $BRAIN_USER, else git config brain.user   that name (it must pass brain-kit's USER_RE, lowercased)
  no GitLab (gitlab.base empty)             the machine's login name, when brain-kit falls back to it (brain-kit after
                                            48b6b97: the app looks for cli._login_name); an older brain-kit asks
  GitLab and none of the above              brain-kit refuses ("who are you?"): the page asks for the GitLab username

``set_name`` writes ``git config brain.user <name>`` in the repo (its own .git/config, which its worktrees share): the
name checked with brain-kit's USER_RE, git run without a shell, with a time limit. No token is asked for or stored.
"""

from __future__ import annotations

import getpass
import os
import re
import time
from typing import Any

from . import bk
from .constants import GIT_BIN
from .repo import Git, git_dirs

USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")   # brain-kit 48b6b97 common.USER_RE, while brain-kit is not loaded
SET_TIMEOUT_S = 15
RULE = "只能用小写字母、数字和 . _ -（字母或数字开头，最多 64 个）"
LABEL_GITLAB = "你的 GitLab 用户名"
LABEL_NAME = "你的名字"
WHY_GITLAB = "这个仓库用 GitLab：认领和交接都按这个名字认人，要和你在 GitLab 上的用户名一样"
WHY_OLD = "要一个名字，认领需求时用：这个仓库不用 GitLab，用登录名就行"
WHY_LOGIN = "这台机器的登录名不能当名字用：写一个"
WHY_LOCAL = "这个仓库不用 GitLab：名字只在认领需求时用"


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


def of(top: str, gitlab_base: str, *, token: bool) -> dict[str, Any]:
    """Who brain-kit will say the person is in the repo at *top* (see the module doc). Read only.

    name     the name brain-kit uses here (None: GitLab's, with a token; or brain-kit would refuse)
    source   token | env | git | login | none
    text     one line for the page; ask: the page shows the input (label, why, suggest: the login name); error: what
             is wrong with the name that is set"""
    base = str(gitlab_base or "")
    out: dict[str, Any] = {"name": None, "source": "none", "gitlab": base, "text": "", "ask": False, "label": "",
                           "why": "", "suggest": "", "error": ""}
    if base and token:
        out.update(source="token", text="GitLab 令牌认你：用你在 GitLab 上的用户名")
        return out
    env = os.environ.get("BRAIN_USER", "").strip()
    conf = "" if env else git_name(top)
    who = env or conf
    if who and valid(who.lower()):
        out.update(name=who.lower(), source="env" if env else "git",
                   text="%s（%s）" % (who.lower(), "这台机器的环境变量里设的" if env else "你在这个仓库的名字"))
        return out
    if env:     # git config does not win over it: nothing to fix here
        out.update(source="env", error="你在环境变量 BRAIN_USER 里设的名字「%s」不能用：%s。改掉它" % (env[:64], RULE))
        return out
    login = login_name()
    if not who and not base and falls_back() and login:
        out.update(name=login, source="login", text="%s（这台机器的登录名：这个仓库不用 GitLab）" % login)
        return out
    if who:
        out["error"] = "你在这个仓库的名字「%s」不能用：%s" % (who[:64], RULE)
    why = WHY_GITLAB if base else WHY_LOCAL if who else WHY_LOGIN if falls_back() else WHY_OLD
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
    _names.clear()
    if p.returncode != 0:
        raise NameRefused("名字没存上，git 说：%s" % (last_lines(tail(p), 3) or "退出码 %d" % p.returncode))
    return n


NAME_TTL_S = 60.0   # git config brain.user is read again at least this often (an onbranch include, the system's config)
_names: dict[str, tuple[tuple[str, ...], tuple, float, str]] = {}   # top -> (the files stamped, their stamp, when, name)


def config_files(top: str) -> tuple[str, ...]:
    """The git config files a name is set in, as far as they are known before git is asked: the repo's own (a
    worktree's common one and its config.worktree), ~/.gitconfig, git's user config and $GIT_CONFIG_GLOBAL's."""
    home = os.path.expanduser("~")
    gitdir, common = git_dirs(top)
    alt = os.environ.get("GIT_CONFIG_GLOBAL")
    return (os.path.join(common, "config"), os.path.join(gitdir, "config.worktree"), os.path.join(home, ".gitconfig"),
            os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "git", "config")) + (
            (alt,) if alt else ())


def _stats(files: tuple[str, ...]) -> tuple:
    out: list[Any] = []
    for p in files:
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def config_stamp(files: tuple[str, ...]) -> tuple:
    """git's environment, and *files* (mtime and size; None: not there)."""
    return (tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith("GIT_CONFIG"))),) + _stats(files)


def git_name(top: str) -> str:
    """`git config brain.user` in *top*, read again only when a git config file it may come from changes (config_files,
    each file git read a setting from, each file an include names), or after NAME_TTL_S."""
    kept = _names.get(top)
    if kept is not None and kept[1] == config_stamp(kept[0]) and time.monotonic() - kept[2] < NAME_TTL_S:
        return kept[3]
    at, files = time.monotonic(), config_files(top)
    stamp = config_stamp(files)
    p = Git(top).run("config", "--list", "--show-origin", "-z")
    if p.returncode != 0:
        return ""                       # a bad config file, a timeout: asked again next time
    name, read = "", []
    said = p.stdout.split("\0")
    for origin, entry in zip(said[0::2], said[1::2]):
        key, _, value = entry.partition("\n")
        if key == "brain.user":
            name = value.strip()         # the last one wins, as in `git config brain.user`
        if origin.startswith("file:"):
            path = os.path.join(top, origin[5:])
            read.append(path)
            if key == "include.path" or (key.startswith("includeif.") and key.endswith(".path")):
                read.append(os.path.join(os.path.dirname(path), os.path.expanduser(value)))
    more = tuple(dict.fromkeys(os.path.normpath(x) for x in read if os.path.normpath(x) not in files))
    _names[top] = (files + more, stamp + _stats(more), at, name)
    return name
