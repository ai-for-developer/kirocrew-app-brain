"""The app's settings: ``settings.json`` in its data dir, edited on the page (owner only), checked before saving.

  brainkit    the brain-kit checkout to import (default: $BRAINKIT_HOME or a usual place, see bk.default_path)
  repos       project repos with brain-kit installed, shown on this machine (ids r1, r2 …: repoids.py, kept per path)
  work_root   where each requirement's worktree goes (KiroCrew lets agents work under ~/workplace or ~/workspace only)
  fetch_every_min  every repo with a remote is fetched this often by itself, in minutes (0: only on 「从远端取最新」)
  auto_push   {repo path: false} for a listed repo whose run branches are not pushed at Lean's checkpoints (on when
              left out; progress.py)
  role        what this install is: personal (the default, a person's own machine: it never runs the team runner,
              运行机 shows the team runner as GitLab shows it) or team_server (the team's one runner: 设置 has 团队模式
              and 一键建运行机, 运行机 is its own bridge). Settings of 0.4.2 and before name none: a machine in team mode
              or with team.bridge_config filled in then is the team server, any other a personal one
  team        team mode (the team server only): the bridge loop and runnerd run inside the app
    enabled          on / off
    bridge_config    brain-kit's bridge.json (projects, gitlab, identities, ledger, spool_dir, vouchers_dir …) with
                     "signer": {"type": "spool", "spool_dir", "allowed_signers"} and no signing_key: the separate
                     signer (signer/) holds the key; brain-kit's SpoolSigner talks to it and verifies every signature
    runnerd_config   brain-kit's runnerd.json (empty: runnerd runs elsewhere)
    dev_same_user    local trial only: the signer runs as the same OS user (it could sign whatever the app asks)
  wip_limit, wait_timeout_h, stale_after_h, verify_fail_limit
                     交付总览's team view (roster.py): requirements a person has open at once (开需求 reminds them, it
                     never stops them), working hours a wait on a person may last, working hours someone else's run may
                     go without new progress, failed verify runs in a row before the AI should stop and ask (LIMITS)
Settings of an older version (team.signer_spool, team.allowed_signers) are dropped on reading: they are in
bridge.json's signer block now.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import bk
from .constants import WORK_ROOT

MAX_REPOS = 20
MAX_FETCH_MIN = 120
TEAM_FIELD = {"bridge_config": "桥的配置文件", "runnerd_config": "运行机的配置文件"}     # 设置's labels
PERSONAL, TEAM_SERVER = "personal", "team_server"
ROLES = (PERSONAL, TEAM_SERVER)
ROLE_SWITCH = "这台机器是团队运行机（服务器）"     # 设置's switch for TEAM_SERVER, at the bottom of the page
AGAIN = "：刷新页面再存一次"
# the team view's limits: (default, smallest, largest, what 设置 calls it)
LIMITS = {"wip_limit": (3, 1, 20, "每人同时在做的需求上限"),
          "wait_timeout_h": (4, 1, 200, "等人超过几个工作小时算超时"),
          "stale_after_h": (8, 1, 400, "几个工作小时没有新进度算很久没动"),
          "verify_fail_limit": (3, 1, 20, "verify 连续失败几次该停下来问人")}


def defaults() -> dict[str, Any]:
    return {"brainkit": bk.default_path(), "repos": [], "work_root": str(WORK_ROOT), "fetch_every_min": 5,
            "auto_push": {}, "role": PERSONAL,
            "team": {"enabled": False, "bridge_config": "", "runnerd_config": "", "dev_same_user": False},
            **{k: v[0] for k, v in LIMITS.items()}}


def load(path: Path) -> dict[str, Any]:
    out = defaults()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        clean, _ = check(data)
        out.update({k: v for k, v in clean.items() if k != "team"})
        out["team"].update(clean.get("team") or {})
    return out


def save(path: Path, settings: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _path(v: Any) -> str:
    return os.path.expanduser(v.strip()) if isinstance(v, str) else ""


def check(data: Any) -> tuple[dict[str, Any], list[str]]:
    """(the settings that hold, what is wrong). Only absolute paths; nothing is created here."""
    probs: list[str] = []
    out = defaults()
    if not isinstance(data, dict):
        return out, ["设置的格式不对" + AGAIN]
    if "brainkit" in data:
        p = _path(data["brainkit"])
        if p and not os.path.isabs(p):
            probs.append("brain-kit 的位置要写绝对路径")
        elif p:
            out["brainkit"] = p
    repos = data.get("repos", [])
    if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
        probs.append("仓库列表的格式不对" + AGAIN)
        repos = []
    seen = []
    for r in repos[:MAX_REPOS]:
        p = _path(r)
        if not p:
            continue
        if not os.path.isabs(p):
            probs.append("项目目录要写绝对路径：%s" % r)
            continue
        p = os.path.realpath(p)
        if p not in seen:
            seen.append(p)
    out["repos"] = seen
    if "work_root" in data:
        p = _path(data["work_root"])
        home = os.path.realpath(os.path.expanduser("~"))
        rp = os.path.realpath(p) if p else ""
        if p and not os.path.isabs(p):
            probs.append("「每个需求的工作目录」要写绝对路径")
        elif rp and not any(rp == os.path.join(home, w) or rp.startswith(os.path.join(home, w) + os.sep)
                            for w in ("workplace", "workplaces", "workspace", "workspaces")):
            probs.append("「每个需求的工作目录」要在 ~/workplace 或 ~/workspace 下面（KiroCrew 只让 AI 在那里干活）")
        elif p:
            out["work_root"] = p
    if "fetch_every_min" in data:
        n = data["fetch_every_min"]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_FETCH_MIN:
            probs.append("「每隔几分钟从远端取最新」要填 0 到 %d 的整数（填 0 就不自动取）" % MAX_FETCH_MIN)
        else:
            out["fetch_every_min"] = n
    pushes = data.get("auto_push", {})
    if not isinstance(pushes, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in pushes.items()):
        probs.append("「过关卡时把进度推到远端」的格式不对" + AGAIN)
    else:
        listed = set(seen)
        out["auto_push"] = {p: v for p, v in ((os.path.realpath(_path(k)), v) for k, v in pushes.items() if _path(k))
                            if p in listed}
    for k, (_, lo, hi, name) in LIMITS.items():
        if k in data:
            v = data[k]
            if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                out[k] = v
            else:
                probs.append("「%s」要写 %d 到 %d 的整数" % (name, lo, hi))
    team = data.get("team")
    if team is not None:
        if not isinstance(team, dict):
            probs.append("运行机的设置格式不对" + AGAIN)
        else:
            t = out["team"]
            t["enabled"] = team.get("enabled") is True
            t["dev_same_user"] = team.get("dev_same_user") is True
            for k in ("bridge_config", "runnerd_config"):
                p = _path(team.get(k, ""))
                if p and not os.path.isabs(p):
                    probs.append("「%s」要写绝对路径" % TEAM_FIELD[k])
                    continue
                t[k] = p
            if t["enabled"] and not t["bridge_config"]:
                probs.append("勾了「这台机器是团队运行机」，就要填「%s」" % TEAM_FIELD["bridge_config"])
    team_on, role = out["team"]["enabled"], data.get("role")
    # settings of 0.4.2 and before name no role: a machine in team mode, or with its bridge's config file filled in
    # (team mode switched off for a while), is the team server
    was = TEAM_SERVER if team_on or out["team"]["bridge_config"] else PERSONAL
    if role is None:
        role = was
    elif role not in ROLES:
        probs.append("「%s」的格式不对%s" % (ROLE_SWITCH, AGAIN))
        role = was
    if role == PERSONAL and team_on:
        probs.append("要开团队模式，先在设置最下面勾上「%s」" % ROLE_SWITCH)
        out["team"]["enabled"] = False
    out["role"] = role
    return out, probs
