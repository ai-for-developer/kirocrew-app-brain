"""运行机 on a personal install (GET /team/status, backend/core/runner_view.py), offline: each listed repo's team runner
as the patrol tests' in-memory GitLab shows it, read with the person's own token.

At work: the open MRs with the running label, and those MR 巡检 replied on within a day (not one it said it left alone,
not a hand-over's, not a closed one). Waiting: the bridge's open questions that name the person and that nobody answered
after. The runner last acted at the newest note of its account on any MR read, a merged one too. A repo whose kit.json
names no bridge_user has no runner. Without a token, or when GitLab refuses, the page is told so in words, the last read
kept."""

import pytest

from appkit import mod
from brainfix import make_project, make_service, needs_brainkit, run
from http_fakes import AppRoute, FakeWeb, call
from patrolfix import BASE, Clock, PatrolGitLab
from test_contract import diff_keys, fixture

pytestmark = needs_brainkit
P, H, T0 = "grp/pay", 3600, 1_800_000_000
PARK = {"question": "退款超过 500 元要不要人工审核？", "options": [{"id": "A", "text": "要"}, {"id": "B", "text": "不要"}],
        "recommend": "A"}


def app(tmp_path, monkeypatch):
    """alice's machine: pay (kit.json names the bridge's account) and web (no team runner), on one GitLab."""
    clock = Clock(T0)
    gl = PatrolGitLab(clock)
    gl.who["alice-tok"] = "alice"
    gl.project(P, 7)
    monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
    pay = make_project(tmp_path, kit={"gitlab": {"base": BASE, "project": P}, "bridge_user": "brain-bridge"})
    web = make_project(tmp_path, name="web", prefix="WEB")
    svc = make_service(tmp_path, [pay, web], transport=gl)
    svc.now = clock
    return clock, gl, svc, mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)


def at(clock, ago):
    clock.t = T0 - ago


def note(gl, iid, who, body):
    return gl.thread(P, iid, who, body, resolvable=False)["notes"][0]


