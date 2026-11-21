"""状态中转's writing (team/relay.py Relay, in TeamRuntime's round), offline: the status project in memory
(relayfix.RepoGitLab.status), a clock the test moves.

snapshot.json is written on branch main when its content changed apart from when it was made and the bridge's last
round, at most once every 5 minutes, and written again unchanged once it is 30 minutes old (the heartbeat). A relay that
starts again reads the one there and writes nothing new. A file there or not is created or updated, whichever GitLab
takes. A write that fails is said on the server's 运行机 page and tried again after the floor; it never stops the
round, which goes on with the hand-overs as before."""

import asyncio
import json
import subprocess
import time
from urllib.parse import unquote, urlsplit

import pytest

from appkit import mod
from brainfix import FakeGateway, make_service, needs_brainkit, run
from relayfix import API, BASE, RepoGitLab
from teamfix import world  # noqa: F401  (the fixture)

pytestmark = needs_brainkit
STATUS = "grp/brain-status"
relay, snapshot = mod("team.relay"), mod("snapshot")


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def a_snapshot(now, *, title="退款审核", round_at=None):
    return {"schema": 1, "generated_at": now,
            "server": {"version": "0.4.2", "team_mode": True, "last_round_at": now if round_at is None else round_at,
                       "projects": [{"project": "shop/pay", "on": True, "sources": ["mr-handoff"], "error": ""}]},
            "runner": {"handovers": [], "patrol": [], "staging": [], "cap": 2, "working": 0},
            "delivery": [{"project": "shop/pay", "key": "PAY-1", "title": title, "owner": "alice", "stage": "需求",
                          "state": "local", "branch": "ai/alice/PAY-1-refund", "mr": None, "deployed": None,
                          "waits": {"who": "people", "do": "none", "text": "alice 在自己的机器上做", "since": None, "on": []},
                          "updated_at": 1_799_999_000.0}]}


@pytest.fixture
def status(home):
    bk = mod("bk")
    from appkit import BRAINKIT
    assert bk.load(str(BRAINKIT))["ok"]
    gl = RepoGitLab()
    gl.status(STATUS)
    clock = Clock()
    rl = relay.Relay(lambda: bk.m("rest").GitLab(BASE, "bridge-tok", gl), STATUS, now=clock)
    yield gl, clock, rl
    bk.unload()


def there(gl):
    return snapshot.parse(gl.status_files["main"]["snapshot.json"])


def test_a_new_snapshot_is_written_then_only_when_it_changed_at_most_every_five_minutes(status):
    gl, clock, rl = status
    title = ["退款审核"]
    built = []

    def build(now):
        built.append(now)
        return a_snapshot(now, title=title[0])

    assert rl.round(build) == "wrote"
    first = gl.status_commits[-1]
    assert first["branch"] == "main" and first["actions"][0]["action"] == "create"
    assert first["actions"][0]["file_path"] == "snapshot.json" and there(gl)["generated_at"] == clock.t
    clock.t += 4 * 60                                         # within the floor: nothing built
    assert rl.round(build) == "wait" and len(built) == 1
    clock.t += 60                                             # five minutes on, the same: built, not written
    assert rl.round(build) == "same" and len(built) == 2 and len(gl.status_commits) == 1
    title[0] = "退款审核（改了标题）"
    clock.t += 60
    assert rl.round(build) == "wait"                          # changed, but the floor holds it
    clock.t += 4 * 60
    assert rl.round(build) == "wrote" and len(gl.status_commits) == 2
    second = gl.status_commits[-1]
    assert second["actions"][0]["action"] == "update" and second["commit_message"] == "团队状态：更新"
    assert there(gl)["delivery"][0]["title"] == "退款审核（改了标题）"
    assert rl.view() == {"project": STATUS, "written_at": clock.t, "heartbeat": False, "checked_at": clock.t,
                         "error": "", "todo": "", "raw": ""}


