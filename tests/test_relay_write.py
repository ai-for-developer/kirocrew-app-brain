"""状态中转's writing (team/relay.py Relay, in TeamRuntime's round), offline: shop/pay and shop/web on GitLab, each a
bare repo relayfix.RepoGitLab serves with GitLab's commits API, and a clock the test moves.

Each project's snapshot.json is on its own branch brain/status, holding that project's status only. The first write of a
project makes the branch with this machine's git, as the runner pushes: one commit, no parent, a README only; every
commit there says [skip ci] [ci-skip]. A project's snapshot is written when it changed apart from what moves without
news, at most once every 5 minutes, and again unchanged once it is 30 minutes old (the heartbeat). A round builds for
20 seconds at most, the longest-waiting project first; the rest wait for the next round. A relay that starts again
reads the one there and writes nothing new. A file there or not is created or updated, whichever GitLab takes. A write
that fails is said for its project and tried again after the floor; it never stops the round, which goes on with the
hand-overs as before."""

import asyncio
import json
import subprocess
import time
from urllib.parse import unquote, urlsplit

import pytest

from appkit import mod, sh
from brainfix import FakeGateway, make_service, needs_brainkit, run
from relayfix import API, BASE, RepoGitLab, git
from teamfix import world  # noqa: F401  (the fixture)

pytestmark = needs_brainkit
PAY, WEB = "shop/pay", "shop/web"
relay, snapshot = mod("team.relay"), mod("snapshot")


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def a_snapshot(now, *, project=PAY, title="退款审核", round_at=None):
    return {"schema": 1, "generated_at": now, "project": project,
            "server": {"version": "0.4.2", "team_mode": True, "last_round_at": now if round_at is None else round_at,
                       "cap": 2, "working": 0},
            "state": {"on": True, "sources": ["mr-handoff"], "error": ""},
            "runner": {"handovers": [], "patrol": [], "staging": []},
            "delivery": [{"project": project, "key": "PAY-1", "title": title, "owner": "alice", "stage": "需求",
                          "state": "local", "branch": "ai/alice/PAY-1-refund", "mr": None, "deployed": None,
                          "waits": {"who": "people", "do": "none", "text": "alice 在自己的机器上做", "since": None, "on": []},
                          "updated_at": 1_799_999_000.0}],
            "delivery_at": now}


def project(tmp_path, path, pid):
    """A project with main and a commit on it, as every team project has."""
    bare = tmp_path / (path.replace("/", "-") + ".git")
    work = tmp_path / (path.replace("/", "-") + "-work")
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "README.md").write_text("code\n")
    sh("git", "-C", work, "add", "-A")
    sh("git", "-C", work, "commit", "-qm", "init")
    sh("git", "clone", "-q", "--bare", work, bare)
    return bare


@pytest.fixture
def status(tmp_path, home):
    bk = mod("bk")
    from appkit import BRAINKIT
    assert bk.load(str(BRAINKIT))["ok"]
    gl = RepoGitLab()
    for path, pid in ((PAY, 7), (WEB, 8)):
        gl.project(path, pid, project(tmp_path, path, pid))
    clock, tick = Clock(), Clock(0.0)
    work = str(tmp_path / "status.git")

    def make_branch(path):
        relay.push_orphan(gl.repos[path]["bare"], work, "brain-bot", "brain-bot@noreply.invalid")
    rl = relay.Relay(bk.m("rest").GitLab(BASE, "bridge-tok", gl), make_branch, now=clock, clock=tick)
    yield gl, clock, tick, rl
    bk.unload()


def there(gl, path=PAY):
    return snapshot.parse(git(gl.repos[path]["bare"], "show", "brain/status:snapshot.json").stdout)


