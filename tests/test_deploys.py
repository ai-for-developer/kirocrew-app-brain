"""上线: what a merged requirement's deploys did (brain-kit's deploys: the trace's outcome.deploy) on 需求现状, and the
eval's three 部署 numbers on 交付总览. A fake GitLab answers for the deployments to staging and their deploy evidence the
way a real one did for two demo pipelines (4138161 promoted; 4138009 aborted, create_order and read_order failing).
The deploys are read only when kit.json on the default branch has "deploys", and with one GitLab client however many
traces and evals read them: a GitLab behind single sign-on signs each new client in."""

import asyncio
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import make_project, make_service, run, start_in_worktree

GL = "https://gitlab.example.com"
PROJECT = "shop/pay"
DEPLOYS = {"environment": "staging", "deploy_job": "deploy-staging", "deploy_evidence": "deploy-evidence.json"}
NEW = "sha256:168d16c5a244384d3d434ccd4adc5a8e6ee168b4ce638e7653ed9541f82750e1"
OLD = "sha256:7d479c543ed89c65139a43a02de40420d902c7787518c27974c0df43cd2f189e"
BAD = "sha256:4120ee577b2a1d036324052bf70364b3abc01461fa13b01558cf9bf403e23c5b"
TOKEN = "glpat-" + "deploys-test"


@pytest.fixture(autouse=True)
def deploys_kit():
    bk = mod("bk")
    if not bk.load(str(BRAINKIT))["ok"] or not bk.has("deploys"):
        pytest.skip("this brain-kit reads no deploys (brainkit/deploys.py)")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def merged(tmp_path, home, deploys=DEPLOYS, mr_md=False):
    """PAY-1 started and merged into main two hours ago (the merge's time); kit.json on main reads *deploys*. *mr_md*:
    its runs/PAY-1/MR.md is committed, as `brain handoff` writes it."""
    kit = dict({"gitlab": {"base": GL, "project": PROJECT}}, **({"deploys": deploys} if deploys else {}))
    repo = make_project(tmp_path, kit=kit)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "orders", "orders", "下单")
    if mr_md:
        (wt / "runs" / "PAY-1" / "MR.md").write_text("## 下单\n\n**一句话**：下单接口。\n", encoding="utf-8")
        sh("git", "-C", wt, "add", "-A")
        sh("git", "-C", wt, "commit", "-qm", "PAY-1: MR.md")
    at = int(time.time()) - 2 * 3600
    when = {"GIT_COMMITTER_DATE": "@%d +0000" % at, "GIT_AUTHOR_DATE": "@%d +0000" % at}
    sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge PAY-1", "ai/alice/PAY-1-orders", env=when)
    sh("git", "-C", repo, "worktree", "remove", "--force", wt)
    return repo, sh("git", "-C", repo, "rev-parse", "main").stdout.strip(), at


def later_commit(repo, at):
    """A commit on main after the merge (a fix), made at *at*: its deploy ships PAY-1 too."""
    (repo / "FIX.md").write_text("fix\n", encoding="utf-8")
    when = {"GIT_COMMITTER_DATE": "@%d +0000" % at, "GIT_AUTHOR_DATE": "@%d +0000" % at}
    sh("git", "-C", repo, "add", "FIX.md")
    sh("git", "-C", repo, "commit", "-qm", "orders: create_order answers 201 again", env=when)
    return sh("git", "-C", repo, "rev-parse", "main").stdout.strip()


def deployment(did, pipeline, job, sha, started, finished, status="success"):
    """One deployment to staging as GitLab lists it (GET /projects/:id/deployments)."""
    return {"id": did, "iid": did - 844000, "ref": "main", "sha": sha, "status": status, "created_at": iso(started),
            "updated_at": iso(finished), "finished_at": iso(finished),
            "deployable": {"id": job, "name": "deploy-staging", "status": status, "started_at": iso(started),
                           "finished_at": iso(finished), "web_url": "%s/%s/-/jobs/%d" % (GL, PROJECT, job),
                           "pipeline": {"id": pipeline, "sha": sha, "ref": "main", "status": status}}}


def promoted(sha, at):
    return {"environment": "staging", "deploy_commit": sha, "digest": NEW, "image": "registry.example.com/pay@" + NEW,
            "previous_stable_digest": OLD, "result": "promoted", "reason": "every canary step passed",
            "stable_digest_after": NEW,
            "steps_reached": [{"index": i, "step": s, "seen_at": iso(at + 10 * i)} for i, s in enumerate(
                ["setWeight 20", "analysis smoke", "setWeight 50", "analysis smoke", "complete"])]}


