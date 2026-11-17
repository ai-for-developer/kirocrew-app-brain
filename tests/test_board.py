"""需求看板 (GET /board/{repo}/{key}): on real brain-kit projects, the stage graph's nodes, runs and hand-overs, the
events placed on it, the conveyor, the check wall and 验收: a local requirement waiting for its requirements approval,
one approved and handed over, one merged; the approval line is shown, never sent; the route is the owner's only. And
the snapshot board.build folds from Lean's own records, read the way the page reads it: where the time went (a quiet
session waits for the person, the merge ends the requirement's time), what the check wall counts, who is asked, the
one step."""

import json
import re
import subprocess
import time

from appkit import ROOT, mod
from brainfix import (CREW_AGENT, filled_notes, PY, brain, lean, make_project, make_service, needs_brainkit, request_requirements, run, sh,
                      start_in_worktree)

board, deliveries = mod("board"), mod("deliveries")


def approve(wt, line):
    """The person types the approval line in the session: Lean's UserPromptSubmit hook records it."""
    p = subprocess.run([PY, ".kiro/aidlc-lean/aidlc_lean.py", "hook", "UserPromptSubmit"], cwd=wt, text=True,
                       capture_output=True, timeout=60,
                       input=json.dumps({"prompt": line, "cwd": str(wt), "session_id": "s-test"}))
    assert p.returncode == 0, p.stderr


@needs_brainkit
def test_a_local_requirement_waits_for_its_approval_on_the_board(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款导出")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    line = request_requirements(wt, intent)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    b = run(go())
    req = node(b, "requirements")
    # the requirements approval waits for the person, with the exact line to type (shown, never sent)
    assert req["status"] == "awaiting_approval"
    assert [(g["kind"], g["state"], g["line"]) for g in req["gates"]] == [("REQUIREMENTS", "pending", line)]
    assert (b["runs"][0]["key"], b["runs"][0]["now"]["status"], b["runs"][0]["now"]["do"]) == (
        "local", "awaiting_approval", "approve")
    assert b["person"]["status"] == "awaiting_approval" and b["person"]["owner"] == "alice"
    assert b["accept"]["step"]["line"] == line and b["accept"]["state"] == "not_yet"
    # a repo without GitLab: nothing is handed over, nobody runs it but this machine
    assert b["relays"] == [] and [r["key"] for r in b["runs"]] == ["local"]
    # the events, in time order, numbered; the stage events are Lean's detail
    kinds = [e["kind"] for e in b["events"]]
    assert "start" in kinds and "request" in kinds and [e["seq"] for e in b["events"]] == list(range(1, len(b["events"]) + 1))
    assert all(e["detail"] for e in b["events"] if e["kind"] == "stage")
    asked = next(e for e in b["events"] if e["kind"] == "request")
    assert (asked["from"], asked["to"], asked["node"]) == ("requirements", "person", "requirements")
    # the node's history says when it began to wait: the replay shows it
    assert req["history"] and req["history"][-1]["status"] == "awaiting_approval"
    assert req["spans"] and req["spans"][-1]["kind"] == "wait" and req["spans"][-1]["b"] is None
    # the check wall: the requirements gate waits, verify has not run
    cells = {c["id"]: c for c in b["checks"]["cells"]}
    assert cells["gate-REQUIREMENTS"]["status"] == "wait" and cells["verify"]["status"] == "skip"
    assert b["row"]["key"] == "PAY-1" and b["mr_desc"]["available"] in (True, False)
    assert "approve REQUIREMENTS" not in json.dumps(b["events"])  # the line itself only in the gate and the step


@needs_brainkit
def test_an_approved_requirement_handed_over_and_one_merged(tmp_path, home):
    # handed over through GitLab: a remote too (without one a repo is done only on this machine)
    repo = make_project(tmp_path, origin=True, kit={"gitlab": {"base": "https://gitlab.example.com", "project": "shop/pay"}})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    line = request_requirements(wt, intent)
    approve(wt, line)
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "refund.py").write_text("def refund():\n    return 1\n", encoding="utf-8")
    sh("git", "-C", wt, "add", "-A")
    sh("git", "-C", wt, "commit", "-qm", "refund: the CSV export")
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(), "Status: Construction step 2 of 4"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    sh("git", "-C", wt, "push", "-q", "origin", "HEAD")    # on the remote: handed over (not push_handoff)
    wt2 = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "done", "done", "做完了")
    sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge PAY-2", "ai/alice/PAY-2-done")
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    sh("git", "-C", repo, "worktree", "remove", "--force", wt2)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.board("r1", "PAY-1"), await svc.board("r1", "PAY-2"), await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    b1, b2, d1 = run(go())
    assert [(g["state"], g["line"]) for g in node(b1, "requirements")["gates"]] == [("approved", None)]
    assert b1["runs"][0]["now"]["doing"] == "做完 · 交出去了"
    [relay] = b1["relays"]
    assert [(x["key"], x["status"]) for x in relay["steps"]][:2] == [("handover", "merged"), ("bridge", "running")]
    ok = next(e for e in b1["events"] if e["kind"] == "approve")
    assert (ok["from"], ok["to"]) == ("person", "requirements")
    if b1["source"] == "trace":
        # the conveyor: the person's commits on the run branch and the hand-over commit
        kinds = [x["kind"] for x in b1["belt"]]
        assert "handover" in kinds and kinds.index("handover") == len(kinds) - 1
        subj = [x["text"] for x in b1["belt"] if x["kind"] == "commit"]
        assert any("refund: the CSV export" in s for s in subj)
        assert any(e["kind"] == "handover" and e["to"] == "relay" for e in b1["events"])
        # BOARD-21: each commit with the files it changed, as git says it
        export = next(x for x in b1["belt"] if x["kind"] == "commit" and "refund: the CSV export" in x["text"])
        assert {"path": "src/refund.py", "added": 2, "removed": 0, "status": "A"} in export["changes"]
        # the sources brain-kit could not read, in words, said the same on the board and in 需求详情 (DETAIL-6)
        assert b1["missing"] and b1["missing"] == d1["timeline"]["missing"]
        assert not any(w in json.dumps(b1["missing"]) for w in ("ledger", "--", "not given"))
    cells = {c["id"]: c for c in b1["checks"]["cells"]}
    assert cells["gate-REQUIREMENTS"]["status"] == "pass" and cells["bridge"]["status"] == "wait"
    assert b2["row"]["state"] == "merged" and b2["accept"]["state"] == "merged"
    if b2["source"] == "trace":
        assert b2["ended_at"] is not None and any(e["kind"] == "merge" for e in b2["events"])
        assert all(n["status"] in ("merged", "skipped") for n in b2["nodes"]), \
            sorted((n["id"], n["status"], n["doing"]) for n in b2["nodes"] if n["status"] not in ("merged", "skipped"))
        # a merged requirement that never went to the runner: no hand-over, only this machine's run
        assert b2["relays"] == [] and [r["key"] for r in b2["runs"]] == ["local"]
    text = json.dumps([b1, b2])
    assert "glpat-" not in text and "gitlab_token" not in text.lower() and "@example" not in text  # no secret, no email


@needs_brainkit
def test_an_idle_session_waits_since_one_moment_and_a_claim_has_nothing_to_trace(tmp_path, home):
    """The row (交付总览, 待办) and the board say one moment the session went quiet: not before Lean's last step, even
    when KiroCrew reported only the kickoff's time. A requirement only claimed has no run: no error on its board
    (BOARD-6)."""
    from brainfix import FakeGateway, install_brain_dev
    repo = make_project(tmp_path)
    install_brain_dev(home)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    brain(repo, "claim", "--title", "只认领")
    gw = FakeGateway()
    gw.templates = {"brain-dev-crew"}
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            await svc.open_session("r1", "PAY-1")
            gw.finish(next(iter(gw.slot_rows)))
            for _ in range(6):
                await svc.sessions.poll()
            lean(wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
            svc.repos["r1"].invalidate()
            rows = {r["key"]: r for r in (await svc.overview("all"))["rows"]}
            return rows, await svc.board("r1", "PAY-1"), await svc.board("r1", "PAY-2")
        finally:
            await svc.stop()

    rows, b, claimed = run(go())
    w = rows["PAY-1"]["waits"]
    assert (w["who"], w["do"]) == ("you", "reply")
    assert w["since"] > time.time() - 600          # the Lean step just now, not the transcript's 2026-11-08 stamp
    now = b["runs"][0]["now"]
    assert (now["status"], now["who"], now["do"]) == ("awaiting_approval", "you", "reply")
    assert now["since"] == w["since"] == b["row"]["waits"]["since"]
    assert claimed["row"]["state"] == "claimed" and claimed["error"] == "" and claimed["source"] == "app"


@needs_brainkit
def test_stop_stops_only_this_requirements_own_session_and_only_while_it_works(tmp_path, home):
    """停止 on the board (UI-24): the turn PAY-1's 本机会话 is working on, never PAY-2's; with nothing working, a 409
    that says so."""
    from brainfix import FakeGateway, install_brain_dev
    repo = make_project(tmp_path)
    install_brain_dev(home)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "notify", "notify", "通知")
    gw = FakeGateway()
    gw.templates = {CREW_AGENT}
    svc = make_service(tmp_path, [repo], gw=gw)
    errors = mod("httpkit")

    async def go():
        await svc.start()
        try:
            one = (await svc.open_session("r1", "PAY-1"))["slot"]
            two = (await svc.open_session("r1", "PAY-2"))["slot"]
            for _ in range(6):
                await svc.sessions.poll()
            done = await svc.stop_session("r1", "PAY-1")
            for _ in range(5):
                await svc.sessions.poll()
            try:
                await svc.stop_session("r1", "PAY-1")
                again = None
            except errors.AppError as exc:
                again = exc
            board = await svc.board("r1", "PAY-1")
            return one, two, done, again, board
        finally:
            await svc.stop()

    one, two, done, again, b = run(go())
    assert done == {"ok": True, "stopped": [one]}
    assert ("stop", one) in gw.calls and ("stop", two) not in gw.calls
    assert gw.slot_rows[two]["running"] is True
    assert again is not None and (again.status, again.code) == (409, "nothing_to_stop")
    assert "没有在做的会话" in str(again)
    assert not [s for r in b["runs"] for s in r["sessions"] if s["busy"]]


@needs_brainkit
def test_a_quiet_session_waits_since_its_own_last_record_not_since_the_app_looked(tmp_path, home):
    """UI-11: DEMO-1 sat quiet for five days, and the drawer said 「从 11-14 16:14 起」 and the lane 「刚刚」: the moment this
    app first read the finished turn. The wait starts at a record's own time (here the app's kickoff is the newest:
    the fake transcript's stamps are older), however late the app looks."""
    import asyncio
    from brainfix import FakeGateway, install_brain_dev
    repo = make_project(tmp_path)
    install_brain_dev(home)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    gw = FakeGateway()
    gw.templates = {CREW_AGENT}
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            await svc.open_session("r1", "PAY-1")
            gw.finish(next(iter(gw.slot_rows)))
            await asyncio.sleep(1.5)
            looked = time.time()
            for _ in range(2):
                await svc.sessions.poll()
            rows = {r["key"]: r for r in (await svc.overview("all"))["rows"]}
            return looked, rows, await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    looked, rows, b = run(go())
    w = rows["PAY-1"]["waits"]
    assert (w["who"], w["do"]) == ("you", "reply")
    assert w["since"] < looked, "since the kickoff went in, not since the app read the transcript"
    assert run_now(b, "local")["since"] == w["since"] == b["row"]["waits"]["since"]