def log(gl, path=PAY):
    """brain/status's commits, oldest first: (parents, author, message, files)."""
    bare = gl.repos[path]["bare"]
    out = []
    for sha in git(bare, "rev-list", "--reverse", "brain/status").stdout.split():
        parents, author, msg = git(bare, "show", "-s", "--format=%P%x00%an%x00%s", sha).stdout.strip().split("\0")
        out.append((parents.split(), author, msg, git(bare, "ls-tree", "--name-only", sha).stdout.split()))
    return out


def test_a_projects_first_write_makes_brain_status_a_branch_of_its_own_then_writes_only_what_changed(status):
    gl, clock, _tick, rl = status
    title = ["退款审核"]
    built = []

    def build(path, now, deadline, was):
        built.append((path, now))
        return a_snapshot(now, project=path, title=title[0])

    assert rl.round([PAY], build) == {PAY: "wrote"}
    (root, _, made, root_files), (parents, author, said, files) = log(gl)
    assert root == [] and root_files == ["README.md"] and made.endswith("[skip ci] [ci-skip]")       # no parent, no CI
    assert parents == [git(gl.repos[PAY]["bare"], "rev-list", "--max-parents=0", "brain/status").stdout.strip()]
    assert (author, said, files) == ("brain-bridge", "团队状态：更新 [skip ci] [ci-skip]", ["README.md", "snapshot.json"])
    assert gl.status_commits(PAY)[-1]["actions"][0]["action"] == "create" and there(gl)["generated_at"] == clock.t
    assert git(gl.repos[PAY]["bare"], "merge-base", "brain/status", "main", check=False).returncode == 1   # unrelated to the code
    clock.t += 4 * 60                                         # within the floor: nothing built
    assert rl.round([PAY], build) == {PAY: "wait"} and len(built) == 1
    clock.t += 60                                             # five minutes on, the same: built, not written
    assert rl.round([PAY], build) == {PAY: "same"} and len(built) == 2 and len(gl.status_commits(PAY)) == 1
    title[0] = "退款审核（改了标题）"
    clock.t += 60
    assert rl.round([PAY], build) == {PAY: "wait"}            # changed, but the floor holds it
    clock.t += 4 * 60
    assert rl.round([PAY], build) == {PAY: "wrote"} and len(gl.status_commits(PAY)) == 2
    assert gl.status_commits(PAY)[-1]["actions"][0]["action"] == "update"
    assert there(gl)["delivery"][0]["title"] == "退款审核（改了标题）"
    assert rl.view() == {"branch": "brain/status", "projects": [{
        "project": PAY, "written_at": clock.t, "heartbeat": False, "checked_at": clock.t, "error": "", "todo": "", "raw": ""}]}


def test_each_project_gets_its_own_file_holding_only_itself_and_one_failing_stops_no_other(status):
    gl, clock, _tick, rl = status
    gl.protect(WEB, "brain/status")                           # shop/web's rules keep the bridge from its branch
    got = rl.round([PAY, WEB], lambda path, now, deadline, was: a_snapshot(now, project=path))
    assert got == {PAY: "wrote", WEB: "failed"}
    assert {r["project"] for r in there(gl, PAY)["delivery"]} == {PAY} and there(gl, PAY)["project"] == PAY
    web = next(p for p in rl.view()["projects"] if p["project"] == WEB)
    assert web["error"] == "GitLab 不让桥的账号推 brain/status，状态没写成" and "403" in web["raw"]
    assert "Developer 能推 brain/status" in web["todo"] and web["written_at"] is None


