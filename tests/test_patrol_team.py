"""MR 巡检 inside the app's team mode, offline. TeamRuntime hosts brain-kit's bridge on a light bridge.json (the spool
signer named, no signer process: nothing is handed over here), a runnerd.json whose clone address is a local bare repo,
the in-memory GitLab of the patrol tests, and the app's own session runner on an in-memory KiroCrew.

A round ends with patrol. An MR with an open review thread gets a brain-patrol session of its own in the runner folder,
working in a real clone at the MR head, told the thread and the reply commands; GET /team/patrol says so, with the
page's keys. Its turn ends: the thread is answered. A new comment goes to the same session. A project whose kit.json
leaves mr-handoff out is not polled for hand-overs, and one that does not name work_sources is not patrolled.
"""

import json
import os
import time
from types import SimpleNamespace

import pytest

from appkit import BRAINKIT, ROOT, FakeGateway, GatewayError, commit_file, git_repo, mod, sh
from brainfix import make_service, needs_brainkit, run
from http_fakes import AppRoute, FakeWeb, call
from patrolfix import BASE, Clock, PatrolGitLab
from test_contract import diff_keys, fixture

pytestmark = needs_brainkit
P, PID = "grp/repo", 42


def light_world(tmp_path, home, monkeypatch, kit):
    root, work = tmp_path / "team-setup", home / "workplace" / "brain-team"
    for d in ("spool/requests", "spool/ready", "spool/failed", "signer-spool/requests", "signer-spool/signed",
              "signer-spool/refused", "keys", "vouchers"):
        (root / d).mkdir(parents=True)
    for d in ("runs", "cache"):
        (work / d).mkdir(parents=True)
    (root / "identities.json").write_text(json.dumps({"users": {"alice": {}}, "roles": {"product": ["alice"]}}))
    allowed = root / "keys" / "allowed_signers"
    allowed.write_text("")
    origin = tmp_path / "origin" / "grp" / "repo.git"
    origin.parent.mkdir(parents=True)
    sh("git", "init", "-q", "--bare", origin)
    src = git_repo(tmp_path / "src")
    commit_file(src, "README.md", "# repo\n", "init")
    sh("git", "-C", src, "push", "-q", origin, "main")
    sh("git", "-C", src, "checkout", "-q", "-b", "feature")
    head = commit_file(src, "src/discount.py", "RATE = 120\n", "discount")
    sh("git", "-C", src, "push", "-q", origin, "feature")
    spool, sspool = root / "spool", root / "signer-spool"
    (root / "bridge.json").write_text(json.dumps({
        "projects": [P], "gitlab": {"base": BASE, "token_env": "TEST_BRIDGE_TOKEN"}, "identities": str(root / "identities.json"),
        "ledger": str(root / "bridge-ledger.sqlite"), "spool_dir": str(spool), "vouchers_dir": str(root / "vouchers"),
        "runner_runs_root": str(work / "runs"), "publish_vouchers": False, "pseudonym_salt": "s",
        "kirocrew": {"agent": "brain-run", "memory_mode": "incognito"},
        "signer": {"type": "spool", "spool_dir": str(sspool), "allowed_signers": str(allowed)}}))
    (root / "runnerd.json").write_text(json.dumps({
        "spool_dir": str(spool), "cache_dir": str(work / "cache"), "runs_root": str(work / "runs"),
        "allowed_signers": str(allowed), "clone_url": str(tmp_path / "origin") + "/{project}.git",
        "git_name": "brain-bot", "git_email": "brain-bot@noreply.invalid"}))
    monkeypatch.setenv("TEST_BRIDGE_TOKEN", "bridge-tok")
    gl = PatrolGitLab(Clock(time.time()))
    gl.project(P, PID, kit=kit)
    team = {"enabled": True, "bridge_config": str(root / "bridge.json"), "runnerd_config": str(root / "runnerd.json"),
            "dev_same_user": True}
    return SimpleNamespace(root=root, work=work, gl=gl, head=head, team=team)


