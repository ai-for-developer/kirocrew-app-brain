"""A fast run's row (快速): what it waits on, from its commits, its newest `lean verify`, its risk paths and its own MR.

A fast run has no Lean intent, so a Lean row's stages, approvals and hand-over do not apply: the AI writes the code,
Lean's commit gate still wants a green `lean verify`, and the run ends with an MR (开 MR) a person merges.
deliveries.classify hands a run without an intent to local(), or a teammate's to theirs(), after the checks every row
shares (closed, merged, no run, a question waiting, a tool call waiting). Its state stays "local" until the MR is
merged or closed; waits.do says the step (open_mr, merge, full). Nothing here imports deliveries or board: classify
passes its own put and merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import bk, lean_view, stages
from .team import sources

CI_RUNNING = ("created", "waiting_for_resource", "preparing", "pending", "running", "scheduled")
RISKY = "改到了风险目录（%s）：快速需求不能合并这个改动。点「转完整流程」"
READY = "代码提交了，检查是绿的：做完了就点「开 MR」，没做完就回会话"
READY_UNCHECKED = "代码提交了：做完了就点「开 MR」，没做完就回会话"
BY_HAND = "推上去了：MR 在 GitLab 上开，等人审、合并（这台机器没有 GitLab 令牌，读不到 MR）"
STALE = "（GitLab 这会儿读不到：这是 %s前读到的）"


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
    # changed files under the default branch's gates/risk-paths: CI fails a fast MR on them; None when git could not
    # tell what the branch changed (`brain mr` and CI still check)
    risky: Optional[tuple[str, ...]]
    patrol: bool                 # MR 巡检 works this repo's open MRs (kit.json work_sources has mr-patrol)
    stale_s: Optional[float] = None     # the MR shown was read this long ago: this machine's reads since failed
    # no GitLab token here, and 开 MR here pushed the branch as it is now: its MR is opened on GitLab by hand
    by_hand: bool = False


def newest_verify(evs: list[dict[str, Any]]) -> Optional[Verify]:
    """The run's newest VERIFY among its own ledger records (oldest first)."""
    e = next((e for e in reversed(evs) if e.get("event") == "VERIFY"), None)
    if e is None:
        return None
    d = e.get("data") if isinstance(e.get("data"), dict) else {}
    failed = [stages.check_name(r.get("cmd")) for r in d.get("results") or []
              if isinstance(r, dict) and r.get("exit") not in (0, None)]
    return Verify(ok=bool(d.get("ok")), at=e.get("_at"), failed=tuple(dict.fromkeys(failed)))


def _git_facts(repo: Any, tip: str, main: str) -> tuple[int, Optional[tuple[str, ...]]]:
    """(code commits, risky files) of the branch at *tip* against the default branch at *main*: what changed since
    they parted, judged by brain-kit as brain-ci and `brain mr` judge it (the default branch's risk paths); risky None
    when git could not diff them."""
    base = repo.git.out("merge-base", main, tip)
    if not base:
        return 0, ()
    runs = repo.cfg["runs_dir"]
    n = repo.git.out("rev-list", "--count", "%s..%s" % (base, tip), "--", ".", ":(exclude)%s/" % runs,
                     ":(exclude)%s/" % lean_view.DOCS)
    count = int(n) if n.isdigit() else 0
    raw = repo.git.show(main, repo.cfg["risk_paths_file"])
    text = raw.decode("utf-8", "replace") if raw else ""
    ci = bk.m("ci")
    try:
        changed, links = ci.changed_paths(repo.path, base, tip), ci.gitlinks(repo.path, base, tip)
    except bk.brain_error():
        return count, None
    _, risky = bk.m("riskpaths").hits(text, changed, links)
    return count, tuple(risky)


