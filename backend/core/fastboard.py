"""运行过程 of a fast run (快速): five steps in place of the Lean stage graph.

写代码 (the run's commits), 自动检查 (its newest `lean verify`), 开 MR, CI (the MR's newest pipeline on its head) and 合并.
The rest of the board is what every run has: the events and the conveyor of its commits (their diffs come from
outputs.file_view, which serves any file the run's own commits changed), the check wall, who it waits on. A fast run
has no Lean stage, round, relay or acceptance, so those parts are empty.
"""

from __future__ import annotations

from typing import Any, Optional

from .fastrow import FastFacts, ci_state, head_pipeline

STEPS: tuple[tuple[str, str], ...] = (("code", "写代码"), ("verify", "自动检查"), ("mr", "开 MR"), ("ci", "CI"),
                                      ("merge", "合并"))
RISK_STEP = "改到了风险目录：转完整流程"


def steps(f: dict[str, Any], *, fast: FastFacts, mr: Optional[dict[str, Any]], busy: bool) -> list[dict[str, Any]]:
    """The five steps (ui/src/types.ts FastStep), each from the facts alone."""
    mr = mr or {}
    state, url = mr.get("state"), mr.get("url")
    opened = state in ("opened", "merged")
    pipe = head_pipeline(mr) if opened else None
    p_at, p_url = (pipe or {}).get("at"), (pipe or {}).get("url")
    v = fast.verify

    if busy:
        code = ("running", "AI 在写", None, None)
    elif fast.code_commits:
        code = ("done", "提交了 %d 次" % fast.code_commits, f.get("tip_at"), None)
    else:
        code = ("waiting", "还没提交代码", None, None)

    if v is None:
        check = ("waiting", "还没跑", None, None)
    elif v.ok:
        check = ("done", "过了", v.at, None)
    else:
        check = ("failed", "没过：%s" % ("、".join(v.failed[:3]) or "有检查失败"), v.at, None)

    if opened:
        open_mr = ("done", "MR !%s" % mr.get("iid"), None, url)
    elif fast.risky:
        open_mr = ("failed", RISK_STEP, None, None)
    else:
        open_mr = ("waiting", "做完了点「开 MR」", None, None)

    ci = ci_state(mr) if opened else "none"
    if not opened:
        cis = ("waiting", "开了 MR 才跑", None, None)
    elif fast.risky and state == "opened":
        cis = ("failed", RISK_STEP, p_at, p_url)
    elif ci == "success":
        cis = ("done", "过了", p_at, p_url)
    elif ci == "failed":
        cis = ("failed", "没过", p_at, p_url)
    elif ci == "running":
        cis = ("running", "在跑", None, p_url)
    else:
        cis = ("waiting", "没有流水线", None, None)

    if state == "merged" or f.get("merged"):
        merge = ("done", "合并了", f.get("merged_at"), url)
    elif state == "opened":
        merge = ("waiting", "等人审、合并", None, url)
    else:
        merge = ("waiting", "", None, None)

    said = {"code": code, "verify": check, "mr": open_mr, "ci": cis, "merge": merge}
    return [{"id": sid, "label": label, "status": said[sid][0], "text": said[sid][1], "at": said[sid][2],
             "url": said[sid][3]} for sid, label in STEPS]