@needs_brainkit
def test_a_requirement_split_into_units_draws_each_planned_unit_before_its_code_generation(tmp_path, home):
    """Rule 1 through the service: unit-of-work.md's first table, as the run branch's newest commit has it, gives
    构建 a row per unit in the table's order before any code generation is recorded. The worktree's copy is not read."""
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    uow = wt / "aidlc-docs" / intent / "inception" / "application-design" / "unit-of-work.md"
    lean(wt, "stage", "start", "units")
    uow.write_text("# Units of Work\n\n| Unit | Owner team | Paths | Depends on |\n|---|---|---|---|\n"
                   "| web | pay | web/** | api |\n| api | pay | services/api/** | - |\n", encoding="utf-8")
    lean(wt, "stage", "done", "units", "--note", "web 和 api")
    sh("git", "-C", wt, "add", "-A")
    sh("git", "-C", wt, "commit", "-qm", "PAY-1：拆成 web 和 api")
    uow.write_text(uow.read_text(encoding="utf-8") + "| jobs | pay | jobs/** | - |\n", encoding="utf-8")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    b = run(go())
    assert [(n["id"], n["status"]) for n in b["nodes"] if n["stage"] in ("units", "code-generation")] == [
        ("units", "done"), ("code-generation/web", "waiting"), ("code-generation/api", "waiting")]


@needs_brainkit
def test_the_board_route_is_the_owners_only_and_checks_its_path(tmp_path):
    """Only the dashboard's owner gets a board (never an app token), and a bad repo id or key is refused before anything
    runs (test_skeleton checks every route the same way; this one is named here)."""
    import asyncio
    from types import SimpleNamespace

    import backend.routes as routes
    from http_fakes import FakeRequest, FakeWeb

    class Route:
        def __init__(self, method, path, handler):
            self.method, self.path, self.handler = method, path, handler

    def handler(owner):
        svc = routes.service.Service(SimpleNamespace(name="brain", logger=None, data_dir=str(tmp_path / "data")))
        r = {(x.method, x.path): x.handler for x in routes.handlers.build_routes(svc, Route, web=FakeWeb(),
                                                                                  is_owner=lambda _r: owner)}
        return r[("GET", "/board/{repo}/{key}")]

    def call(h, **req):
        return asyncio.run(h(FakeRequest(**req), None))

    h = handler(False)
    assert call(h, user=None).status == 401
    assert call(h, app="brain").status == 403
    assert call(h).status == 403
    h = handler(True)
    resp = call(h, match_info={"repo": "../etc", "key": "PAY-1"})
    assert resp.status == 404 and resp.body["code"] == "no_repo"
    resp = call(h, match_info={"repo": "r1", "key": "pay-1; rm"})
    assert resp.status == 400 and resp.body["code"] == "invalid_key"


# ---------------------------------------------------------------- the fold, on Lean's own records

T = 1794185486.0   # DEMO-3's claim (2026-11-09 08:51:25 +08)
M, H = 60, 3600
SESSION = "sess_3ac8"


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def ev(at, event, **data):
    return {"event": event, "ts": iso(at), "_at": at, "data": data}


def turn(at, n, **data):
    return ev(at, "HUMAN_TURN", session=SESSION, turn=n, chars=4, **data)


def demo3_events():
    """DEMO-3 as Lean recorded it: the app's kickoff, the person's answer to Q1 and Q2, the approval line, then code,
    a green verify; nothing after 09:07."""
    return [ev(T + 1, "STAGE_STARTED", stage="requirements"), ev(T + 21, "SESSION_START", session=SESSION),
            turn(T + 22, 1), ev(T + 4 * M, "STAGE_STARTED", stage="reverse-engineering"),
            ev(T + 4 * M + 30, "STAGE_COMPLETED", stage="reverse-engineering"), turn(T + 6 * M + 17, 2),
            ev(T + 9 * M + 12, "STAGE_COMPLETED", stage="requirements"),
            ev(T + 9 * M + 16, "STAGE_SKIPPED", stage="user-stories", reason="只有一类用户"),
            ev(T + 9 * M + 24, "STAGE_SKIPPED", stage="application-design", reason="Size: small"),
            ev(T + 9 * M + 50, "APPROVAL_REQUESTED", kind="REQUIREMENTS", sha256="dfc1969f" * 8),
            turn(T + 11 * M + 2, 3), ev(T + 11 * M + 2.2, "APPROVED", kind="REQUIREMENTS", sha256="dfc1969f" * 8),
            ev(T + 11 * M + 30, "STAGE_SKIPPED", stage="functional-design", unit="app", reason="没有新实体"),
            ev(T + 11 * M + 47, "STAGE_STARTED", stage="code-generation", unit="app"),
            ev(T + 14 * M + 4, "STAGE_COMPLETED", stage="code-generation", unit="app"),
            ev(T + 14 * M + 13, "VERIFY", ok=True, results=[{"cmd": "python3 -m unittest discover -s tests", "exit": 0}]),
            ev(T + 14 * M + 13, "STAGE_COMPLETED", stage="build-and-test")]


STAGES = mod("lean_view").stages([
    ev(T, "STAGE_COMPLETED" if st == "done" else "STAGE_SKIPPED", stage=sid, **unit) for sid, st, unit in (
        ("reverse-engineering", "done", {}), ("requirements", "done", {}), ("user-stories", "skipped", {}),
        ("application-design", "skipped", {}), ("units", "skipped", {}), ("functional-design", "skipped", {"unit": "app"}),
        ("nfr-design", "skipped", {"unit": "app"}), ("infrastructure-design", "skipped", {"unit": "app"}),
        ("code-generation", "done", {"unit": "app"}), ("build-and-test", "done", {}))])


def facts(events, *, key="DEMO-3", owner="dana", run=True, handoff=None, merged=False, approvals=(), park=None,
          state_text=None):
    r = {"key": key, "owner": owner, "branch": "ai/%s/%s-x" % (owner, key), "intent": "261109-x", "created_at": iso(T + 1)}
    if handoff:
        r["handoff"] = handoff
    return {"key": key, "owner": owner, "claim": {"owner": owner, "title": "计算器乘法", "created_at": iso(T)},
            "run": r if run else None, "branch": r["branch"] if run else None, "events": events, "merged": merged,
            "approvals": list(approvals), "stages": STAGES if run else [], "park": park, "state_text": state_text,
            "tip_at": None}


def row_of(state, who, text, **waits):
    return {"key": "DEMO-3", "state": state, "waits": dict({"who": who, "text": text, "line": None, "raw": ""}, **waits),
            "lean": {"stage": "release" if state != "claimed" else None, "done": 10, "total": 11}, "mr": None,
            "run_ids": []}


def snapshot(f, row, *, t=None, sessions=(), me="dana", now=T + 11.5 * H, local_only=True, mr=None,
             app_events=(), reflect_refs=(), brain_=None, team_decisions=(), changes=None, units=(), live=None):
    """*live*: the live dev session's slot as service._live names it; by default the last local session given."""
    sessions = list(sessions)
    if live is None:
        live = next((s["slot"] for s in reversed(sessions) if s.get("kind", "local") == "local"), None)
    return board.build(f, row, t, mr=mr, sessions=sessions, app_events=list(app_events),
                       team_decisions=list(team_decisions), subjects={}, reflect_refs=list(reflect_refs),
                       default_branch="main", local_only=local_only, kit_rows=[], decisions=[], brain=brain_, me=me, now=now,
                       changes=changes, units=units, live_slot=live)


def session(last, busy=False, awaiting=False):
    return {"slot": "brain-r1-demo-3-1", "url": "/chat?sid=brain-r1-demo-3-1", "kind": "local", "busy": busy,
            "awaiting": awaiting, "last_activity": last, "credits": 36.954}


def run_now(b, key):
    return next(r["now"] for r in b["runs"] if r["key"] == key)


def spent(b, kinds, end):
    """Seconds of the nodes' spans of *kinds*, an open one drawn to *end* (as 需求详情's 时间花在哪 draws it)."""
    return sum((s["b"] if s["b"] is not None else end) - s["a"] for n in b["nodes"] for s in n["spans"] if s["kind"] in kinds)


def said(b):
    """Every line the page shows a person (a run id only on hover: Run.run_id, an event's src)."""
    return json.dumps([[e["text"] for e in b["events"]], [(r["label"], r["now"]["doing"]) for r in b["runs"]],
                       [[(s["title"], s["text"], s["todo"]) for s in x["steps"]] for x in b["relays"]],
                       [(n["doing"], [h["doing"] for h in n["history"]], [m["title"] for m in n["marks"]])
                        for n in b["nodes"]]], ensure_ascii=False)


def test_a_big_requirement_waiting_on_its_small_ones_has_a_quiet_session_that_is_nobodys_turn():
    """A big requirement whose small ones are being built (family.classify: people / none): its own session sits quiet,
    and the board says what the row says, never 轮到你回话 beside it: its run waits on nobody, at no node."""
    last = T + 15 * M + 43
    text = "等小需求合进来：已合并 1/3（1 个待认领，1 个在做）"
    b = snapshot(facts(demo3_events()), row_of("local", "people", text, do="none"), sessions=[session(last)])
    now = run_now(b, "local")
    assert (now["status"], now["doing"], now["since"], now["who"], now["node"], now["do"]) == (
        "waiting", text, last, None, None, None)
    assert "轮到" not in said(b)
    assert [n["id"] for n in b["nodes"] if n["status"] == "awaiting_approval"] == []


def test_without_a_session_nobody_works_the_hours_are_a_wait():
    """UI-4: DEMO-1's AI worked about 20 minutes and the page said 126 hours of 「AI 在本机做」. With no session on this
    machine nobody is at work: from Lean's last record on, its hours wait for the person to open one."""
    coding = [e for e in demo3_events() if e["_at"] <= T + 11 * M + 47]     # code generation begun, nothing after
    b = snapshot(facts(coding), row_of("local", "you", "没有开着的会话：打开会话接着做。", do="reply"), now=T + 126 * H)
    assert run_now(b, "local")["status"] == "stalled"
    assert spent(b, ("work", "fix"), T + 126 * H) < 12 * M
    assert spent(b, ("wait",), T + 126 * H) > 125 * H


def test_a_session_asking_for_a_tool_waits_on_the_person_on_every_page():
    """UI-4: KiroCrew reports a session waiting on a tool approval as busy too. 交付总览 counted it as 「AI 在本机做」
    while the board said it waits for the person: the row says 等你批准工具调用, as the board does."""
    deliveries = mod("deliveries")
    f = facts(demo3_events(), owner="dana")
    row = deliveries.base_row("r1", f)
    deliveries.classify(row, f, mr=None, slot={"busy": True, "awaiting": True, "last_activity": T + 16 * M}, cfg={},
                        me="dana")
    assert (row["waits"]["who"], row["waits"]["do"]) == ("you", "tool")
    b = snapshot(f, row, sessions=[session(T + 16 * M, busy=True, awaiting=True)])
    assert (run_now(b, "local")["who"], run_now(b, "local")["do"]) == ("you", "tool")


def test_a_quiet_session_waits_for_the_person_from_its_last_message_not_works_on():
    """DEMO-3 (UI问题清单 #2): the AI worked about 15 minutes, then its session sat quiet for eleven hours. The board
    says its run waits for the person since the session's last message, at the stage it would do next, and none of
    those hours is AI work: every node's work ends before it, nothing glows."""
    last = T + 15 * M + 43   # the session's last message, 09:07:08
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(last)])
    now = run_now(b, "local")
    assert (now["status"], now["doing"], now["since"], now["who"], now["node"], now["do"]) == (
        "awaiting_approval", "会话空着：轮到你回话", last, "you", "review", "reply")
    assert all(s["b"] is not None and s["b"] <= last for n in b["nodes"] for s in n["spans"])
    assert [n["id"] for n in b["nodes"] if n["status"] in ("running", "fixing")] == []
    assert not b["live"]
    assert spent(b, ("work", "fix"), T + 11.5 * H) < 16 * M
    # the person's own reply in the session is a person's act; the kickoff and the approval line are not replies
    assert [e["text"] for e in b["events"] if e["kind"] == "answer"] == ["你在会话里回话了"]
    # while the AI wrote the requirements it waited for that reply: since Lean's last record before it (逆向工程 done)
    assert {"a": T + 4 * M + 30, "b": T + 6 * M + 17, "kind": "wait", "run": "local"} in node(b, "requirements")["spans"]


