"""A fast run's row (快速): what it waits on, from its commits, its newest `lean verify`, its risk paths and its own MR.

A fast run has no Lean intent, so a Lean row's stages, approvals and hand-over do not apply: the AI writes the code,
Lean's commit gate still wants a green `lean verify`, and the run ends with an MR (开 MR) a person merges.
deliveries.classify hands a run without an intent to local() after the checks every row shares (closed, merged, no
run, a question waiting, someone else's, a tool call waiting). Its state stays "local" until the MR is merged or
closed; waits.do says the step (open_mr, merge, full). Nothing here imports deliveries or board: classify passes its
own put and merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

CI_RUNNING = ("created", "waiting_for_resource", "preparing", "pending", "running", "scheduled")
RISKY = "改到了风险目录（%s）：快速需求不能合并这个改动。点「转完整流程」"
READY = "代码提交了，检查是绿的：做完了就点「开 MR」，没做完就回会话"
READY_UNCHECKED = "代码提交了：做完了就点「开 MR」，没做完就回会话"


@dataclass(frozen=True)
class Verify:
    """The run's newest `lean verify` (Lean's VERIFY record)."""
    ok: bool
    at: Optional[float]
    failed: tuple[str, ...]      # the short names of the checks that failed (stages.check_name)


@dataclass(frozen=True)
class FastFacts:
    """What a fast row and its board read besides the requirement's facts."""
    code_commits: int            # commits since the default branch that change a file outside the run's records
    verify: Optional[Verify]
    risky: tuple[str, ...]       # changed files under the default branch's gates/risk-paths: CI fails a fast MR on them
    patrol: bool                 # MR 巡检 works this repo's open MRs (kit.json work_sources has mr-patrol)


def head_pipeline(mr: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The MR's newest pipeline on its head commit (mrinfo's pipelines, newest first); None when there is none, or this
    machine could not read them."""
    rows = [p for p in (mr or {}).get("pipelines") or [] if isinstance(p, dict)]
    head = (mr or {}).get("sha")
    return next((p for p in rows if p.get("sha") == head), None) if head else (rows[0] if rows else None)


def ci_state(mr: Optional[dict[str, Any]]) -> str:
    """running, failed, success, or none (no pipeline on the head, CI off, or not readable here)."""
    st = str((head_pipeline(mr) or {}).get("status") or "")
    return "running" if st in CI_RUNNING else st if st in ("failed", "success") else "none"


def local(put: Callable[..., None], f: dict[str, Any], *, merged: Callable[[str], None],
          slot: Optional[dict[str, Any]], mr: Optional[dict[str, Any]], fast: FastFacts,
          since: Optional[float]) -> None:
    """Fill the row of the person's own fast run. *since*: when its session went quiet (or the branch's last commit)."""
    mr = mr or {}
    n, state = mr.get("iid"), mr.get("state")
    if slot and slot.get("busy"):
        return put("local", "agent", "none", "AI 在写代码")
    if state == "merged":
        return merged("MR 已合并")
    if state == "closed":
        return put("closed", "nobody", "none", "MR 已关闭")
    if fast.risky:
        shown = "、".join(fast.risky[:3]) + ("等 %d 个文件" % len(fast.risky) if len(fast.risky) > 3 else "")
        return put("local", "you", "full", RISKY % shown, where="app", since=since)
    if state == "opened":
        ci = ci_state(mr)
        at = (head_pipeline(mr) or {}).get("at")
        if ci == "running":
            return put("local", "nobody", "none", "MR !%s：CI 在跑" % n, since=at)
        if ci == "failed" and fast.patrol:
            return put("local", "runner", "none", "MR !%s：CI 没过，运行机的 MR 巡检会去修" % n, since=at)
        if ci == "failed":
            return put("local", "you", "reply", "MR !%s：CI 没过：回会话让 AI 修" % n,
                       where="session" if slot else "app", since=at)
        return put("local", "people", "merge", "MR !%s：%s" % (n, "CI 过了，等人审、合并" if ci == "success" else "等人审、合并"),
                   where="mr", since=at)
    if not f.get("worktree"):
        return put("local", "you", "reply", "这台机器上没有它的工作区：先检出分支 %s，再打开会话接着做" % f.get("branch"),
                   since=since)
    v = fast.verify
    red = v is not None and not v.ok
    if fast.code_commits and not red:
        return put("local", "you", "open_mr", READY if v is not None else READY_UNCHECKED, where="app", since=since)
    checks = "、".join(v.failed[:3]) if red and v.failed else "有检查失败"
    if slot:
        step = "自动检查没过（%s）：看会话，回它" % checks if red else "会话停下了：看它最后说了什么，回它"
        return put("local", "you", "reply", step, where="session", since=since)
    step = "自动检查没过（%s）：打开会话让 AI 修" % checks if red else "没有开着的会话：打开会话，跟 AI 说要做什么"
    return put("local", "you", "reply", step, where="app", since=since)