def a_day_of_the_runner(clock, gl):
    """The MRs of grp/pay over the last day and a half, as the bridge, MR 巡检 and people left them."""
    patrol = mod("team.patrol")
    ask = mod("bk").m("bridge.decisions").question_text
    at(clock, 30 * H)
    gl.mr(P, 14, title="旧的 CI")
    note(gl, 14, "brain-bridge", "CI 修好了：少装了一个依赖")                      # patrol, too long ago
    at(clock, 5 * H)
    gl.mr(P, 41, title="退款审核", labels=["ai-running"])
    d3 = note(gl, 41, "brain-bridge", ask("D-3", "RUN-PAY-1-1", PARK, ["alice", "carol"], "clarify", "a" * 40, "b" * 64))
    note(gl, 41, "brain-bridge", ask("D-4", "RUN-PAY-1-1", PARK, ["bob"], "clarify", "a" * 40, "c" * 64))
    note(gl, 41, "brain-bridge", ask("D-5", "RUN-PAY-1-1", PARK, ["alice"], "clarify", "a" * 40, "d" * 64))
    note(gl, 41, "brain-bridge", ask("D-6", "RUN-PAY-1-1", PARK, ["alice"], "clarify", "a" * 40, "e" * 64))
    note(gl, 41, "brain-bridge", "D-6 作废：代码有新推送，问题要重新问")                  # nobody answers it any more
    at(clock, 4 * H)
    note(gl, 41, "alice", "answer D-5 B 小额不审")                                 # answered, not counted yet
    at(clock, 3 * H)
    gl.mr(P, 13, title="缓存过期")
    row = patrol.MrRow(project=P, iid=13, state=patrol.BLOCKED, attempts=2)
    item = patrol.PatrolItem(P, 7, 13, "缓存过期", None, "fix", "main", "e" * 40, (patrol.Conflict("main"),))
    note(gl, 13, "brain-bridge", patrol.blocked_text(row, item))                   # patrol left it alone
    at(clock, 2 * H)
    gl.mr(P, 12, title="折扣按百分比算")
    note(gl, 12, "carol", "百分比要限制在 0-100")
    note(gl, 12, "brain-bridge", "> 百分比要限制在 0-100\n\n改好了：超出范围的按 0 或 100 算")   # patrol's reply
    at(clock, H)
    note(gl, 14, "dave", "还要再看一下")                                          # a person: the MR moves, not the runner
    at(clock, H // 2)
    gl.mr(P, 16, title="导出报表", labels=["ai-handoff-rejected"])
    note(gl, 16, "brain-bridge", "AI 接手被拒\n- 交接提交改了 .gitlab-ci.yml")      # the hand-over's, not patrol's
    at(clock, 600)
    gl.mr(P, 15, title="对账单")
    note(gl, 15, "brain-bridge", "改好了，流水线过了")
    gl.close(P, 15)                                                                # merged since
    at(clock, 0)
    return d3


def test_the_runner_at_work_the_questions_waiting_on_me_and_a_repo_without_a_runner(tmp_path, home, monkeypatch):
    clock, gl, svc, routes = app(tmp_path, monkeypatch)

    async def go():
        await svc.start()
        try:
            d3 = a_day_of_the_runner(clock, gl)
            return d3, await call(routes, "GET", "/team/status")
        finally:
            await svc.stop()

    d3, got = run(go())
    assert got.status == 200, got.body
    pay, web = got.body["repos"]
    assert (web["repo"], web["name"], web["runner"]) == ("r2", "web", None)
    runner = pay["runner"]
    assert (pay["repo"], runner["bridge_user"], runner["project"], runner["me"], runner["problem"]) == (
        "r1", "brain-bridge", P, "alice", None)
    seen = runner["seen"]
    url = "%s/%s/-/merge_requests/%d" % (BASE, P, 41)
    assert seen["at_work"] == [
        {"iid": 41, "title": "退款审核", "url": url, "why": "running", "at": T0 - 5 * H},
        {"iid": 12, "title": "折扣按百分比算", "url": url.replace("/41", "/12"), "why": "patrol", "at": T0 - 2 * H}]
    q, = seen["waiting"]
    assert (q["id"], q["iid"], q["title"], q["url"], q["kind_label"], q["question"], q["at"]) == (
        "D-3", 41, "退款审核", "%s#note_%d" % (url, d3["id"]), "澄清", PARK["question"], T0 - 5 * H)
    assert q["options"] == [{"id": "A", "text": "要"}, {"id": "B", "text": "不要"}]
    assert seen["last_at"] == T0 - 600 and seen["at"] == T0 and got.body["recent_h"] == 24
    assert "alice-tok" not in got.text


def test_a_q_question_waits_on_me_until_a_reply_names_its_number(tmp_path, home, monkeypatch):
    """brain-kit 81380eb asks Q-<n>, and `answer D-<n>` (as people answered before) still answers Q-<n>, in either case,
    unless that D-<n> is one of the acceptance question's own review items. A question void since is not waiting."""
    clock, gl, svc, routes = app(tmp_path, monkeypatch)
    acceptance = {"question": "可以验收了吗？", "options": [], "lean_request": "RELEASE 1234abcd",
                  "review_items": [{"id": "D-8", "text": "退款按自然日算"}]}

    async def go():
        await svc.start()
        try:
            ask = mod("bk").m("bridge.decisions").question_text
            at(clock, H)
            gl.mr(P, 42, title="退款导出", labels=["ai-running"])
            for did, park, kind in (("Q-7", PARK, "clarify"), ("Q-8", acceptance, "acceptance"),
                                    ("Q-9", PARK, "clarify"), ("Q-10", PARK, "clarify"), ("Q-12", PARK, "clarify")):
                park_hash = ("%02d" % int(did[2:])) * 32
                note(gl, 42, "brain-bridge", ask(did, "RUN-PAY-2-1", park, ["alice"], kind, "a" * 40, park_hash))
            note(gl, 42, "alice", "answer D-7 A")                 # the old habit: answers Q-7
            note(gl, 42, "alice", "answer D-8 approve")           # D-8 is Q-8's review item: no answer to it
            note(gl, 42, "brain-bridge", "Q-10 作废：问题发出后分支又有了新推送。run 需要时会重新提问。")
            note(gl, 42, "alice", "answer q-12 B")
            at(clock, 0)
            return await call(routes, "GET", "/team/status")
        finally:
            await svc.stop()

    got = run(go())
    assert got.status == 200, got.body
    assert [q["id"] for q in got.body["repos"][0]["runner"]["seen"]["waiting"]] == ["Q-8", "Q-9"]


def test_without_a_token_or_when_gitlab_refuses_it_says_so_and_keeps_the_last_read(tmp_path, home, monkeypatch):
    clock, gl, svc, routes = app(tmp_path, monkeypatch)
    monkeypatch.delenv("GITLAB_TOKEN")
    tick = [0.0]
    svc.runners.clock = lambda: tick[0]
    view = mod("runner_view")

    async def go():
        await svc.start()
        try:
            a_day_of_the_runner(clock, gl)
            got = [(await call(routes, "GET", "/team/status")).body]
            asked = len(gl.calls)
            monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
            got.append((await call(routes, "GET", "/team/status")).body)       # within TTL_S: not read again
            tick[0] += view.TTL_S + 1
            got.append((await call(routes, "GET", "/team/status")).body)
            gl.who.pop("alice-tok")                                             # the token revoked
            tick[0] += view.TTL_S + 1
            got.append((await call(routes, "GET", "/team/status")).body)
            return asked, got
        finally:
            await svc.stop()

    asked, (none, cached, read, refused) = run(go())
    runner = none["repos"][0]["runner"]
    assert runner["seen"] is None and runner["problem"]["text"] == "这台机器上没有你的 GitLab 令牌，读不了团队运行机"
    assert "~/.brain/gitlab-token" in runner["problem"]["todo"]
    assert not any("/merge_requests" in c[1] for c in gl.calls[:asked]), "no token: GitLab is not asked"
    assert cached["repos"][0]["runner"] == runner
    assert read["repos"][0]["runner"]["problem"] is None and len(read["repos"][0]["runner"]["seen"]["at_work"]) == 2
    stale = refused["repos"][0]["runner"]
    assert stale["seen"] == read["repos"][0]["runner"]["seen"]
    assert (stale["problem"]["text"], stale["problem"]["todo"]) == ("GitLab 不认你的令牌", "看看你的 GitLab 令牌有没有过期，有没有 read_api 权限")
    assert "401" in stale["problem"]["raw"]
    # the page's fixture has exactly the keys the backend answers with: a read, a repo without a runner, a failed read
    fix = fixture("team-status.json")
    assert diff_keys(fix, read) == []
    assert diff_keys(next(r["runner"] for r in fix["repos"] if (r["runner"] or {}).get("problem")), stale) == []


@pytest.mark.parametrize("kit", [{"bridge_user": "brain-bridge"}, {"bridge_user": "brain-bridge",
                                                                   "gitlab": {"base": BASE, "project": ""}}])
def test_a_runner_repo_without_a_gitlab_project_says_what_kit_json_lacks(tmp_path, home, monkeypatch, kit):
    monkeypatch.setenv("GITLAB_TOKEN", "alice-tok")
    gl = PatrolGitLab(Clock(T0))
    svc = make_service(tmp_path, [make_project(tmp_path, name="ops", prefix="OPS", kit=kit)], transport=gl)

    async def go():
        await svc.start()
        try:
            return await svc.runner_status()
        finally:
            await svc.stop()

    runner = run(go())["repos"][0]["runner"]
    assert runner["seen"] is None and runner["problem"]["text"] == "这个仓库的 kit.json 没写 GitLab 项目，读不了团队运行机"
    assert not any("/merge_requests" in c[1] for c in gl.calls)
