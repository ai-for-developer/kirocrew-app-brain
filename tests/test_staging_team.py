"""测试环境报错 inside the app's team mode, offline: TeamRuntime on the light world of the MR 巡检 team tests, its
bridge.json entry naming the staging log and VERSION file, the in-memory GitLab answering Issues too, the app's own
session runner on an in-memory KiroCrew. A round after an error is logged opens its Issue and gives the fix to a
brain-patrol session of its own, in a real clone at the default branch, told the helper's --issue and --open-mr; GET
/team/staging says so, with the page's keys.
"""

import json

from appkit import FakeGateway, mod, sh
from brainfix import needs_brainkit, run
from http_fakes import call
from stagingfix import StagingGitLab
from test_contract import diff_keys, fixture
from test_patrol_team import P, PID, app, light_world

pytestmark = needs_brainkit


def test_a_round_opens_an_issue_for_a_staging_error_and_gives_its_fix_to_a_session_of_its_own(tmp_path, home, monkeypatch):
    kit = {"work_sources": ["mr-handoff", "staging-errors"]}
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
