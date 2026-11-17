"""A session longer than one page of KiroCrew's transcript (at most 500 rows a call): the app's own message is found
however far back it is, is never sent twice into a session that took it, and the session's credits and turns are its
whole transcript's."""

import asyncio
import itertools
import logging

from appkit import FakeGateway, mod

sessions = mod("sessions")
T0 = 1_800_000_000.0
MIDS = itertools.count()    # KiroCrew gives each row of a transcript its own id (meta.mid)


def tool(i):
    return {"role": "tool", "content": "🔧 Reading f%d.py" % i, "meta": {"tool_name": "read", "mid": "m-%d" % next(MIDS)},
            "ts": "2026-11-08T10:01:00Z"}


def reply(text, credits=None):
    meta = {"turn_stats": {"credits": credits}} if credits is not None else {}
    return {"role": "assistant", "content": text, "meta": dict(meta, mid="m-%d" % next(MIDS)), "ts": "2026-11-08T10:02:00Z"}


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
        out.append({"role": "user", "content": "第 %d 轮" % t, "meta": {"human": True, "mid": "m-%d" % next(MIDS)},
                    "ts": "2026-11-08T10:00:00Z"})
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


def test_a_long_session_s_stats_read_only_what_came_after_the_last_read(tmp_path):
    """Every STATS_EVERY polls a tracked slot's credits and turns are read: once its transcript was read whole, the
    next read is its newest page, and the totals stay the whole transcript's. One rewound (shorter, or other rows
    where the last read stopped) is read whole again."""
    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        gw.msgs[info.slot] += long_session(20, 85)
        pages = lambda: sum(1 for c in gw.calls if c[:2] == ("page", info.slot))

        def whole_says(busy=False):
            return dict(sessions.spend(gw.msgs[info.slot], busy), whole=True)

        def said(st):
            return {k: st[k] for k in ("credits", "turns", "subagents", "unreported", "whole")}

        st = await stats_of(mgr, gw, info.slot)
        assert info.done and said(st) == whole_says() and st["credits"] == 30.0
        gw.msgs[info.slot] += long_session(2, 30)
        n = pages()
        st = await stats_of(mgr, gw, info.slot)
        assert pages() - n == 1 and said(st) == whole_says() and st["credits"] == 33.0 and st["turns"] == 23
        cut_short = st["unreported"]
        gw.msgs[info.slot].append({"role": "user", "content": "再来", "meta": {"human": True}})
        gw.msgs[info.slot] += [tool(i) for i in range(3)]
        gw.slot_rows[info.slot]["running"] = True
        st = await stats_of(mgr, gw, info.slot)
        assert said(st) == whole_says(busy=True) and st["unreported"] == cut_short, "the turn at work is not cut short"
        gw.slot_rows[info.slot]["running"] = False
        st = await stats_of(mgr, gw, info.slot)
        assert said(st) == whole_says() and st["unreported"] == cut_short + 1
        n = len(gw.msgs[info.slot])
        del gw.msgs[info.slot][600:]
        gw.msgs[info.slot] += long_session(14, 85)
        assert len(gw.msgs[info.slot]) > n, "other rows where the last read stopped"
        st = await stats_of(mgr, gw, info.slot)
        assert said(st) == whole_says() and st["credits"] == 1.5 * (6 + 14)    # 6 turns left of the 20, the 14 new

    asyncio.run(go())


def test_a_session_replaced_by_look_alike_rows_without_ids_is_read_whole(tmp_path):
    """Rows without KiroCrew's row id (meta.mid) look the same at one index in another session: one put in the slot
    with the same calls and replies but other credits is counted and shown as read whole, not from the old one."""
    def no_ids(rows):
        for m in rows:
            m["meta"].pop("mid", None)
        return rows

    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        gw.msgs[info.slot] = no_ids(long_session(20, 85))
        assert (await stats_of(mgr, gw, info.slot))["credits"] == 30.0
        assert (await mgr.kept_transcript(info.slot))[0] == gw.msgs[info.slot]
        gw.msgs[info.slot] = no_ids(long_session(20, 85))
        for m in gw.msgs[info.slot][:200]:
            if m["role"] == "assistant":
                m["meta"]["turn_stats"]["credits"] = 4.0
        st = await stats_of(mgr, gw, info.slot)
        assert (st["credits"], st["whole"]) == (sessions.spend(gw.msgs[info.slot], False)["credits"], True) == (35.0, True)
        assert (await mgr.kept_transcript(info.slot))[0] == gw.msgs[info.slot]

    asyncio.run(go())


def test_the_rows_kept_of_all_sessions_stay_within_kept_rows(tmp_path, monkeypatch):
    """产出 keeps the final rows of the transcripts it read last, KEPT_ROWS rows in all: one with more than that is
    read whole every time, and the oldest kept are dropped (read whole on their next read) to keep the rest."""
    monkeypatch.setattr(sessions, "KEPT_ROWS", 1000, raising=False)

    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        for slot, n in (("a", 600), ("b", 600), ("c", 1300), ("d", 400)):
            gw.msgs[slot] = [tool(i) for i in range(n)]

        async def pages(slot):
            gw.calls.clear()
            rows, whole = await mgr.kept_transcript(slot)
            assert rows == gw.msgs[slot] and whole
            return len(gw.calls)

        assert [await pages(x) for x in "abab"] == [2, 2, 1, 1]
        assert [await pages("c"), await pages("c"), await pages("b")] == [3, 3, 1], "a long one is never kept"
        assert [await pages("d"), await pages("b"), await pages("a")] == [1, 1, 2], "a was kept longest: dropped"

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

def test_a_turn_is_a_message_and_the_agent_s_work_up_to_the_next_one(tmp_path):
    """UI-8: 「轮」 counted the agent's replies: 3 turns with 11 replies showed as 11 轮. The 产出 drawer's rule
    (turns.py): a turn is a user row and the rows after it, up to the next user row."""
    async def go():
        gw = FakeGateway()
        mgr, _ = manager(tmp_path, gw)
        gw.script = [[tool(0), reply("a", 1.0), tool(1), reply("b", 1.0), reply("c", 1.0), reply("d", 1.0)]]
        info = await opened(mgr, gw)
        gw.finish(info.slot)
        for _ in range(3):
            await mgr.poll()
        assert info.done and info.turns == 1
        for t in (1, 2):
            gw.msgs[info.slot] += [{"role": "user", "content": "u%d" % t, "meta": {"human": True}}] + [
                x for a in range(4 if t == 1 else 3) for x in (tool(a), reply("r%d" % a, 1.0))]
        assert sum(1 for m in gw.msgs[info.slot] if m["role"] == "assistant") == 11
        st = await stats_of(mgr, gw, info.slot)
        assert st["turns"] == 3 and st["credits"] == 11.0
        snap = mod("service").Service._sessions_snapshot(type("S", (), {"sessions": mgr})())
        assert [r["turns"] for r in snap["by"]["r1:PAY-1"]] == [3]

    asyncio.run(go())
