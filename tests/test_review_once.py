"""开审查会话 opens one review session per review: clicked again while that review is not written yet, it hands the same
session back and sends nothing. A new one opens once the review it opened is written (review.md changed after it
opened: the code changed since, so Lean wants another review) or KiroCrew no longer has it."""

import asyncio
import json
import os

from appkit import mod
from brainfix import make_service, needs_brainkit, run
from test_todo_answer import setup

pytestmark = needs_brainkit
SM = mod("sessions")
LV = mod("lean_view")


def review_doc(wt):
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    return wt / LV.DOCS / intent / LV.REVIEW_DOC


def told(gw, slot):
    return [m["content"] for m in gw.msgs[slot] if m["role"] == "user"]


def test_clicked_twice_it_hands_back_the_review_session_it_opened(tmp_path, home):
    repo, wt, _, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            await svc.open_session("r1", "PAY-1")
            first = await svc.open_session("r1", "PAY-1", review=True)
            await svc.sessions.poll()
            again = await svc.open_session("r1", "PAY-1", review=True)
            return first, again, await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    first, again, d = run(go())
    assert again["slot"] == first["slot"] and again["url"] == first["url"], (first, again)
    assert again["said"]
    assert [s["title"] for s in gw.slot_rows.values()] == ["PAY-1 · 本地开发", "PAY-1 · 审查"], d["sessions"]
    assert len(told(gw, first["slot"])) == 1


def test_two_clicks_at_once_open_one_review_session(tmp_path, home):
    repo, wt, _, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            return await asyncio.gather(*(svc.open_session("r1", "PAY-1", review=True) for _ in range(2)))
        finally:
            await svc.stop()

    a, b = run(go())
    assert a["slot"] == b["slot"] and len(gw.msgs) == 1, (a, b, list(gw.msgs))


def test_once_its_review_is_written_the_next_click_opens_a_new_one(tmp_path, home):
    repo, wt, _, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            first = await svc.open_session("r1", "PAY-1", review=True)
            opened = svc.sessions.by_slot(first["slot"]).started
            doc = review_doc(wt)
            doc.parent.mkdir(parents=True, exist_ok=True)
            doc.write_text("| finding | severity | file:line | fix |\n", encoding="utf-8")
            os.utime(doc, (opened + 10, opened + 10))
            for r in svc.repos.values():
                r._scan = None          # the review is written minutes later, never inside the scan's 4 s cache
            gw.finish(first["slot"])
            await svc.sessions.poll()
            return first, await svc.open_session("r1", "PAY-1", review=True)
        finally:
            await svc.stop()

    first, second = run(go())
    assert second["slot"] != first["slot"] and second["said"] == "", second
    assert len(told(gw, second["slot"])) == 1


def test_a_review_written_before_it_opened_does_not_count_as_its_review(tmp_path, home):
    repo, wt, _, gw = setup(tmp_path, home)
    doc = review_doc(wt)
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("| finding | severity | file:line | fix |\n", encoding="utf-8")
    os.utime(doc, (1_000_000, 1_000_000))
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            first = await svc.open_session("r1", "PAY-1", review=True)
            await svc.sessions.poll()
            return first, await svc.open_session("r1", "PAY-1", review=True)
        finally:
            await svc.stop()

    first, again = run(go())
    assert again["slot"] == first["slot"], (first, again)


def test_a_review_session_kirocrew_no_longer_has_is_replaced_by_a_new_one(tmp_path, home):
    repo, wt, _, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            first = await svc.open_session("r1", "PAY-1", review=True)
            gw.finish(first["slot"])
            for _ in range(3):
                await svc.sessions.poll()
            gw.slot_rows.pop(first["slot"])
            for _ in range(SM.STATS_EVERY * SM.MISSING_POLLS + 1):
                await svc.sessions.poll()
            return first, await svc.open_session("r1", "PAY-1", review=True)
        finally:
            await svc.stop()

    first, second = run(go())
    assert second["slot"] != first["slot"] and second["slot"] in gw.slot_rows, second
