"""团队运行机 on a personal install reading 状态中转 (GET /team/status, runner_view.Runners), offline: each project a bare
repo relayfix.RepoGitLab serves, its state on its own brain/status, read with the person's own token.

A repo whose project has brain/status has its snapshot.json there read, parsed, at most once a minute, and the projects
of one GitLab are put together: the person sees the projects they can read and nothing of any other, whatever a
snapshot holds. One older than 75 minutes (two heartbeats missed) is said to be stale. A file not written yet, one of
another schema, of another project or not a snapshot, and a GitLab that does not answer are said in words on the
project, its last good one kept. A repo whose project has no brain/status keeps the runner as GitLab shows it, alone."""

import json
import subprocess

import pytest

from appkit import mod, sh
from brainfix import make_project, make_service, needs_brainkit, run
from http_fakes import AppRoute, FakeWeb, call
from relayfix import BASE, RepoGitLab
from test_contract import diff_keys, fixture
from test_relay_write import a_snapshot

pytestmark = needs_brainkit
T0, MIN = 1_800_000_000.0, 60
PAY, WEB, HR = "grp/pay", "grp/web", "hr/payroll"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def bare_of(tmp_path, path):
    """GitLab's copy of *path*: main with a commit."""
    work = tmp_path / ("gl-" + path.replace("/", "-"))
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("code\n")
    sh("git", "-C", work, "add", "-A")
    sh("git", "-C", work, "commit", "-qm", "init")
    bare = tmp_path / ("gl-" + path.replace("/", "-") + ".git")
    sh("git", "clone", "-q", "--bare", work, bare)
    return bare


def app(tmp_path, monkeypatch, *, listed=(PAY, WEB)):
    """alice's machine: pay and web, each a repo whose kit.json names the bridge, one GitLab; hr/payroll is there too,
    a project alice is no member of."""
    snapshot = mod("snapshot")
    gl = RepoGitLab()
    for path, pid in ((PAY, 7), (WEB, 8), (HR, 9)):
        gl.project(path, pid, bare_of(tmp_path, path))
    gl.access(HR, "carol")
    monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
    repos = [make_project(tmp_path, name=path.split("/")[1], prefix=path.split("/")[1].upper()[:3],
                          kit={"gitlab": {"base": BASE, "project": path}, "bridge_user": "brain-bridge"}) for path in listed]
    svc = make_service(tmp_path, repos, transport=gl)
    clock, tick = Clock(T0), Clock(0.0)
    svc.now = clock
    svc.runners.clock = tick
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    return gl, svc, routes, clock, tick, snapshot


def written(gl, snapshot, project, generated_at, **kw):
    snap = snapshot.parse(a_snapshot(generated_at, project=project, **kw))
    gl.put_status(project, snapshot.dump(snap))
    return snap


def reads(gl, project=None):
    return [c for c in gl.calls if c[1].endswith("/repository/files/snapshot.json/raw")
            and (project is None or c[1].startswith("/api/v4/projects/%s/" % project))]


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


