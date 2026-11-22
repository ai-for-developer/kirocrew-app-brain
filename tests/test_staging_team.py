"""测试环境报错 inside the app's team mode, offline: TeamRuntime on the light world of the MR 巡检 team tests, its
bridge.json entry naming the staging log and VERSION file, the in-memory GitLab answering Issues too, the app's own
session runner on an in-memory KiroCrew. A round after an error is logged opens its Issue and gives the fix to a
brain-patrol session of its own, in a real clone at the default branch, told the helper's --issue and --open-mr; GET
/team/staging says so, with the page's keys. Its fixes and MR 巡检's sessions are two at a time together.
"""

import json

import pytest

from appkit import FakeGateway, mod, sh
from brainfix import needs_brainkit, run
from http_fakes import call
from patrolfix import SHA
from stagingfix import StagingGitLab
from test_contract import diff_keys, fixture
from test_patrol_team import P, PID, app, light_world

pytestmark = needs_brainkit


def staging_world(tmp_path, home, monkeypatch, kit):
    """The light world, its GitLab answering Issues too, and bridge.json's entry naming a staging log (empty) and a
    VERSION file (main's commit). -> (the world, the log, main's commit)."""
    w = light_world(tmp_path, home, monkeypatch, kit)
    w.gl = StagingGitLab(w.gl.clock)
    w.gl.project(P, PID, kit=kit)
    main = sh("git", "-C", tmp_path / "origin" / "grp" / "repo.git", "rev-parse", "main").stdout.strip()
    w.gl.heads[(P, "main")] = main
    env = tmp_path / "staging-env"
    env.mkdir()
    (env / "VERSION").write_text(main + "\n")
    log = env / "app.log"
    log.write_text("")
    cfg = json.loads((w.root / "bridge.json").read_text())
    cfg["projects"] = [{"project": P, "staging": {"log": {"kind": "file", "path": str(log)},
                                                  "version": {"kind": "file", "path": str(env / "VERSION")}}}]
    (w.root / "bridge.json").write_text(json.dumps(cfg))
    return w, log, main


def test_a_round_opens_an_issue_for_a_staging_error_and_gives_its_fix_to_a_session_of_its_own(tmp_path, home, monkeypatch):
    kit = {"work_sources": ["mr-handoff", "staging-errors"]}
    w, log, main = staging_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)
    slot = "staging-42-1"

    async def go():
        await svc.start()
        try:
            assert svc.team is not None and svc.team.host is not None, svc.team and svc.team.error
            first = await call(routes, "POST", "/team/loop")
            with open(log, "a") as fh:
                fh.write("2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)\n")
            loop = await call(routes, "POST", "/team/loop")
            return str(mod("bk").root()), first, loop, await call(routes, "GET", "/team/staging")
        finally:
            await svc.stop()

    kit_dir, first, loop, seen = run(go())
    assert first.body["ok"] and first.body["summary"]["staging"] == 1, first.body
    assert loop.body["ok"] and loop.body["summary"]["staging"] == 1, loop.body
    issue, = w.gl.opened(P)
    assert issue["title"] == "测试环境报错：app.pay: gateway timeout after <n>s (order <n>)" and issue["labels"] == ["staging-error"]
    # its session: brain-patrol's own, in the Issue's folder under 「Brain 运行机」, in a real clone at main, trusted
    assert ("agent", "brain-patrol", "template") in gw.calls
    assert [c[1] for c in gw.calls if c[0] == "folder"][:2] == ["Brain 运行机", "巡检 grp/repo#1"]
    ws = w.work / "staging" / "42-1"
    assert gw.slot_rows[slot]["project"] == str(ws) and gw.slot_rows[slot]["trust"]
    assert sh("git", "-C", ws, "rev-parse", "HEAD").stdout.strip() == main
    assert sh("git", "-C", ws, "config", "user.email").stdout.strip() == "brain-bot@noreply.invalid"
    told, = [m["content"] for m in gw.msgs[slot] if m["role"] == "user"]
    helper = "%s -I %s --config %s --brainkit %s --project grp/repo --issue 1" % (
        mod("constants").PYTHON_BIN, mod("constants").APP_ROOT / "backend" / "core" / "team" / "patrol_reply.py",
        w.root / "bridge.json", kit_dir)
    assert helper + " --open-mr --source fix/staging-" in told and helper + " --note '回复的话'" in told
    # the page: the error at work in that session, with the fixture's keys
    item, = seen.body["items"]
    assert (item["state"], item["slot"], item["count"], item["issue"]["iid"]) == ("fixing", slot, 1, 1)
    assert seen.body["projects"] == [{"project": P, "on": True, "error": "", "deployed_sha": main}]
    assert diff_keys(fixture("team-staging.json"), seen.body) == []


def test_mr_patrol_waits_while_two_staging_fixes_are_at_work(tmp_path, home, monkeypatch):
    """Two errors in the test environment's log, each fix at work in its session: an MR with an open review thread
    waits, MR 巡检 counting those two toward its two."""
    kit = {"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]}
    w, log, main = staging_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            with open(log, "a") as fh:
                fh.write("2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)\n"
                         "2026-11-21 10:00:03,000 CRITICAL app.stock: warehouse aaa is gone\n")
            await call(routes, "POST", "/team/loop")
            w.gl.mr(P, 7, sha=w.head)
            w.gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 1)
            loop = await call(routes, "POST", "/team/loop")
            return loop, await call(routes, "GET", "/team/patrol"), await call(routes, "GET", "/team/staging")
        finally:
            await svc.stop()

    loop, patrolled, staged = run(go())
    assert loop.body["ok"] and staged.body["working"] == 2, (loop.body, staged.body)
    assert [i["state"] for i in patrolled.body["items"]] == ["queued"] and patrolled.body["working"] == 0
    assert sorted(k for k in gw.slot_rows if k.startswith(("patrol-", "staging-"))) == ["staging-42-1", "staging-42-2"]