def test_a_review_session_beside_the_live_one_is_not_where_the_person_replies():
    """main's live_slot: the live dev session is service._live's, never a review session. With the review session
    newer and at work, 你要做的一步 opened the review session and the local run said AI 在做; it is the quiet dev
    session the person replies in."""
    last = T + 15 * M + 43
    dev = session(last)
    review = dict(session(last + 5 * M, busy=True), slot="brain-r1-demo-3-2", url="/chat?sid=brain-r1-demo-3-2")
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话", do="reply"),
                 sessions=[dev, review], live=dev["slot"])
    now = run_now(b, "local")
    assert (now["status"], now["doing"], now["do"]) == ("awaiting_approval", "会话空着：轮到你回话", "reply")
    assert b["accept"]["step"]["session_url"] == dev["url"]


def test_a_reply_after_a_long_quiet_does_not_turn_the_quiet_into_ai_work():
    """The session goes quiet in the middle of code generation: its lane waits for the person from the session's last
    message. The person answers the next morning: the night is still a wait, and the AI works from the reply on. Quiet
    between two stages (the night before 发布 began) is on no lane at all."""
    last, reply = T + 15 * M + 43, T + 24 * H
    coding = demo3_events()[:14]            # code generation begun at 09:03:33, not done
    b = snapshot(facts(coding), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(last)], now=T + 11.5 * H)
    cg = node(b, "code-generation/app")
    assert (cg["status"], cg["doing"]) == ("awaiting_approval", "会话空着：轮到你回话")
    assert cg["spans"] == [{"a": T + 11 * M + 47, "b": last, "kind": "work", "run": "local"},
                           {"a": last, "b": None, "kind": "wait", "run": "local"}]
    assert spent(b, ("wait",), T + 11.5 * H) > 11 * H and not b["live"]
    evs = coding + [turn(reply, 4), ev(reply + 20 * M, "STAGE_COMPLETED", stage="code-generation", unit="app")]
    b = snapshot(facts(evs), row_of("local", "agent", "AI 在做"), sessions=[session(reply + 20 * M, busy=True)],
                 now=reply + 30 * M)
    assert node(b, "code-generation/app")["spans"] == [{"a": T + 11 * M + 47, "b": reply, "kind": "wait", "run": "local"},
                                                       {"a": reply, "b": reply + 20 * M, "kind": "work", "run": "local"}]
    assert spent(b, ("work", "fix"), reply + 30 * M) < 16 * M + 30 * M
    evs = demo3_events() + [turn(reply, 4), ev(reply + 20 * M, "STAGE_STARTED", stage="release")]
    b = snapshot(facts(evs), row_of("local", "agent", "AI 在做"), sessions=[session(reply + 20 * M, busy=True)],
                 now=reply + 30 * M)
    assert run_now(b, "local")["status"] == "running" and b["live"]
    assert [s for n in b["nodes"] for s in n["spans"] if s["a"] < reply and (s["b"] is None or s["b"] > T + 16 * M)] == []
    assert node(b, "release")["spans"] == [{"a": reply + 20 * M, "b": None, "kind": "work", "run": "local"}]


def test_the_idle_step_says_what_to_say_next_and_where():
    """BOARD-4: an idle session's one step names Lean's next stage, gives the line to copy, and opens the session."""
    notes = ("## Session notes\nStatus: done\n### Next\n1. owner 开一个新会话做审查（这个会话写了代码，不能自己审）。"
             "对新会话说：「审查 intent 261109-x：看 `git diff 5d331b4..HEAD`」。\n### Open questions and blockers\n- 无\n")
    last = T + 15 * M + 43
    b = snapshot(facts(demo3_events(), state_text=notes), row_of("local", "you", "会话空着：轮到你回话"),
                 sessions=[session(last)])
    step = b["accept"]["step"]
    assert step["who"] == "你" and step["session_url"] == "/chat?sid=brain-r1-demo-3-1" and step["mr_url"] is None
    assert "下一步是「发布」" in step["text"] and "你开一个新会话做审查" in step["text"]
    # the line for the new session is Lean's own review line, the one 开审查会话 sends: the session that wrote the code
    # does not word its reviewer's task
    review = "Review intent 261109-x against its requirements, design and plans, using the diff, and write " \
             "construction/build-and-test/review.md."
    assert step["line"] == review
    assert "`" not in step["text"] + step["line"] and "owner" not in step["text"]
    # without Lean's notes it still says what to do: the review first, then the release
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(last)])
    assert "先开一个新会话做审查" in b["accept"]["step"]["text"] and b["accept"]["step"]["line"] == review
    reviewed = dict(facts(demo3_events()), review={"at": None})
    b = snapshot(reviewed, row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(last)])
    assert "在会话里让它接着做「发布」" in b["accept"]["step"]["text"] and b["accept"]["step"]["line"] == "接着做下一步：发布"


def test_a_design_lean_skipped_is_not_needed_and_the_wall_says_what_is_still_to_come():
    """BOARD-3, BOARD-12, DETAIL-19: past code generation without a DESIGN or PLAN request the design approval is not on
    the way; the wall counts what happened and names the release approval still to come; no citation is no check."""
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 16 * M)],
                 t={"brain": {"loaded": 5, "cited": [], "stale": [], "unknown": []}})
    assert (node(b, "application-design")["status"], node(b, "application-design")["doing"]) == ("skipped", "跳过了")
    cells = {c["id"]: c for c in b["checks"]["cells"]}
    assert (cells["gate-DESIGN"]["status"], cells["gate-DESIGN"]["detail"], cells["gate-DESIGN"]["node"]) == (
        "not_needed", "这次不需要：Lean 跳过了设计", "application-design")
    assert "brain-cite" not in cells
    w = b["checks"]
    assert (w["passed"], w["failed"], w["total"], w["todo"]) == (2, 0, 2, ["发布审批"])
    assert b["accept"]["checks"] == "目前 2 项都过 · 还剩 发布审批"
    assert "自动检查通过（1 项）" in [e["text"] for e in b["events"] if e["kind"] == "verify"]


def test_a_design_skipped_for_size_small_is_never_waited_for_on_any_page():
    """UI-20: DEMO-3's design was skipped (Size: small) and the board still said its code generation waited for the
    design approval. From the skip on, no line of the board, 交付总览, 需求详情 or 待办 (the row's waits) waits on it."""
    deliveries, lv = mod("deliveries"), mod("lean_view")
    evs = demo3_events()
    skip = next(i for i, e in enumerate(evs) if e["data"].get("stage") == "application-design")
    for cut in range(skip + 1, len(evs) + 1):
        f = dict(facts(evs[:cut]), stages=lv.stages(evs[:cut]))
        row = deliveries.base_row("r1", f)
        deliveries.classify(row, f, mr=None, slot={"last_activity": evs[cut - 1]["_at"]}, cfg={}, me="dana")
        b = snapshot(f, row, sessions=[session(evs[cut - 1]["_at"])])
        shown = json.dumps([b, row], ensure_ascii=False)
        assert re.findall(r'等[^"]*设计', shown) == [], evs[cut - 1]["event"]


def merged_trace(merged_at, window_end):
    return {"brain": {"loaded": 3, "cited": ["T-1"], "stale": [], "unknown": []},
            "humans": [{"at": iso(T + 11 * M), "kind": "approval", "what": "RELEASE", "who": "G-3F2A1C", "channel": "local"}],
            "outcome": {"merged_at": {"value": iso(merged_at), "how": "merge commit", "commit": "c" * 40},
                        "reverted_or_hotfixed_within_14d": {"value": None, "window_end": iso(window_end)}}}


def test_the_merge_ends_the_requirements_time_and_its_14_days_are_watched_not_waited():
    """DETAIL-2, BOARD-13: nothing after the merge is this requirement's time (the write-back waits fifteen hours after
    it); the 14 days after it are watched, nobody's turn, and still to come."""
    merged_at = T + 20 * M
    evs = demo3_events() + [ev(T + 15 * M, "APPROVAL_REQUESTED", kind="RELEASE", sha256="ab" * 32),
                            ev(T + 16 * M, "APPROVED", kind="RELEASE", sha256="ab" * 32),
                            ev(T + 17 * M, "STAGE_STARTED", stage="ci-pipeline")]     # still at it when it merged
    b = snapshot(facts(evs, merged=True), row_of("merged", "nobody", "已合并"), sessions=[session(T + 17 * M)],
                 t=merged_trace(merged_at, merged_at + 14 * 86400), brain_={"proposals": 2},
                 app_events=[{"at": iso(merged_at + 15 * H), "label": "回写 Brain：成功", "detail": "brain/DEMO-3-reflect-1"}],
                 reflect_refs=[("brain/DEMO-3-reflect-1", merged_at + 15 * H, None)], now=merged_at + 2 * 86400)
    assert b["ended_at"] == merged_at
    assert all(s["b"] is not None and s["b"] <= merged_at for n in b["nodes"] for s in n["spans"])
    assert node(b, "ci-pipeline")["spans"] == [{"a": T + 17 * M, "b": merged_at, "kind": "work", "run": "local"}]
    # merged into main: every stage it finished is finished for good, and what was still at work is over
    assert [(n["id"], n["status"]) for n in b["nodes"] if n["rounds"] and n["stage"] != "reflect"
            and n["status"] != "skipped"] == [(x, "merged") for x in (
                "reverse-engineering", "requirements", "code-generation/app", "build-and-test", "ci-pipeline", "release")]
    cells = {c["id"]: c for c in b["checks"]["cells"]}
    assert cells["after-14d"]["status"] == "watch" and cells["after-14d"]["detail"].startswith("观察中")
    # watched is not still to come: nothing is left, one cell is watched
    assert b["checks"]["todo"] == [] and b["checks"]["passed"] == b["checks"]["total"]
    assert b["accept"]["checks"] == "目前 %d 项都过 · 1 项在观察" % b["checks"]["passed"]
    # one write-back, one event
    assert [e["text"] for e in b["events"] if e["kind"] == "reflect"] == ["回写 Brain：成功（回写分支 brain/DEMO-3-reflect-1）"]
    # the owner approved the release in their own session: they are named, never a pseudonym
    assert b["accept"]["done"]["who"] == "dana"
    # DETAIL-25: the write-back branch waits to be merged, then it is in the default branch
    reflect = node(b, "reflect")
    assert (reflect["status"], reflect["doing"], reflect["since"]) == (
        "done", "写好了回写分支：等合进 Brain", merged_at + 15 * H)
    assert [(h["status"], h["who"]) for h in reflect["history"]] == [("awaiting_approval", "you"), ("done", None)]
    b = snapshot(facts(evs, merged=True), row_of("merged", "nobody", "已合并"), t=merged_trace(merged_at, merged_at + 14 * 86400),
                 brain_={"proposals": 2}, reflect_refs=[("brain/DEMO-3-reflect-1", merged_at + 15 * H, merged_at + 16 * H)],
                 now=merged_at + 2 * 86400)
    reflect = node(b, "reflect")
    assert (reflect["status"], reflect["doing"], reflect["since"]) == ("merged", "已合进 Brain", merged_at + 16 * H)
    assert [e["text"] for e in b["events"] if e["kind"] == "reflect"][-1] == "回写分支 brain/DEMO-3-reflect-1 已合并"


def test_the_replay_knows_whose_turn_each_wait_was():
    """A release asked on the MR after the hand-over (the runner's clone asks it) waits for the product, not for you:
    each history entry of a wait says whom it is on, as Waits.who does, and the release is the product's acceptance."""
    hand = T + 16 * M
    evs = demo3_events() + [dict(ev(T + 40 * M, "APPROVAL_REQUESTED", kind="RELEASE", sha256="cd" * 32), _file=RUNNER_FILE)]
    b = snapshot(facts(evs, handoff={"requested_at": iso(hand), "by": "dana"}),
                 row_of("acceptance", "product", "等产品在 MR 上验收 D-3（approve / reject）", do="accept"),
                 local_only=False, now=T + 2 * H)
    assert [(h["doing"], h["who"]) for h in node(b, "release")["history"] if h["status"] == "awaiting_approval"] == [
        ("等产品验收", "product")]
    assert [h["who"] for h in node(b, "requirements")["history"] if h["status"] == "awaiting_approval"] == ["you"]
    assert run_now(b, "runner-1")["do"] == "accept" and run_now(b, "local")["do"] is None


