"""产出's session tabs (GET /outputs/{repo}/{key}/turns?node&round): for a round its run did on this machine, the reply,
the message and the calls of each session turn it was done in, found by the sha256 Lean recorded of the message; for a
round done elsewhere, a card that says where. What a person typed is never shown, the owner included: only its length
and time. The app shows its own messages as it wrote them down when it sent them; a bridge message only as its kind
allows; anything else is hidden. Transcripts are read whole, a page of 500 rows at a time."""

import asyncio
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

from appkit import FakeGateway, mod
from brainfix import PY, install_brain_dev, lean, make_project, make_service, needs_brainkit, run, start_in_worktree

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


# ---------------------------------------------------------------- a local round on a real Lean worktree

outputs = mod("outputs")
turns = mod("turns")
stages = mod("stages")
runs = mod("runs")
service = mod("service")

PARK = {"schema": 1, "status": "waiting", "kind": "clarify", "ask": "owner", "question": "退款要不要人工审核？",
        "options": [{"id": "A", "text": "要"}, {"id": "B", "text": "不要"}], "recommend": "A", "refs": []}
PERSON = "PERSON-WORDS 验收标准写细一点，金额按分算"
UNVOUCHED = "UNVOUCHED-WORDS 这条不知道是谁发的"
NOTE = "NOTE-WORDS 金额小于 100 元的不审核"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def tool(kind, args):
    return {"role": "tool", "content": "🔧 Tool", "meta": {"kind": kind, "input": json.dumps(args)}, "ts": now_iso()}


def said(text):
    return {"role": "assistant", "content": text, "meta": {}, "ts": now_iso()}


class NowGateway(FakeGateway):
    """The FakeGateway with each message stamped when it is sent, as KiroCrew does."""

    async def send(self, key, message, send_id):
        got = await super().send(key, message, send_id)
        self.msgs[key][-1]["ts"] = now_iso()
        return got


def hook(wt, text):
    """Lean's UserPromptSubmit hook sees a message arrive in the session (HUMAN_TURN: its sha256, never its words)."""
    p = subprocess.run([PY, ".kiro/aidlc-lean/aidlc_lean.py", "hook", "UserPromptSubmit"], cwd=wt, text=True,
                       capture_output=True, timeout=60,
                       input=json.dumps({"prompt": text, "cwd": str(wt), "session_id": "kiro-1"}))
    assert p.returncode == 0, p.stderr


async def turn_over(svc, gw, slot):
    gw.finish(slot)
    for _ in range(3):
        await svc.sessions.poll()


