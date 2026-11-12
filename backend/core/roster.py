"""交付总览's team view: who has how many requirements open against the limit (按人看), and what is stuck (全队卡点), read
from the overview's rows (deliveries.base_row and classify) with the limits in 设置 (settings.LIMITS). Pure: the
service calls it once per overview, and the preview world with the same rows.

What is stuck is one table, STUCK_RULES, the worst kind first: a wait on a person past its timeout (超时), someone
else's run on their own machine without new progress (很久没动), two open requirements that change the same directory
(目录撞车), verify failing round after round (verify 失败). Time counts in working hours: Monday to Friday, 09:00 to
18:00 local time (work_hours_between).
"""

from __future__ import annotations

from datetime import datetime, time as clock, timedelta
from typing import Any, Callable, Iterator, NamedTuple, Optional

from . import deliveries
from .settings import LIMITS

WORKDAY = (clock(9), clock(18))
PERSON = ("you", "people", "product", "builder")      # the waits.who that is a person (ui/src/types.ts WaitWho)
COUNTED = tuple(s for s in deliveries.OPEN_STATES if s != "unclaimed")    # 在做: what someone has open


def work_hours_between(a: float, b: float) -> float:
    """The working hours from *a* to *b* (epoch seconds): Monday to Friday, 09:00 to 18:00 local time; 0 when *b* is
    not after *a*."""
    total, day, last = 0.0, datetime.fromtimestamp(a).date(), datetime.fromtimestamp(b).date()
    while day <= last:
        if day.weekday() < 5:
            start, end = (datetime.combine(day, t).timestamp() for t in WORKDAY)
            total += max(0.0, min(b, end) - max(a, start))
        day += timedelta(days=1)
    return total / 3600


def hours(h: float) -> str:
    return "%d 个工作小时" % h


class Ctx(NamedTuple):
    me: Optional[str]
    now: float
    limits: dict


class Hit(NamedTuple):
    """A stuck requirement a rule found: how bad (the worst first within its kind), its row, the line said about it,
    since when (None: no one moment), the other requirement of a pair."""
    worse: float
    row: dict
    text: str
    since: Optional[float] = None
    other: Optional[str] = None


def _open(r: dict[str, Any]) -> bool:
    """Someone has it and it is not done: what 在做 counts."""
    return r["state"] in COUNTED and bool(r["owner"])


def overdue(rows: list[dict[str, Any]], ctx: Ctx) -> Iterator[Hit]:
    """A wait on a person (an approval, an answer, the acceptance, 开工, a reply …) for wait_timeout_h or longer. On
    someone's machine (local, claimed) a wait with no step to take is the work, or what it depends on, moving there:
    that is stale's, or the dependency's own row's."""
    for r in rows:
        w = r["waits"]
        if not _open(r) or w["who"] not in PERSON or w["since"] is None:
            continue
        if w["do"] == "none" and r["state"] in ("local", "claimed"):
            continue
        h = work_hours_between(w["since"], ctx.now)
        if h >= ctx.limits["wait_timeout_h"]:
            yield Hit(h, r, "%s，已等 %s（超时）" % (deliveries.wait_head(w["text"]), hours(h)), since=w["since"])


def stale(rows: list[dict[str, Any]], ctx: Ctx) -> Iterator[Hit]:
    """Someone else's run on their own machine with no new progress for stale_after_h or longer: classify says it waits
    on them since its run branch's tip, the last progress they pushed."""
    for r in rows:
        w = r["waits"]
        if not _open(r) or r["state"] != "local" or r["owner"] == ctx.me or (w["who"], w["do"]) != ("people", "none") \
                or w["since"] is None:
            continue
        h = work_hours_between(w["since"], ctx.now)
        if h >= ctx.limits["stale_after_h"]:
            yield Hit(h, r, "%s，%s没有新进度：问问 %s 卡在哪" % (deliveries.wait_head(w["text"]), hours(h), r["owner"]),
                      since=w["since"])


def overlap(rows: list[dict[str, Any]], ctx: Ctx) -> Iterator[Hit]:
    """Two open requirements of one repo that change the same directory, the pairs 「这些需求要改同一处」 lists: one line
    per pair, on its first requirement."""
    for a, b, paths in deliveries.overlap_pairs(rows):
        todo = "都是 %s 的，先合一个再做另一个" % a["owner"] if a["owner"] and a["owner"] == b["owner"] else "先商量好谁先合"
        yield Hit(0, a, "和 %s 都要改 %s：%s" % (b["key"], "、".join(paths[:3]), todo), other=b["key"])


def verify_streak(rows: list[dict[str, Any]], ctx: Ctx) -> Iterator[Hit]:
    """A run whose verify failed verify_fail_limit times or more in a row since it last passed: the AI should stop
    fixing and ask a person."""
    for r in rows:
        n = r["verify_fails"]
        if _open(r) and n >= ctx.limits["verify_fail_limit"]:
            yield Hit(n, r, "verify 连续失败 %d 次，该停下来问人了" % n)


# kind -> the rule that finds it, the worst kind first: 全队卡点 lists and counts them in this order
STUCK_RULES: dict[str, Callable[[list[dict[str, Any]], Ctx], Iterator[Hit]]] = {
    "overdue": overdue, "stale": stale, "overlap": overlap, "verify_fails": verify_streak}


def roster(rows: list[dict[str, Any]], *, me: str | None, now: float, settings: dict[str, Any]) -> dict[str, Any]:
    """Roster (ui/src/types.ts): each person with requirements open, you first, then the most open; what is stuck,
    the worst first; how many of each kind; the limits it went by. *me*: who this machine's person is."""
    limits = {k: settings[k] for k in LIMITS}
    ctx = Ctx(me, now, limits)
    stuck = [{"repo": h.row["repo"], "key": h.row["key"], "kind": kind, "text": h.text, "since": h.since,
              "other_key": h.other}
             for kind, find in STUCK_RULES.items() for h in sorted(find(rows, ctx), key=lambda h: -h.worse)]
    keys: dict[str, list[str]] = {}
    for r in rows:
        if _open(r):
            keys.setdefault(r["owner"], []).append(r["key"])
    people = sorted(({"owner": o, "you": o == me, "open": len(k), "limit": limits["wip_limit"], "keys": k}
                     for o, k in keys.items()), key=lambda p: (not p["you"], -p["open"], p["owner"]))
    return {"people": people, "stuck": stuck,
            "counts": {kind: sum(1 for s in stuck if s["kind"] == kind) for kind in STUCK_RULES}, "limits": limits}