def test_a_repo_done_only_here_has_no_runner_half_and_reads_no_runner_sources():
    """#5 (BOARD-9, DETAIL-6): no remote, so there is no runner half: no hand-over on the rail, no runner run in the
    roster, no 桥 or 签名 on the wall, and its sources are not missing. Nobody accepts it on an MR: its release waits
    for its person's approval in the session."""
    t = {"missing": ["bridge ledger (--ledger not given)", "vouchers (--vouchers not given and no intake store)",
                     "pushes (git keeps no push log; person commits after hand-over stand in for them)",
                     "CI pipelines (GitLab, not readable here)", "credits (not passed in)"]}
    b = snapshot(facts(demo3_events()), dict(row_of("local", "you", "会话空着：轮到你回话"), local_only=True),
                 sessions=[session(T + 16 * M)], local_only=False, t=t)
    assert b["relays"] == [] and [r["key"] for r in b["runs"]] == ["local"]
    assert not [c for c in b["checks"]["cells"] if c["group"] == "handover"]
    assert b["missing"] == ["花费：KiroCrew 还没报 credits"]
    asked = demo3_events() + [ev(T + 20 * M, "STAGE_STARTED", stage="release"),
                              ev(T + 21 * M, "APPROVAL_REQUESTED", kind="RELEASE", sha256="ab" * 32)]
    b = snapshot(facts(asked), dict(row_of("local", "you", "等你批准发布", do="approve"), local_only=True),
                 sessions=[session(T + 21 * M)], local_only=False, t=t)
    assert (node(b, "release")["status"], node(b, "release")["doing"]) == ("awaiting_approval", "等你批发布")
    assert [(g["state"], g["who"], g["mr"]) for g in node(b, "release")["gates"]] == [("pending", "you", None)]


def test_someone_elses_requirement_names_them_and_a_question_for_the_product_is_not_yours():
    """BOARD-11, BOARD-5, BOARD-8: carol's requirement, read by alice, says carol approved; parked on D-2 for the
    product, the owner's node does not ask for anyone here and the step is on the MR, not a line in a session."""
    hand = T + 30 * M
    evs = demo3_events()[:12]
    f = facts(evs, owner="carol", handoff={"requested_at": iso(hand), "by": "carol"})
    mr = {"url": "https://gitlab.example.com/p/-/merge_requests/41",
          "questions": [{"id": "D-2", "kind": "clarify", "state": "open", "at": iso(hand + H),
                         "options": [{"id": "A", "text": "按商户"}, {"id": "B", "text": "按门店"}]}]}
    row = dict(row_of("parked", "product", "等回答 D-2（澄清）"), mr={"iid": 41, "url": mr["url"], "state": "opened"})
    b = snapshot(f, row, me="alice", mr=mr, local_only=False, now=hand + 3 * H)
    text = json.dumps(b, ensure_ascii=False)
    assert "carol 批准了需求" in [e["text"] for e in b["events"]] and "你批准了" not in text
    assert b["person"]["status"] != "awaiting_approval" and b["person"]["me"] is False and b["person"]["you"] is False
    step = b["accept"]["step"]
    assert step["who"] == "产品" and step["text"].startswith("在 MR 上回答 D-2（A / B）")
    assert step["mr_url"] == mr["url"] and step["line"] is None and step["session_url"] is None
    assert "在会话里" not in step["text"]
    # the team runner's board of it, whoever reads it (or nobody known), names carol too
    for me in ("alice", None):
        b = snapshot(f, dict(row, where="runner", repo="runner"), me=me, mr=mr, local_only=False, now=hand + 3 * H)
        assert "carol 批准了需求" in [e["text"] for e in b["events"]], (me, [e["text"] for e in b["events"]])
        assert b["person"]["you"] is False


def test_a_claim_not_started_says_since_when_and_to_start_it():
    """#3 (BOARD-6): a claim only: the board starts at the claim, no run is there yet and nothing was done, the first
    stage waits to be started, and the step is to start this very key."""
    b = snapshot(facts([], run=False), row_of("claimed", "you", "认领了，还没开工"), now=T + 12 * H)
    assert (b["started_at"], [(e["kind"], e["at"]) for e in b["events"]]) == (T, [("claim", T)])
    assert b["runs"] == [] and b["relays"] == [] and [n for n in b["nodes"] if n["rounds"]] == []
    assert (node(b, "requirements")["status"], node(b, "requirements")["doing"]) == ("waiting", "等开工")
    assert b["accept"]["step"]["text"] == "认领了，还没开工：在开需求里开工 DEMO-3"
    assert b["person"]["you"] is True
    b = snapshot(facts([], run=False, owner="carol"), row_of("claimed", "people", "已认领，还没开工"), me="alice")
    assert b["accept"]["step"]["text"] == "carol 认领的，等 carol 开工"
    # BOARD-11: the board calls carol by her name, so the page offers alice no 开工 and says no 你
    assert b["person"]["you"] is False
    b = snapshot(facts([], run=False, owner="carol"), row_of("claimed", "people", "等 carol 开工"), me=None)
    assert b["person"]["you"] is False and b["accept"]["step"]["text"] == "carol 认领的，等 carol 开工"


def test_each_commit_on_the_conveyor_carries_the_files_it_changed(tmp_path):
    """BOARD-21: git's own answer to the board's one log call (board.GIT_LOG), read back: each commit's files with
    their status and lines, a rename as R under its new name; the merge has none."""
    g = ["git", "-C", str(tmp_path), "-c", "user.email=a@example.com", "-c", "user.name=a"]
    subprocess.run(g[:3] + ["init", "-q"], check=True)
    (tmp_path / "keep.py").write_text("a\nb\n")
    (tmp_path / "old.py").write_text("x\ny\nz\n")
    (tmp_path / "gone.py").write_text("gone\n")
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "one"], check=True)
    (tmp_path / "keep.py").write_text("a\nB\nc\n")
    subprocess.run(g + ["mv", "old.py", "new.py"], check=True)
    subprocess.run(g + ["rm", "-q", "gone.py"], check=True)
    (tmp_path / "add.py").write_text("n\n")
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "two: 改"], check=True)
    shas = subprocess.run(g + ["rev-list", "HEAD"], capture_output=True, text=True, check=True).stdout.split()
    log = subprocess.run(g + list(board.GIT_LOG) + shas, capture_output=True, text=True, check=True).stdout
    assert board.subjects_from_log(log) == {shas[0]: "two: 改", shas[1]: "one"}
    got = board.changes_from_log(log)
    assert sorted(got[shas[0]], key=lambda c: c["path"]) == [
        {"path": "add.py", "added": 1, "removed": 0, "status": "A"},
        {"path": "gone.py", "added": 0, "removed": 1, "status": "D"},
        {"path": "keep.py", "added": 2, "removed": 1, "status": "M"},
        {"path": "new.py", "added": 0, "removed": 0, "status": "R"}]
    assert {c["path"] for c in got[shas[1]]} == {"keep.py", "old.py", "gone.py"}
    t = merged_trace(T + 20 * M, T + 15 * 86400)
    t["actions"] = {"commits": [{"sha": shas[0], "at": iso(T + 5 * M), "by": "person", "files": 4}]}
    b = snapshot(facts(demo3_events(), merged=True), row_of("merged", "nobody", "已合并"), t=t, changes=got)
    belt = {x["kind"]: x for x in b["belt"]}
    assert belt["commit"]["changes"] == got[shas[0]] and belt["merge"]["changes"] == []


def test_the_replay_says_what_the_session_is_doing_while_it_works():
    """A stage done inside another (逆向工程 inside 需求分析) leaves the session at work on the outer one: the replay
    shows 需求分析 at work until it asks for its approval, never 做完, and what the person said meanwhile is its."""
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 16 * M)])
    assert [(h["at"], h["status"]) for h in node(b, "reverse-engineering")["history"]][:2] == [
        (T + 4 * M, "running"), (T + 4 * M + 30, "done")]
    assert [(h["at"], h["status"]) for h in node(b, "requirements")["history"]][:2] == [
        (T + 1, "running"), (T + 9 * M + 50, "awaiting_approval")]
    assert [(e["node"], e["run"], e["round"]) for e in b["events"] if e["kind"] == "answer"] == [("requirements", "local", 1)]


def test_a_gate_history_stays_in_time_order_with_no_repeats_under_a_later_plan():
    """Measured on PAY-3's replay: the approved DESIGN was said again at its own time after a later PLAN request, so
    设计审批 read 已批准 during the first plan's wait, and the second plan's wait was listed twice."""
    d, p1, p2 = T + 1 * H, T + 2 * H, T + 5 * H
    req = lambda at, kind, h, **u: ev(at, "APPROVAL_REQUESTED", kind=kind, sha256=h * 64, **u)  # noqa: E731
    ok = lambda at, kind, h, **u: ev(at, "APPROVED", kind=kind, sha256=h * 64, **u)  # noqa: E731
    evs = [req(T + 30 * M, "DESIGN", "a"), ok(d, "DESIGN", "a"), req(p1 - 20 * M, "PLAN", "b", unit="export"),
           ok(p1, "PLAN", "b", unit="export"), req(p2, "PLAN", "c", unit="api")]
    approvals = [{"kind": k, "unit": u, "state": st, "sha8": h * 8, "at": at, "line": line, "path": None}
                 for k, u, st, h, at, line in (("DESIGN", None, "approved", "a", d, None),
                                               ("PLAN", "export", "approved", "b", p1, None),
                                               ("PLAN", "api", "pending", "c", p2, "approve PLAN cccccccc"))]
    b = snapshot(facts(evs, approvals=approvals),
                 row_of("local", "you", "等你批准 api 的代码生成计划：读完再在会话里自己打那一行", do="approve"), now=p2 + H)
    for n in b["nodes"]:
        h = n["history"]
        assert [x["at"] for x in h] == sorted(x["at"] for x in h) and len({(x["at"], x["status"]) for x in h}) == len(h), n["id"]
    assert [(x["at"], x["status"]) for x in node(b, "application-design")["history"]] == [
        (T + 30 * M, "awaiting_approval"), (d, "merged")]
    assert [(x["at"], x["status"]) for x in node(b, "code-generation/export")["history"]] == [
        (p1 - 20 * M, "awaiting_approval"), (p1, "merged")]
    api = node(b, "code-generation/api")
    assert ([(x["at"], x["status"]) for x in api["history"]], [g["line"] for g in api["gates"]]) == (
        [(p2, "awaiting_approval")], ["approve PLAN cccccccc"])


def test_a_push_without_its_label_waits_on_the_person_not_on_the_runner():
    """DETAIL-1 (PAY-10): pushed, but the MR has no hand-over label, so nothing was handed over. The hours since are
    the person's to label it, not the bridge or the runner at work."""
    hand = T + 30 * M
    f = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    row = row_of("handed_over", "you", "推上去了，但 MR 上没有交接标签：什么也没交出去。看一眼 MR，再交接一次",
                 do="label_handoff", since=hand)
    now = hand + 7 * H + 46 * M
    b = snapshot(f, row, local_only=False, now=now)
    assert [r["kind"] for r in b["runs"]] == ["local"]
    assert [s for n in b["nodes"] for s in n["spans"] if s["b"] is None or s["b"] > hand] == []
    assert (run_now(b, "local")["status"], run_now(b, "local")["since"], run_now(b, "local")["who"],
            run_now(b, "local")["do"]) == ("awaiting_approval", hand, "you", "label_handoff")
    [relay] = b["relays"]
    assert relay["to"] is None
    assert [(s["key"], s["status"], s["todo"]) for s in relay["steps"]] == [
        ("handover", "stalled", "在 MR 上补上交接标签，或者在分支上再交接一次"), ("bridge", "waiting", None),
        ("signer", "waiting", None), ("workspace", "waiting", None)]
    # the check wall (and 需求详情's 检查, which shows it) says the label is missing, as the relay's 桥 step does:
    # nothing is being checked, the person labels it
    cell = next(c for c in b["checks"]["cells"] if c["group"] == "handover" and c["id"] == "bridge")
    assert (cell["label"], cell["name"], cell["status"], cell["node"], cell["run"]) == ("标签", "补上交接标签", "wait", None, None)
    assert cell["detail"] == relay["steps"][1]["text"] == "MR 上没有交接标签：桥不会接"
    assert "在核对" not in json.dumps(b["checks"], ensure_ascii=False)