@needs_brainkit
def test_a_local_round_shows_each_turn_and_never_what_a_person_typed(tmp_path, home):
    repo = make_project(tmp_path)
    install_brain_dev(home)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    (wt / "runs" / "PAY-1" / "park.json").write_text(json.dumps(PARK, ensure_ascii=False))
    gw = NowGateway()
    gw.templates = {"brain-dev"}
    gw.script = [[tool("read", {"path": str(wt / "AGENTS.md")}), said("有一个问题：退款要不要人工审核？")]]
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            slot = (await svc.open_session("r1", "PAY-1", kickoff=True))["slot"]
            hook(wt, gw.msgs[slot][-1]["content"])
            lean(wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")   # brain start began requirements
            await turn_over(svc, gw, slot)
            for text, meta in ((PERSON, {"human": True, "sendId": "web-1"}), (UNVOUCHED, {"turnActor": "app", "sendId": "x-1"})):
                gw.msgs[slot] += [{"role": "user", "content": text, "meta": meta, "ts": now_iso()}, said("好的。")]
                hook(wt, text)
            gw.script.append([tool("search", {"query": "退款 审核"})] * 600 + [
                tool("execute", {"command": "python3 .kiro/aidlc-lean/aidlc_lean.py request REQUIREMENTS"}),
                said("需求写好了，等你批：`approve REQUIREMENTS …`")])
            await svc.answer("r1", "PAY-1", "B", NOTE)
            hook(wt, gw.msgs[slot][-1]["content"])
            doc = wt / "aidlc-docs" / intent / "inception" / "requirements" / "requirements.md"
            doc.write_text("# Requirements\n\n- AC-1 退款\n", encoding="utf-8")
            lean(wt, "request", "REQUIREMENTS")
            gw.finish(slot)
            svc._repo("r1").invalidate()      # the repo's facts are read again (a session takes minutes, not 4 s)
            return slot, await svc.outputs_turns("r1", "PAY-1", outputs.node_query("requirements", None))
        finally:
            await svc.stop()

    slot, got = run(go())
    seen = json.dumps(got, ensure_ascii=False)
    for words in ("PERSON-WORDS", "UNVOUCHED-WORDS", "NOTE-WORDS", "金额"):
        assert words not in seen, words
    kickoff = next(m["content"] for m in gw.msgs[slot] if m["role"] == "user")
    one, typed, unvouched, answer = got["turns"]
    assert [t["n"] for t in got["turns"]] == [1, 2, 3, 4] and got["why_not"] is None
    assert one["also"] == []                 # reverse engineering was skipped in it: a skip is not work done
    assert one["task"] == {"kind": "app", "cause": "开工消息，只有编号", "text": kickoff}
    assert typed["task"] == {"kind": "person", "chars": len(PERSON), "url": "/chat?sid=" + slot} and typed["at"] > one["at"]
    assert unvouched["task"]["kind"] == "hidden" and unvouched["reply"] == "好的。"
    assert answer["task"] == {"kind": "app", "cause": "澄清问题的回答", "text": "（本地）澄清问题已答：B。\n说明："
                              + service.NOTE_HIDDEN + "\n把 park.json 的 status 改成 answered，按这个回答继续。"}
    assert one["calls"] == [{"at": one["calls"][0]["at"], "tool": "读文件", "detail": "AGENTS.md", "in_round": True}]
    assert answer["reply"] == "需求写好了，等你批：`approve REQUIREMENTS …`"
    assert len(answer["calls"]) == turns.CALLS_MAX and answer["more_calls"] == 601 - turns.CALLS_MAX
    assert answer["calls"][0]["tool"] == "搜索" and all(c["in_round"] for c in answer["calls"])
    assert got["meta"]["turns"] == 4 and got["meta"]["calls"] == 1 + 601
    assert [c[2] for c in gw.calls if c[:2] == ("page", slot)][-2:] == [None, len(gw.msgs[slot]) - 500]


# ---------------------------------------------------------------- which turns, and rounds read elsewhere

T = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc).timestamp()     # FakeGateway's user rows are stamped then
HAND = "接手合同 V-7（RUN-PAY-9-1，摘要 sha256:0123456789abcdef）。工作区已检出 ai/alice/PAY-9-refund-sms @ 3b8e01f00000。"
BRIDGE_ANSWER = "决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。\n说明：BRIDGE-NOTE 旧订单也发了"


def rnd(n, run_, started, ended=None, outcome=stages.Outcome.OPEN, ask_at=None, decision=None, commits=()):
    end = stages.RoundEnd(outcome, ended, decision=decision)
    ask = stages.Ask("RELEASE", (), ask_at, run_) if ask_at is not None else None
    return stages.Round(n=n, run=run_, started=started, ended=ended, end=end, why=stages.Why("first"), note=None,
                        outputs=(), commits=tuple(commits), ask=ask, decided=end if decision else None)


def fold_of(stage_id, rounds, run_views, lean_turns):
    n = stages.Node(stages.node_id(stage_id, None), stages.BY_ID[stage_id], None, list(rounds), [],
                    stages.NodeStatus("done", "", None))
    return outputs.Fold(reader=None, intent="261108-refund-sms", branch="ai/alice/PAY-9-refund-sms", nodes=(n,),
                        commits=(), changes={}, runs=tuple(run_views), turns=tuple(lean_turns))


def runner(readable, sessions_=()):
    return runs.RunView("runner-1", "runner", "运行机 第 1 次", readable, n=1, run_id="RUN-PAY-9-1", sessions=list(sessions_))


def test_a_runner_round_on_the_owners_machine_says_where_its_session_is_and_shows_no_turn():
    r = rnd(1, "runner-1", T, ended=T + 3600, outcome=stages.Outcome.SENT_BACK, ask_at=T + 60, decision="D-2",
            commits=("a" * 40, "b" * 40))
    f = fold_of("release", [r], [runner("elsewhere")], [stages.Turn(T, "runner-1", sha(HAND))])
    here = turns.Slot("RUN-PAY-9-1", "/chat?sid=RUN-PAY-9-1", "", False,
                      [{"role": "user", "content": HAND, "meta": {"sendId": "V-7"}, "ts": "2026-11-08T10:00:00Z"}], True,
                      {"V-7": sessions.bridge_shown(HAND)})
    q = outputs.node_query("release", "1")
    assert turns.sessions_to_read(f, q) == []
    got = turns.turn_views(f, q, [here], "PAY-9", T + 7200, str)
    assert got["turns"] == [] and got["why"] is None
    assert got["why_not"] == {"title": "这一轮是运行机做的（RUN-PAY-9-1）",
                              "missing": "它的回复、接到的消息和工具调用留在运行机上，这台机器读不到。",
                              "here": ["它的 2 次提交（「产出文件」里都能看）", "它在 MR 上问的 D-2"],
                              "do": "要看它的回复和工具调用：请管运行机的人在运行机上的 Brain 交付里打开 PAY-9。",
                              "asked": T + 60}
    assert got["meta"] == {"took": 3600.0, "turns": None, "calls": None}


