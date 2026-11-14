"""产出's session tabs (GET /outputs/{repo}/{key}/turns?node&round): for a round its run did on this machine, the reply,
the message and the calls of each session turn it was done in, found by the sha256 Lean recorded of the message; for a
round done elsewhere, a card that says where. What a person typed is never shown, the owner included: only its length
and time. The app shows its own messages as it wrote them down when it sent them; a bridge message only as its kind
allows; anything else is hidden. Transcripts are read whole, a page of 500 rows at a time."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

from appkit import FakeGateway, mod

gateway = mod("gateway")
sessions = mod("sessions")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KiroCrewSlots(gateway.Gateway):
    """The real Gateway over KiroCrew 0.11.2's GET /api/chat/slots/{key} (dashboard/chat_api/slot_detail.py): at most
    500 rows a call, `before` the row index the page ends at, has_more and next_before (the page's first index). Each
    call may first append rows to the slot (*grow*), as a busy session does while it is read."""

    def __init__(self, rows, grow=()):
        super().__init__("brain", "/nonexistent")
        self.rows, self.grow, self.calls = list(rows), list(grow), []

    async def request(self, method, path, params=None, body=None):
        self.calls.append(dict(params or {}))
        if self.grow:
            self.rows.extend(self.grow.pop(0))
        limit, before = min(int(params["limit"]), 500), params.get("before")
        end = len(self.rows) if before is None else max(0, min(int(before), len(self.rows)))
        start = max(0, end - limit)
        return {"key": path.rsplit("/", 1)[-1], "messages": self.rows[start:end], "has_more": start > 0,
                "next_before": start}


def row(i):
    return {"role": "tool", "content": "🔧 Read File", "meta": {"mid": "m-%d" % i}, "ts": "2026-11-08T10:00:00Z"}


def manager(gw):
    return sessions.SessionManager(gw, app="brain", state_file="/nonexistent/sessions.json", log=None)


def test_a_long_transcript_is_read_whole_page_by_page_without_losing_or_repeating_a_row():
    gw = KiroCrewSlots([row(i) for i in range(1234)], grow=[[row(9000), row(9001)], [row(9002)]])
    rows, whole = asyncio.run(manager(gw).transcript("brain-PAY-9-7f2c"))
    mids = [r["meta"]["mid"] for r in rows]
    assert whole and mids == ["m-%d" % i for i in range(1234)] + ["m-9000", "m-9001"]
    assert [c.get("before") for c in gw.calls] == [None, 736, 236]


def test_a_transcript_past_the_page_cap_says_it_is_not_whole():
    gw = KiroCrewSlots([row(i) for i in range(500 * sessions.TRANSCRIPT_PAGES + 7)])
    rows, whole = asyncio.run(manager(gw).transcript("s"))
    assert not whole and len(rows) == 500 * sessions.TRANSCRIPT_PAGES and rows[0]["meta"]["mid"] == "m-7"


def test_a_cursor_that_does_not_move_back_stops_the_read():
    class Stuck(KiroCrewSlots):
        async def request(self, method, path, params=None, body=None):
            self.calls.append(dict(params or {}))
            return {"messages": self.rows[-2:], "has_more": True, "next_before": 5}

    gw = Stuck([row(i) for i in range(9)])
    rows, whole = asyncio.run(manager(gw).transcript("s"))
    assert not whole and len(gw.calls) == 2


# ---------------------------------------------------------------- what the drawer may show, written when it is sent

def test_a_bridge_message_shows_by_its_kind_and_anything_else_not_at_all():
    shown = sessions.bridge_shown
    hand = "接手合同 V-7（RUN-PAY-9-1，摘要 sha256:0123456789abcdef）。工作区已检出 ai/alice/PAY-9-refund-sms @ 3b8e01f00000。\n" \
           "按 brain-run 的接手步骤继续：先读 runs/PAY-9/run.json。"
    assert shown(hand) == {"by": "bridge", "cause": "合同", "text": hand}
    assert shown("CI 没过：MR 的流水线 #77（提交 3b8e01f00000）失败了。\n> FAILED tests/test_sms.py") == \
        {"by": "bridge", "cause": "CI 没过 #77", "text": "CI 没过：MR 的流水线 #77（提交 3b8e01f00000）失败了。"}
    assert shown("决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。\n说明：旧订单不该发") == \
        {"by": "bridge", "cause": "D-2 退回", "text": "决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。"}
    assert shown("决策 D-1 已答（凭证 V-8，答复人 G-1a2b3c）：A。")["cause"] == "D-1 已答"
    for other in ("请把短信改短一点", "approve RELEASE 1a2b3c4d", " 接手合同 V-7", "CI 没过"):
        assert shown(other) is None, other


def test_a_bridge_message_is_shown_as_its_kind_allows_when_the_bridge_sends_it():
    gw = FakeGateway()
    mgr = manager(gw)

    async def go():
        await mgr.adapter_create("RUN-PAY-9-1", agent="brain-run", model="", memory_mode="", title="t", folder="f",
                                 parent="brain:runner", key="RUN-PAY-9-1", root_folder="Brain 运行机")
        await mgr.adapter_send("RUN-PAY-9-1", "决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。\n说明：别发旧订单", "V-9")
        await mgr.adapter_send("RUN-PAY-9-1", "（运行机）随便说点什么", "N-1")

    asyncio.run(go())
    assert mgr.sent("RUN-PAY-9-1") == {"V-9": {"by": "bridge", "cause": "D-2 退回",
                                                "text": "决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。"},
                                       "N-1": None}


def test_shown_survives_a_restart():
    info = sessions.SessionInfo(task="t", shown={"by": "app", "cause": "开工消息，只有编号", "text": "t"})
    assert sessions.SessionInfo.from_dict(json.loads(json.dumps(info.to_dict()))).shown == info.shown
    assert sessions.SessionInfo.from_dict({"task": "older"}).shown is None
