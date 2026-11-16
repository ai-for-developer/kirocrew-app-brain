"""选文件… / 选文件夹… (开需求's documents): this machine's own file dialog, opened by the backend and answering the
paths chosen. The gateway is loopback, so the dialog opens on the machine the page is on.

The commands are fixed: nothing from the request reaches them but the kind, one of KINDS. macOS: osascript; Linux
with a display: zenity, else kdialog; anything else: none (the paths are pasted instead). ``$BRAIN_APP_PICKER`` names a
command used instead (tests, the harness): ``<it> file|folder`` prints one path per line, exit 1 for a cancel.
One dialog at a time, whichever page asks; one left open past TIMEOUT_S is closed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable, Mapping, NamedTuple

from .httpkit import AppError

KINDS = ("file", "folder")
TIMEOUT_S = 300.0

INVALID = "kind 是 file（选文件）或 folder（选文件夹）：刷新页面再试"
NO_PICKER = "这台机器上打不开选文件的窗口：把路径粘贴到下面的框里，一行一个"
BUSY = "选文件的窗口已经开着了（可能在别的窗口后面）：在那里选好，或点「取消」"
TIMED_OUT = "选文件的窗口开得太久没选，已经关了：要选的话再点一次"
FAILED = "选文件的窗口没打开（%s）：把路径粘贴到下面的框里，一行一个"

_POSIX_LINES = 'set out to ""\nrepeat with f in picked\n  set out to out & POSIX path of f & linefeed\nend repeat\nreturn out'
_MAC_CHOOSE = {
    "file": 'set picked to choose file with prompt "选需求文档（按住 ⌘ 可以多选）" with multiple selections allowed\n',
    "folder": 'set picked to choose folder with prompt "选需求文档所在的文件夹（按住 ⌘ 可以多选）" with multiple selections allowed\n',
}
# osascript's own `activate` makes it a GUI app on every click (about 2.5 s on the user's Mac); System Events, once
# running, comes to the front in about 0.6 s. Without permission to drive System Events, the plain dialog is used.
MAC_SCRIPTS = {k: 'tell application "System Events"\nactivate\n' + v + 'end tell\n' + _POSIX_LINES for k, v in _MAC_CHOOSE.items()}
MAC_PLAIN = {k: 'activate\n' + v + _POSIX_LINES for k, v in _MAC_CHOOSE.items()}
_MAC_NO_HOST = ("(-1743)", "(-600)", "(-10810)")   # not allowed to drive System Events; it is not running; cannot launch
LINUX_TITLES = {"file": "选需求文档（可以多选）", "folder": "选需求文档所在的文件夹"}


class Picker(NamedTuple):
    argv: list[str]
    cancelled: Callable[[int, str], bool]     # (exit code, stderr): closed without a choice
    plain: list[str] | None = None            # run instead when argv's host refused (_MAC_NO_HOST)


def _exit_1(code: int, _err: str) -> bool:
    return code == 1


def _mac_cancel(code: int, err: str) -> bool:
    return code == 1 and "(-128)" in err      # osascript: "execution error: User canceled. (-128)"


def picker_for(kind: str, platform: str, env: Mapping[str, str], which: Callable[[str], Any]) -> Picker | None:
    own = env.get("BRAIN_APP_PICKER")
    if own:
        return Picker([own, kind], _exit_1)
    if platform == "darwin":
        return Picker(["/usr/bin/osascript", "-e", MAC_SCRIPTS[kind]], _mac_cancel, ["/usr/bin/osascript", "-e", MAC_PLAIN[kind]])
    if not platform.startswith("linux") or not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return None
    folder = kind == "folder"
    zenity = which("zenity")
    if zenity:
        return Picker([zenity, "--file-selection", "--multiple", "--separator=\n", "--title=" + LINUX_TITLES[kind]]
                      + (["--directory"] if folder else []), _exit_1)
    kdialog = which("kdialog")
    if kdialog:
        how = ["--getexistingdirectory"] if folder else ["--multiple", "--separate-output", "--getopenfilename"]
        return Picker([kdialog, "--title", LINUX_TITLES[kind], *how, os.path.expanduser("~")], _exit_1)
    return None


_LOCK = threading.Lock()


def pick(kind: Any, *, run: Callable[..., Any] = subprocess.run, platform: str | None = None,
         env: Mapping[str, str] | None = None, which: Callable[[str], Any] = shutil.which) -> list[str]:
    """The paths chosen in this machine's dialog for *kind*; [] for a cancel. Blocks until the dialog closes."""
    if kind not in KINDS:
        raise AppError(INVALID, "invalid_kind")
    p = picker_for(kind, platform or sys.platform, os.environ if env is None else env, which)
    if p is None:
        raise AppError(NO_PICKER, "no_picker", 501)
    if not _LOCK.acquire(blocking=False):
        raise AppError(BUSY, "picker_busy", 409)
    try:
        r = run(p.argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_S)
        if r.returncode != 0 and p.plain and any(code in (r.stderr or "") for code in _MAC_NO_HOST):
            r = run(p.plain, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                    timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:      # subprocess.run has killed it: the dialog is gone
        raise AppError(TIMED_OUT, "picker_timeout", 408)
    except OSError as exc:
        raise AppError(FAILED % (exc.strerror or exc), "picker_failed", 502)
    finally:
        _LOCK.release()
    if r.returncode == 0:
        return chosen(r.stdout or "")
    if p.cancelled(r.returncode, r.stderr or ""):
        return []
    said = (r.stderr or "").strip().splitlines()
    raise AppError(FAILED % (said[-1] if said else "退出码 %d" % r.returncode), "picker_failed", 502)


def chosen(out: str) -> list[str]:
    """The dialog's lines as paths: absolute ones only, without a trailing /, each once."""
    paths: list[str] = []
    for line in out.splitlines():
        p = line.strip()
        p = p.rstrip("/") or p
        if os.path.isabs(p) and p not in paths:
            paths.append(p)
    return paths