def test_a_hand_over_whose_push_failed_is_not_handed_over_on_the_board():
    """main's push_handoff: the hand-over commit is here but never reached the remote, so nothing went out. The board
    said 交给运行机 in its events, 交接了 in the relay and 桥 在核对 on its check wall. Like the row, it says the push
    did not go out and what to do (需求详情's 交接给运行机 again); the hours since are the person's, on this machine."""
    hand = T + 30 * M
    f = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    row = row_of("local", "you", deliveries.NOT_PUSHED, do="push_handoff", since=hand)
    b = snapshot(f, row, local_only=False, now=hand + 2 * H)
    assert [r["kind"] for r in b["runs"]] == ["local"]
    assert run_now(b, "local")["do"] == "push_handoff"
    [relay] = b["relays"]
    assert relay["to"] is None
    assert [(s["key"], s["status"], s["text"], s["todo"]) for s in relay["steps"]] == [
        ("handover", "failed", "交接提交了，但没推上去", "推上去以后，在需求详情再点「交接给运行机」"),
        ("bridge", "waiting", "什么也没交出去：桥收不到", None), ("signer", "waiting", "等桥核对完", None),
        ("workspace", "waiting", "等签名", None)]
    cell = next(c for c in b["checks"]["cells"] if c["group"] == "handover" and c["id"] == "bridge")
    assert (cell["label"], cell["name"], cell["status"], cell["detail"]) == (
        "交接", "推上交接", "fail", "交接提交了，但没推上去：什么也没交出去")
    shown = said(b) + json.dumps(b["checks"], ensure_ascii=False)
    for word in ("交给运行机了", "在核对", "桥接手了", "交接了（"):
        assert word not in shown, word
    hand_ev = [e for e in b["events"] if e["kind"] == "handover"]
    assert [e["text"] for e in hand_ev] == ["交接提交了，没推上去"], hand_ev


def test_a_question_the_runner_asks_on_the_mr_waits_on_whom_park_json_asks_as_the_row_says():
    """Preview r1/PAY-2: after the hand-over the runner's 澄清 asks the owner (park.json's ask), yet the replay said
    等别人 to the owner. What the runner's run asks is said of whom the row's waits would name (deliveries.classify):
    park.json's ask, the owner being 你 only to the owner; the bridge's D-n asks whom its park.json asks; a code
    generation plan asked of the builder on the MR waits for the builder, and its run waits for the row's answer."""
    hand, q_at, plan_at = T + 30 * M, T + 90 * M, T + 3 * H
    plan = "PLAN " + "b" * 8

    def trace(ask):
        return {"actions": {"questions": [
            {"at": iso(q_at), "kind": "clarify", "ask": ask, "status": "waiting", "lean_request": None},
            {"at": iso(q_at + 40 * M), "kind": "clarify", "ask": ask, "status": "answered", "lean_request": None},
            {"at": iso(plan_at + M), "kind": "plan", "ask": "builder", "status": "waiting", "lean_request": plan}]},
            "decisions": [{"source": "bridge", "id": "D-1", "kind": "clarify", "state": "delivered",
                           "asked_at": iso(q_at + M), "at": iso(q_at + 30 * M), "choice": "A"}]}

    evs = demo3_events()[:12] + [dict(ev(plan_at, "APPROVAL_REQUESTED", kind="PLAN", unit="api", sha256="b" * 64),
                                      _file=RUNNER_FILE)]
    f = facts(evs, handoff={"requested_at": iso(hand), "by": "dana"})
    text = "等开发的人在 MR 上回答 D-2（approve / reject）"
    row = row_of("parked", "builder", text, do="answer", where="mr")

    def asked(b):
        return [e["text"] for e in b["events"] if e["kind"] == "question"][:2]

    b = snapshot(f, row, t=trace("owner"), local_only=False, now=plan_at + H)
    assert asked(b) == ["停下问你：澄清", "D-1 在 MR 上问你：澄清"]
    cg = node(b, "code-generation/api")
    assert (cg["status"], cg["doing"], cg["run"], [g["who"] for g in cg["gates"]]) == (
        "awaiting_approval", "等开发的人在 MR 上批代码生成计划", "runner-1", ["builder"])
    assert [(h["doing"], h["who"]) for h in cg["history"]] == [("等开发的人在 MR 上批代码生成计划", "builder")]
    assert "请开发的人在 MR 上批准代码生成计划" in [e["text"] for e in b["events"]]
    now = run_now(b, "runner-1")
    assert (now["status"], now["doing"], now["who"], now["node"], now["do"]) == (
        "awaiting_approval", text, "builder", "code-generation/api", "answer")
    b = snapshot(f, row, t=trace("owner"), me="alice", local_only=False, now=plan_at + H)
    assert asked(b) == ["停下问 dana：澄清", "D-1 在 MR 上问 dana：澄清"]
    b = snapshot(f, row, t=trace("product"), local_only=False, now=plan_at + H)
    assert asked(b) == ["停下问产品：澄清", "D-1 在 MR 上问产品：澄清"]


def test_the_board_says_the_bridges_refusal_the_repos_kit_and_what_it_could_not_read_in_words():
    """REST-29: the bridge's English reason for a refused hand-over is said as the MR's is; the kit's cells are named
    in words (适配 for the hook adapter, as brain-kit's own line tells, 记录 for Lean counting runs/ as code), never a
    glob; a source it could not read is said without its file name; an event's source says what record it is."""
    hand = T + 30 * M
    t = {"actions": {"handovers": [{"at": iso(hand + 2 * M), "kind": "bridge rejected", "reason": "MR comes from a fork"}]},
         "missing": ["Lean audit (no aidlc-docs/audit/*.jsonl in worktree)", "aidlc-docs/261109-x/decisions.md (not in worktree)",
                     "runs/DEMO-3/run.json: intent is not a string (ignored)"]}
    said = mod("problems").row   # the KiroCrew rows as 设置 says them, brain-kit's own line kept in raw
    rows = [said("FAIL", line, repo="/r", team=True) for line in (
        ".kiro/brain/lean-hook.py is an older adapter (version 1, needs 2)",
        "runs/ 下的文件（MR.md、park.json）算在 Lean 的代码指纹里", "AGENTS.md at repo root")]
    f = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    b = board.build(f, row_of("rejected", "you", "接手被拒：MR 是从复制出去的仓库开的", do="redo_handoff"), t, mr=None,
                    sessions=[], app_events=[], team_decisions=[], subjects={}, reflect_refs=[], default_branch="main",
                    local_only=False, kit_rows=board.kit_cells(rows, "/r"), decisions=[], brain=None, me="dana",
                    now=hand + H)
    assert "桥拒绝接手：MR 是从复制出去的仓库开的" in [e["text"] for e in b["events"]]
    assert [c["label"] for c in b["checks"]["cells"] if c["id"].startswith("kit")] == ["适配", "记录", "kit 文件"]
    assert b["missing"] == ["Lean 的记录：这个需求的分支上没有，阶段和审批都看不到", "决定记录：AI 还没写下决定",
                            "开工记录里有一项格式不对，没用上"]
    assert {e["kind"]: e["src"] for e in b["events"] if e["src"]} == {
        "claim": "认领记录 claim/DEMO-3", "start": "开工记录 runs/DEMO-3/run.json"}


def test_a_write_back_branch_says_when_it_was_merged_into_the_default_branch(tmp_path):
    """DETAIL-25: brain/<KEY>-reflect-<n> fast-forwarded into main is merged at its own time, one merged with a merge
    commit at that commit's, and one still waiting is not merged."""
    from appkit import commit_file, git_repo, sh
    svc = mod("service")
    repo = git_repo(tmp_path / "r")
    commit_file(repo, "a.md", "a\n", "init")
    for n, how in ((1, "ff"), (2, "merge"), (3, "wait")):
        sh("git", "-C", repo, "checkout", "-q", "-b", "brain/DEMO-3-reflect-%d" % n, "main")
        commit_file(repo, "b%d.md" % n, "b\n", "Brain: DEMO-3 reflect %d" % n)
        sh("git", "-C", repo, "checkout", "-q", "main")
        if how == "ff":
            sh("git", "-C", repo, "merge", "-q", "--ff-only", "brain/DEMO-3-reflect-1")
        elif how == "merge":
            commit_file(repo, "c.md", "c\n", "main moves on")
            sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge reflect 2", "brain/DEMO-3-reflect-2",
               env={"GIT_COMMITTER_DATE": "2030-01-01T00:00:00Z", "GIT_AUTHOR_DATE": "2030-01-01T00:00:00Z"})
    git = mod("repo").Git(repo)
    got = {ref: (at, merged) for ref, at, merged in svc.Service._reflect_refs(git, "DEMO-3", "main")}
    assert got["brain/DEMO-3-reflect-1"][1] == got["brain/DEMO-3-reflect-1"][0]
    assert got["brain/DEMO-3-reflect-2"][1] == 1893456000.0
    assert got["brain/DEMO-3-reflect-3"][1] is None


def test_write_back_branches_are_read_with_git_again_only_when_a_brain_branch_or_the_default_branch_moves(
        tmp_path, home, monkeypatch):
    """The board polls every 2 s: its 回写 Brain branches cost no git while nothing moved, and are read again once a
    branch is added, merged or main moves."""
    from appkit import commit_file, git_calls, git_repo, sh
    repo = git_repo(tmp_path / "r")
    commit_file(repo, "a.md", "a\n", "init")
    sh("git", "-C", repo, "checkout", "-q", "-b", "brain/DEMO-3-reflect-1", "main")
    commit_file(repo, "b.md", "b\n", "Brain: DEMO-3 reflect 1")
    sh("git", "-C", repo, "checkout", "-q", "main")
    svc = make_service(tmp_path, [])
    git = mod("repo").Git(repo)
    calls = git_calls(monkeypatch)

    def read():
        calls.clear()
        return {ref: merged for ref, _, merged in svc._reflects_of(git, "DEMO-3", "main")}, len(calls)

    got, n = read()
    assert got == {"brain/DEMO-3-reflect-1": None} and n > 0
    assert read() == (got, 0)
    sh("git", "-C", repo, "merge", "-q", "--ff-only", "brain/DEMO-3-reflect-1")
    got, n = read()
    assert got["brain/DEMO-3-reflect-1"] is not None and n > 0
    assert read()[1] == 0
    sh("git", "-C", repo, "branch", "brain/DEMO-3-reflect-2", "main")
    got, n = read()
    assert set(got) == {"brain/DEMO-3-reflect-1", "brain/DEMO-3-reflect-2"} and n > 0
    sh("git", "-C", repo, "pack-refs", "--all")
    commit_file(repo, "c.md", "c\n", "main moves on")
    assert read()[1] > 0 and read()[1] == 0


def test_a_for_each_ref_that_failed_once_is_not_kept_as_no_write_back_branch(tmp_path, home, monkeypatch):
    """git timed out listing the brain/ branches (rc 124): the next poll reads them again, nothing moved or not."""
    from appkit import commit_file, git_repo, sh
    repo = git_repo(tmp_path / "r")
    commit_file(repo, "a.md", "a\n", "init")
    sh("git", "-C", repo, "branch", "brain/DEMO-3-reflect-1", "main")
    svc = make_service(tmp_path, [])
    Git = mod("repo").Git
    real = Git.run
    fail = [True]

    def git_run(self, *args, **kw):
        if fail[0] and args[:1] == ("for-each-ref",):
            return subprocess.CompletedProcess(args, 124, "", "timed out")
        return real(self, *args, **kw)

    monkeypatch.setattr(Git, "run", git_run)
    git = Git(repo)
    assert svc._reflects_of(git, "DEMO-3", "main") == []
    fail[0] = False
    assert [ref for ref, _, _ in svc._reflects_of(git, "DEMO-3", "main")] == ["brain/DEMO-3-reflect-1"]


