"""设置: saved settings reload brain-kit and the repos; a repo that is not a brain-kit repo is reported, not fatal."""

import json

from appkit import BRAINKIT, ROOT, mod
from brainfix import make_project, make_service, needs_brainkit, run

pytestmark = needs_brainkit


def test_saving_settings_reloads_repos_and_reports_bad_ones(tmp_path, home):
    repo = make_project(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    svc = make_service(tmp_path, [])

    async def go():
        await svc.start()
        try:
            first = await svc.status()
            saved = await svc.save_settings({"brainkit": str(BRAINKIT), "repos": [str(repo), str(plain)],
                                             "work_root": str(home / "workplace" / "brain-app"), "team": {"enabled": False}})
            view = await svc.overview("all")
            return first, saved, view
        finally:
            await svc.stop()

    first, saved, view = run(go())
    assert first["repos"] == [] and first["brainkit"]["ok"] and first["brainkit"]["modules"]["trace"] is True
    st = saved["status"]
    assert [r["ok"] for r in st["repos"]] == [True, False] and st["repos"][0]["kit"]["req_prefix"] == "PAY"
    assert "还没装 brain-kit" in st["repos"][1]["error"] and "导入仓库" in st["repos"][1]["error"]
    assert json.loads((tmp_path / "data" / "settings.json").read_text())["repos"][1] == str(plain.resolve())
    assert view["errors"] and view["errors"][0]["repo"] == "r2"
    e = view["errors"][0]    # what happened and what to do in words; the path under 细节
    assert "还没装 brain-kit" in e["error"] and str(plain.resolve()) not in e["error"] and e["raw"] == str(plain.resolve())
    # named and placed as /status says it, for the banner to use when /status cannot be read
    assert (e["name"], e["path"]) == (st["repos"][1]["name"], st["repos"][1]["path"])


def test_a_wrong_brainkit_path_turns_the_app_off_politely(tmp_path, home):
    repo = make_project(tmp_path)
    svc = make_service(tmp_path, [repo])
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"brainkit": str(tmp_path / "nowhere"),
                                                                 "repos": [str(repo)]}))
    svc.settings = __import__("appkit").mod("settings").load(tmp_path / "data" / "settings.json")

    async def go():
        await svc.start()
        try:
            return await svc.status(), await svc.crew_checks()
        finally:
            await svc.stop()

    st, crew = run(go())
    assert st["brainkit"]["ok"] is False and "brain-kit" in st["brainkit"]["error"]
    assert st["repos"][0]["ok"] is False and crew["rows"][0]["level"] == "FAIL"


