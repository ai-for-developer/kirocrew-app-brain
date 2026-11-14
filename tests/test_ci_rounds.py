"""CI rounds on a person's machine: the MR refresh also reads the MR's newest pipelines with the person's token, and a
round the runner did because a pipeline failed says 「CI 没过 #<id>」 with the pipeline's page. When they are not read
(no token, GitLab down, CI off, team mode) the rounds say what they said before and the board still answers."""

import json

import pytest

from appkit import BRAINKIT, mod
from brainfix import (filled_notes, brain, make_project, make_service, needs_brainkit, run, sh, start_in_worktree)
from test_board import RUNNER_FILE, M, T, demo3_events, ev, facts, iso, node, row_of, snapshot

mrinfo, stages = mod("mrinfo"), mod("stages")
GL = "https://gitlab.example.com"
REL = "aidlc-docs/261109-x/operations/release.md"
ASKED, FIX = "%040x" % 0xA5, "%040x" % 0xF1      # the runner's 请批发布 commit, then its CI fix
RED, GREEN = 1234, 1240


@pytest.fixture(autouse=True)
def brainkit():
    """mrinfo reads GitLab with brain-kit's own client (rest.GitLab, rest.gl_time)."""
    if not mod("bk").load(str(BRAINKIT))["ok"]:
        pytest.skip("brain-kit checkout not found")


def pipeline(pid, sha, status, finished, **more):
    return dict({"id": pid, "iid": pid - 1000, "sha": sha, "ref": "ai/dana/DEMO-3-x", "status": status,
                 "source": "merge_request_event", "created_at": iso(finished - 5 * M), "updated_at": iso(finished),
                 "web_url": "%s/shop/pay/-/pipelines/%d" % (GL, pid)}, **more)


def ci_board(pipelines):
    """DEMO-3 handed over; on the runner it checks (green), asks for the release at 10:20 on its commit ASKED; that
    commit's pipeline fails at 10:31; it fixes CI (FIX, 10:45), checks again (10:47) and asks again (10:50). *pipelines*:
    the MR's, as mrinfo read them (None: not read here)."""
    hand = T + 30 * M
    on_runner = [(T + 40 * M, "VERIFY", {"ok": True, "results": [{"cmd": "pytest -q", "exit": 0, "secs": 4.0}]}),
                 (T + 50 * M, "STAGE_STARTED", {"stage": "release", "path": REL, "sha256": "e" * 64}),
                 (T + 51 * M, "APPROVAL_REQUESTED", {"kind": "RELEASE", "path": REL, "sha256": "e" * 64}),
                 (T + 77 * M, "VERIFY", {"ok": True, "results": [{"cmd": "pytest -q", "exit": 0, "secs": 4.0}]}),
                 (T + 80 * M, "STAGE_STARTED", {"stage": "release", "path": REL, "sha256": "f" * 64}),
                 (T + 81 * M, "APPROVAL_REQUESTED", {"kind": "RELEASE", "path": REL, "sha256": "f" * 64})]
    evs = demo3_events()[:12] + [dict(ev(at, kind, **d), _file=RUNNER_FILE) for at, kind, d in on_runner]
    f = facts(evs, handoff={"requested_at": iso(hand), "by": "dana"},
              approvals=[{"kind": "RELEASE", "unit": None, "state": "pending", "sha8": "f" * 8, "at": T + 81 * M,
                          "line": "approve RELEASE ffffffff", "path": None}])
    t = {"actions": {"handovers": [{"kind": "requested", "at": iso(hand), "commit": "%040x" % 1},
                                   {"kind": "bridge accepted", "at": iso(hand + M), "mr_iid": 41, "run_id": "RUN-DEMO-3-1"},
                                   {"kind": "voucher", "at": iso(hand + 2 * M), "run_id": "RUN-DEMO-3-1"}],
                     "commits": [{"sha": "%040x" % 1, "at": iso(hand), "by": "person", "records_only": True, "files": 2},
                                 {"sha": ASKED, "at": iso(T + 50 * M + 30), "by": "bot", "after_handover": True,
                                  "records_only": True, "files": 3},
                                 {"sha": FIX, "at": iso(T + 75 * M), "by": "bot", "after_handover": True, "files": 1}],
                     "questions": []},
         "missing": ["CI pipelines (GitLab, not readable here)"]}
    mr = {"url": GL + "/shop/pay/-/merge_requests/41", "questions": [], "pipelines": pipelines}
    row = dict(row_of("running", "runner", "运行机在跑"), mr={"iid": 41, "url": mr["url"], "state": "opened"},
               run_ids=["RUN-DEMO-3-1"])
    return snapshot(f, row, t=t, mr=mr, local_only=False, now=T + 82 * M)


def read(*rows):
    return mrinfo.parse_pipelines(list(rows), GL, "shop/pay")