def test_any_git_call_on_the_write_back_branches_that_failed_once_is_not_kept(tmp_path, home, monkeypatch):
    """Each git call after for-each-ref (which default branch, each branch's time, merged or not, its merge commit)
    timed out once (rc 124): the next poll, nothing moved, answers as a cold read does. reflect-1 is merged with a
    merge commit, reflect-2 fast-forwarded; origin's main has both, the local main has neither."""
    from appkit import commit_file, git_repo, sh
    repo, origin = git_repo(tmp_path / "r"), tmp_path / "origin.git"
    sh("git", "init", "-q", "--bare", "-b", "main", origin)
    sh("git", "-C", repo, "remote", "add", "origin", origin)

    def at(t):
        monkeypatch.setenv("GIT_COMMITTER_DATE", "@%d +0000" % t)
        monkeypatch.setenv("GIT_AUTHOR_DATE", "@%d +0000" % t)
        return t

    at(1800000000)
    base = commit_file(repo, "a.md", "a\n", "init")
    sh("git", "-C", repo, "checkout", "-q", "-b", "brain/DEMO-3-reflect-1")
    t1 = at(1800003600)
    commit_file(repo, "b1.md", "b\n", "Brain: DEMO-3 reflect 1")
    sh("git", "-C", repo, "checkout", "-q", "main")
    t2 = at(1800007200)
    sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge reflect 1", "brain/DEMO-3-reflect-1")
    sh("git", "-C", repo, "checkout", "-q", "-b", "brain/DEMO-3-reflect-2")
    t3 = at(1800010800)
    commit_file(repo, "b2.md", "b\n", "Brain: DEMO-3 reflect 2")
    sh("git", "-C", repo, "checkout", "-q", "main")
    sh("git", "-C", repo, "merge", "-q", "--ff-only", "brain/DEMO-3-reflect-2")
    at(1800014400)
    commit_file(repo, "c.md", "c\n", "main moves on")
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    sh("git", "-C", repo, "reset", "-q", "--hard", base)
    Git, Service = mod("repo").Git, mod("service").Service
    git = Git(repo)
    cold = Service._reflect_refs(git, "DEMO-3", "main")
    assert cold == [("brain/DEMO-3-reflect-1", t1, t2), ("brain/DEMO-3-reflect-2", t3, t3)]
    real, calls, fail = Git.run, [], [0]

    def git_run(self, *args, **kw):
        calls.append(" ".join(args))
        if len(calls) == fail[0]:
            return subprocess.CompletedProcess(args, 124, "", "timed out")
        return real(self, *args, **kw)

    monkeypatch.setattr(Git, "run", git_run)
    Service._reflect_refs(git, "DEMO-3", "main")
    kept = []
    for k in range(2, len(calls) + 1):
        svc = make_service(tmp_path / ("s%d" % k), [])
        calls.clear()
        fail[0] = k
        svc._reflects_of(git, "DEMO-3", "main")
        failed, fail[0] = calls[k - 1], 0
        if svc._reflects_of(git, "DEMO-3", "main") != cold:
            kept.append(failed)
    assert kept == []


def test_a_commit_log_that_failed_once_is_not_kept_as_no_subjects(tmp_path, home, monkeypatch):
    """git timed out reading the board's commits (board.GIT_LOG, rc 124): the next poll, the same commits, reads them
    again and answers as a cold service does."""
    from appkit import commit_file, git_repo
    repo = git_repo(tmp_path / "r")
    shas = [commit_file(repo, "a.md", "a\n", "init"), commit_file(repo, "b.md", "b\n", "Brain: DEMO-3 add b")]
    Git = mod("repo").Git
    git = Git(repo)
    cold = make_service(tmp_path / "cold", [])._commits(git, shas)
    assert cold[0] == {shas[0]: "init", shas[1]: "Brain: DEMO-3 add b"}
    real, fail = Git.run, [True]

    def git_run(self, *args, **kw):
        if fail[0] and args[:1] == ("log",):
            return subprocess.CompletedProcess(args, 124, "", "timed out")
        return real(self, *args, **kw)

    monkeypatch.setattr(Git, "run", git_run)
    svc = make_service(tmp_path / "warm", [])
    assert svc._commits(git, shas) == ({}, {})
    fail[0] = False
    assert svc._commits(git, shas) == cold


def test_the_runners_page_of_a_hand_over_it_cannot_read_yet_tells_one_story():
    """runner/PAY-9: the team runner has it in its ledger, so the bridge took it; the signer signs its contract and the
    runner has no working directory for it yet. The hand-over is behind it on every part of the board: no 等开工, no
    桥 at work while 签名 waits for the bridge, no approvals 还剩 that were the person's before the hand-over."""
    f = dict(facts([], run=False), claim=None, owner=None)
    row = dict(row_of("handed_over", "bridge", "签名服务在签接手合同", do="none"), where="runner", repo="runner",
               updated_at=T, mr={"iid": 53, "url": "https://gitlab.example.com/p/-/merge_requests/53", "state": None},
               run_ids=["RUN-DEMO-3-1"])
    b = snapshot(f, row, me="alice", local_only=False, now=T + M)
    [relay] = b["relays"]
    assert [(s["key"], s["status"], s["text"]) for s in relay["steps"]] == [
        ("handover", "merged", "交接了"), ("bridge", "merged", "桥接手了"), ("signer", "running", "在签接手合同"),
        ("workspace", "waiting", "等签好合同")]
    assert b["runs"] == [] and relay["to"] is None
    assert [(n["id"], n["status"], n["doing"]) for n in b["nodes"] if n["phase"] in ("inception", "construction")][:2] == [
        ("requirements", "done", mod("stages").RUNNER_BLIND), ("code-generation/app", "waiting", "等运行机开工")]
    assert "等开工" not in said(b) and "等桥核对" not in said(b)
    cells = {c["id"]: c for c in b["checks"]["cells"]}
    assert cells["bridge"]["status"] == "pass" and cells["signer"]["detail"] == "在签"
    assert "gate-REQUIREMENTS" not in cells and "需求审批" not in b["checks"]["todo"]
    assert "签接手合同" in b["checks"]["todo"] and "发布审批" in b["checks"]["todo"]
    # signed: the runner prepares its working directory
    row = dict(row, waits=dict(row["waits"], who="runner", text="运行机在准备工作区"))
    b = snapshot(f, row, me="alice", local_only=False, now=T + M)
    assert [(s["key"], s["status"], s["text"]) for s in b["relays"][0]["steps"]][2:] == [
        ("signer", "merged", "签了接手合同"), ("workspace", "running", "运行机在准备工作区")]


def test_a_run_is_said_as_the_runner_page_says_it_the_id_only_on_hover():
    """ovrest: the feed said 「签名服务签了接手合同（RUN-PAY-4-1）」 and the runner's lane had a 「RUN-PAY-4-1」 chip; the
    运行机 page says 「第 1 次运行」, and the roster 「运行机 第 1 次」 with the run id on hover (Run.run_id)."""
    hand = T + 30 * M
    f = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    t = {"actions": {"handovers": [{"kind": "bridge accepted", "at": iso(hand + M), "mr_iid": 41},
                                   {"kind": "voucher", "at": iso(hand + 2 * M), "run_id": "RUN-DEMO-3-1"}]}}
    row = dict(row_of("running", "runner", "运行机在跑"), run_ids=["RUN-DEMO-3-1"])
    b = snapshot(f, row, t=t, local_only=False, now=hand + H)
    signed = next(e for e in b["events"] if e["kind"] == "signed")
    assert (signed["text"], signed["src"]) == ("签名服务签了接手合同（第 1 次运行）", "运行号 RUN-DEMO-3-1")
    runner = handed_over()
    assert [(r["label"], r["run_id"]) for r in runner["runs"] if r["kind"] == "runner"] == [("运行机 第 1 次", "RUN-DEMO-3-1")]
    for x in (b, runner):
        assert "RUN-DEMO-3-1" not in said(x)
    assert board.run_words(["RUN-PAY-4-1", "RUN-PAY-4-2"]) == "第 1、2 次运行"


# ---------------------------------------------------------------- the stage graph: nodes, rounds, runs, relays

BIG_FILE, SMALL_FILE = "aidlc-docs/audit/laptop-1a2b3c.jsonl", "aidlc-docs/audit/laptop-9f8e7d.jsonl"
RUNNER_FILE = "aidlc-docs/audit/runner-4d5e6f.jsonl"
IDEATION = [s.id for s in mod("stages").LEAN if s.phase == "ideation"]


def node(b, nid):
    return next(n for n in b["nodes"] if n["id"] == nid)


def on_way(b):
    return [n["id"] for n in b["nodes"] if n["status"] != "skipped"]


def started():
    """lean new without --ideation, then the requirements begun: what `brain start` leaves."""
    return [ev(T, "PHASE_SKIPPED", phase="ideation", stages=IDEATION, reason="not requested (lean new without --ideation)"),
            ev(T + 1, "STAGE_STARTED", stage="requirements", path="aidlc-docs/261109-x/inception/requirements/requirements.md",
               created=True)]


def test_a_requirement_just_begun_draws_the_default_way_waiting_and_one_band_for_a_phase_skipped_whole():
    """Rule 1: the default way's stages are on the board before they are reached, each saying what it waits for; Lean's
    one unit is app while unit-of-work.md names none; a phase skipped as a whole is one folded band."""
    b = snapshot(facts(started()), row_of("local", "agent", "AI 在做"), sessions=[session(T + M, busy=True)], now=T + 2 * M)
    assert on_way(b) == ["requirements", "code-generation/app", "build-and-test", "review", "release", "reflect"]
    assert (node(b, "requirements")["status"], node(b, "code-generation/app")["doing"]) == ("running", "等批了设计")
    assert [(p["key"], p["state"], p["note"]) for p in b["phases"]] == [
        ("init", "used", None), ("ideation", "unused", "这次不需要"), ("inception", "used", None),
        ("construction", "used", None), ("release", "used", None), ("operations", "used", None)]
    assert [(r["key"], r["label"], r["now"]["status"], r["now"]["node"]) for r in b["runs"]] == [
        ("local", "本机会话", "running", "requirements")]
    assert b["relays"] == [] and b["live"] is True
    # the units unit-of-work.md plans come first, in its order
    planned = board.build(facts(started()), row_of("local", "agent", "AI 在做"), None, mr=None, sessions=[], app_events=[],
                          team_decisions=[], subjects={}, reflect_refs=[], default_branch="main", local_only=True,
                          kit_rows=[], decisions=[], brain=None, me="dana", now=T + 2 * M,
                          units=[mod("stages").UnitPlan("web"), mod("stages").UnitPlan("api")])
    assert [n["id"] for n in planned["nodes"] if n["stage"] == "code-generation"] == ["code-generation/web",
                                                                                     "code-generation/api"]


def test_a_lean_0_2_run_has_no_record_of_ideation_and_its_band_says_so():
    """Rule 6: DEMO-3's records are Lean 0.2's shape (no PHASE_SKIPPED): the 构思 band it is past is unrecorded."""
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 16 * M)])
    assert [(p["state"], p["note"]) for p in b["phases"] if p["key"] == "ideation"] == [("unrecorded", "Lean 没记这一段")]
    assert next(p for p in b["phases"] if p["key"] == "construction")["note"] == "每个单元都跳过了功能设计"


def small(state="local", busy=True, merged_in=False, units=()):
    """DEMO-4, a small requirement of DEMO-1 (unit api): its branch holds its big one's records and its own clone's."""
    design = {"kind": "DESIGN", "path": "aidlc-docs/261109-x/inception/application-design/application-design.md",
              "sha256": "d" * 64}
    evs = [dict(ev(T, "STAGE_COMPLETED", stage="requirements"), _file=BIG_FILE),
           dict(ev(T + 5 * M, "APPROVAL_REQUESTED", **design), _file=BIG_FILE),
           dict(ev(T + 10 * M, "APPROVED", **design), _file=BIG_FILE),
           dict(ev(T + H, "STAGE_STARTED", stage="code-generation", unit="api"), _file=SMALL_FILE, actor={"team": "api"})]
    f = facts(evs, key="DEMO-4")
    f["run"].update(parent="DEMO-1", unit="api", lean_clone="9f8e7d")
    fam = {"role": "child", "parent": "DEMO-1", "unit": "api", "state": "merged" if merged_in else "in_progress"}
    row = dict(row_of("merged_into_parent" if merged_in else state, "nobody" if merged_in else "agent",
                      "已合进大需求 DEMO-1 的分支：等它评审、发布" if merged_in else "AI 在做"), key="DEMO-4", family=fam)
    return snapshot(f, row, sessions=[session(T + H, busy=busy)], now=T + 2 * H, units=units, local_only=False)