def fast_facts(repo: Any, f: dict[str, Any], cache: dict[tuple, tuple[int, Optional[tuple[str, ...]]]], *,
               git: bool = True, stale_s: Optional[float] = None, by_hand: bool = False) -> FastFacts:
    """A fast run's FastFacts. The git half only changes with its tip or the default branch: kept in *cache*, unless
    git failed (read again next time); without *git* (a row that never reads it: merged, closed, a teammate's) it is
    not read, and says 0 commits, risk paths not known."""
    tip, main = f.get("tip"), repo.main_sha()
    k = (repo.path, tip, main)
    got = cache.get(k) if git else (0, None)
    if got is None:
        if len(cache) > 512:
            cache.clear()
        got = _git_facts(repo, tip, main) if tip and main else (0, ())
        if got[1] is not None:
            cache[k] = got
    commits, risky = got
    return FastFacts(code_commits=commits, verify=newest_verify(f.get("events") or []), risky=risky,
                     patrol=sources.PATROL in sources.enabled(repo.cfg), stale_s=stale_s, by_hand=by_hand)


def polls_mr(f: dict[str, Any], mr: Optional[dict[str, Any]]) -> bool:
    """Whether this machine keeps reading a fast run's MR from GitLab: until it is merged (on GitLab, or the run is on
    the default branch), whoever's run it is. Nothing on this machine says when its MR opens, closes or opens again
    (开 MR here, `brain mr` in a terminal or the AI's session, GitLab itself)."""
    return not f.get("merged") and (mr or {}).get("state") != "merged"


def _ago(s: float) -> str:
    return "%d 小时" % (s // 3600) if s >= 5400 else "%d 分钟" % max(1, s // 60)


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


def _opened(put: Callable[..., None], mr: dict[str, Any], fast: FastFacts, *, fixer: str, fix: str,
            where: Optional[str]) -> None:
    """The waits of a fast run whose MR is open: its CI on the MR's head, then the people who review and merge it.
    *fixer* fixes a red CI that MR 巡检 does not (*fix*: how it is said, *where*: where it is done)."""
    n, ci, at = mr.get("iid"), ci_state(mr), (head_pipeline(mr) or {}).get("at")
    old = STALE % _ago(fast.stale_s) if fast.stale_s is not None else ""
    if ci == "running":
        return put("local", "nobody", "none", "MR !%s：CI 在跑%s" % (n, old), since=at)
    if ci == "failed" and fast.patrol:
        return put("local", "runner", "none", "MR !%s：CI 没过，运行机的 MR 巡检会去修%s" % (n, old), since=at)
    if ci == "failed":
        return put("local", fixer, "reply" if fixer == "you" else "none", "MR !%s：CI 没过：%s%s" % (n, fix, old),
                   where=where, since=at)
    return put("local", "people", "merge", "MR !%s：%s%s" % (
        n, "CI 过了，等人审、合并" if ci == "success" else "等人审、合并", old), where="mr", since=at)


def theirs(put: Callable[..., None], f: dict[str, Any], *, merged: Callable[[str], None],
           mr: Optional[dict[str, Any]], fast: FastFacts, owner: str) -> None:
    """Fill the row of a teammate's fast run (*owner*'s): their session is on their machine, its MR on GitLab, which
    every machine reads alike (its CI, its review, its merge, waiting on people there, not on them alone); before they
    open it, they work on their own machine."""
    mr = mr or {}
    if mr.get("state") == "merged":
        return merged("MR 已合并")
    if mr.get("state") == "closed":
        return put("closed", "nobody", "none", "MR 已关闭")
    if mr.get("state") == "opened":
        return _opened(put, mr, fast, fixer="people", fix="等 %s 修" % owner, where="mr")
    return put("local", "people", "none", "%s 在自己的机器上做" % owner, since=f.get("tip_at"))


def local(put: Callable[..., None], f: dict[str, Any], *, merged: Callable[[str], None],
          slot: Optional[dict[str, Any]], mr: Optional[dict[str, Any]], fast: FastFacts,
          since: Optional[float]) -> None:
    """Fill the row of the person's own fast run. *since*: when its session went quiet (or the branch's last commit)."""
    mr = mr or {}
    state = mr.get("state")
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
        return _opened(put, mr, fast, fixer="you", fix="回会话让 AI 修", where="session" if slot else "app")
    if fast.by_hand:
        return put("local", "people", "merge", BY_HAND, where="mr", since=since)
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
