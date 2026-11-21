"""需求现状's 时间花在哪 of a fast run, which has no stage lanes: from its sessions' transcripts on this machine, the AI's
turns (a message to the last row it brought) and the waits for a person between them, between its sessions and since
its newest row, up to the merge."""

from appkit import mod
from brainfix import needs_brainkit, run, start_in_worktree
from test_fast_flow import START, idle, project, service as fast_service

sessions, fastboard = mod("sessions"), mod("fastboard")
H = "2026-11-08T%s:00Z"


def rows(*pairs):
    return [{"role": role, "content": "x", "meta": {}, "ts": H % at} for role, at in pairs]


TRANSCRIPT = rows(("user", "10:00"), ("assistant", "10:05"), ("tool", "10:07"), ("assistant", "10:10"),
                  ("user", "10:30"), ("assistant", "10:45"))


def test_a_transcripts_time_goes_to_the_ai_and_to_the_waits_between_its_turns():
    got = sessions.Spent().add(TRANSCRIPT).times()
    assert (got["work_s"], got["wait_s"], got["waits"]) == (600 + 900, 1200, 1)
    assert got["last_at"] - got["first_at"] == 45 * 60
    assert sessions.Spent().add(TRANSCRIPT[:3]).add(TRANSCRIPT[3:]).times() == got, "read in parts it adds up to the whole"
    quick = sessions.Spent().add(rows(("user", "10:00"), ("assistant", "10:01"), ("user", "10:01"))).times()
    assert quick["waits"] == 0, "an answer within a minute is no wait"


def stat(first, last, work, wait=0, waits=0, busy=False, whole=True):
    t = sessions._epoch(H % "10:00")
    return {"first_at": t + first * 60, "last_at": t + last * 60, "work_s": work * 60, "wait_s": wait * 60,
            "waits": waits, "busy": busy, "whole": whole}


def test_its_sessions_are_merged_with_the_waits_between_them_and_since():
    now = sessions._epoch(H % "13:00")
    a, b = stat(0, 45, 25, wait=20, waits=1), stat(120, 150, 30)
    got = fastboard.time_spent([b, a], now=now, until=None)
    assert got == {"agent": 55 * 60, "person": (20 + 75 + 30) * 60, "waits": 3, "sessions": 2, "whole": True}
    busy = fastboard.time_spent([a, dict(b, busy=True)], now=now, until=None)
    assert (busy["agent"], busy["person"], busy["waits"]) == ((55 + 30) * 60, 95 * 60, 2), "a turn still going is the AI's"
    merged = fastboard.time_spent([a, b], now=now, until=sessions._epoch(H % "12:40"))
    assert merged["person"] == (20 + 75 + 10) * 60, "nothing after the merge"
    assert fastboard.time_spent([a, None], now=now, until=None)["whole"] is False, "a session not read yet: 至少"
    assert fastboard.time_spent([dict(a, whole=False)], now=now, until=None)["whole"] is False
    assert fastboard.time_spent([None], now=now, until=None) is None


@needs_brainkit
def test_a_fast_board_says_its_time_and_a_lean_board_none(tmp_path, home):
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)
    gw.script = [[{"role": "assistant", "content": "好了", "meta": {}, "ts": H % "10:20"}]]

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            for _ in range(sessions.STATS_EVERY + 1):
                await svc.sessions.poll()
            start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
            svc._repo("r1").invalidate()
            return (await svc.board("r1", out["key"]))["time"], (await svc.board("r1", "PAY-2"))["time"]
        finally:
            await svc.stop()

    fast, lean = run(go())
    assert lean is None
    assert fast["sessions"] == 1 and fast["agent"] == 20 * 60, fast
