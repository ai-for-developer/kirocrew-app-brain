"""The working box's ticker on the 需求看板: what a run whose session is on this machine did last (its last call, the end of
the last line its agent wrote), cut and pseudonymized the way the 产出 drawer's replies are, and never a person's typed
words."""

import asyncio
import json
import logging

from appkit import FakeGateway, mod

sessions = mod("sessions")
board = mod("board")
runs = mod("runs")

TYPED = "我的口令是 OPEN-SESAME-42"


def transcript(cwd):
    return [
        {"role": "user", "content": "开工 PAY-16", "meta": {"sendId": "s-1-0"}, "ts": "2026-11-08T10:00:00Z"},
        {"role": "assistant", "content": "先读需求。\n要通知 alice@example.com 一声。", "meta": {}, "ts": "2026-11-08T10:01:00Z"},
        {"role": "tool", "content": "🔧 Writing", "ts": "2026-11-08T10:02:00Z",
         "meta": {"tool_name": "fs_write", "kind": "edit", "input": json.dumps({"command": "create", "path": cwd + "/aidlc-docs/a/plan.md"})}},
        {"role": "user", "content": TYPED, "meta": {"human": True}, "ts": "2026-11-08T10:03:00Z"},
    ]


def test_the_tail_reads_the_agent_s_rows_only_never_the_line_a_person_typed():
    t = sessions.tail(transcript("/w/shop"), lambda s: s, "/w/shop")
    assert t["call"] == {"at": t["call"]["at"], "tool": "写文件", "detail": "aidlc-docs/a/plan.md"}
    assert t["said"] == "要通知 alice@example.com 一声。"
    assert TYPED not in json.dumps(t, ensure_ascii=False)
    assert sessions.tail([{"role": "user", "content": TYPED, "meta": {"human": True}}], lambda s: s) is None


def test_the_ticker_cuts_at_a_line_end_pseudonymizes_and_leaves_out_a_line_too_long_to_send_whole():
    t = board.ticker(sessions.tail(transcript("/w/shop"), lambda s: s, "/w/shop"))
    assert t["tool"] == "写文件" and t["detail"] == "aidlc-docs/a/plan.md"
    assert "alice@example.com" not in t["text"] and t["text"].startswith("要通知 ") and t["text"].endswith(" 一声。")
    long = board.ticker({"call": {"at": 1.0, "tool": "跑命令", "detail": "pytest"}, "said": "x" * (board.TICKER_TEXT + 1),
                         "said_at": 2.0})
    assert long == {"at": 1.0, "tool": "跑命令", "detail": "pytest", "text": None}
    assert board.ticker(None) is None


def test_only_a_run_whose_session_is_here_has_a_ticker():
    tail = sessions.tail(transcript("/w"), lambda s: s, "/w")
    here = runs.RunView("local", "local", "本机会话", "here", tail=tail)
    away = runs.RunView("runner-1", "runner", "运行机 第 1 次", "elsewhere", n=1, tail=tail)
    assert board._run_wire(here)["ticker"]["tool"] == "写文件"
    assert board._run_wire(away)["ticker"] is None


def test_the_session_manager_keeps_a_slot_s_tail_live_and_never_writes_it_to_its_state_file(tmp_path):
    async def go():
        gw = FakeGateway()
        gw.templates = {"brain-dev"}
        mgr = sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"))
        info = await mgr.open_session("开工", parent_session_key="brain:r1-PAY-16", cwd="/w/shop", title="t", folder="PAY-16",
                                      agent="brain-dev", strict_agent=True, nudge_ok=False, kind="local", key="r1-PAY-16")
        gw.finish(info.slot)
        gw.msgs[info.slot] += transcript("/w/shop")[1:]
        for _ in range(sessions.STATS_EVERY + 1):
            await mgr.poll()
        t = mgr.slot_tail(info.slot)
        assert t["said"] == "要通知 alice@example.com 一声。" and t["call"]["detail"] == "aidlc-docs/a/plan.md"
        mgr._save_now()
        saved = (tmp_path / "sessions.json").read_text(encoding="utf-8")
        assert "tail" not in json.loads(saved)["stats"][info.slot] and TYPED not in saved

    asyncio.run(go())