def test_the_bridges_last_round_alone_is_no_change_and_half_an_hour_later_the_same_one_is_written_again(status):
    gl, clock, _tick, rl = status
    build = lambda path, now, deadline, was: a_snapshot(now)  # noqa: E731
    start = clock.t
    assert rl.round([PAY], build) == {PAY: "wrote"}
    for _ in range(5):                                         # 25 minutes of rounds: each one later, nothing new
        clock.t += 5 * 60
        assert rl.round([PAY], build) == {PAY: "same"}
    assert len(gl.status_commits(PAY)) == 1
    clock.t += 5 * 60                                          # 30 minutes after the last write
    assert rl.round([PAY], build) == {PAY: "heartbeat"}
    assert gl.status_commits(PAY)[-1]["commit_message"] == "团队状态：没有变化（心跳） [skip ci] [ci-skip]"
    assert there(gl)["generated_at"] == start + 30 * 60
    assert rl.view()["projects"][0]["heartbeat"] is True and rl.view()["projects"][0]["written_at"] == start + 30 * 60
    clock.t += 5 * 60
    assert rl.round([PAY], build) == {PAY: "same"}
    assert relay.content_key(a_snapshot(1.0)) == relay.content_key(a_snapshot(2.0, round_at=9.0))
    assert relay.content_key(a_snapshot(1.0)) != relay.content_key(a_snapshot(1.0, title="别的"))


def busy(now, n):
    """A quiet team's snapshot at round *n*: a dismissed staging error the log shows again every round (its count and
    when it was last seen move, as staging.seen does) and an open MR patrol keeps seeing touched (its updated_at)."""
    snap = a_snapshot(now)
    snap["runner"]["staging"] = [{
        "project": PAY, "signature": "TimeoutError: upstream <n> ms", "count": 10 + n, "state": "dismissed",
        "issue": None, "origin": None, "fix_mr": None, "first_seen": 1_799_000_000.0, "last_seen": now,
        "deployed_sha": "a" * 40, "attempts": 0, "slot": None, "note": "人关了这个 Issue，或者打了 wontfix 标签：不会再打开，也不修"}]
    snap["runner"]["patrol"] = [{
        "project": PAY, "iid": 52, "title": "折扣", "url": None, "author": "bob", "state": "ok", "problems": [],
        "skip": "", "skip_code": "", "note": "", "attempts": 0, "slot": None,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}]
    return snap


def test_a_quiet_team_whose_dismissed_error_keeps_coming_back_writes_the_heartbeat_only(status):
    """The reviewer's churn run: 3 hours of 60 s rounds. What moves every round without news (a staging error's count
    and last_seen, a patrol MR's updated_at, when the rows were read) is written with a real change or the heartbeat
    only."""
    gl, clock, _tick, rl = status
    for n in range(180):
        rl.round([PAY], lambda path, now, deadline, was: busy(now, n))
        clock.t += 60
    said = [c["commit_message"] for c in gl.status_commits(PAY)]
    assert said == ["团队状态：更新 [skip ci] [ci-skip]"] + ["团队状态：没有变化（心跳） [skip ci] [ci-skip]"] * 5, said
    staging, = there(gl)["runner"]["staging"]
    assert staging["count"] == 10 + 150 and staging["last_seen"] == there(gl)["generated_at"]     # the last heartbeat's
    snap = busy(clock.t, 1)
    snap["runner"]["staging"][0]["state"] = "issued"
    assert relay.content_key(snap) != relay.content_key(busy(clock.t, 1))                       # a real change is one


def test_a_round_builds_for_twenty_seconds_the_longest_waiting_first_and_the_rest_wait(status):
    gl, clock, tick, rl = status
    order = []

    def slow(path, now, deadline, was):
        order.append(path)
        tick.t += 21                                            # each project's rows take 21 s to read
        return a_snapshot(now, project=path)
    assert rl.round([PAY, WEB], slow) == {PAY: "wrote", WEB: "later"}
    clock.t += 60                                               # the next round: shop/web, never built, goes
    assert rl.round([PAY, WEB], slow) == {PAY: "wait", WEB: "wrote"} and order == [PAY, WEB]
    clock.t += 5 * 60                                           # both due: shop/pay was built longer ago
    assert rl.round([WEB, PAY], slow) == {PAY: "same", WEB: "later"} and order == [PAY, WEB, PAY]


