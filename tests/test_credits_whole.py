"""E2E-14: a requirement's credits are every one of its sessions this machine reads (its brain-dev session, its review
session, its runs' sessions when the team runner is here too), and say what they leave out (a subagent, a turn that
reported none, a session too long, the runner's sessions on another machine) instead of a smaller number shown whole."""

import json
import subprocess

import pytest

from appkit import sh
from brainfix import (FakeGateway, brain, filled_notes, install_brain_dev, make_project, make_service, mod,
                      needs_brainkit, run, start_in_worktree)
from teamfix import world  # noqa: F401  (world: the fixture)

sessions = mod("sessions")
service = mod("service")


def tool(i, name="read"):
    return {"role": "tool", "content": "🔧 Reading f%d.py" % i, "meta": {"tool_name": name}, "ts": "2026-11-08T10:01:00Z"}


def reply(text, credits=None):
    return {"role": "assistant", "content": text, "meta": {"turn_stats": {"credits": credits}} if credits is not None else {},
            "ts": "2026-11-08T10:02:00Z"}


def turn(text, rows, credits):
    return [{"role": "user", "content": text, "meta": {"human": True}, "ts": "2026-11-08T10:00:00Z"}] + \
        [tool(i) for i in range(rows)] + [reply("好了", credits)]


@needs_brainkit
def test_the_runner_and_review_sessions_count_into_the_requirement_and_a_subagent_is_said_left_out(tmp_path, home):
    repo = make_project(tmp_path)
    install_brain_dev(home)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    gw = FakeGateway()
    gw.templates = {"brain-dev", "brain-run"}
    svc = make_service(tmp_path, [repo], gw=gw)

    gw.script = [[tool(i) for i in range(600)] + [reply("问题 1", 40.0)], [tool(i) for i in range(20)] + [reply("审完了", 6.5)]]

    async def go():
        await svc.start()
        try:
            dev = (await svc.open_session("r1", "PAY-1"))["slot"]
            review = (await svc.open_session("r1", "PAY-1", review=True))["slot"]
            await svc.sessions.adapter_create("run-pay-1-1", agent="brain-run", model="", memory_mode="incognito",
                                              title="RUN-PAY-1-1 · 运行机", folder="RUN-PAY-1-1",
                                              parent="brain-run:RUN-PAY-1-1", key="RUN-PAY-1-1", root_folder="Brain 运行机")
            for slot in (dev, review):
                gw.finish(slot)
            gw.msgs[dev] += turn("接着做", 30, 10.0)          # longer than one page
            gw.msgs["run-pay-1-1"] = turn("接手合同", 700, 18.4)[:-1] + [tool(0, "spawn_run"), reply("等审查", 66.0)]
            for _ in range(sessions.STATS_EVERY + 1):
                await svc.sessions.poll()
            rows = {r["key"]: r for r in (await svc.overview("all"))["rows"]}
            return rows["PAY-1"], await svc.requirement("r1", "PAY-1"), await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    row, d, b = run(go())
    assert d["credits"]["total"] == 40.0 + 10.0 + 6.5 + 66.0 == row["credits"] == b["credits"]["total"]
    assert sorted(s["kind"] for s in d["sessions"]) == ["local", "local", "runner"] and row["sessions"] == 3
    assert [s["turns"] for s in d["sessions"]] == [2, 1, 1]
    assert d["credits"]["whole"] is False is row["credits_whole"]
    assert d["credits"]["missing"] == ["起过 1 次子代理（比如审查）：子代理的 credits KiroCrew 不报给这里，没算进来"]


class LabelledMr:
    """GitLab REST as brain-kit's client reads it: the MR of the branch at *sha*, with the labels a test sets."""

    def __init__(self, sha):
        self.sha, self.labels = sha, ["ai-handoff"]

    def __call__(self, method, url, headers, body):
        from urllib.parse import unquote, urlsplit
        path = unquote(urlsplit(url).path)
        if method == "GET" and path.endswith("/merge_requests"):
            return 200, json.dumps([{"iid": 7, "web_url": "https://gl.example/grp/pay/-/merge_requests/7",
                                     "state": "opened", "labels": list(self.labels), "sha": self.sha}]).encode()
        if method == "GET" and path.endswith("/notes"):
            return 200, b"[]"
        return 404, b"{}"