def told(n):
    return [(r["n"], r["outcome"]["kind"], r["why"]["kind"]) for r in n["rounds"]]


def test_a_round_after_a_failed_pipeline_says_ci_failed_with_its_number_and_page():
    b = ci_board(read(pipeline(RED, ASKED, "failed", T + 61 * M), pipeline(GREEN, FIX, "running", T + 80 * M)))
    rel, bt = node(b, "release"), node(b, "build-and-test")
    page = {"id": RED, "url": GL + "/shop/pay/-/pipelines/%d" % RED, "at": T + 61 * M}
    # 发布: round 1 was asked on ASKED, its pipeline failed, the runner fixed CI: round 2 is there because of #1234
    assert told(rel) == [(1, "superseded", "first"), (2, "open", "ci_failed")]
    assert rel["rounds"][0]["outcome"]["pipeline"] == page and rel["rounds"][1]["why"]["pipeline"] == page
    assert rel["rounds"][1]["why"]["sha"] == FIX[:12]
    # 构建测试: the check after the fix is the fix's round, from the failure on
    assert told(bt) == [(1, "passed", "first"), (2, "passed", "ci_failed")]
    assert bt["rounds"][1]["why"]["pipeline"] == page and bt["rounds"][1]["started"] == T + 61 * M
    assert FIX[:12] in bt["rounds"][1]["commits"]
    # what the replay plays: 发布 between the fix and the next request
    assert any(h["doing"] == "CI 没过 #1234，代码又改了：等它重新请批" for h in rel["history"])
    # read here: the CI pipelines are not said to be missing
    assert not any("CI 流水线" in x for x in b["missing"])


def test_without_pipelines_read_here_the_rounds_say_what_they_said_before():
    for pipelines in (None, [], read(pipeline(RED, ASKED, "success", T + 61 * M))):
        b = ci_board(pipelines)
        rel, bt = node(b, "release"), node(b, "build-and-test")
        assert told(rel) == [(1, "superseded", "first"), (2, "open", "code_changed")], pipelines
        assert rel["rounds"][0]["outcome"]["pipeline"] is None and rel["rounds"][1]["why"]["pipeline"] is None
        assert told(bt) == [(1, "passed", "first"), (2, "passed", "reopened")]
        assert any(h["doing"] == "请批以后代码又改了：等它重新请批" for h in rel["history"])
        assert ("CI 流水线：在 GitLab 上，这里读不到" in b["missing"]) == (pipelines is None)


def test_a_pipeline_of_someone_elses_commit_or_after_the_fix_is_no_cause():
    after = read(pipeline(RED, ASKED, "failed", T + 76 * M))          # it failed only after the fix was committed
    other = read(pipeline(RED, "%040x" % 0xBEEF, "failed", T + 61 * M))  # on a commit that is not the run's
    for pipelines in (after, other):
        rel = node(ci_board(pipelines), "release")
        assert told(rel)[1] == (2, "open", "code_changed")


def test_the_newest_pipelines_are_kept_newest_first_and_bounded():
    many = [pipeline(1000 + i, ASKED, "failed", T + i * M) for i in range(mrinfo.MAX_PIPELINES + 7)]
    got = read(*many)
    assert len(got) == mrinfo.MAX_PIPELINES and [p["id"] for p in got] == sorted((p["id"] for p in got), reverse=True)
    assert got[0]["id"] == 1000 + mrinfo.MAX_PIPELINES + 6


def test_a_pipeline_row_is_read_at_the_boundary():
    finished = pipeline(RED, ASKED.upper(), "failed", T, finished_at=iso(T + 30))
    running = pipeline(1235, ASKED, "running", T + 60)
    foreign = pipeline(1236, ASKED, "failed", T, web_url="https://evil.example.net/x")
    bad = [pipeline(0, ASKED, "failed", T), pipeline(1237, "abc", "failed", T), dict(pipeline(1238, ASKED, "failed", T),
                                                                                        id="1238"), "not a row"]
    got = {p["id"]: p for p in read(finished, running, foreign, *bad)}
    assert sorted(got) == [RED, 1235, 1236]
    assert (got[RED]["sha"], got[RED]["at"]) == (ASKED, T + 30)       # finished_at first, sha in lower case
    assert got[1235]["at"] is None                                    # still running: not finished
    assert got[1236]["url"] == GL + "/shop/pay/-/pipelines/1236"      # a page off GitLab is not linked


# ---------------------------------------------------------------- the refresh, with a fake GitLab