def running(gw):
    """The brain-patrol sessions whose turn runs now."""
    return sorted(k for k, s in gw.slot_rows.items() if k.startswith(("patrol-", "staging-")) and s["running"])


def test_after_team_mode_starts_again_mr_patrol_still_counts_the_staging_fixes_at_work(tmp_path, home, monkeypatch):
    """Two staging fixes at work, then a 设置 save starts team mode again (a new MR 巡检 and 测试环境报错, the old turns
    still running): MR 巡检, whose round comes first, counts them from 测试环境报错's ledger, and its MR waits."""
    kit = {"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]}
    w, log, main = staging_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            with open(log, "a") as fh:
                fh.write("2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)\n"
                         "2026-11-21 10:00:03,000 CRITICAL app.stock: warehouse aaa is gone\n")
            await call(routes, "POST", "/team/loop")
            before = svc.team
            await svc.save_settings({})
            assert svc.team is not before and svc.team.host is not None, svc.team.error
            w.gl.mr(P, 7, sha=w.head)
            w.gl.thread(P, 7, "carol", "百分比要限制在 0-100", "src/discount.py", 1)
            loop = await call(routes, "POST", "/team/loop")
            return loop, await call(routes, "GET", "/team/patrol")
        finally:
            await svc.stop()

    loop, patrolled = run(go())
    assert loop.body["ok"], loop.body
    assert running(gw) == ["staging-42-1", "staging-42-2"]
    assert [i["state"] for i in patrolled.body["items"]] == ["queued"]


@pytest.mark.parametrize("pushed", [1, 2])
def test_mr_patrol_s_sessions_whose_mr_head_moved_count_while_their_turns_run(tmp_path, home, monkeypatch, pushed):
    """MR 巡检's two sessions at work; the runner pushes its fix on *pushed* of the MRs and goes on (its replies, its
    summary): those turns still run. As many staging errors then wait: two sessions at once, not 2 + *pushed*."""
    kit = {"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]}
    w, log, main = staging_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)
    for iid in (1, 2):
        w.gl.mr(P, iid, sha=w.head)
        w.gl.fail(P, iid)

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            for iid in range(1, pushed + 1):
                w.gl.push(P, iid, SHA[4 + iid])
            with open(log, "a") as fh:
                fh.write("".join(["2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)\n",
                                  "2026-11-21 10:00:03,000 CRITICAL app.stock: warehouse aaa is gone\n"][:pushed]))
            loop = await call(routes, "POST", "/team/loop")
            return loop, await call(routes, "GET", "/team/staging")
        finally:
            await svc.stop()

    loop, staged = run(go())
    assert loop.body["ok"], loop.body
    assert running(gw) == ["patrol-42-1", "patrol-42-2"]
    assert [(i["state"], i["note"]) for i in staged.body["items"]] == [
        ("issued", "排队：同时最多 2 个会话在修（MR 巡检的也算）")] * pushed


def test_a_staging_fix_whose_mr_is_open_counts_while_its_turn_runs(tmp_path, home, monkeypatch):
    """The fix session opened its MR and goes on (its reply on the Issue): the row waits on that MR, its turn still
    runs. Two MRs with work for MR 巡检 then get one session, not two."""
    kit = {"work_sources": ["mr-handoff", "mr-patrol", "staging-errors"]}
    w, log, main = staging_world(tmp_path, home, monkeypatch, kit)
    gw = FakeGateway()
    svc, routes = app(w, tmp_path, gw)

    async def go():
        await svc.start()
        try:
            await call(routes, "POST", "/team/loop")
            with open(log, "a") as fh:
                fh.write("2026-11-21 10:00:02,000 ERROR app.pay: gateway timeout after 30s (order 77)\n")
            await call(routes, "POST", "/team/loop")
            branch = "fix/staging-" + mod("team.stagelog").short_id("app.pay: gateway timeout after <n>s (order <n>)")
            w.gl.mr(P, 52, sha=w.head, source=branch)
            await call(routes, "POST", "/team/loop")
            fixing = (await call(routes, "GET", "/team/staging")).body["items"][0]["state"]
            for iid in (1, 2):
                w.gl.mr(P, iid, sha=w.head)
                w.gl.fail(P, iid)
            loop = await call(routes, "POST", "/team/loop")
            return fixing, loop, await call(routes, "GET", "/team/patrol")
        finally:
            await svc.stop()

    fixing, loop, patrolled = run(go())
    assert fixing == "fix_mr" and loop.body["ok"], loop.body
    assert len(running(gw)) == 2 and "staging-42-1" in running(gw), running(gw)
    assert sorted(i["state"] for i in patrolled.body["items"] if i["iid"] in (1, 2)) == ["queued", "working"]


def test_a_rerun_keeps_a_hand_written_staging_block_whose_path_differs_in_case_from_gitlab_s(tmp_path):
    """一键建运行机 run again lists the projects by GitLab's paths: a hand-written entry naming its project in another
    case (GitLab's paths are not case-sensitive) is the same project, kept with its staging block, under GitLab's path."""
    setup = mod("team.setup")
    block = {"log": {"kind": "file", "path": "/srv/staging/pay/app.log"},
             "version": {"kind": "file", "path": "/srv/staging/pay/VERSION"}}
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps({"projects": [{"project": "Grp/Pay", "staging": block}, "grp/old"]}))
    assert setup.kept_entries(path, ["grp/pay", "grp/new"]) == [{"project": "grp/pay", "staging": block}, "grp/new"]