@needs_brainkit
def test_once_a_runner_elsewhere_has_the_run_its_sessions_are_said_left_out(tmp_path, home):
    """Before the runner takes the hand-over there is nothing of it to count; once it runs (on another machine) the
    total is this machine's sessions only, and says so."""
    repo = make_project(tmp_path, kit={"gitlab": {"base": "https://gl.example", "project": "grp/pay"},
                                       "bridge_user": "brain-bridge"})
    install_brain_dev(home)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    st = wt / "aidlc-docs" / intent / "aidlc-state.md"
    st.write_text(filled_notes(st.read_text(), "Status: Construction"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    gw = FakeGateway()
    gw.templates = {"brain-dev"}
    gw.script = [[reply("写完了", 20.0)]]
    gl = LabelledMr("")
    svc = make_service(tmp_path, [repo], gw=gw, transport=gl)

    async def cost():
        svc.repos["r1"].invalidate()      # the hand-over commit is read now, not after the 4 s scan cache
        svc.mr._rows.clear()
        await svc.refresh_mrs()
        row = (await svc.overview("r1"))["rows"][0]
        d = await svc.requirement("r1", "PAY-1")
        return row["state"], row["credits"], row["credits_whole"], d["credits"]["missing"]

    async def go():
        await svc.start()
        try:
            gw.finish((await svc.open_session("r1", "PAY-1"))["slot"])
            for _ in range(sessions.STATS_EVERY + 1):
                await svc.sessions.poll()
            brain(wt, "handoff", "--no-push")
            gl.sha = sh("git", "-C", wt, "rev-parse", "HEAD").stdout.strip()
            tok = home / ".brain" / "gitlab-token"     # after the hand-over: brain handoff would reach GitLab with it
            tok.parent.mkdir(parents=True, exist_ok=True)
            tok.write_text("t0ken-for-tests")
            tok.chmod(0o600)
            waiting = await cost()
            gl.labels = ["ai-running"]
            return waiting, await cost()
        finally:
            await svc.stop()

    waiting, running = run(go())
    assert waiting == ("handed_over", 20.0, True, [])
    assert running == ("running", 20.0, False, [service.RUNNER_ELSEWHERE])


def test_what_a_transcript_cost_and_what_it_leaves_out():
    """A turn cut short reports no credits; the last one, while the slot is at work, has not reported yet."""
    rows = turn("一", 2, 3.0) + turn("二", 4, None)[:-1] + turn("三", 1, None)[:-1]
    assert sessions.spend(rows, busy=True) == {"credits": 3.0, "turns": 3, "subagents": 0, "unreported": 1}
    assert sessions.spend(rows, busy=False)["unreported"] == 2
    whole = sessions.requirement_credits([{"credits": 3.0, "whole": True}, {"credits": None}])
    assert whole == {"total": 3.0, "whole": True, "missing": []}
    cut = sessions.requirement_credits([{"title": "PAY-1 · 本地开发", "credits": 3.0, "whole": False, "unreported": 2}],
                                       service.RUNNER_ELSEWHERE)
    assert cut == {"total": 3.0, "whole": False, "missing": [
        "「PAY-1 · 本地开发」太长，最早的部分没读到，没算进来", "2 轮没报 credits（多半是中途被停了），没算进来", service.RUNNER_ELSEWHERE]}


@needs_brainkit
@pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode, reason="ssh-keygen not found")
def test_on_the_team_runner_a_requirement_counts_its_run_and_the_sessions_before_the_hand_over_when_they_are_here(
        world, tmp_path):
    """Team mode, the runner here: the runner's row stands for the requirement. Its credits are its run's session and
    the sessions of its repo on this machine; with none of those here, it says they are left out."""
    gw = FakeGateway()
    gw.templates = {"brain-dev", "brain-run"}
    svc = make_service(tmp_path / "app", [world["repo"]], gw=gw, team=world["team"], transport=world["gl"],
                       kick=lambda: world["signer"].once())

    async def costs():
        for _ in range(sessions.STATS_EVERY + 1):
            await svc.sessions.poll()
        (row,) = [r for r in (await svc.overview("all"))["rows"] if r["key"] == "PAY-1"]
        d = await svc.requirement("runner", "PAY-1")
        return row["repo"], row["credits"], row["credits_whole"], d["credits"], [s["kind"] for s in d["sessions"]]

    async def go():
        await svc.start()
        try:
            await svc.team_loop()
            gw.finish("run-pay-1-1")
            gw.msgs["run-pay-1-1"].append(reply("等回答", 30.0))
            before = await costs()
            dev = await svc.sessions.open_session("开工", parent_session_key="brain:r1-PAY-1", cwd="", title="PAY-1 · 本地开发",
                                                  folder="PAY-1", agent="brain-dev", strict_agent=True, kind="local",
                                                  key="r1-PAY-1")
            gw.finish(dev.slot)
            gw.msgs[dev.slot] = turn("开工", 3, 12.0)
            return before, await costs()
        finally:
            await svc.stop()

    before, after = run(go())
    assert before == ("runner", 30.0, False, {"total": 30.0, "whole": False, "missing": [service.LOCAL_ELSEWHERE],
                                              "note": mod("detail").NO_TOKENS}, ["runner"])
    assert after == ("runner", 42.0, True, {"total": 42.0, "whole": True, "missing": [], "note": mod("detail").NO_TOKENS},
                     ["local", "runner"])
