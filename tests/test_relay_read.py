"""团队运行机 on a personal install reading 状态中转 (GET /team/status, runner_view.Runners), offline: the status project
in memory (relayfix.RepoGitLab), read with the person's own token.

A repo whose kit.json names team_status.project has its snapshot (snapshot.json on main) read, parsed, at most once a
minute for each project however many repos name it; one older than 40 minutes is said to be stale, the team server may
have stopped. A file not there yet, one of another schema or not a snapshot, and a GitLab that does not answer are said
in words, the last good one kept. A repo whose kit.json names none keeps the runner as GitLab shows it, alone."""

import json

import pytest

from appkit import mod
from brainfix import make_project, make_service, needs_brainkit, run
from http_fakes import AppRoute, FakeWeb, call
from relayfix import BASE, RepoGitLab
from test_contract import diff_keys, fixture
from test_relay_write import a_snapshot

pytestmark = needs_brainkit
STATUS, T0, MIN = "grp/brain-status", 1_800_000_000.0, 60


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def app(tmp_path, monkeypatch, *, team_status=True):
    """alice's machine: pay (its kit.json names the bridge and, with team_status, the status project) and web (its
    runner as GitLab shows it), one GitLab."""
    snapshot = mod("snapshot")
    gl = RepoGitLab()
    gl.status(STATUS)
    for path, pid in (("grp/pay", 7), ("grp/web", 8)):
        gl.project(path, pid, tmp_path)
    monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
    kit = {"gitlab": {"base": BASE, "project": "grp/pay"}, "bridge_user": "brain-bridge"}
    if team_status:
        kit["team_status"] = {"project": STATUS}
    pay = make_project(tmp_path, kit=kit)
    web = make_project(tmp_path, name="web", prefix="WEB", kit={"gitlab": {"base": BASE, "project": "grp/web"},
                                                                 "bridge_user": "brain-bridge"})
    svc = make_service(tmp_path, [pay, web], transport=gl)
    clock, tick = Clock(T0), Clock(0.0)
    svc.now = clock
    svc.runners.clock = tick
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    return gl, svc, routes, clock, tick, snapshot


def written(gl, snapshot, generated_at, **kw):
    snap = a_snapshot(generated_at, **kw)
    gl.status_files["main"] = {"snapshot.json": snapshot.dump(snapshot.parse(snap))}
    return snapshot.parse(snap)


def reads(gl):
    return [c for c in gl.calls if c[1].endswith("/repository/files/snapshot.json/raw")]


def statuses(svc, routes, *steps):
    """GET /team/status after each step (a callable run before it, on the event loop's side)."""
    async def go():
        await svc.start()
        try:
            out = []
            for step in steps:
                step()
                got = await call(routes, "GET", "/team/status")
                assert got.status == 200, got.body
                out.append(got)
            return out
        finally:
            await svc.stop()
    return run(go())