def test_a_relay_that_starts_again_reads_the_one_there_and_writes_nothing_new(status):
    gl, clock, tick, rl = status
    build = lambda path, now, deadline, was: a_snapshot(now)  # noqa: E731
    assert rl.round([PAY], build) == {PAY: "wrote"}
    written = clock.t
    again = relay.Relay(rl.gl, rl.make_branch, now=clock, clock=tick)
    clock.t += 60                                               # a restart a minute later: its first round builds
    seen = []
    assert again.round([PAY], lambda path, now, deadline, was: seen.append(was) or a_snapshot(now)) == {PAY: "same"}
    assert len(gl.status_commits(PAY)) == 1 and again.view()["projects"][0]["written_at"] == written
    assert seen == [there(gl)]                                  # the one there, for its rows if a build runs out of time
    clock.t = written + 30 * 60                                  # the heartbeat counts from the one there
    assert again.round([PAY], build) == {PAY: "heartbeat"} and len(gl.status_commits(PAY)) == 2


def test_a_file_there_or_not_and_a_branch_deleted_are_written_whichever_gitlab_takes(status):
    gl, clock, _tick, rl = status
    bare = gl.repos[PAY]["bare"]
    gl.put_status(PAY, "not a snapshot\n")                      # something there, unreadable
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now)) == {PAY: "wrote"}
    assert gl.status_commits(PAY)[-1]["actions"][0]["action"] == "update" and there(gl)["schema"] == 1
    rl.files[PAY].there = False                                  # it thinks the file is gone, it is not
    clock.t += 5 * 60
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now, title="新")) == {PAY: "wrote"}
    assert [c["actions"][0]["action"] for c in gl.status_commits(PAY)] == ["update", "update"]     # the create refused, then this
    sh("git", "-C", bare, "update-ref", "-d", "refs/heads/brain/status")       # someone deleted the branch
    clock.t += 5 * 60
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now, title="再新")) == {PAY: "failed"}
    clock.t += 5 * 60                                            # read again: made again, a branch of its own
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now, title="再新")) == {PAY: "wrote"}
    assert log(gl)[0][0] == [] and there(gl)["delivery"][0]["title"] == "再新"


def test_a_write_gitlab_refuses_is_said_and_tried_again_after_the_floor(status):
    gl, clock, _tick, rl = status
    build = lambda path, now, deadline, was: a_snapshot(now)  # noqa: E731
    gl.fail_writes = 1
    assert rl.round([PAY], build) == {PAY: "failed"}
    v = rl.view()["projects"][0]
    assert (v["error"], v["written_at"]) == ("这一轮没写成这个项目的状态", None) and "500" in v["raw"]
    assert "5 分钟以后会再写" in v["todo"]
    clock.t += 60
    assert rl.round([PAY], build) == {PAY: "wait"}
    clock.t += 4 * 60
    assert rl.round([PAY], build) == {PAY: "wrote"} and rl.view()["projects"][0]["error"] == ""
    gl.who.pop("bridge-tok")                                             # the token revoked
    clock.t += 5 * 60
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now, title="新")) == {PAY: "failed"}
    assert rl.view()["projects"][0]["error"] == "GitLab 不认桥的令牌，状态没写成"


def test_without_a_way_to_push_the_branch_it_is_said_for_the_project(status):
    gl, clock, tick, rl = status
    common = mod("bk").m("common")

    def cannot(path):
        raise common.BrainError("这台机器不推代码")
    rl = relay.Relay(rl.gl, cannot, now=clock, clock=tick)
    assert rl.round([PAY], lambda path, now, deadline, was: a_snapshot(now)) == {PAY: "failed"}
    v = rl.view()["projects"][0]
    assert (v["error"], v["raw"]) == ("这一轮没写成这个项目的状态", "这台机器不推代码") and gl.status_commits(PAY) == []


def test_a_push_that_git_refuses_says_why_without_the_token_in_the_address(tmp_path, status):
    common = mod("bk").m("common")
    with pytest.raises(common.BrainError) as got:
        relay.push_orphan("https://oauth2:glpat-SECRET123456789@127.0.0.1:1/shop/pay.git", str(tmp_path / "w.git"),
                          "brain-bot", "brain-bot@noreply.invalid")             # nothing listens there: refused at once
    assert str(got.value).startswith("brain/status 没推上去：") and "SECRET" not in str(got.value)


