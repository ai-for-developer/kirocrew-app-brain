"""A session longer than one page of KiroCrew's transcript (at most 500 rows a call): the app's own message is found
however far back it is, is never sent twice into a session that took it, and the session's credits and turns are its
whole transcript's."""

import asyncio
import logging

from appkit import FakeGateway, mod

sessions = mod("sessions")
T0 = 1_800_000_000.0


def tool(i):
    return {"role": "tool", "content": "🔧 Reading f%d.py" % i, "meta": {"tool_name": "read"}, "ts": "2026-11-08T10:01:00Z"}


def reply(text, credits=None):
    meta = {"turn_stats": {"credits": credits}} if credits is not None else {}
    return {"role": "assistant", "content": text, "meta": meta, "ts": "2026-11-08T10:02:00Z"}


def manager(tmp_path, gw):
    clock = [T0]
    mgr = sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"),
                                  now=lambda: clock[0])
    return mgr, clock


async def opened(mgr, gw, task="开工：PAY-1"):
    gw.templates = {"brain-dev"}
    return await mgr.open_session(task, parent_session_key="brain:r1:PAY-1", cwd="", title="PAY-1 · 本地开发",
                                  folder="PAY-1 · refund", agent="brain-dev", strict_agent=True, resend_ok=True,
                                  nudge_ok=False, kind="local", key="r1:PAY-1")


def sends(gw):
    return [c for c in gw.calls if c[0] == "send"]


def test_an_opening_turn_longer_than_a_page_is_never_sent_again(tmp_path):
    """UI-6: the newest 300 rows did not hold the app's own message, so after 90 s idle it was sent again into the
    person's session."""
    async def go(rows):
        gw = FakeGateway()
        mgr, clock = manager(tmp_path / str(rows), gw)
        gw.script = [[tool(i) for i in range(rows - 1)] + [reply("问题 1：退款要人工审核吗？", 3.0)]]
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        clock[0] += sessions.NO_START_S + 30
        for _ in range(4):
            await mgr.poll()
            clock[0] += 5
        assert len(sends(gw)) == 1, (rows, sends(gw))
        assert info.done and info.error is None and info.result == "问题 1：退款要人工审核吗？"
        assert info.tool_count == rows - 1 and info.credits == 3.0

    for rows in (350, 1200):
        asyncio.run(go(rows))


def test_a_busy_turn_longer_than_a_page_keeps_moving(tmp_path):
    """UI-6: past 300 rows a busy turn's count of rows stood still, so it was shown as 卡住 though it was writing."""
    async def go():
        gw = FakeGateway()
        mgr, clock = manager(tmp_path, gw)
        info = await opened(mgr, gw)
        slot = info.slot
        gw.slot_rows[slot]["_pending"] = []
        gw.msgs[slot] += [tool(i) for i in range(400)]
        await mgr.poll()
        clock[0] += sessions.STALL_S + 10
        gw.msgs[slot].append(tool(400))
        await mgr.poll()
        assert gw.slot_rows[slot]["running"] and not info.stalled and info.last_activity == clock[0]
        assert info.tool_count == 401 and len(sends(gw)) == 1

    asyncio.run(go())


def test_a_message_that_never_landed_is_sent_again_once_and_one_not_found_in_a_part_read_never(tmp_path, monkeypatch):
    """The resend is for a message KiroCrew took but never wrote down: only when the whole transcript was read and has
    no row of it. A transcript too long to read to its start may hold it: nothing is sent."""
    async def lost():
        gw = FakeGateway()
        mgr, clock = manager(tmp_path / "lost", gw)
        info = await opened(mgr, gw)
        gw.msgs[info.slot] = [{"role": "user", "content": "我自己打的", "meta": {}}, reply("好")]
        gw.slot_rows[info.slot].update(running=False, _pending=[])
        clock[0] += sessions.NO_START_S + 1
        await mgr.poll()
        assert len(sends(gw)) == 2 and info.resends == 1

    async def unread():
        gw = FakeGateway()
        mgr, clock = manager(tmp_path / "unread", gw)
        info = await opened(mgr, gw)
        gw.slot_rows[info.slot].update(running=False, _pending=[])
        gw.msgs[info.slot] += [tool(i) for i in range(3 * 500)]
        clock[0] += sessions.NO_START_S + 1
        await mgr.poll()
        assert len(sends(gw)) == 1 and info.resends == 0

    asyncio.run(lost())
    monkeypatch.setattr(sessions, "TRANSCRIPT_PAGES", 2)
    asyncio.run(unread())


def long_session(turns, rows_each):
    """*turns* turns, each a message, its calls and one reply of 1.5 credits: rows_each + 2 rows a turn."""
    out = []
    for t in range(turns):
        out.append({"role": "user", "content": "第 %d 轮" % t, "meta": {"human": True}, "ts": "2026-11-08T10:00:00Z"})
        out += [tool(i) for i in range(rows_each)]
        out.append(reply("做完第 %d 轮" % t, 1.5))
    return out


async def stats_of(mgr, gw, slot):
    for _ in range(sessions.STATS_EVERY + 1):
        await mgr.poll()
    return mgr.slot_stats(slot)


def test_a_session_longer_than_a_page_counts_every_turn_s_credits(tmp_path):
    """UI-7: KiroCrew answers at most 500 rows a call; a session of 1741 rows was counted 663 credits, not 1990."""
    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        gw.msgs[info.slot] += long_session(20, 85)
        assert len(gw.msgs[info.slot]) == 1742
        st = await stats_of(mgr, gw, info.slot)
        assert st["credits"] == 30.0 and st["whole"] is True
        assert mgr.credits_by_key() == {"r1:PAY-1": 30.0}

    asyncio.run(go())


def test_a_session_too_long_to_read_whole_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "TRANSCRIPT_PAGES", 2)

    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        gw.msgs[info.slot] += long_session(20, 85)
        st = await stats_of(mgr, gw, info.slot)
        read = gw.msgs[info.slot][-2 * 500:]
        assert st["whole"] is False and st["credits"] == 1.5 * sum(1 for m in read if m["role"] == "assistant") < 30.0

    asyncio.run(go())