def test_a_small_requirement_has_no_release_or_write_back_and_its_big_ones_rounds_are_the_big_ones():
    """Rule 2: no 发布 and no 回写 Brain on a small requirement's way, their bands say the big one does them; what its
    big one recorded is that one's; no runner run and no hand-over by construction."""
    b = small()
    assert "release" not in on_way(b) and "reflect" not in on_way(b)
    assert [(p["state"], p["note"]) for p in b["phases"] if p["key"] in ("release", "operations")] == [
        ("unused", "跟大需求一起发布"), ("unused", "跟大需求一起回写 Brain")]
    assert [r["run"] for r in node(b, "requirements")["rounds"]] == ["parent-DEMO-1"]
    assert node(b, "application-design")["rounds"][0]["outcome"]["by"] == "大需求的负责人"
    assert (node(b, "code-generation/api")["run"], node(b, "code-generation/api")["status"]) == ("local", "running")
    assert [(r["key"], r["kind"], r["label"], r["req"]) for r in b["runs"]] == [
        ("local", "local", "本机会话", None), ("parent-DEMO-1", "parent", "大需求 DEMO-1", "DEMO-1")]
    assert b["relays"] == []
    # the check wall says the same: nothing of it is handed over, and its release is the big one's
    cells = {c["id"]: c for c in b["checks"]["cells"]}
    assert (cells["gate-RELEASE"]["status"], cells["gate-RELEASE"]["detail"]) == ("not_needed", "跟大需求一起发布")
    assert [c["id"] for c in b["checks"]["cells"] if c["group"] == "handover"] == []
    assert "发布审批" not in b["checks"]["todo"]


def test_an_event_never_travels_to_a_stage_the_graph_draws_no_box_for():
    """A skipped stage keeps its node, and the graph draws no box for it: what the run does after Lean skipped a stage
    travels from the node it really was at (a commit after 拆分单元 was skipped, the hand-over after CI 流水线 was
    skipped), 开工 goes to the first stage on the way, and a session opened as 构思 is skipped whole is at no node. A
    run waiting after 构建测试 with CI 流水线 skipped is next at 审查."""
    plan = "aidlc-docs/261109-x/construction/plans/app-code-generation-plan.md"
    evs = started() + [ev(T + 10 * M, "STAGE_COMPLETED", stage="requirements"),
                       ev(T + 11 * M, "STAGE_SKIPPED", stage="units", reason="只有一个单元"),
                       ev(T + 12 * M, "STAGE_STARTED", stage="code-generation", unit="app", path=plan),
                       ev(T + 20 * M, "STAGE_COMPLETED", stage="code-generation", unit="app"),
                       ev(T + 21 * M, "VERIFY", ok=True, results=[{"cmd": "pytest -q", "exit": 0}]),
                       ev(T + 21 * M, "STAGE_COMPLETED", stage="build-and-test"),
                       ev(T + 22 * M, "STAGE_SKIPPED", stage="ci-pipeline", reason="现有的 CI 够用")]
    hand = T + 30 * M
    t = {"actions": {"commits": [{"sha": "%040x" % 3, "at": iso(T + 11 * M + 30), "by": "person", "files": 1},
                                 {"sha": "%040x" % 4, "at": iso(hand), "by": "person", "records_only": True, "files": 1}],
                     "handovers": [{"kind": "requested", "at": iso(hand), "commit": "%040x" % 4}]}}
    b = snapshot(facts(evs, handoff={"requested_at": iso(hand), "by": "dana"}),
                 row_of("handed_over", "bridge", "桥在核对交接"), t=t, local_only=False, now=hand + M,
                 app_events=[{"at": iso(T), "label": "开了本机会话"}])
    skipped = {n["id"] for n in b["nodes"] if n["status"] == "skipped"}
    assert {"units", "ci-pipeline", "intent-capture"} <= skipped
    assert [(e["kind"], e["from"], e["to"]) for e in b["events"] if e["kind"] != "stage" and {e["from"], e["to"]} & skipped] == []
    said = {e["kind"]: e for e in b["events"]}
    assert (said["start"]["to"], said["commit"]["node"], said["handover"]["from"]) == (
        "requirements", "requirements", "build-and-test")
    assert (said["session"]["node"], said["session"]["from"], said["session"]["to"]) == (None, "person", "person")
    quiet = snapshot(facts(evs), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 23 * M)],
                     now=T + H)
    assert quiet["runs"][0]["now"]["node"] == "review"


def test_a_small_requirement_builds_its_own_unit_of_its_big_ones_plan():
    """Its branch holds its big one's unit-of-work.md: of the units it plans, only the small one's is on its way."""
    plan = [mod("stages").UnitPlan(u) for u in ("web", "api", "jobs")]
    assert [n["id"] for n in small(units=plan)["nodes"] if n["stage"] == "code-generation"] == ["code-generation/api"]


def test_a_small_requirement_merged_into_its_big_one_is_not_live_and_nothing_of_it_runs():
    """Rule 3 (the preview's PAY-12 glowed, 此刻有人在做): merged into its big one, its session still busy."""
    b = small(merged_in=True)
    assert b["live"] is False
    assert [n["id"] for n in b["nodes"] if n["status"] in ("running", "fixing")] == []
    assert [(r["key"], r["now"]["status"]) for r in b["runs"]] == [("local", "merged"), ("parent-DEMO-1", "done")]


def test_a_big_requirement_draws_each_unit_of_its_small_ones():
    """Rule 4: a unit whose small requirement has not merged is one node of that small one, with its state; once it
    merged, its clone's rounds are on the big one's branch, done by child-<KEY>."""
    evs = [dict(ev(T, "STAGE_COMPLETED", stage="requirements"), _file=BIG_FILE),
           dict(ev(T + H, "STAGE_STARTED", stage="code-generation", unit="api"), _file=SMALL_FILE, actor={"team": "api"}),
           dict(ev(T + 2 * H, "STAGE_COMPLETED", stage="code-generation", unit="api"), _file=SMALL_FILE,
                actor={"team": "api"})]
    fam = {"role": "parent", "children": [{"key": "DEMO-4", "unit": "api", "state": "merged", "owner": "dana"},
                                          {"key": "DEMO-5", "unit": "web", "state": "in_progress", "owner": "bob"},
                                          {"key": "DEMO-6", "unit": "jobs", "state": "unclaimed", "owner": None}]}
    b = snapshot(facts(evs), dict(row_of("local", "people", "等小需求合进来：已合并 1/3", do="none"), family=fam),
                 sessions=[session(T + 3 * H)], now=T + 4 * H)
    cg = {n["unit"]: n for n in b["nodes"] if n["stage"] == "code-generation"}
    assert list(cg) == ["api", "web", "jobs"]
    assert ([r["run"] for r in cg["api"]["rounds"]], cg["api"]["child"]) == (["child-DEMO-4"], None)
    assert (cg["web"]["child"], cg["web"]["status"], cg["web"]["doing"]) == (
        {"req": "DEMO-5", "state": "in_progress"}, "running", "bob 在做（DEMO-5）")
    assert (cg["jobs"]["child"], cg["jobs"]["doing"]) == ({"req": "DEMO-6", "state": "unclaimed"}, "待认领（DEMO-6）")
    assert [(r["key"], r["unit"]) for r in b["runs"]] == [("local", None), ("child-DEMO-4", "api")]
    # the big one waits on its small ones: nobody's turn, and no command path
    assert (b["runs"][0]["now"]["status"], b["runs"][0]["now"]["node"]) == ("waiting", None)


def handed_over(state="running", who="runner", text="运行机在跑", **waits):
    """DEMO-3 handed over 30 minutes after its approval; the runner's clone checks it (a red verify), fixes it, asks
    for the release; the MR question D-3 asks the product to accept it."""
    hand = T + 30 * M
    evs = demo3_events()[:12] + [
        dict(ev(T + 40 * M, "VERIFY", ok=False, results=[{"cmd": "pytest -q", "exit": 1, "secs": 4.0}]), _file=RUNNER_FILE),
        dict(ev(T + 70 * M, "VERIFY", ok=True, results=[{"cmd": "pytest -q", "exit": 0, "secs": 4.0}]), _file=RUNNER_FILE),
        dict(ev(T + 80 * M, "STAGE_STARTED", stage="release", path="aidlc-docs/261109-x/operations/release.md",
                sha256="e" * 64), _file=RUNNER_FILE),
        dict(ev(T + 81 * M, "APPROVAL_REQUESTED", kind="RELEASE", path="aidlc-docs/261109-x/operations/release.md",
                sha256="e" * 64), _file=RUNNER_FILE)]
    f = facts(evs, handoff={"requested_at": iso(hand), "by": "dana"},
              approvals=[{"kind": "RELEASE", "unit": None, "state": "pending", "sha8": "e" * 8, "at": T + 81 * M,
                          "line": "approve RELEASE eeeeeeee", "path": None}])
    fix = "%040x" % 7
    t = {"actions": {"handovers": [{"kind": "requested", "at": iso(hand), "commit": "%040x" % 1},
                                   {"kind": "bridge accepted", "at": iso(hand + M), "mr_iid": 41, "run_id": "RUN-DEMO-3-1"},
                                   {"kind": "voucher", "at": iso(hand + 2 * M), "run_id": "RUN-DEMO-3-1"}],
                     "commits": [{"sha": "%040x" % 1, "at": iso(hand), "by": "person", "records_only": True, "files": 2},
                                 {"sha": fix, "at": iso(T + 60 * M), "by": "bot", "after_handover": True, "files": 1}]},
         "decisions": [{"source": "bridge", "id": "D-3", "kind": "acceptance", "lean_request": "RELEASE eeeeeeee",
                        "asked_at": iso(T + 82 * M), "at": None, "choice": None, "state": "open"}]}
    mr = {"url": "https://gitlab.example.com/p/-/merge_requests/41"}
    row = dict(row_of(state, who, text, **waits), mr={"iid": 41, "url": mr["url"], "state": "opened"},
               run_ids=["RUN-DEMO-3-1"])
    return snapshot(f, row, t=t, mr=mr, local_only=False, now=T + 90 * M)


def test_an_approval_on_the_mr_gives_the_comment_to_write_there_not_a_session_line():
    """UI-23: after the hand-over Lean's approval is a question on the MR, answered with the comment
    `answer D-n approve` (reject sends it back). The step names the MR, gives that comment to copy and the MR to open,
    and no session; the runner asks for the code-generation plan's approval as a clarify, and the board names the
    approval, not 澄清."""
    b = handed_over("acceptance", "product", "等产品在 MR 上验收 D-3（approve / reject）", do="accept", where="mr")
    step = b["accept"]["step"]
    assert (step["comment"], step["line"], step["session_url"]) == ("answer D-3 approve", None, None)
    assert step["mr_url"] == "https://gitlab.example.com/p/-/merge_requests/41" and "会话" not in step["text"]

    hand = T + 30 * M
    f = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    mr = {"url": "https://gitlab.example.com/p/-/merge_requests/41",
          "questions": [{"id": "D-4", "kind": "clarify", "kind_label": "澄清", "state": "open", "at": iso(hand + H),
                         "lean_request": "PLAN 5d1e7a90", "options": []}]}
    row = dict(row_of("parked", "you", "等你在 MR 上回答 D-4（approve / reject）", do="answer", where="mr"),
               mr={"iid": 41, "url": mr["url"], "state": "opened"})
    b = snapshot(f, row, mr=mr, local_only=False, now=hand + 2 * H)
    step = b["accept"]["step"]
    assert step["text"] == "在 MR 上回答 D-4（approve 通过 / reject 退回）：桥会把回答带回运行机"
    assert (step["comment"], step["line"], step["session_url"]) == ("answer D-4 approve", None, None)
    assert "D-4 在 MR 上问人：代码生成计划审批" in [e["text"] for e in b["events"]]