class CountingGitLab:
    """GitLab REST as brain-kit's client calls it, every call counted by its path: the MR of a branch, its notes, its
    pipelines (*pipelines*: the rows, or an HTTP status to answer with). *down*: every call fails."""

    def __init__(self, pipelines):
        self.pipelines, self.calls, self.down = pipelines, [], False

    def __call__(self, method, url, headers, body):
        from urllib.parse import parse_qsl, unquote, urlsplit
        u = urlsplit(url)
        path = unquote(u.path)
        self.calls.append((method, path, dict(parse_qsl(u.query))))
        if self.down:
            raise mod("bk").m("rest").BrainError("cannot reach gitlab.example.com: timed out")
        if method == "GET" and path.endswith("/merge_requests"):
            return 200, json.dumps([{"iid": 7, "web_url": GL + "/shop/pay/-/merge_requests/7", "state": "opened",
                                     "labels": ["ai-running"], "sha": ASKED}]).encode()
        if method == "GET" and path.endswith("/merge_requests/7/notes"):
            return 200, b"[]"
        if method == "GET" and path.endswith("/merge_requests/7/pipelines"):
            if isinstance(self.pipelines, int):
                return self.pipelines, b'{"message": "fake"}'
            return 200, json.dumps(self.pipelines).encode()
        return 404, b"{}"

    def pipeline_calls(self):
        return [c for c in self.calls if c[1].endswith("/pipelines")]


def with_token(home):
    tok = home / ".brain" / "gitlab-token"
    tok.parent.mkdir(parents=True, exist_ok=True)
    secret = "glpat-" + "t0ken-for-ci-tests"
    tok.write_text(secret)
    tok.chmod(0o600)
    return secret


def test_one_refresh_reads_the_mrs_newest_pipelines_once_with_the_same_token(home):
    secret = with_token(home)
    gl = CountingGitLab([pipeline(RED, ASKED, "failed", T + 61 * M)])
    cache = mrinfo.MrCache()
    mr = cache.refresh(GL, "shop/pay", "ai/dana/DEMO-3-x", transport=gl)
    [(method, path, query)] = gl.pipeline_calls()
    assert (method, path, query) == ("GET", "/api/v4/projects/shop/pay/merge_requests/7/pipelines",
                                     {"per_page": str(mrinfo.MAX_PIPELINES)})
    assert [p["id"] for p in mr["pipelines"]] == [RED] and cache.get("shop/pay", "ai/dana/DEMO-3-x") == mr
    assert secret not in json.dumps(mr)


def test_ci_off_gitlab_down_or_no_token_leave_the_mr_as_it_was_read(home, monkeypatch):
    with_token(home)
    off = CountingGitLab(403)
    assert mrinfo.MrCache().refresh(GL, "shop/pay", "b", transport=off)["pipelines"] is None
    cache, gl = mrinfo.MrCache(), CountingGitLab([pipeline(RED, ASKED, "failed", T)])
    first = cache.refresh(GL, "shop/pay", "b", transport=gl)
    gl.pipelines = 502                                     # the pipelines fail halfway: the last copy stays
    assert cache.refresh(GL, "shop/pay", "b", transport=gl)["pipelines"] == first["pipelines"]
    gl.down = True                                         # GitLab down: the whole last copy stays
    assert cache.refresh(GL, "shop/pay", "b", transport=gl) == first
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    (home / ".brain" / "gitlab-token").unlink()
    nobody = CountingGitLab([pipeline(RED, ASKED, "failed", T)])
    assert mrinfo.MrCache().refresh(GL, "shop/pay", "b", transport=nobody) is None and nobody.calls == []


@needs_brainkit
def test_only_the_mr_refresh_calls_the_pipelines_and_the_token_never_reaches_the_page(tmp_path, home):
    repo = make_project(tmp_path, kit={"gitlab": {"base": GL, "project": "shop/pay"}, "bridge_user": "brain-bridge"})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    st = wt / "aidlc-docs" / intent / "aidlc-state.md"
    st.write_text(filled_notes(st.read_text(), "Status: Construction"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    secret = with_token(home)
    gl = CountingGitLab([pipeline(RED, ASKED, "failed", T)])
    svc = make_service(tmp_path, [repo], transport=gl)

    async def go():
        await svc.start()
        try:
            await svc.overview("r1")                       # the overview asks for the MR of the handed-over run
            await svc.refresh_mrs()
            after_one = len(gl.pipeline_calls())
            boards = [await svc.board("r1", "PAY-1") for _ in range(3)]
            await svc.refresh_mrs()                        # not due yet: the same copy
            details = await svc.requirement("r1", "PAY-1")
            return after_one, len(gl.pipeline_calls()), boards, details, await svc.status()
        finally:
            await svc.stop()

    after_one, after_all, boards, details, status = run(go())
    assert (after_one, after_all) == (1, 1)
    assert not any("CI 流水线" in x for x in boards[-1]["missing"])
    assert boards[-1]["row"]["mr"]["iid"] == 7
    for x in boards + [details, status]:
        assert secret not in json.dumps(x, ensure_ascii=False)
    assert all(c[0] == "GET" for c in gl.calls)