def aborted(sha, at):
    smoke = [{"check": "healthz", "ok": True, "ms": 21},
             {"check": "create_order", "ok": False, "error": "expected HTTP 201, got 500: Internal Server Error"},
             {"check": "read_order", "ok": False, "error": "no order to read because create_order failed"}]
    return {"environment": "staging", "deploy_commit": sha, "digest": BAD, "image": "registry.example.com/pay@" + BAD,
            "previous_stable_digest": OLD, "result": "aborted", "stable_digest_after": OLD,
            "reason": "RolloutAborted: Rollout aborted update to revision 5: Step-based analysis phase error/failed",
            "steps_reached": [{"index": 0, "step": "setWeight 20", "seen_at": iso(at)},
                              {"index": 1, "step": "analysis smoke", "seen_at": iso(at + 8)}],
            "analysis_runs": [{"step_index": "1", "phase": "Failed", "smoke": smoke}]}


class FakeGitLab:
    """The project's deployments to staging and its jobs' artifacts; every other path is GitLab's 404."""

    def __init__(self, deployments=(), artifacts=None):
        self.deployments, self.artifacts, self.calls = list(deployments), dict(artifacts or {}), []

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path = unquote(u.path)
        self.calls.append((method, path, headers.get("PRIVATE-TOKEN")))
        if method == "GET" and path == "/api/v4/projects/%s/deployments" % PROJECT:
            page = int(dict(parse_qsl(u.query)).get("page", "1"))
            newest_first = sorted(self.deployments, key=lambda d: -d["id"])
            return 200, json.dumps(newest_first if page == 1 else []).encode()
        m = re.match(r"^/api/v4/projects/%s/jobs/(\d+)/artifacts/(.+)$" % re.escape(PROJECT), path)
        if method == "GET" and m and (int(m.group(1)), m.group(2)) in self.artifacts:
            return 200, json.dumps(self.artifacts[(int(m.group(1)), m.group(2))]).encode()
        return 404, b'{"message": "404 Not Found"}'

    def listed(self):
        return sum(1 for _, path, _ in self.calls if path.endswith("/deployments"))


async def detail_of(svc):
    await svc.start()
    try:
        return await svc.requirement("r1", "PAY-1")
    finally:
        await svc.stop()


def test_a_deploy_that_went_live_says_its_digest_when_how_long_after_the_merge_and_its_pipeline(tmp_path, home,
                                                                                               monkeypatch):
    repo, merge, at = merged(tmp_path, home)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)     # after `brain start`, which would ask GitLab
    gl = FakeGitLab([deployment(844623, 4138161, 34418480, merge, at + 120, at + 300)],
                    {(34418480, "deploy-evidence.json"): promoted(merge, at + 200)})
    got = run(detail_of(make_service(tmp_path, [repo], transport=gl)))
    d = got["deploy"]
    assert (d["value"], d["environment"], d["digest"], d["at"]) == ("promoted", "staging", NEW, at + 300)
    assert abs(d["lead_s"] - 300) <= 36, "five minutes after the merge (brain-kit says hours to 0.01)"
    [a] = d["attempts"]
    assert (a["result"], a["pipeline"], a["sha"]) == ("promoted", 4138161, merge[:12])
    assert a["url"] == GL + "/shop/pay/-/pipelines/4138161"
    assert all(tok == TOKEN for _, _, tok in gl.calls) and TOKEN not in json.dumps(got, ensure_ascii=False)


def test_an_aborted_canary_names_the_smoke_checks_that_failed_and_the_digest_kept_then_a_fix_goes_live(
        tmp_path, home, monkeypatch):
    repo, merge, at = merged(tmp_path, home)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)     # after `brain start`, which would ask GitLab
    first = deployment(844613, 4138009, 34417471, merge, at + 120, at + 260, status="failed")
    gl = FakeGitLab([first], {(34417471, "deploy-evidence.json"): aborted(merge, at + 200)})
    d = run(detail_of(make_service(tmp_path / "one", [repo], transport=gl)))["deploy"]
    assert (d["value"], d["digest"], d["at"], d["lead_s"]) == ("aborted", None, None, None)
    [a] = d["attempts"]
    assert (a["result"], a["pipeline"], a["kept"], a["digest"]) == ("aborted", 4138009, OLD, BAD)
    assert a["failed"] == [{"check": "create_order", "error": "expected HTTP 201, got 500: Internal Server Error"},
                           {"check": "read_order", "error": "no order to read because create_order failed"}]

    fix = later_commit(repo, at + 1800)
    gl.deployments.append(deployment(844623, 4138161, 34418480, fix, at + 1900, at + 2100))
    gl.artifacts[(34418480, "deploy-evidence.json")] = promoted(fix, at + 2000)
    d = run(detail_of(make_service(tmp_path / "two", [repo], transport=gl)))["deploy"]
    assert (d["value"], d["digest"]) == ("promoted", NEW)
    assert [(a["result"], a["pipeline"]) for a in d["attempts"]] == [("aborted", 4138009), ("promoted", 4138161)]
    assert abs(d["lead_s"] - 2100) <= 36, "from the merge to the fix's deploy"