def test_the_bridges_last_round_alone_is_no_change_and_half_an_hour_later_the_same_one_is_written_again(status):
    gl, clock, rl = status
    start = clock.t
    assert rl.round(lambda now: a_snapshot(now)) == "wrote"
    for _ in range(5):                                         # 25 minutes of rounds: each one later, nothing new
        clock.t += 5 * 60
        assert rl.round(lambda now: a_snapshot(now)) == "same"
    assert len(gl.status_commits) == 1
    clock.t += 5 * 60                                          # 30 minutes after the last write
    assert rl.round(lambda now: a_snapshot(now)) == "heartbeat"
    beat = gl.status_commits[-1]
    assert beat["commit_message"] == "团队状态：没有变化（心跳）" and there(gl)["generated_at"] == start + 30 * 60
    assert rl.view()["heartbeat"] is True and rl.view()["written_at"] == start + 30 * 60
    clock.t += 5 * 60
    assert rl.round(lambda now: a_snapshot(now)) == "same"
    assert relay.content_key(a_snapshot(1.0)) == relay.content_key(a_snapshot(2.0, round_at=9.0))
    assert relay.content_key(a_snapshot(1.0)) != relay.content_key(a_snapshot(1.0, title="别的"))


def busy(now, n):
    """A quiet team's snapshot at round *n*: a dismissed staging error the log shows again every round (its count and
    when it was last seen move, as staging.seen does) and an open MR patrol keeps seeing touched (its updated_at)."""
    snap = a_snapshot(now)
    snap["runner"]["staging"] = [{
        "project": "shop/pay", "signature": "TimeoutError: upstream <n> ms", "count": 10 + n, "state": "dismissed",
        "issue": None, "origin": None, "fix_mr": None, "first_seen": 1_799_000_000.0, "last_seen": now,
        "deployed_sha": "a" * 40, "attempts": 0, "slot": None, "note": "人关了这个 Issue，或者打了 wontfix 标签：不会再打开，也不修"}]
    snap["runner"]["patrol"] = [{
        "project": "shop/pay", "iid": 52, "title": "折扣", "url": None, "author": "bob", "state": "ok", "problems": [],
        "skip": "", "skip_code": "", "note": "", "attempts": 0, "slot": None,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}]
    return snap


def test_a_quiet_team_whose_dismissed_error_keeps_coming_back_writes_the_heartbeat_only(status):
    """The reviewer's churn run: 3 hours of 60 s rounds. What moves every round without news (a staging error's count
    and last_seen, a patrol MR's updated_at) is written with a real change or the heartbeat only."""
    gl, clock, rl = status
    for n in range(180):
        rl.round(lambda now: busy(now, n))
        clock.t += 60
    said = [c["commit_message"] for c in gl.status_commits]
    assert said == ["团队状态：更新"] + ["团队状态：没有变化（心跳）"] * 5, said
    staging, = there(gl)["runner"]["staging"]
    assert staging["count"] == 10 + 150 and staging["last_seen"] == there(gl)["generated_at"]     # the last heartbeat's
    snap = busy(clock.t, 1)
    snap["runner"]["staging"][0]["state"] = "issued"
    assert relay.content_key(snap) != relay.content_key(busy(clock.t, 1))                       # a real change is one


def test_a_relay_that_starts_again_reads_the_one_there_and_writes_nothing_new(status):
    gl, clock, rl = status
    assert rl.round(lambda now: a_snapshot(now)) == "wrote"
    written = clock.t
    again = relay.Relay(rl.connect, STATUS, now=clock)
    clock.t += 60                                               # a restart a minute later: its first round builds
    assert again.round(lambda now: a_snapshot(now)) == "same" and len(gl.status_commits) == 1
    assert again.view()["written_at"] == written
    clock.t = written + 30 * 60                                  # the heartbeat counts from the one there
    assert again.round(lambda now: a_snapshot(now)) == "heartbeat" and len(gl.status_commits) == 2


def test_a_file_there_or_not_is_created_or_updated_whichever_gitlab_takes(status):
    gl, clock, rl = status
    gl.status_files["main"] = {"snapshot.json": "not a snapshot\n"}     # something there, unreadable
    assert rl.round(lambda now: a_snapshot(now)) == "wrote"
    assert gl.status_commits[-1]["actions"][0]["action"] == "update" and there(gl)["schema"] == 1
    rl.there = False                                                     # it thinks the file is gone, it is not
    clock.t += 5 * 60
    assert rl.round(lambda now: a_snapshot(now, title="新")) == "wrote"
    assert [c["actions"][0]["action"] for c in gl.status_commits] == ["update", "update"]
    del gl.status_files["main"]["snapshot.json"]                         # someone deleted it: created again
    clock.t += 5 * 60
    assert rl.round(lambda now: a_snapshot(now, title="再新")) == "wrote"
    assert gl.status_commits[-1]["actions"][0]["action"] == "create" and there(gl)["delivery"][0]["title"] == "再新"