def test_a_snapshot_that_is_not_one_is_not_written(status):
    gl, clock, _tick, rl = status
    bad = a_snapshot(clock.t)
    del bad["server"]["cap"]
    assert rl.round([PAY], lambda path, now, deadline, was: bad) == {PAY: "failed"}
    v = rl.view()["projects"][0]
    assert v["error"] == "这一轮做出来的状态不对，没有写" and gl.status_commits(PAY) == []
    assert "server: missing cap" in v["raw"]


# ---- in the bridge's round ---------------------------------------------------------------------------------------- #

RELAYED = ("/repository/commits", "/repository/branches", "/repository/tree", "/repository/blobs", "/merge_base",
           "/pipelines")


class Both:
    """The team world's GitLab (teamfix.GitLab, grp/repo): the hand-over's MRs, notes and files as before; what the
    relay reads and writes beside them (branches, trees, blobs, commits) from the same bare repo (relayfix)."""

    def __init__(self, code, relayed) -> None:
        self.code, self.relayed = code, relayed

    def __call__(self, method, url, headers, body):
        path = unquote(urlsplit(url).path)
        sub = path[len(API + "/projects/grp/repo"):] if path.startswith(API + "/projects/grp/repo/") else ""
        if sub.startswith(RELAYED) and not (sub.startswith("/repository/commits/") and method == "GET"):
            return self.relayed(method, url, headers, body)
        return self.code(method, url, headers, body)


def relaying(world, tmp_path):
    rg = RepoGitLab()
    rg.project("grp/repo", 1, world["gl"].bare)
    svc = make_service(tmp_path / "app", [], gw=FakeGateway(), team=world["team"], transport=Both(world["gl"], rg),
                       kick=lambda: world["signer"].once())
    return svc, rg


@pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode, reason="ssh-keygen not found")
def test_a_failed_write_never_stops_the_round_and_the_next_one_relays_the_runner(world, tmp_path):
    svc, rg = relaying(world, tmp_path)
    rg.fail_writes = 1                                  # the first round's write: GitLab answers 500
    clock = Clock()

    async def go():
        await svc.start()
        try:
            rt = svc.team
            assert rt is not None and rt.host is not None and rt.relay is not None, rt.error
            for _ in range(600):                        # the service's own first round, right after the start, done
                if rt.last_summary:
                    break
                await asyncio.sleep(0.05)
            first = (dict(rt.last_summary), rt.loop_error, rt.ledger(), (await svc.status())["team"])
            rt.relay.now = clock                        # its next round comes in 60 s: this test's rounds are alone
            second = await svc.team_loop()
            return first, second, (await svc.status())["team"]
        finally:
            await svc.stop()

    (summary, loop_error, led, status), second, after = run(go())
    # the round whose write failed took the hand-over as without the relay; only the relay's line says it did not write
    assert summary.get("poll") == 1 and summary["relay"] == 0 and "relay" not in loop_error, (summary, loop_error)
    assert [r["req"] for r in led["runs"]] == ["PAY-1"]
    mine, = status["relay"]["projects"]
    assert mine["project"] == "grp/repo" and mine["error"] == "这一轮没写成这个项目的状态" and "500" in mine["raw"]
    assert status["relay"]["branch"] == "brain/status" and mine["written_at"] is None
    from test_contract import diff_keys, fixture
    assert diff_keys(fixture("team.json")["status"]["relay"], status["relay"]) == []
    assert second["summary"]["relay"] == 1, after["relay"]
    assert after["relay"]["projects"][0]["error"] == "" and after["relay"]["projects"][0]["written_at"] == clock.t
    snap = snapshot.parse(git(world["gl"].bare, "show", "brain/status:snapshot.json").stdout)
    assert (snap["project"], snap["server"]["version"], snap["server"]["team_mode"]) == (
        "grp/repo", mod("constants").VERSION, True)
    assert (snap["state"]["on"], snap["state"]["sources"]) == (True, ["mr-handoff"])
    run_row, = snap["runner"]["handovers"]
    assert (run_row["key"], run_row["run_id"], run_row["project"], run_row["iid"], run_row["owner"]) == (
        "PAY-1", led["runs"][0]["run_id"], "grp/repo", 5, "alice")
    assert run_row["state"] == led["runs"][0]["state"] and run_row["waits"]["text"]
    assert snap["runner"]["patrol"] == [] and snap["server"]["cap"] == 2
    root = git(world["gl"].bare, "rev-list", "--max-parents=0", "brain/status").stdout.split()
    assert len(root) == 1 and git(world["gl"].bare, "ls-tree", "--name-only", root[0]).stdout.split() == ["README.md"]


@pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode, reason="ssh-keygen not found")
def test_rows_not_read_whole_in_time_carry_the_last_read_with_when_and_why(world, tmp_path):
    svc, rg = relaying(world, tmp_path)

    async def go():
        await svc.start()
        try:
            rt = svc.team
            for _ in range(600):
                if rt.last_summary:
                    break
                await asyncio.sleep(0.05)
            last = rt.deliveries.last["grp/repo"]

            def late(*_a, **_kw):
                raise relay.OutOfTime("grp/repo")
            rt.deliveries.read = late
            now = last.at + 600
            carried = rt.snapshot_of("grp/repo", now, 0.0, None, rt._shared())
            rt.deliveries.last.clear()                  # after a restart: what its brain/status holds
            was = dict(carried, delivery=[dict(carried["delivery"][0], title="写在分支上的")], delivery_at=last.at - 60)
            from_there = rt.snapshot_of("grp/repo", now, 0.0, was, rt._shared())
            return last, carried, from_there
        finally:
            await svc.stop()

    last, carried, from_there = run(go())
    assert last.rows and carried["delivery"] == last.rows and carried["delivery_at"] == last.at
    assert carried["state"]["error"] == "全队交付这一轮没读完（20 秒到了），下一轮接着读：下面是上次读完的"
    assert from_there["delivery"][0]["title"] == "写在分支上的" and from_there["delivery_at"] == last.at - 60
    assert snapshot.parse(json.loads(json.dumps(carried))) == carried


def test_a_labelled_mr_from_or_into_brain_status_is_never_taken_for_a_hand_over(status, tmp_path):
    """The hand-over poll (TeamRuntime._poll) gives brain-kit's bridge the labelled MRs, never one of 状态中转's branch."""
    from types import SimpleNamespace
    bridgehost = mod("team.bridgehost")
    taken = []
    mrs = [{"iid": 5, "sha": "a" * 40, "source_branch": "ai/alice/PAY-1-refund", "target_branch": "main"},
           {"iid": 6, "sha": "b" * 40, "source_branch": "brain/status", "target_branch": "main"},
           {"iid": 7, "sha": "c" * 40, "source_branch": "ai/bob/PAY-2-x", "target_branch": "brain/status"}]
    host = SimpleNamespace(cfg={"projects": ["grp/repo"]}, out=lambda *_: None,
                           kit=lambda p: {"handoff_label": "ai-handoff", "work_sources": ["mr-handoff"]},
                           gl=SimpleNamespace(mrs_with_label=lambda p, label: mrs),
                           process=lambda project, kit, mr: taken.append(mr["iid"]) or "accepted")
    rt = bridgehost.TeamRuntime({"enabled": True, "bridge_config": str(tmp_path / "bridge.json")}, None, None, None)
    try:
        assert rt._poll(host) == 1
    finally:
        rt.executor.shutdown(wait=False)
    assert taken == [5] and [h["iid"] for h in rt.handovers()] == [5]