def app(w, tmp_path, gw):
    svc = make_service(tmp_path / "app", [], gw=gw, team=w.team, transport=w.gl)
    return svc, mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)


def test_a_round_gives_an_mr_s_open_thread_to_a_brain_patrol_session_of_its_own(tmp_path, home, monkeypatch):
    w = light_world(tmp_path, home, monkeypatch, {"work_sources": ["mr-handoff", "mr-patrol"]})
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)
    gl, slot = w.gl, "patrol-42-7"
    gl.mr(P, 7, sha=w.head)
    d = gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 1)

    async def go():
        await svc.start()
        try:
            assert svc.team is not None and svc.team.host is not None, svc.team and svc.team.error
            kit_dir = str(mod("bk").root())
            loop = await call(routes, "POST", "/team/loop")
            seen = await call(routes, "GET", "/team/patrol")
            gw.finish(slot)
            for _ in range(4):
                await svc.sessions.poll()
            await call(routes, "POST", "/team/loop")
            answered = await call(routes, "GET", "/team/patrol")
            gl.thread(P, 7, "dave", "这里也要改")
            await call(routes, "POST", "/team/loop")
            return kit_dir, loop, seen, answered
        finally:
            await svc.stop()

    kit_dir, loop, seen, answered = run(go())
    assert loop.status == 200 and loop.body["ok"] and loop.body["summary"]["patrol"] == 1, loop.body
    # the session: brain-patrol's own, in its MR's folder under 「Brain 运行机」, in its workspace, trusted
    assert ("agent", "brain-patrol", "template") in gw.calls
    folders = [c[1] for c in gw.calls if c[0] == "folder"]
    assert folders[:2] == ["Brain 运行机", "巡检 grp/repo!7"]
    ws = w.work / "patrol" / "42-7"
    assert gw.slot_rows[slot]["project"] == str(ws) and gw.slot_rows[slot]["trust"]
    assert sh("git", "-C", ws, "rev-parse", "HEAD").stdout.strip() == w.head
    assert sh("git", "-C", ws, "config", "user.email").stdout.strip() == "brain-bot@noreply.invalid"
    told = [m["content"] for m in gw.msgs[slot] if m["role"] == "user"]
    assert "讨论 %s，评论 #%d，@carol，在 src/discount.py:1" % (d["id"], d["notes"][0]["id"]) in told[0]
    helper = "%s -I %s --config %s --brainkit %s --project grp/repo --mr 7" % (
        mod("constants").PYTHON_BIN, ROOT / "backend" / "core" / "team" / "patrol_reply.py", w.root / "bridge.json", kit_dir)
    assert helper + " --discussion" in told[0] and "工作区：%s" % ws in told[0]
    # the page: the MR at work in that session, with the fixture's keys
    item, = seen.body["items"]
    assert (item["state"], item["slot"], item["problems"]) == (
        "working", slot, [{"kind": "review", "text": "@carol 的评审意见（src/discount.py:1）"}])
    assert seen.body["projects"] == [{"project": P, "on": True, "error": ""}] and seen.body["working"] == 1
    assert diff_keys(fixture("team-patrol.json"), seen.body) == []
    # the turn ended: answered; a new comment goes to the same session, without the answered one
    assert answered.body["items"][0]["state"] == "ok"
    assert [k for k in gw.slot_rows if k.startswith("patrol-")] == [slot]
    assert len(told) == 2 and "这里也要改" in told[1] and "百分比要限制在 0-100" not in told[1]