def test_each_projects_state_is_read_with_my_token_from_its_own_branch_and_put_together(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    pay = written(gl, snapshot, PAY, T0 - 5 * MIN)
    web = written(gl, snapshot, WEB, T0 - 2 * MIN, title="首页")
    got, = statuses(svc, routes, lambda: None)
    body = got.body
    assert (body["at"], body["stale_after_s"]) == (T0, 75 * MIN)
    assert [(r["repo"], r["relay"]) for r in body["repos"]] == [("r1", True), ("r2", True)]
    relay, = body["relays"]
    assert (relay["repos"], relay["me"], relay["repo_of"], relay["stale"]) == (["r1", "r2"], "alice", {PAY: "r1", WEB: "r2"}, False)
    assert [(p["project"], p["repo"], p["written_at"], p["read_at"], p["stale"], p["problem"]) for p in relay["projects"]] == [
        (PAY, "r1", T0 - 5 * MIN, T0, False, None), (WEB, "r2", T0 - 2 * MIN, T0, False, None)]
    assert relay["projects"][0]["state"] == pay["state"] and relay["projects"][0]["version"] == "0.4.2"
    view = relay["view"]
    assert (view["generated_at"], view["server"]) == (web["generated_at"], web["server"])
    assert view["delivery"] == pay["delivery"] + web["delivery"] and view["runner"] == {"handovers": [], "patrol": [], "staging": []}
    assert [(path, query, who) for _, path, query, who in reads(gl)] == [
        ("/api/v4/projects/%s/repository/files/snapshot.json/raw" % p, {"ref": "brain/status"}, "alice") for p in (PAY, WEB)]
    assert "alice-tok" not in got.text
    fix = fixture("team-status.json")
    assert diff_keys(fix, body, skip=(".relays[].repo_of",)) == []
    assert all(fix["relays"][0]["view"]["runner"][k] for k in ("handovers", "patrol", "staging"))


def test_a_person_sees_the_projects_gitlab_lets_them_read_and_nothing_of_any_other(tmp_path, home, monkeypatch):
    """The review's F1 case: hr/payroll's state is on hr/payroll's own brain/status, which alice's token cannot read;
    her page has grp/pay's alone, even when (a writer's bug) grp/pay's snapshot names a row of hr/payroll."""
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch, listed=(PAY,))
    written(gl, snapshot, HR, T0 - MIN, title="工资单")
    snap = a_snapshot(T0 - MIN, project=PAY)
    snap["delivery"].append(dict(snap["delivery"][0], project=HR, key="HR-1", title="工资单"))
    gl.put_status(PAY, snapshot.dump(snapshot.parse(snap)))
    asked = {}

    def straight():                                     # what GitLab answers each of them for hr/payroll's, asked straight
        asked["by her app"] = reads(gl, HR)
        for who in ("alice", "carol"):
            asked[who] = mod("bk").m("rest").GitLab(BASE, who + "-tok", gl).file_raw(HR, "snapshot.json", "brain/status")
    got, _ = statuses(svc, routes, lambda: None, straight)
    relay, = got.body["relays"]
    assert [p["project"] for p in relay["projects"]] == [PAY]
    assert [(r["project"], r["key"]) for r in relay["view"]["delivery"]] == [(PAY, "PAY-1")]
    assert "工资单" not in got.text and HR not in got.text
    assert asked["alice"] is None and snapshot.parse(asked["carol"])["delivery"][0]["title"] == "工资单"
    assert asked["by her app"] == []


def test_a_snapshot_two_heartbeats_old_says_the_server_may_have_stopped(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch, listed=(PAY,))
    written(gl, snapshot, PAY, T0 - 70 * MIN)       # a heartbeat missed, the next one late: the server still at work

    def later():
        clock.t = T0 + 6 * MIN                     # the same snapshot, 76 minutes old now (read again, the cache over)
        tick.t += 61

    fresh, old = statuses(svc, routes, lambda: None, later)
    assert fresh.body["relays"][0]["stale"] is False and fresh.body["relays"][0]["projects"][0]["stale"] is False
    assert old.body["relays"][0]["stale"] is True and old.body["relays"][0]["projects"][0]["stale"] is True
    assert old.body["relays"][0]["view"] == fresh.body["relays"][0]["view"]
    assert old.body["relays"][0]["projects"][0]["problem"] is None


def test_a_repo_whose_project_has_no_brain_status_keeps_the_runner_as_gitlab_shows_it(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch)
    written(gl, snapshot, PAY, T0)
    got, = statuses(svc, routes, lambda: None)
    assert [(r["repo"], r["relay"]) for r in got.body["repos"]] == [("r1", True), ("r2", False)]
    assert [p["project"] for p in got.body["relays"][0]["projects"]] == [PAY]
    assert got.body["repos"][1]["runner"]["project"] == WEB


