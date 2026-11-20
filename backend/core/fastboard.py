"""运行过程 of a fast run (快速): five steps in place of the Lean stage graph.

写代码 (the run's commits), 自动检查 (its newest `lean verify`), 开 MR, CI (the MR's newest pipeline on its head) and 合并.
The rest of the board is what every run has: the events and the conveyor of its commits (their diffs come from
outputs.file_view, which serves any file the run's own commits changed), the check wall, who it waits on. A fast run
has no Lean stage, round, relay or acceptance, so those parts are empty.
"""

from __future__ import annotations

from typing import Any, Optional

from . import board as board_mod
from . import stages
from .deliveries import CAT_LABEL, CAT_ORDER, category
from .fastrow import FastFacts, ci_state, head_pipeline

STEPS: tuple[tuple[str, str], ...] = (("code", "写代码"), ("verify", "自动检查"), ("mr", "开 MR"), ("ci", "CI"),
                                      ("merge", "合并"))
RISK_STEP = "改到了风险目录：转完整流程"
RISK_UNKNOWN = "这台机器上 git 比不出这个分支改了哪些文件：开 MR 和 CI 照样会查风险目录"


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
    elif fast.by_hand:
        open_mr = ("done", "推上去了，在 GitLab 上开", None, None)
    else:
        open_mr = ("waiting", "做完了点「开 MR」", None, None)

    ci = ci_state(mr) if opened else "none"
    if fast.by_hand and not opened:
        cis = ("waiting", "这台机器读不到（没有 GitLab 令牌）", None, None)
    elif not opened:
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
    elif state == "opened" or fast.by_hand:
        merge = ("waiting", "等人审、合并", None, url)
    else:
        merge = ("waiting", "", None, None)

    said = {"code": code, "verify": check, "mr": open_mr, "ci": cis, "merge": merge}
    return [{"id": sid, "label": label, "status": said[sid][0], "text": said[sid][1], "at": said[sid][2],
             "url": said[sid][3]} for sid, label in STEPS]


def _placed(evs: list[dict[str, Any]]) -> None:
    """Each event where the board shows it with no stage graph: on no node, round or run, travelling between the
    anchors it has (the owner, the branch, the checks)."""
    def end(this: str, other: str) -> Optional[str]:
        return this if this not in (board_mod.NODE, board_mod.RUN_AT) else \
            other if other not in (board_mod.NODE, board_mod.RUN_AT) else None
    for e in evs:
        frm, to = board_mod.ANCHORS[e["kind"]]
        e["node"], e["run"], e["round"] = None, None, None
        e["from"], e["to"] = end(frm, to), end(to, frm)


def _checks(f: dict[str, Any], fast: FastFacts, mr: Optional[dict[str, Any]], kit_rows: list[dict[str, Any]],
            local_only: bool) -> dict[str, Any]:
    """The check wall of a fast run: its newest verify's checks, its MR's CI, the risk paths, the repo's kit."""
    cells: list[dict[str, Any]] = []
    todo: list[str] = []

    def cell(cid: str, group: str, label: str, name: str, status: str, at: Optional[float], detail: str) -> None:
        cells.append({"id": cid, "group": group, "label": label, "name": name, "status": status, "at": at,
                      "detail": board_mod._clip(detail, 160), "node": None, "run": None})

    verifies = [e for e in f.get("events") or [] if e.get("event") == "VERIFY"]
    history = []
    for e in verifies:
        res = [r for r in (e.get("data") or {}).get("results") or [] if isinstance(r, dict)][:40]
        history.append({"at": e.get("_at"), "passed": sum(1 for r in res if r.get("exit") in (0, None)),
                        "failed": sum(1 for r in res if r.get("exit") not in (0, None))})
    last = verifies[-1] if verifies else None
    if last is None:
        cell("verify", "verify", "自动检查", "自动检查", "skip", None, "还没跑过自动检查：AI 提交前会跑")
        todo.append("自动检查")
    else:
        d = last.get("data") or {}
        res = [r for r in d.get("results") or [] if isinstance(r, dict)][:40]
        if not res:
            ok = bool(d.get("ok"))
            cell("verify", "verify", "自动检查", "自动检查", "pass" if ok else "fail", last.get("_at"),
                 "自动检查通过" if ok else "自动检查没过：有检查失败")
        seen: dict[str, int] = {}
        for r in res:
            name = stages.check_name(r.get("cmd"))
            k = seen[name] = seen.get(name, 0) + 1
            ok = r.get("exit") in (0, None)
            cell("verify-%s-%d" % (name, k), "verify", name, "自动检查：%s" % name, "pass" if ok else "fail",
                 last.get("_at"), "通过" if ok else "没过：在会话里看自动检查的输出")
    opened = (mr or {}).get("state") in ("opened", "merged")
    pipe = head_pipeline(mr) if opened else None
    ci = ci_state(mr) if opened else "none"
    if local_only:
        cell("ci", "ci", "CI", "MR 的流水线", "not_needed", None, "只在本机做：没有 MR，也没有 CI")
    elif not opened and fast.by_hand:
        cell("ci", "ci", "CI", "MR 的流水线", "skip", None, "这台机器没有 GitLab 令牌，读不到 MR 的流水线：在 GitLab 上看")
    elif not opened:
        cell("ci", "ci", "CI", "MR 的流水线", "skip", None, "开了 MR 才跑")
        todo.append("CI")
    else:
        status, detail = {"success": ("pass", "过了"), "failed": ("fail", "没过：看 MR 上的流水线"),
                          "running": ("watch", "在跑")}.get(ci, ("skip", "MR 上还没有流水线"))
        cell("ci", "ci", "CI", "MR 的流水线", status, (pipe or {}).get("at") if status in ("pass", "fail") else None,
             detail)
    if fast.risky:
        cell("risk", "risk", "风险", "风险目录（gates/risk-paths）", "fail", None,
             "改到了 %d 个：快速需求不能合并，转完整流程" % len(fast.risky))
    elif fast.risky is None:
        cell("risk", "risk", "风险", "风险目录（gates/risk-paths）", "skip", None, RISK_UNKNOWN)
    else:
        cell("risk", "risk", "风险", "风险目录（gates/risk-paths）", "pass", None, "没改到风险目录")
    for i, k in enumerate(kit_rows):     # a ! reminder is about the team runner, which never takes a fast run
        level = k.get("level")
        cell("kit-%d" % i, "brain", k.get("label") or board_mod.KIT_LABEL, k.get("text") or "",
             "fail" if level == "FAIL" else "not_needed" if level == "WARN" else "pass", None,
             "快速需求不交给运行机：用不上" if level == "WARN" else (k.get("todo") or k.get("text") or ""))
    ats = [c["at"] for c in cells if c["at"] is not None]
    return {"cells": cells, "passed": sum(1 for c in cells if c["status"] == "pass"),
            "failed": sum(1 for c in cells if c["status"] == "fail"),
            "total": sum(1 for c in cells if c["status"] in ("pass", "fail", "wait")), "todo": todo,
            "at": max(ats) if ats else None, "history": history}


