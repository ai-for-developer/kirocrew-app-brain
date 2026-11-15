"""The requirement's live brain-dev session on this machine: one answer from the backend (service._live), sent to the
page as live_slot. 打开会话 opens it, 重开一个会话 replaces exactly it, an answer goes into it. Never a review session
(开审查会话 opens one beside it), never one KiroCrew no longer has. A session whose first message is being sent, or did
not go in, is the requirement's session too: one session per worktree, and only 重开 replaces it."""

import asyncio

from appkit import mod
from brainfix import make_service, run
from test_todo_answer import setup

SM = mod("sessions")
AppError = mod("httpkit").AppError


def stops(gw):
    return [c[1] for c in gw.calls if c[0] == "stop"]


def test_reopen_with_a_review_session_open_replaces_the_dev_session_not_the_review(tmp_path, home):
    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            dev = await svc.open_session("r1", "PAY-1")
            review = await svc.open_session("r1", "PAY-1", review=True)
            await svc.sessions.poll()
            d = await svc.requirement("r1", "PAY-1")
            same = await svc.open_session("r1", "PAY-1")
            got = await svc.reopen_session("r1", "PAY-1", d["live_slot"])
            return dev, review, d, same, got, await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    dev, review, d, same, got, d2 = run(go())
    assert d["live_slot"] == dev["slot"] and same["slot"] == dev["slot"]
    assert got["reopened"] and got["slot"] not in (dev["slot"], review["slot"]), got
    assert dev["slot"] in stops(gw) and review["slot"] not in stops(gw)
    assert d2["live_slot"] == got["slot"]


def test_reopen_after_the_newest_session_was_deleted_in_kirocrew_opens_a_new_one(tmp_path, home):
    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            s1 = await svc.open_session("r1", "PAY-1")
            gw.finish(s1["slot"])
            await svc.sessions.poll()
            s2 = await svc.reopen_session("r1", "PAY-1", s1["slot"])
            gw.finish(s2["slot"])
            for _ in range(3):
                await svc.sessions.poll()
            gw.slot_rows.pop(s2["slot"])          # the person deletes it in KiroCrew
            for _ in range(SM.STATS_EVERY * SM.MISSING_POLLS + 1):
                await svc.sessions.poll()
            d = await svc.requirement("r1", "PAY-1")
            return s1, s2, d, await svc.reopen_session("r1", "PAY-1", d["live_slot"])
        finally:
            await svc.stop()

    s1, s2, d, got = run(go())
    gone = {s["slot"]: s["missing"] for s in d["sessions"]}
    assert gone == {s1["slot"]: False, s2["slot"]: True}, d["sessions"]
    assert d["live_slot"] == s1["slot"]
    assert got["reopened"] and got["slot"] not in (s1["slot"], s2["slot"]) and got["slot"] in gw.slot_rows, got


def test_after_a_reopen_whose_first_message_failed_the_next_reopen_opens_one(tmp_path, home):
    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            s1 = await svc.open_session("r1", "PAY-1")
            gw.finish(s1["slot"])
            await svc.sessions.poll()
            gw.fail_send = True                   # KiroCrew fails the new session's first message
            try:
                await svc.reopen_session("r1", "PAY-1", s1["slot"])
                failed = None
            except AppError as exc:
                failed = exc
            gw.fail_send = False
            await svc.sessions.poll()
            d = await svc.requirement("r1", "PAY-1")
            return s1, failed, d, await svc.reopen_session("r1", "PAY-1", d["live_slot"])
        finally:
            await svc.stop()

    s1, failed, d, got = run(go())
    assert isinstance(failed, AppError) and failed.status == 502, failed
    broken = [s["slot"] for s in d["sessions"] if s["error"]]
    assert len(broken) == 1 and d["live_slot"] == broken[0], d["sessions"]
    assert got["reopened"] and got["slot"] not in (s1["slot"], broken[0]), got
    assert gw.msgs[got["slot"]], "the new session got its first message"


def test_a_second_reopen_from_the_page_that_still_shows_the_old_session_replaces_the_failed_one(tmp_path, home):
    """The page asked to replace s1, the new session's first message failed, and the page has not read again: the next
    重开 (still naming s1) opens one; a session whose first message failed is never the answer to the same click."""
    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            s1 = await svc.open_session("r1", "PAY-1")
            gw.finish(s1["slot"])
            await svc.sessions.poll()
            gw.fail_send = True
            try:
                await svc.reopen_session("r1", "PAY-1", s1["slot"])
            except AppError:
                pass
            gw.fail_send = False
            return s1, await svc.reopen_session("r1", "PAY-1", s1["slot"]), await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    s1, got, d = run(go())
    assert got["reopened"] and len(gw.slot_rows) == 3 and gw.msgs[got["slot"]], (got, list(gw.slot_rows))
    assert d["live_slot"] == got["slot"]


def test_two_open_clicks_at_once_while_kirocrew_is_slow_open_one_session(tmp_path, home):
    """Two tabs, or 开会话 and the question card's, while KiroCrew takes a moment to take the first message."""
    repo, wt, line, gw = setup(tmp_path, home)
    real_send = gw.send

    async def slow_send(key, message, send_id):
        await asyncio.sleep(0.3)
        return await real_send(key, message, send_id)

    gw.send = slow_send
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            return await asyncio.gather(svc.open_session("r1", "PAY-1"), svc.open_session("r1", "PAY-1"))
        finally:
            await svc.stop()

    a, b = run(go())
    assert a["slot"] == b["slot"] and len(gw.slot_rows) == 1, (a, b, list(gw.slot_rows))


def test_open_after_a_failed_first_message_returns_that_session_and_says_so(tmp_path, home):
    """The first message failed (502); the person then typed in that session in KiroCrew. 开会话 again opens no second
    session in the worktree: it hands that one back and says its first message did not go in."""
    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            gw.fail_send = True
            try:
                await svc.open_session("r1", "PAY-1")
                first = None
            except AppError as exc:
                first = exc
            gw.fail_send = False
            s1 = next(iter(gw.slot_rows))
            gw.msgs[s1].append({"role": "user", "content": "接着做", "meta": {}, "ts": "2026-11-08T10:01:00Z"})
            gw.slot_rows[s1]["running"] = True
            for _ in range(2):
                await svc.sessions.poll()
            d = await svc.requirement("r1", "PAY-1")
            return first, s1, d, await svc.open_session("r1", "PAY-1")
        finally:
            await svc.stop()

    first, s1, d, again = run(go())
    assert isinstance(first, AppError) and first.status == 502, first
    assert d["live_slot"] == s1 and again["slot"] == s1 and list(gw.slot_rows) == [s1], (again, list(gw.slot_rows))
    assert "第一条消息没发进去" in again["said"] and "重开" in again["said"], again