def test_a_write_gitlab_refuses_is_said_and_tried_again_after_the_floor(status):
    gl, clock, rl = status
    gl.fail_writes = 1
    assert rl.round(lambda now: a_snapshot(now)) == "failed"
    v = rl.view()
    assert (v["error"], v["written_at"]) == ("这一轮没写成状态", None) and "500" in v["raw"] and "5 分钟以后会再写" in v["todo"]
    clock.t += 60
    assert rl.round(lambda now: a_snapshot(now)) == "wait"
    clock.t += 4 * 60
    assert rl.round(lambda now: a_snapshot(now)) == "wrote" and rl.view()["error"] == ""
    gl.who.pop("bridge-tok")                                             # the token revoked
    clock.t += 5 * 60
    assert rl.round(lambda now: a_snapshot(now, title="新")) == "failed"
    assert rl.view()["error"] == "GitLab 不认写状态用的令牌"


def test_a_snapshot_that_is_not_one_is_not_written(status):
    gl, clock, rl = status
    bad = a_snapshot(clock.t)
    del bad["runner"]["cap"]
    assert rl.round(lambda now: bad) == "failed"
    assert rl.view()["error"] == "这一轮做出来的状态不对，没有写" and gl.status_commits == []
    assert "runner: missing cap" in rl.view()["raw"]


# ---- in the bridge's round ---------------------------------------------------------------------------------------- #

class Both:
    """The team world's GitLab (teamfix.GitLab, grp/repo) with the status project beside it (relayfix)."""

    def __init__(self, code, status) -> None:
        self.code, self.status = code, status

    def __call__(self, method, url, headers, body):
        path = unquote(urlsplit(url).path)
        if path == API + "/projects/" + STATUS or path.startswith(API + "/projects/" + STATUS + "/"):
            return self.status(method, url, headers, body)
        return self.code(method, url, headers, body)


def relaying(world, tmp_path):
    cfg = json.loads(world["bridge_cfg"].read_text())
    cfg["team_status"] = {"project": STATUS}
    world["bridge_cfg"].write_text(json.dumps(cfg))
    st = RepoGitLab()
    st.status(STATUS)
    svc = make_service(tmp_path / "app", [], gw=FakeGateway(), team=world["team"], transport=Both(world["gl"], st),
                       kick=lambda: world["signer"].once())
    return svc, st


@pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode, reason="ssh-keygen not found")
def test_a_failed_write_never_stops_the_round_and_the_next_one_relays_the_runner(world, tmp_path):
    svc, st = relaying(world, tmp_path)
    st.fail_writes = 1                                  # the first round's write: GitLab answers 500
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
    assert status["relay"]["error"] == "这一轮没写成状态" and "500" in status["relay"]["raw"]
    assert status["relay"]["project"] == STATUS and status["relay"]["written_at"] is None
    from test_contract import diff_keys, fixture
    assert diff_keys(fixture("team.json")["status"]["relay"], status["relay"]) == []
    assert second["summary"]["relay"] == 1, after["relay"]
    assert after["relay"]["error"] == "" and after["relay"]["written_at"] == clock.t
    snap = snapshot.parse(st.status_files["main"]["snapshot.json"])
    assert snap["server"]["version"] == mod("constants").VERSION and snap["server"]["team_mode"] is True
    assert [(p["project"], p["on"], p["sources"]) for p in snap["server"]["projects"]] == [("grp/repo", True, ["mr-handoff"])]
    run_row, = snap["runner"]["handovers"]
    assert (run_row["key"], run_row["run_id"], run_row["project"], run_row["iid"], run_row["owner"]) == (
        "PAY-1", led["runs"][0]["run_id"], "grp/repo", 5, "alice")
    assert run_row["state"] == led["runs"][0]["state"] and run_row["waits"]["text"]
    assert snap["runner"]["patrol"] == [] and snap["runner"]["cap"] == 2