def test_on_the_runner_a_bridge_answer_shows_only_its_first_line():
    gw = FakeGateway()
    mgr = manager(gw)

    async def go():
        await mgr.adapter_create("RUN-PAY-9-1", agent="brain-run", model="", memory_mode="", title="t", folder="f",
                                 parent="brain:runner", key="RUN-PAY-9-1", root_folder="Brain 运行机")
        await mgr.adapter_send("RUN-PAY-9-1", HAND, "V-7")
        gw.finish("RUN-PAY-9-1")
        await mgr.adapter_send("RUN-PAY-9-1", BRIDGE_ANSWER, "V-9")
        gw.finish("RUN-PAY-9-1")

    asyncio.run(go())
    rows = {"slot": "RUN-PAY-9-1", "url": "/chat?sid=RUN-PAY-9-1", "busy": False}
    f = fold_of("release", [rnd(1, "runner-1", T + 1)], [runner("here", [rows])],
                [stages.Turn(T, "runner-1", sha(HAND)), stages.Turn(T + 30, "runner-1", sha(BRIDGE_ANSWER))])
    q = outputs.node_query("release", None)
    assert turns.sessions_to_read(f, q) == [rows]
    slot = turns.Slot("RUN-PAY-9-1", rows["url"], "", False, gw.msgs["RUN-PAY-9-1"], True, mgr.sent("RUN-PAY-9-1"))
    got = turns.turn_views(f, q, [slot], "PAY-9", T + 60, str)
    assert [t["task"] for t in got["turns"]] == [{"kind": "bridge", "cause": "合同", "text": HAND},
                                                 {"kind": "bridge", "cause": "D-2 退回",
                                                  "text": "决策 D-2 已答（凭证 V-9，答复人 G-1a2b3c）：reject。"}]
    assert [t["reply"] for t in got["turns"]] == ["好了", "好了"]
    assert "BRIDGE-NOTE" not in json.dumps(got, ensure_ascii=False)


def test_a_persons_decision_is_typed_in_the_next_rounds_turn_and_another_runs_turns_are_not_its_own():
    t = [stages.Turn(T + x, "local", c * 64) for x, c in ((10, "1"), (40, "2"), (79, "3"), (120, "4"))]
    other = stages.Turn(T + 45, "runner-1", "5" * 64)
    sent_back = rnd(1, "local", T + 20, ended=T + 80, outcome=stages.Outcome.SENT_BACK, ask_at=T + 50)
    again = rnd(2, "local", T + 81)
    assert turns.round_turns(t + [other], sent_back) == t[:2]
    assert turns.round_turns(t + [other], again) == t[2:]
    done = rnd(1, "local", T + 20, ended=T + 79.5, outcome=stages.Outcome.DONE)
    assert turns.round_turns(t, done) == t[:3]


def test_a_turn_whose_message_is_in_no_transcript_here_says_so_and_calls_outside_the_round_are_marked():
    local = runs.RunView("local", "local", "本机会话", "here", sessions=[{"slot": "s1", "url": "/chat?sid=s1", "busy": True}])
    gone, kept = "first message", "（本地）澄清问题已答：A。"
    r = rnd(1, "local", T + 100, ask_at=T + 200)
    f = fold_of("requirements", [r], [local], [stages.Turn(T + 50, "local", sha(gone)), stages.Turn(T + 150, "local", sha(kept))])
    stamp = lambda s: datetime.fromtimestamp(T + s, timezone.utc).isoformat()
    rows = [{"role": "user", "content": kept, "meta": {"sendId": "s-1-0"}, "ts": stamp(150)},
            dict(tool("execute", {"command": "git status"}), ts=stamp(160)),
            dict(tool("edit", {"command": "strReplace", "path": "/w/aidlc-docs/x/requirements.md"}), ts=stamp(170)),
            dict(tool("execute", {"command": "git log"}), ts=stamp(260))]
    slot = turns.Slot("s1", "/chat?sid=s1", "/w", True, rows, True, {"s-1-0": {"by": "app", "cause": "澄清问题的回答", "text": kept}})
    got = turns.turn_views(f, outputs.node_query("requirements", "1"), [slot], "PAY-9", T + 300, str)
    first, answer = got["turns"]
    assert first["task"] == {"kind": "hidden", "why": turns.NOT_FOUND} and first["slot"] is None
    assert [(c["tool"], c["detail"], c["in_round"]) for c in answer["calls"]] == [
        ("跑命令", "git status", True), ("写文件", "aidlc-docs/x/requirements.md", True), ("跑命令", "git log", False)]
    assert got["lit"] == {"from": T + 100, "to": T + 200}     # from its start to its request: what is lit
    assert answer["live"] and answer["reply"] is None