def test_a_turn_kiro_crew_cut_short_with_an_error_is_a_failed_attempt_and_its_thread_goes_out_again(
        tmp_path, home, monkeypatch):
    w = light_world(tmp_path, home, monkeypatch, {"work_sources": ["mr-handoff", "mr-patrol"]})
    gw = FakeGateway()
    gw.script = [[{"role": "assistant", "content": "我先看一下 CI 日志。", "meta": {}},
                  {"role": "error", "content": "model runtime failed", "meta": {"code": "runtime_failed"}}]]
    svc, routes = app(w, tmp_path, gw)
    gl, slot = w.gl, "patrol-42-7"
    gl.mr(P, 7, sha=w.head)
    d = gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 1)

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            gw.finish(slot)
            for _ in range(4):
                await svc.sessions.poll()
            await call(routes, "POST", "/team/loop")
            return await call(routes, "GET", "/team/patrol")
        finally:
            await svc.stop()

    seen = run(go())
    item, = seen.body["items"]
    told = [m["content"] for m in gw.msgs[slot] if m["role"] == "user"]
    assert (item["state"], item["attempts"]) == ("working", 2), item
    assert len(told) == 2 and "评论 #%d" % d["notes"][0]["id"] in told[1]


def test_a_message_kiro_crew_refuses_is_a_failed_attempt_and_two_block_the_mr_with_one_comment(
        tmp_path, home, monkeypatch):
    w = light_world(tmp_path, home, monkeypatch, {"work_sources": ["mr-handoff", "mr-patrol"]})
    gw = FakeGateway()

    async def refuse(key, message, send_id):
        raise GatewayError(403, "this app may not send to that slot", "forbidden")
    gw.send = refuse
    svc, routes = app(w, tmp_path, gw)
    w.gl.mr(P, 7, sha=w.head)
    w.gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 1)

    async def go():
        await svc.start()
        try:
            for _ in range(3):
                await call(routes, "POST", "/team/loop")
            return await call(routes, "GET", "/team/patrol")
        finally:
            await svc.stop()

    seen = run(go())
    item, = seen.body["items"]
    assert (item["state"], item["attempts"], item["slot"]) == ("blocked", 2, "patrol-42-7"), item
    assert [k for k in gw.slot_rows if k.startswith("patrol-")] == ["patrol-42-7"]
    told = w.gl.posted("notes")
    assert len(told) == 1 and told[0]["body"].startswith("MR 巡检：运行机派了 2 次，它的会话都没收下")


def test_a_merged_mr_s_turn_is_stopped_and_its_clone_deleted(tmp_path, home, monkeypatch):
    w = light_world(tmp_path, home, monkeypatch, {"work_sources": ["mr-handoff", "mr-patrol"]})
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)
    for iid in (4, 5):
        w.gl.mr(P, iid, sha=w.head, has_conflicts=True, detailed_merge_status="conflict")

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            made = sorted(os.listdir(w.work / "patrol"))
            w.gl.close(P, 4)
            await call(routes, "POST", "/team/loop")
            return made, sorted(os.listdir(w.work / "patrol"))
        finally:
            await svc.stop()

    made, left = run(go())
    assert made == ["42-4", "42-5"] and left == ["42-5"]
    assert [c[1] for c in gw.calls if c[0] == "stop"] == ["patrol-42-4"]


@pytest.mark.parametrize("kit, polled, patrolled",[({}, True, False), ({"work_sources": ["mr-patrol"]}, False, True)])
def test_work_sources_decide_which_rounds_look_at_a_project(tmp_path, home, monkeypatch, kit, polled, patrolled):
    w = light_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)
    w.gl.mr(P, 4, sha=w.head, has_conflicts=True, detailed_merge_status="conflict")

    async def go():
        await svc.start()
        try:
            loop = await call(routes, "POST", "/team/loop")
            return loop, await call(routes, "GET", "/team/patrol")
        finally:
            await svc.stop()

    loop, view = run(go())
    lists = [c[2] for c in w.gl.calls if c[0] == "GET" and c[1] == "/api/v4/projects/grp/repo/merge_requests"]
    assert loop.body["ok"], loop.body
    assert any(q.get("labels") == "ai-handoff" for q in lists) is polled
    assert any(q.get("with_merge_status_recheck") == "true" for q in lists) is patrolled
    assert view.body["projects"] == [{"project": P, "on": patrolled, "error": ""}]
    assert ("patrol-42-4" in gw.slot_rows) is patrolled
    assert os.path.isdir(w.work / "patrol" / "42-4") is patrolled