def test_a_fresh_snapshot_is_read_with_my_token_for_the_repos_that_name_it(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    snap = written(gl, snapshot, T0 - 5 * MIN)
    got, = statuses(svc, routes, lambda: None)
    body = got.body
    assert (body["at"], body["stale_after_s"]) == (T0, 40 * MIN)
    assert [(r["repo"], r["relay"]) for r in body["repos"]] == [("r1", STATUS), ("r2", None)]
    assert body["repos"][1]["runner"]["project"] == "grp/web"                  # the runner as GitLab shows it, as before
    relay, = body["relays"]
    assert relay == {"project": STATUS, "repos": ["r1"], "me": "alice", "repo_of": {"grp/pay": "r1", "grp/web": "r2"},
                     "read_at": T0, "snapshot": snap, "stale": False, "problem": None}
    (_, path, query, who), = reads(gl)
    assert (path, query, who) == ("/api/v4/projects/%s/repository/files/snapshot.json/raw" % STATUS, {"ref": "main"}, "alice")
    assert "alice-tok" not in got.text
    fix = fixture("team-status.json")
    # its keys, but repo_of's (GitLab projects): every list of the fixture's snapshot holds a row to compare
    assert diff_keys(fix, body, skip=(".relays[].repo_of",)) == []
    assert all(fix["relays"][0]["snapshot"]["runner"][k] for k in ("handovers", "patrol", "staging"))
    assert snapshot.parse(fix["relays"][0]["snapshot"]) == fix["relays"][0]["snapshot"]     # the page's is one, whole


def test_a_snapshot_older_than_forty_minutes_says_the_server_may_have_stopped(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    written(gl, snapshot, T0 - 39 * MIN)

    def later():
        clock.t = T0 + 2 * MIN                     # the same snapshot, 41 minutes old now (read again, the cache over)
        tick.t += 61

    fresh, old = statuses(svc, routes, lambda: None, later)
    assert fresh.body["relays"][0]["stale"] is False
    assert old.body["relays"][0]["stale"] is True and old.body["relays"][0]["snapshot"] == fresh.body["relays"][0]["snapshot"]
    assert old.body["relays"][0]["problem"] is None


def test_a_repo_whose_kit_json_names_no_status_project_keeps_the_runner_as_gitlab_shows_it(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch, team_status=False)
    written(gl, snapshot, T0)
    got, = statuses(svc, routes, lambda: None)
    assert got.body["relays"] == [] and [r["relay"] for r in got.body["repos"]] == [None, None]
    assert [r["runner"]["project"] for r in got.body["repos"]] == ["grp/pay", "grp/web"] and reads(gl) == []


def test_what_cannot_be_read_is_said_and_the_last_good_snapshot_kept_reading_once_a_minute(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    newer = dict(a_snapshot(T0), schema=2)
    good, counted = {}, []

    def step(change=None, *, minute=True):
        def go():
            if minute:
                tick.t += 61
            if change:
                change()
            counted.append(len(reads(gl)))
        return go

    steps = [step(minute=False),                                                       # nothing written yet
             step(lambda: good.update(snap=written(gl, snapshot, T0 - MIN)), minute=False),   # within the minute
             step(),                                                                   # read now
             step(lambda: gl.down.add(STATUS)),                                        # GitLab does not answer
             step(lambda: (gl.down.clear(), gl.status_files["main"].update({"snapshot.json": json.dumps(newer)}))),
             step(lambda: gl.status_files["main"].update({"snapshot.json": "{not json"}))]
    none, cached, read, down, other, broken = (g.body["relays"][0] for g in statuses(svc, routes, *steps))
    assert counted == [0, 1, 1, 2, 3, 4] and len(reads(gl)) == 5                # one read a minute, none in between
    assert (none["snapshot"], none["problem"]["text"]) == (None, "团队服务器还没在 %s 写过状态，或者你看不到这个项目" % STATUS)
    assert "把你加进 %s（Reporter 就够）" % STATUS in none["problem"]["todo"]
    assert cached == none
    assert read["snapshot"] == good["snap"] and read["problem"] is None and read["read_at"] == T0
    for kept, text in ((down, "这次没读到 GitLab"), (other, "团队服务器写的状态是第 2 版，这个 app 读不了"),
                       (broken, "团队服务器写的状态读不懂")):
        assert kept["snapshot"] == good["snap"] and kept["read_at"] == T0 and kept["problem"]["text"] == text, kept["problem"]
    assert "502" in down["problem"]["raw"] and other["problem"]["todo"] == "把这个 app 升级到团队服务器的版本"


def test_two_repos_naming_one_status_project_read_it_once(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    ops = make_project(tmp_path, name="ops", prefix="OPS", kit={"gitlab": {"base": BASE, "project": "grp/ops"},
                                                                 "team_status": {"project": STATUS}})
    svc2 = make_service(tmp_path / "two", [tmp_path / "pay", ops], transport=gl)
    svc2.now = clock
    routes2 = mod("handlers").build_routes(svc2, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    written(gl, snapshot, T0)
    got, = statuses(svc2, routes2, lambda: None)
    relay, = got.body["relays"]
    assert relay["repos"] == ["r1", "r2"] and len(reads(gl)) == 1
    assert [(r["repo"], r["relay"], r["runner"] is None) for r in got.body["repos"]] == [("r1", STATUS, False), ("r2", STATUS, True)]


@pytest.mark.parametrize("kit, path", [({"team_status": {"project": "grp/brain-status"}}, "grp/brain-status"),
                                       ({"team_status": {"project": " grp/brain-status/ "}}, "grp/brain-status"),
                                       ({"team_status": {"project": "brain-status"}}, None),
                                       ({"team_status": "grp/brain-status"}, None), ({}, None)])
def test_the_status_project_is_read_from_kit_json_at_the_boundary(kit, path):
    assert mod("runner_view").relay_of(kit) == path
