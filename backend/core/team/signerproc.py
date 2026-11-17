"""The signer as a child process of this app, for the team runner 一键建运行机 built (setup.py): signer/brain_signer.py
with the signer.json beside team mode's bridge.json, started when team mode starts and stopped when it stops.

Same-user mode only (settings team.dev_same_user): the signer runs as this app's OS user, so it is no boundary
(MIGRATE.md 第三部分 runs it as another user). A pid file beside signer.json lets a later app process find a signer an
earlier one started. A pid counts as that signer only while the process is alive and its command line is this signer
with that config: a pid the system has given to something else is never signalled.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SCRIPT = Path("signer") / "brain_signer.py"
PID_FILE = "signer.pid"
LOG_FILE = "signer.log"
SETUP_MARK = "setup-state.json"      # 一键建运行机's own record: the signer.json beside it is that setup's
CHECK_S = 120.0
FAILED = "签名服务起不来"              # how team mode's start error begins when it is this signer (problems.team_start)
_children: dict[int, subprocess.Popen] = {}   # the ones this process started, reaped when they end
_lock = threading.Lock()


class SignerFailed(RuntimeError):
    def __init__(self, text: str, detail: str = "") -> None:
        super().__init__(text)
        self.text, self.detail = text, detail


def config_for(team: dict[str, Any]) -> Path | None:
    """The signer.json 一键建运行机 wrote beside team mode's bridge.json, when team mode is the same-user trial."""
    if not team.get("dev_same_user") or not team.get("bridge_config"):
        return None
    folder = Path(team["bridge_config"]).parent
    path = folder / "signer.json"
    return path if path.is_file() and (folder / SETUP_MARK).is_file() else None


def command(signer_json: Path, app_dir: Path, python: str, mode: str) -> list[str]:
    return [python, "-I", str(Path(app_dir) / SCRIPT), "--config", str(signer_json), mode]


def _record(signer_json: Path) -> dict[str, Any]:
    try:
        rec = json.loads((signer_json.parent / PID_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def running(signer_json: Path) -> int | None:
    """The pid of the signer for *signer_json*, when one is alive."""
    pid = _record(signer_json).get("pid")
    if type(pid) is not int or pid <= 0:
        return None
    child = _children.get(pid)
    if child is not None and child.poll() is not None:
        _children.pop(pid, None)
        return None
    try:
        p = subprocess.run(["ps", "-ww", "-o", "stat=,command=", "-p", str(pid)], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = p.stdout.strip().split(None, 1)
    if p.returncode != 0 or len(parts) != 2 or parts[0].startswith("Z"):
        return None
    cmd = parts[1]
    return pid if str(SCRIPT) in cmd and "--config %s loop" % signer_json in cmd else None


def ensure(signer_json: Path, app_dir: Path, python: str) -> tuple[int, bool]:
    """(pid, started now): the signer for *signer_json* runs with that file as it is now. One that runs with an older
    signer.json is stopped first; a new one is tried with one pass (``once``) before ``loop`` starts."""
    signer_json = Path(signer_json)
    with _lock:
        sha = _sha(signer_json)
        pid = running(signer_json)
        if pid and _record(signer_json).get("config_sha256") == sha:
            return pid, False
        _stop(signer_json)
        if not (Path(app_dir) / SCRIPT).is_file():
            raise SignerFailed("这个 app 里没有签名服务：找不到 %s" % (Path(app_dir) / SCRIPT))
        try:
            check = subprocess.run(command(signer_json, app_dir, python, "once"), capture_output=True, text=True,
                                   timeout=CHECK_S, stdin=subprocess.DEVNULL, cwd=str(signer_json.parent))
        except subprocess.TimeoutExpired:
            raise SignerFailed("签名服务试跑一轮，%d 秒没跑完" % CHECK_S) from None
        except OSError as exc:
            raise SignerFailed("签名服务起不来", str(exc)) from None
        if check.returncode != 0:
            raise SignerFailed("签名服务试跑一轮没过", (check.stderr or check.stdout).strip()[-2000:])
        with open(signer_json.parent / LOG_FILE, "ab") as log:
            child = subprocess.Popen(command(signer_json, app_dir, python, "loop"), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, cwd=str(signer_json.parent),
                                     start_new_session=True)
        _children[child.pid] = child
        rec = {"pid": child.pid, "config": str(signer_json), "config_sha256": sha, "started": time.time()}
        tmp = signer_json.parent / (".%s.%d" % (PID_FILE, os.getpid()))
        tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.replace(tmp, signer_json.parent / PID_FILE)
        return child.pid, True


def stop(signer_json: Path, wait_s: float = 5.0) -> bool:
    """Stop the signer for *signer_json* (SIGTERM, then SIGKILL after *wait_s*). -> whether one was running."""
    with _lock:
        return _stop(Path(signer_json), wait_s)


def _stop(signer_json: Path, wait_s: float = 5.0) -> bool:
    pid = running(signer_json)
    if pid:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except OSError:
                break
            end = time.monotonic() + wait_s
            while time.monotonic() < end and running(signer_json) == pid:
                time.sleep(0.05)
            if running(signer_json) != pid:
                break
        child = _children.pop(pid, None)
        if child is not None:
            try:
                child.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                pass
    try:
        (signer_json.parent / PID_FILE).unlink()
    except OSError:
        pass
    return bool(pid)