def board(f: dict[str, Any], row: dict[str, Any], t: Optional[dict[str, Any]], *, mr: Optional[dict[str, Any]],
          app_events: list[dict[str, Any]], subjects: dict[str, str], changes: dict[str, list[dict[str, Any]]],
          default_branch: str, local_only: bool, kit_rows: list[dict[str, Any]], decisions: list[dict[str, Any]],
          me: Optional[str], fast: FastFacts, fold: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The board's graph part for a fast run, the keys board.build answers with and steps; *fold*, when given, gets
    the run branch's commits (the 产出 drawer reads them; a fast run has no node, round or session turn)."""
    local_only = bool(local_only or row.get("local_only"))
    you = board_mod._you(me, f.get("owner"))
    evs = board_mod.events_of(f, t, mr=mr, app_events=app_events, team_decisions=[], subjects=subjects, reflect_refs=[],
                              default_branch=default_branch, you=you)
    _placed(evs)
    for i, e in enumerate(evs, 1):
        e["seq"] = i
    merged_at = next((e["at"] for e in evs if e["kind"] == "merge"), None)
    busy = (row.get("waits") or {}).get("who") == "agent"
    checks = _checks(f, fast, mr, kit_rows, local_only)
    merged = merged_at is not None or row.get("state") == "merged"
    counts = {c: 0 for c in CAT_ORDER}
    for d in decisions:
        counts[category(d)] = counts.get(category(d), 0) + 1
    accept = {"state": "merged" if merged else "not_yet",
              "step": {"text": "", "who": "", "line": None, "comment": None, "mr_url": None, "session_url": None},
              "by": [{"id": c, "label": CAT_LABEL[c], "count": counts[c]} for c in CAT_ORDER if counts.get(c)],
              "checks": board_mod.checks_line(checks),
              "done": {"at": merged_at or f.get("merged_at") or 0, "who": None, "how": "已合并"} if merged else None}
    if fold is not None:
        fold.update(nodes=[], commits=board_mod._branch_commits(t, subjects, changes, {}), turns=[], runs=[])
    raw = [x for x in (t or {}).get("missing") or [] if not str(x).startswith("Lean audit")]
    return {"events": [{k: e[k] for k in ("seq", "at", "kind", "from", "to", "text", "detail", "src", "node", "run",
                                          "round")} for e in evs][-board_mod.MAX_EVENTS:],
            "belt": board_mod._belt(evs, changes), "checks": checks, "person": board_mod._person(row, me, f, you),
            "accept": accept, "live": busy, "started_at": evs[0]["at"] if evs else None, "ended_at": merged_at,
            "lean_steps": [], "phases": [], "nodes": [], "runs": [], "relays": [], "other_runs": [],
            "missing": board_mod.missing_sources(raw, local_only=local_only, handed=False,
                                                 ci_read=board_mod.pipelines_read(mr)),
            "steps": steps(f, fast=fast, mr=mr, busy=busy)}