def test_what_cannot_be_read_is_said_on_its_project_and_the_last_good_one_kept_reading_once_a_minute(
        tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch, listed=(PAY,))
    bare = gl.repos[PAY]["bare"]
    newer = dict(a_snapshot(T0, project=PAY), schema=2)
    good, counted = {}, []

    def step(change=None, *, minute=True):
        def go():
            if minute:
                tick.t += 61
            if change:
                change()
            counted.append(len(reads(gl)))
        return go

    steps = [step(minute=False),                                                          # no brain/status yet
             step(lambda: sh("git", "-C", bare, "branch", "brain/status", "main"), minute=False),   # within the minute
             step(),                                                                       # the branch, no file yet
             step(lambda: good.update(snap=written(gl, snapshot, PAY, T0 - MIN))),        # read now
             step(lambda: gl.down.add(PAY)),                                               # GitLab does not answer
             step(lambda: (gl.down.clear(), gl.put_status(PAY, json.dumps(newer)))),
             step(lambda: gl.put_status(PAY, "{not json")),
             step(lambda: gl.put_status(PAY, snapshot.dump(snapshot.parse(a_snapshot(T0, project=WEB)))))]
    got = [g.body for g in statuses(svc, routes, *steps)]
    assert counted == [0, 1, 1, 2, 3, 4, 5, 6] and len(reads(gl)) == 7                 # one read a minute, none in between
    assert [g["relays"] for g in got[:2]] == [[], []] and [g["repos"][0]["relay"] for g in got[:3]] == [False, False, True]
    none = got[2]["relays"][0]["projects"][0]
    assert (none["written_at"], none["problem"]["text"]) == (None, "团队服务器还没在这个项目的 brain/status 上写过状态")
    assert got[2]["relays"][0]["view"] is None
    read = got[3]["relays"][0]
    assert read["view"]["delivery"] == good["snap"]["delivery"] and read["projects"][0]["problem"] is None
    for body, text in ((got[4], "这次没读到 GitLab"), (got[5], "团队服务器写的状态是第 2 版，这个 app 读不了"),
                       (got[6], "团队服务器写的状态读不懂"), (got[7], "团队服务器写的状态读不懂")):
        kept = body["relays"][0]
        assert kept["view"] == read["view"] and kept["projects"][0]["read_at"] == T0, kept["projects"][0]
        assert kept["projects"][0]["problem"]["text"] == text and body["repos"][0]["relay"] is True
    assert "502" in got[4]["relays"][0]["projects"][0]["problem"]["raw"]
    assert got[5]["relays"][0]["projects"][0]["problem"]["todo"] == "把这个 app 升级到团队服务器的版本"
    assert got[7]["relays"][0]["projects"][0]["problem"]["raw"] == "project: %s" % WEB


def test_two_checkouts_of_one_project_read_it_once_and_show_it_once(tmp_path, home, monkeypatch):
    gl, svc, routes, clock, tick, snapshot = app(tmp_path, monkeypatch, listed=(PAY,))
    again = make_project(tmp_path / "second", name="pay", prefix="PAY",
                         kit={"gitlab": {"base": BASE, "project": PAY}, "bridge_user": "brain-bridge"})
    svc2 = make_service(tmp_path / "two", [tmp_path / "pay", again], transport=gl)
    svc2.now = clock
    routes2 = mod("handlers").build_routes(svc2, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    written(gl, snapshot, PAY, T0)
    got, = statuses(svc2, routes2, lambda: None)
    relay, = got.body["relays"]
    assert relay["repos"] == ["r1", "r2"] and [p["project"] for p in relay["projects"]] == [PAY] and len(reads(gl)) == 1
    assert len(relay["view"]["delivery"]) == 1


@pytest.mark.parametrize("snap_project, said", [(PAY, None), (WEB, "project: grp/web")])
def test_a_snapshot_is_the_projects_own_or_not_shown(snap_project, said):
    """runner_view.read_relay at its boundary: the project's own snapshot, or kept out with why."""
    rv, snapshot = mod("runner_view"), mod("snapshot")

    class One:
        def file_raw(self, project, path, ref):
            return snapshot.dump(snapshot.parse(a_snapshot(T0, project=snap_project))).encode()

    import unittest.mock as um
    with um.patch.object(mod("mrinfo"), "client", lambda base, transport: One()):
        got = rv.read_relay(BASE, PAY, T0, None, rv.Relayed(None, None, None, None))
    assert (got.problem.raw if got.problem else None) == said
    assert (got.snapshot is not None) == (said is None)