def test_a_round_says_who_did_it_and_each_event_its_node_run_and_round():
    """Rule 7 on the board: build-and-test's two rounds are the runner's (the second because the first was red),
    each verify event says which; the fix commit falls in round 2 on the conveyor; the hand-over is this machine's run
    and on no node; the bridge's acceptance is the runner run's."""
    b = handed_over()
    bt = node(b, "build-and-test")
    assert [(r["n"], r["run"], r["outcome"]["kind"], r["why"]["kind"]) for r in bt["rounds"]] == [
        (1, "runner-1", "failed", "first"), (2, "runner-1", "passed", "verify_failed")]
    assert [(e["node"], e["run"], e["round"]) for e in b["events"] if e["kind"] == "verify"] == [
        ("build-and-test", "runner-1", 1), ("build-and-test", "runner-1", 2)]
    fix = next(x for x in b["belt"] if x["kind"] == "commit")
    assert (fix["run"], fix["node"]) == ("runner-1", "build-and-test") and bt["rounds"][1]["commits"] == [fix["sha"]]
    assert [(e["node"], e["run"]) for e in b["events"] if e["kind"] in ("handover", "accepted", "signed")] == [
        (None, "local"), (None, "runner-1"), (None, "runner-1")]
    assert [(c["id"], c["node"], c["run"]) for c in b["checks"]["cells"] if c["group"] == "verify"] == [
        ("verify-pytest-1", "build-and-test", "runner-1")]


def test_a_request_out_has_its_line_on_this_machine_and_its_mr_question_after_the_hand_over():
    """Rule 8: before the hand-over the person types the line (DEMO-3's REQUIREMENTS, when it was out); after it,
    the gate is the MR question that asks it (D-3) and has no line; a decided gate says who decided."""
    b = handed_over("acceptance", "product", "等产品在 MR 上验收 D-3（approve / reject）", do="accept")
    rel = node(b, "release")
    assert [(g["kind"], g["state"], g["who"], g["line"], g["mr"]) for g in rel["gates"]] == [
        ("RELEASE", "pending", "product", None, {"decision": "D-3", "url": "https://gitlab.example.com/p/-/merge_requests/41"})]
    assert (rel["status"], rel["doing"]) == ("awaiting_approval", "等产品验收（D-3）")
    assert [(g["state"], g["by"], g["line"]) for g in node(b, "requirements")["gates"]] == [("approved", "你", None)]
    out = demo3_events()[:10]          # the REQUIREMENTS request, not answered yet
    asked = snapshot(facts(out, approvals=[{"kind": "REQUIREMENTS", "unit": None, "state": "pending", "sha8": "dfc1969f",
                                            "at": T + 9 * M + 50, "line": "approve REQUIREMENTS dfc1969f", "path": None}]),
                     row_of("local", "you", "等你批准需求", do="approve"), sessions=[session(T + 10 * M)])
    assert [(g["state"], g["who"], g["line"], g["mr"]) for g in node(asked, "requirements")["gates"]] == [
        ("pending", "you", "approve REQUIREMENTS dfc1969f", None)]


def test_the_runner_run_is_numbered_named_and_handed_over_to_through_one_relay():
    """The roster: this machine's session, then 运行机 第 1 次 named by its run id; one relay, every step done since
    the runner's records came; the runner's run waits on the product at the release."""
    b = handed_over("acceptance", "product", "等产品在 MR 上验收 D-3（approve / reject）", do="accept")
    assert [(r["key"], r["label"], r["readable"], r["run_id"]) for r in b["runs"]] == [
        ("local", "本机会话", "here", None), ("runner-1", "运行机 第 1 次", "elsewhere", "RUN-DEMO-3-1")]
    assert (b["runs"][0]["now"]["doing"], b["runs"][1]["now"]["node"]) == ("做完 · 交出去了", "release")
    [relay] = b["relays"]
    assert (relay["to"], relay["sha"]) == ("runner-1", ("%040x" % 1)[:12])
    assert [(s["key"], s["status"]) for s in relay["steps"]] == [
        ("handover", "merged"), ("bridge", "merged"), ("signer", "merged"), ("workspace", "merged")]
    assert relay["steps"][1]["text"] == "桥接手了（MR !41）"


def test_a_repo_done_only_here_has_one_run_and_no_hand_over():
    """Rule 5."""
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 16 * M)])
    assert [r["key"] for r in b["runs"]] == ["local"] and b["relays"] == []
    assert {r["run"] for n in b["nodes"] for r in n["rounds"]} == {"local"}


def test_a_node_s_replay_ends_on_what_it_says_now():
    """Rule 9: node_status alone writes a node's status, so 回放 at the end and the live node say the same."""
    for b in (handed_over(), small(), snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"),
                                                 sessions=[session(T + 16 * M)])):
        for n in b["nodes"]:
            if n["history"]:
                assert (n["history"][-1]["status"], n["history"][-1]["doing"]) == (n["status"], n["doing"]), n["id"]
            assert [h["at"] for h in n["history"]] == sorted(h["at"] for h in n["history"]), n["id"]


def test_live_is_a_run_at_work_now():
    """Rule 10: a busy session is at work; a quiet one waits for the person; a merged requirement is over."""
    assert snapshot(facts(started()), row_of("local", "agent", "AI 在做"), sessions=[session(T + M, busy=True)],
                    now=T + 2 * M)["live"] is True
    assert snapshot(facts(started()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + M)],
                    now=T + 2 * M)["live"] is False
    assert handed_over()["live"] is True
    assert snapshot(facts(started(), merged=True), row_of("merged", "nobody", "已合并"),
                    sessions=[session(T + M, busy=True)], now=T + 2 * M)["live"] is False


def test_the_board_never_shows_typed_words_or_a_clone_s_host():
    """A rejection's words are a person's (never read); a ledger file is named after its clone's host (never sent)."""
    evs = demo3_events()[:10] + [ev(T + 10 * M, "REJECTED", kind="REQUIREMENTS", sha256="dfc1969f" * 8,
                                    words="reject REQUIREMENTS 金额要到分，别四舍五入")]
    b = snapshot(facts([dict(e, _file=BIG_FILE) for e in evs]), row_of("local", "you", "等它改需求"),
                 sessions=[session(T + 11 * M)])
    assert node(b, "requirements")["rounds"][0]["outcome"]["kind"] == "sent_back"
    text = json.dumps(b, ensure_ascii=False)
    assert "四舍五入" not in text and "laptop-1a2b3c" not in text


def test_every_board_event_kind_has_a_place_on_the_graph():
    """Each kind of ui/src/types.ts BoardEventKind says which node and whose run it is (board.PLACE): a kind added
    there without a row fails here, instead of an event landing nowhere."""
    src = (ROOT / "ui" / "src" / "types.ts").read_text(encoding="utf-8")
    union = re.search(r"export type BoardEventKind =([^\n]*\n(?:\s*\|[^\n]*\n)+)", src).group(1)
    kinds = set(re.findall(r"'([a-z]+)'", union))
    assert {"claim", "verify", "reflect"} <= kinds and sorted(kinds - set(board.PLACE)) == []
    assert sorted(kinds - set(board.ANCHORS)) == []
    assert {end for ends in board.ANCHORS.values() for end in ends} <= {
        "person", "branch", "checks", "relay", board.NODE, board.RUN_AT}


def test_every_event_travels_between_its_nodes_and_the_graphs_anchors():
    """BoardEvent.from / to: a node of this board or one of the anchors (the owner box, the run branch rail, the check
    ring, the hand-over pill), by kind as types.ts says: a verify goes from the branch to the checks, the hand-over
    from the node this machine's run was at to the relay, the bridge's delivered answer from the relay to the runner's
    node, a merge blips on the branch."""
    b = handed_over("acceptance", "product", "等产品在 MR 上验收 D-3（approve / reject）", do="accept")
    ids = {n["id"] for n in b["nodes"]}
    assert [(e["kind"], e["from"], e["to"]) for e in b["events"]
            if not {e["from"], e["to"]} <= ids | {"person", "branch", "checks", "relay"}] == []
    by = {}
    for e in b["events"]:
        by.setdefault(e["kind"], []).append((e["from"], e["to"]))
    assert by["verify"] == [("branch", "checks")] * 2 and by["handover"] == [("requirements", "relay")]
    assert by["accepted"] == by["signed"] == [("relay", "relay")]
    assert by["request"][-1] == ("release", "person") and by["approve"] == [("person", "requirements")]
    assert by["commit"] == [("build-and-test", "branch")] and by["claim"] == [("person", "person")]
    merged = snapshot(facts(demo3_events(), merged=True), row_of("merged", "nobody", "已合并"),
                      t=merged_trace(T + 20 * M, T + 15 * 86400))
    assert [(e["from"], e["to"]) for e in merged["events"] if e["kind"] == "merge"] == [("branch", "branch")]


def test_a_finished_stage_is_merged_once_the_way_moved_past_it():
    """A stage done or passed is drawn finished for good (merged, 完成) once a later stage's round began after it ended,
    and only done (做完这步) while nothing after it has begun; the replay turns it at the moment that later round
    began. A stage that ended inside a later one already at work is passed only by the next one to begin."""
    b = snapshot(facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), sessions=[session(T + 16 * M)])
    assert [(n["id"], n["status"], n["doing"]) for n in b["nodes"] if n["status"] in ("done", "merged")] == [
        ("reverse-engineering", "merged", "做完了"), ("requirements", "merged", "你批了"),
        ("code-generation/app", "merged", "做完了"), ("build-and-test", "done", "检查都过了")]
    assert [(h["at"], h["status"]) for h in node(b, "reverse-engineering")["history"]] == [
        (T + 4 * M, "running"), (T + 4 * M + 30, "done"), (T + 9 * M + 16, "merged")]
    assert [(h["at"], h["status"]) for h in node(b, "code-generation/app")["history"]] == [
        (T + 11 * M + 47, "running"), (T + 14 * M + 4, "done"), (T + 14 * M + 13, "merged")]
    later = snapshot(facts(demo3_events() + [ev(T + 20 * M, "STAGE_STARTED", stage="release")]),
                     row_of("local", "agent", "AI 在做"), sessions=[session(T + 20 * M, busy=True)], now=T + 21 * M)
    bt = node(later, "build-and-test")
    assert (bt["status"], bt["history"][-1]) == ("merged", {"at": T + 20 * M, "status": "merged", "doing": "检查都过了",
                                                            "who": None})


def test_each_run_that_waits_on_its_person_names_the_step_they_take():
    """RunNow.do: under each run that waits on its person the page names the step they take (Waits.do's words): reply
    to a quiet session, open one when none is here, approve a tool call, type an approval line, answer a question,
    redo or label a hand-over; a run at work, handed over, or waiting on nobody names none."""
    last, hand = T + 15 * M + 43, T + 30 * M
    req = [{"kind": "REQUIREMENTS", "unit": None, "state": "pending", "sha8": "dfc1969f", "at": T + 9 * M + 50,
            "line": "approve REQUIREMENTS dfc1969f", "path": None}]
    handed = facts(demo3_events()[:12], handoff={"requested_at": iso(hand), "by": "dana"})
    for f, row, sessions, do in (
            (facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), [session(last)], "reply"),
            (facts(demo3_events()), row_of("local", "you", "会话空着：轮到你回话"), [], "reply"),
            (facts(demo3_events()), row_of("local", "you", "等你批准工具调用"), [session(last, awaiting=True)], "tool"),
            (facts(demo3_events()[:10], approvals=req), row_of("local", "you", "等你批准需求", do="approve"),
             [session(last)], "approve"),
            (facts(demo3_events(), park={"kind": "clarify"}), row_of("parked", "you", "停下等你回答", do="answer"),
             [session(last)], "answer"),
            (handed, row_of("rejected", "you", "接手被拒：MR 是从复制出去的仓库开的", do="redo_handoff"), [], "redo_handoff"),
            (handed, row_of("handed_over", "you", "推上去了，但 MR 上没有交接标签", do="label_handoff"), [], "label_handoff"),
            (handed, row_of("handed_over", "bridge", "桥在核对交接"), [], None),
            (facts(demo3_events()), row_of("local", "agent", "AI 在做"), [session(last, busy=True)], None)):
        now = run_now(snapshot(f, row, sessions=sessions, local_only=False, now=last + H), "local")
        assert now["do"] == do and (now["status"] == "awaiting_approval" or now["status"] == "stalled") == (do is not None), (
            row["waits"]["text"], now)