def test_gitlab_saying_success_without_readable_evidence_is_unknown_never_live(tmp_path, home, monkeypatch):
    repo, merge, at = merged(tmp_path, home)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)     # after `brain start`, which would ask GitLab
    gl = FakeGitLab([deployment(844623, 4138161, 34418480, merge, at + 120, at + 300)])   # its artifact expired
    d = run(detail_of(make_service(tmp_path, [repo], transport=gl)))["deploy"]
    assert (d["value"], d["digest"], d["at"]) == ("unknown", None, None)
    assert d["why"] == "部署证据不在了：没写，或者过期了"
    [a] = d["attempts"]
    assert (a["result"], a["why"]) == ("unknown", d["why"]) and any("expired" in r for r in a["raw"])


def test_without_a_token_the_deploys_are_unknown_and_say_so(tmp_path, home):
    repo, merge, at = merged(tmp_path, home)
    gl = FakeGitLab([deployment(844623, 4138161, 34418480, merge, at + 120, at + 300)],
                    {(34418480, "deploy-evidence.json"): promoted(merge, at + 200)})
    d = run(detail_of(make_service(tmp_path, [repo], transport=gl)))["deploy"]
    assert (d["value"], d["why"], d["attempts"]) == ("unknown", "这台机器上没有 GitLab 的令牌", [])
    assert gl.calls == []


def test_without_deploys_in_kit_json_there_is_no_deploy_and_no_gitlab_call(tmp_path, home, monkeypatch):
    repo, _, _ = merged(tmp_path, home, deploys=None)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)     # after `brain start`, which would ask GitLab
    gl = FakeGitLab()
    svc = make_service(tmp_path, [repo], transport=gl)

    async def go():
        await svc.start()
        try:
            got = await svc.requirement("r1", "PAY-1")
            for _ in range(300):
                ov = await svc.overview("r1")
                if not ov["eval"].get("computing"):
                    return got, ov
                await asyncio.sleep(0.1)
            return got, ov
        finally:
            await svc.stop()

    got, ov = run(go())
    assert got["deploy"] is None and gl.listed() == 0
    assert not {"deploy_frequency", "lead_time", "change_failure"} & {m["id"] for m in ov["eval"]["metrics"]}


def test_one_gitlab_client_signs_in_once_for_every_trace_and_the_eval_and_the_numbers_come(tmp_path, home,
                                                                                         monkeypatch):
    """No transport given: each brain-kit GitLab client gets its own transport from the sign-in helper, and a GitLab
    behind single sign-on signs each one in. Two traces a minute apart and the eval read the deploys afresh, all
    through one client (the app's 你是谁 asks GitLab with a client of its own, as before). Its MR.md is committed, so
    the 变更说明 is read, not made: brainkit.mrdesc.build makes a trace that reads the deploys with a client of its
    own, which the app cannot hand it one for."""
    repo, merge, at = merged(tmp_path, home, mr_md=True)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)     # after `brain start`, which would ask GitLab
    gl = FakeGitLab([deployment(844623, 4138161, 34418480, merge, at + 120, at + 300)],
                    {(34418480, "deploy-evidence.json"): promoted(merge, at + 200)})
    clients = []        # the paths each client called

    def transport_for(base):
        mine = []
        clients.append(mine)

        def call(method, url, headers, body):
            mine.append(unquote(urlsplit(url).path))
            return gl(method, url, headers, body)
        return call

    monkeypatch.setattr(mod("bk").m("rest"), "local_transport", lambda: SimpleNamespace(transport_for=transport_for))
    svc = make_service(tmp_path, [repo])
    clock = [time.time()]
    svc.now = lambda: clock[0]

    async def go():
        await svc.start()
        try:
            first = await svc.requirement("r1", "PAY-1")
            clock[0] += 61
            again = await svc.requirement("r1", "PAY-1")
            for _ in range(300):
                ov = await svc.overview("r1")
                if not ov["eval"].get("computing"):
                    return first, again, ov
                await asyncio.sleep(0.1)
            return first, again, ov
        finally:
            await svc.stop()

    first, again, ov = run(go())
    assert first["deploy"]["value"] == again["deploy"]["value"] == "promoted"
    assert gl.listed() >= 3, "each trace and the eval read the deploys again, none kept an old answer"
    readers = [c for c in clients if any(path.endswith("/deployments") for path in c)]
    assert len(readers) == 1, "one client, so one sign-in, for every read of the deploys"
    tiles = {m["id"]: m["value"] for m in ov["eval"]["metrics"]}
    assert tiles["deploy_frequency"] == 0.25, "one deploy went live in the last 28 days: a quarter a week"
    assert abs(tiles["lead_time"] - 300 / 3600) <= 0.01
    assert tiles["change_failure"] == [0, 0], "its 14 days are not over: not judged yet"