def test_a_turns_text_takes_the_drawers_own_path_cut_at_a_line_end_emails_as_pseudonyms_scrubbed_before_a_cut():
    local = runs.RunView("local", "local", "本机会话", "here", sessions=[{"slot": "s1", "url": "/chat?sid=s1", "busy": False}])
    msg = "（本地）澄清问题已答：A。"
    f = fold_of("requirements", [rnd(1, "local", T, ask_at=T + 100)], [local], [stages.Turn(T, "local", sha(msg))])
    stamp = lambda s: datetime.fromtimestamp(T + s, timezone.utc).isoformat()
    long_reply = "".join("第 %d 行，问 alice@example.com\n" % i for i in range(2000))
    command = "echo " + "a" * 91 + " PRIVATE-TOKEN: SECRETTOKENabcdefgh -o out.json"
    assert command.index("SECRETTOKEN") == 112      # a detail is cut at 119 characters: inside the token
    rows = [{"role": "user", "content": msg, "meta": {"sendId": "s-1-0"}, "ts": stamp(1)},
            dict(tool("execute", {"command": command}), ts=stamp(5)), dict(said(long_reply), ts=stamp(9))]
    sent = {"s-1-0": {"by": "app", "cause": "澄清问题的回答", "text": msg + "\n抄送 bob@example.com"}}
    hide = lambda s: re.sub(r"SECRETTOKEN[a-z]+", "[已隐藏]", s)
    got = turns.turn_views(f, outputs.node_query("requirements", "1"), [turns.Slot("s1", "/chat?sid=s1", "/w", False, rows, True, sent)],
                           "PAY-9", T + 60, hide)
    (one,) = got["turns"]
    body, note = one["reply"].split(turns.LONG)
    assert note == "" and body.endswith("\n") and len(body) <= turns.REPLY_MAX and body.startswith("第 0 行")
    assert "example.com" not in json.dumps(got, ensure_ascii=False) and "G-" in body and "G-" in one["task"]["text"]
    assert "[已隐藏]" in one["calls"][0]["detail"] and "SECRET" not in one["calls"][0]["detail"]


def test_brain_reflect_has_no_ai_session():
    f = fold_of("reflect", [rnd(1, "local", T, ended=T + 9, outcome=stages.Outcome.DONE)],
                [runs.RunView("local", "local", "本机会话", "here")], [stages.Turn(T, "local", "1" * 64)])
    q = outputs.node_query("reflect", "1")
    got = turns.turn_views(f, q, [], "PAY-2", T + 60, str)
    assert turns.sessions_to_read(f, q) == [] and got["turns"] == []
    assert got["why_not"]["title"] == "这一步是 brain reflect 程序做的，没有 AI 会话"


def test_the_turns_route_is_the_owners_only(tmp_path):
    import backend.routes as br
    from http_fakes import FakeRequest, FakeWeb

    class Route:
        def __init__(self, method, path, handler):
            self.method, self.path, self.handler = method, path, handler

    def call(owner, **req):
        svc = br.service.Service(SimpleNamespace(name="brain", logger=None, data_dir=str(tmp_path / "data")))
        h = {(x.method, x.path): x.handler for x in br.handlers.build_routes(svc, Route, web=FakeWeb(),
                                                                          is_owner=lambda _r: owner)}
        req = dict({"query": {"node": "requirements"}}, **req)
        return asyncio.run(h[("GET", "/outputs/{repo}/{key}/turns")](FakeRequest(match_info={"repo": "r1", "key": "PAY-9"}, **req), None))

    assert call(False, user=None).status == 401
    assert call(True, app="brain").status == 403
    assert call(False).body["code"] == "owner_only"
    assert call(True, query={"node": "../x"}).status == 400
