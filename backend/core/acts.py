"""开 MR and 转完整流程 of a fast run (brain-kit's flows.Action mr and full): `brain mr --json` and `brain full --json` in
the requirement's worktree on this machine, refused in words before anything runs when the app can tell, and their
answer read at this boundary (ui/src/types.ts ActResult). brain-kit pushes, opens or updates the MR, or switches the
run to the full flow; the app only says what it did.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

from . import flowview
from .family import run_brain
from .lean_view import INTENT_RE

TIMEOUT_S = 300
COMMAND = {"mr": ("mr", "--json"), "full": ("full", "--json")}
OUT_MAX = 8000


def why_not(f: dict[str, Any], row: dict[str, Any], action: str, *, me: Optional[str], busy: bool
            ) -> Optional[tuple[str, str, int]]:
    """Why the app does not run *action* (its words, code, HTTP status), or None when it may: brain-kit's refusal for
    the run's flow first, then what only this machine knows."""
    if not f.get("run"):
        return "还没开工", "not_started", 409
    why = f.get("flow_error") or flowview.action_problem(f["flow"], action)
    if why:
        return why, "not_this_flow", 409
    if f.get("merged") or row.get("state") in ("merged", "closed"):
        return "它已经合并或关掉了", "merged", 409
    if me and me != f.get("owner"):
        return "只有需求负责人本人能做这一步（按你在这个仓库的名字认人，在「设置」的「仓库」里填）", "not_owner", 403
    if not f.get("worktree"):
        return "这台机器上没有它的工作区：到开工的那台机器上做", "no_worktree", 409
    if action == "mr" and row.get("local_only"):
        return flowview.LOCAL_ONLY, "local_only", 409
    if busy:
        return "AI 正在这个需求的会话里做事：等它停下来再点（它做着的时候可能会提交）", "busy", 409
    return None


def _str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def parse(code: int, stdout: str, stderr: str, action: str) -> dict[str, Any]:
    """ActResult (without told) from brain's exit status and output: one JSON object on success; otherwise its own
    `brain: <why>` line, or what happened."""
    output = ((stdout or "") + ("\n" + stderr if stderr else "")).strip()[-OUT_MAX:]
    out: dict[str, Any] = {"ok": False, "action": action, "text": "", "pushed": False, "mr": None, "commands": [],
                           "new_mr_url": None, "intent": None, "output": output}
    doc: Any = None
    if code == 0:
        text = (stdout or "").strip()
        for cand in (text, text.rsplit("\n", 1)[-1]):
            try:
                doc = json.loads(cand)
                break
            except ValueError:
                continue
    if not isinstance(doc, dict):
        said = [ln.strip()[len("brain:"):].strip() for ln in (stderr or "").splitlines() if ln.strip().startswith("brain:")]
        out["text"] = said[-1] if said else ("brain %s 超过 %d 秒没结束" % (action, TIMEOUT_S) if code == 124 else
                                            "brain %s 没做成（退出码 %d）：原话在「细节」里" % (action, code))
        return out
    mr = doc.get("mr") if isinstance(doc.get("mr"), dict) else None
    iid = mr.get("iid") if mr else None
    intent = _str(doc.get("intent"))
    out.update(ok=doc.get("ok") is not False, text=_str(doc.get("text")) or "", pushed=doc.get("pushed") is True,
               mr={"iid": iid, "url": _str(mr.get("url")) or "", "existing": mr.get("existing") is True}
               if isinstance(iid, int) and not isinstance(iid, bool) else None,
               commands=[c for c in doc.get("commands") or [] if isinstance(c, str)][:20],
               new_mr_url=_str(doc.get("new_mr_url")), intent=intent if intent and INTENT_RE.match(intent) else None)
    return out


def run(worktree: str, action: str) -> dict[str, Any]:
    """`brain <action> --json` in *worktree* (blocking: call it in a thread)."""
    try:
        p = run_brain(list(COMMAND[action]), worktree, TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return parse(127, "", "brain: 跑不了 brain（%s）" % exc, action)
    return parse(p.returncode, p.stdout or "", p.stderr or "", action)