def test_the_default_brainkit_is_brainkit_home_then_the_one_bundled_in_the_app_then_a_usual_place(tmp_path,
                                                                                                    monkeypatch):
    bk = mod("bk")
    assert bk.BUNDLED == ROOT / "vendor" / "brain-kit"           # what scripts/release.sh puts in the app
    bundled = tmp_path / "app" / "vendor" / "brain-kit"
    (bundled / "brainkit").mkdir(parents=True)
    (bundled / "brainkit" / "__init__.py").write_text("", encoding="utf-8")
    usual = tmp_path / "home" / "brain-kit"
    (usual / "brainkit").mkdir(parents=True)
    (usual / "brainkit" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(bk, "BUNDLED", bundled)
    monkeypatch.setenv("BRAINKIT_HOME", str(tmp_path / "mine"))
    assert bk.default_path() == str(tmp_path / "mine")
    monkeypatch.delenv("BRAINKIT_HOME")
    assert bk.default_path() == str(bundled)
    monkeypatch.setattr(bk, "BUNDLED", tmp_path / "app" / "vendor" / "none")   # a dev checkout: nothing bundled
    assert bk.default_path() == str(usual)


def test_the_team_views_limits_have_defaults_and_bounds_and_reach_the_overview(tmp_path, home):
    settings = mod("settings")
    assert {k: settings.defaults()[k] for k in settings.LIMITS} == {"wip_limit": 3, "wait_timeout_h": 4,
                                                                   "stale_after_h": 8, "verify_fail_limit": 3}
    clean, probs = settings.check({"wip_limit": 2, "wait_timeout_h": 1})
    assert probs == [] and (clean["wip_limit"], clean["wait_timeout_h"], clean["stale_after_h"]) == (2, 1, 8)
    for bad in (0, 21, "3", True, 2.5, None):
        clean, probs = settings.check({"wip_limit": bad})
        assert len(probs) == 1 and "每人同时在做的需求上限" in probs[0] and clean["wip_limit"] == 3
    (tmp_path / "old.json").write_text(json.dumps({"repos": []}))      # a settings.json from before them
    assert {k: settings.load(tmp_path / "old.json")[k] for k in settings.LIMITS} == {
        "wip_limit": 3, "wait_timeout_h": 4, "stale_after_h": 8, "verify_fail_limit": 3}
    repo = make_project(tmp_path)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            saved = await svc.save_settings({"wip_limit": 2, "verify_fail_limit": 5})
            return saved, await svc.overview("all")
        finally:
            await svc.stop()

    saved, view = run(go())
    assert (saved["settings"]["wip_limit"], saved["settings"]["stale_after_h"]) == (2, 8)    # one left out keeps its value
    assert json.loads((tmp_path / "data" / "settings.json").read_text())["verify_fail_limit"] == 5
    assert view["roster"]["limits"] == {"wip_limit": 2, "wait_timeout_h": 4, "stale_after_h": 8, "verify_fail_limit": 5}


def test_an_install_is_personal_unless_marked_the_team_server_and_one_in_team_mode_already_is(tmp_path):
    """settings.role: personal by default; settings of 0.4.2 and before name none, and a machine in team mode then is
    the team server (the live runner keeps running); a team server may have team mode off, a personal install never on."""
    settings = mod("settings")
    assert settings.defaults()["role"] == "personal" and settings.check({})[0]["role"] == "personal"
    team = {"enabled": True, "bridge_config": "/srv/brain/bridge.json", "runnerd_config": "", "dev_same_user": False}
    (tmp_path / "runner.json").write_text(json.dumps({"team": team}))
    (tmp_path / "laptop.json").write_text(json.dumps({"team": {"enabled": False}}))
    runner = settings.load(tmp_path / "runner.json")
    assert (runner["role"], runner["team"]) == ("team_server", team)
    assert settings.load(tmp_path / "laptop.json")["role"] == "personal"
    clean, probs = settings.check({"role": "team_server", "team": dict(team, enabled=False)})
    assert (clean["role"], clean["team"]["enabled"], probs) == ("team_server", False, [])
    clean, probs = settings.check({"role": "personal", "team": team})
    assert (clean["role"], clean["team"]["enabled"]) == ("personal", False)
    assert probs == ["要开团队模式，先在设置最下面勾上「这台机器是团队运行机（服务器）」"]
    clean, probs = settings.check({"role": "boss", "team": team})
    assert clean["role"] == "team_server" and len(probs) == 1 and "这台机器是团队运行机（服务器）" in probs[0]


def test_a_0_4_2_team_server_upgraded_with_team_mode_off_is_still_the_team_server(tmp_path):
    """Its owner had switched 团队模式 off (maintenance) before the upgrade: the bridge's config file it names says
    what the machine is, so 团队模式 and 一键建运行机 stay in 设置."""
    settings = mod("settings")
    team = {"enabled": False, "bridge_config": "/Users/x/.brain/team-setup/bridge.json",
            "runnerd_config": "/Users/x/.brain/team-setup/runnerd.json", "dev_same_user": True}
    (tmp_path / "settings.json").write_text(json.dumps({"repos": [], "team": team}))
    got = settings.load(tmp_path / "settings.json")
    assert (got["role"], got["team"]) == ("team_server", team)
    (tmp_path / "settings.json").write_text(json.dumps({"repos": [], "team": dict(team, bridge_config="")}))
    assert settings.load(tmp_path / "settings.json")["role"] == "personal"


def test_only_the_team_server_builds_a_runner_and_one_made_personal_again_stops_team_mode(tmp_path, home):
    """The role is the app's to read on every page (/status app.role) and changes only when it is sent: the 设置 form
    sends none. A personal install refuses 一键建运行机 and team mode; a team server made personal again has its team
    mode off, its two config files kept for the day it is the server again."""
    from http_fakes import AppRoute, FakeWeb, call
    svc = make_service(tmp_path, [])
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    team = {"enabled": True, "bridge_config": str(tmp_path / "srv" / "bridge.json"), "runnerd_config": "",
            "dev_same_user": False}
    setup = {"gitlab_base": "https://gitlab.example.com", "project": "grp/pay", "admin_token": "one-time", "roles": {}}

    async def go():
        await svc.start()
        try:
            first = (await call(routes, "GET", "/status")).body["app"]
            refused = await call(routes, "POST", "/team/setup", json_body=setup)
            team_on_laptop = await call(routes, "POST", "/settings", json_body={"team": team})
            server = (await call(routes, "POST", "/settings", json_body={"role": "team_server", "team": team})).body
            form = (await call(routes, "POST", "/settings", json_body={"wip_limit": 4})).body
            back = (await call(routes, "POST", "/settings", json_body={"role": "personal"})).body
            return first, refused, team_on_laptop, server, form, back, svc.team
        finally:
            await svc.stop()

    first, refused, team_on_laptop, server, form, back, runtime = run(go())
    assert (first["role"], first["team"]) == ("personal", False)
    assert (refused.status, refused.body["code"]) == (409, "not_team_server")
    assert "这台机器是团队运行机（服务器）" in refused.body["error"]
    assert (team_on_laptop.status, team_on_laptop.body["code"]) == (400, "invalid_settings")
    assert (server["status"]["app"]["role"], server["status"]["app"]["team"]) == ("team_server", True)
    assert (form["settings"]["role"], form["settings"]["team"]["enabled"], form["settings"]["wip_limit"]) == (
        "team_server", True, 4)
    assert (back["status"]["app"]["role"], back["status"]["app"]["team"], runtime) == ("personal", False, None)
    assert back["settings"]["team"] == dict(team, enabled=False)
    saved = json.loads((tmp_path / "data" / "settings.json").read_text())
    assert (saved["role"], saved["team"]["enabled"]) == ("personal", False)
