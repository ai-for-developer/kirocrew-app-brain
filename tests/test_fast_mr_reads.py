"""What a fast run reads of its MR on GitLab, in the background and on its row: until it is merged, whoever's run it
is, since nothing on this machine says when its MR opens, closes or opens again (开 MR here, `brain mr` in a terminal
or the AI's session, GitLab itself) and a read GitLab did not answer is tried again; a copy GitLab cannot refresh said
to be old, and without a GitLab token a 开 MR that pushed the branch says its MR is opened on GitLab by hand instead of
asking for 开 MR again."""

from urllib.parse import parse_qsl, urlsplit

from appkit import BRAINKIT, mod, sh
from brainfix import lean, needs_brainkit, run
from test_fast_flow import START, idle, project, service as fast_service, verified_commit
from test_kitswitch import TOKEN, GitLab

pytestmark = needs_brainkit
fastrow = mod("fastrow")


def test_the_owners_fast_run_follows_its_mr_on_gitlab_until_it_is_merged(tmp_path, home, monkeypatch):
    repo = project(tmp_path)
    gl = GitLab((repo / ".brain" / "kit.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(mod("mrinfo"), "TTL_S", 0.0)
    monkeypatch.setattr(mod("service"), "MR_EVERY_S", 3600.0)    # only the rounds below read GitLab
    svc, gw = fast_service(tmp_path, home, repo)
    asked, down = [], {"branch": None}

    def gitlab(method, url, headers, body):
        u = urlsplit(url)
        if method == "GET" and u.path.endswith("/merge_requests"):
            br = dict(parse_qsl(u.query)).get("source_branch")
            asked.append(br)
            if br == down["branch"]:
                down["branch"] = None
                raise OSError("GitLab is down")
        return gl(method, url, headers, body)
    svc.transport = gitlab

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            monkeypatch.setenv("GITLAB_TOKEN", TOKEN)      # after the start: brain start would ask GitLab for the key
            br, seen = out["branch"], {}
            tip = verified_commit(home / "workplace" / "brain-app" / "pay" / "coupon", "shop/coupon.py", "RATE = 0.9\n")

            async def look(name):
                svc._repo("r1").invalidate()
                await svc.overview("all")
                before = asked.count(br)
                await svc.refresh_mrs()
                svc._repo("r1").invalidate()
                row = {r["key"]: r for r in (await svc.overview("all"))["rows"]}[out["key"]]
                seen[name] = (asked.count(br) - before, row["state"], (row.get("mr") or {}).get("state"))

            await look("no MR yet")
            await look("no MR yet, again")
            gl.mrs.append({"iid": 12, "source_branch": br, "target_branch": "main", "state": "opened", "sha": tip,
                           "labels": [], "web_url": "https://gitlab.example.com/grp/pay/-/merge_requests/12"})
            down["branch"] = br
            await look("brain mr in a terminal opened it, GitLab down")
            await look("GitLab back")
            gl.mrs[-1]["state"] = "closed"
            await look("closed")
            gl.mrs[-1]["state"] = "opened"
            await look("opened again on GitLab")
            gl.mrs[-1]["state"] = "merged"
            await look("merged")
            await look("after the merge")
            return seen
        finally:
            await svc.stop()

    assert run(go()) == {
        "no MR yet": (1, "local", None), "no MR yet, again": (1, "local", None),
        "brain mr in a terminal opened it, GitLab down": (1, "local", None), "GitLab back": (1, "local", "opened"),
        "closed": (1, "closed", "closed"), "opened again on GitLab": (1, "local", "opened"),
        "merged": (1, "merged", "merged"), "after the merge": (0, "merged", "merged")}


def test_polls_mr_until_it_is_merged():
    f = {"merged": False}
    assert all(fastrow.polls_mr(f, mr) for mr in (None, {"state": "opened"}, {"state": "closed"}))
    assert not fastrow.polls_mr(f, {"state": "merged"})
    assert not fastrow.polls_mr({"merged": True}, {"state": "opened"}), "the run is on the default branch"


def test_a_row_that_never_reads_the_git_half_skips_it():
    class Repo:
        path, cfg = "/nowhere", {"work_sources": []}

        def main_sha(self):
            return "a" * 40

        @property
        def git(self):
            raise AssertionError("no git read")

    got = fastrow.fast_facts(Repo(), {"tip": "b" * 40, "events": []}, {}, git=False)
    assert (got.code_commits, got.risky) == (0, None)


def test_without_a_token_open_mr_says_its_mr_is_opened_on_gitlab(tmp_path, home):
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            wt = home / "workplace" / "brain-app" / "pay" / "coupon"
            verified_commit(wt, "shop/coupon.py", "RATE = 0.9\n")
            opened = await svc.act("r1", out["key"], "mr")
            row = {r["key"]: r for r in (await svc.overview("all"))["rows"]}[out["key"]]
            todo = [(i["kind"], i["key"]) for i in (await svc.todo())["items"]]
            board = await svc.board("r1", out["key"])
            (wt / "shop" / "coupon.py").write_text("RATE = 0.85\n", encoding="utf-8")
            lean(wt, "verify")
            sh("git", "-C", wt, "commit", "-qam", "a better rate")
            svc._repo("r1").invalidate()
            later = {r["key"]: r for r in (await svc.overview("all"))["rows"]}[out["key"]]
            return out, opened, row, todo, board, later
        finally:
            await svc.stop()

    out, opened, row, todo, board, later = run(go())
    assert opened["ok"] and opened["pushed"] and opened["mr"] is None
    w = row["waits"]
    assert (w["who"], w["do"], w["where"], w["text"]) == ("people", "merge", "mr", fastrow.BY_HAND)
    assert ("open_mr", out["key"]) not in todo, "待办 no longer asks for 开 MR"
    steps = {s["id"]: (s["status"], s["text"]) for s in board["steps"]}
    assert steps["mr"][0] == "done" and steps["merge"] == ("waiting", "等人审、合并")
    assert (later["waits"]["do"], later["waits"]["text"]) == ("open_mr", fastrow.READY), "a new commit: 开 MR again"


def test_an_mr_copy_gitlab_cannot_refresh_is_said_to_be_old(monkeypatch):
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    br, base, project = "ai/alice/PAY-1-coupon", "https://gitlab.example.com", "grp/pay"
    try:
        monkeypatch.setenv("GITLAB_TOKEN", TOKEN)
        gl = GitLab("{}")
        gl.mrs.append({"iid": 12, "source_branch": br, "target_branch": "main", "state": "opened", "sha": "d" * 40,
                       "labels": [], "web_url": base + "/grp/pay/-/merge_requests/12"})
        cache = mod("mrinfo").MrCache()
        cache.refresh(base, project, br, transport=gl)
        fresh = cache.stale_since(project, br)

        def down(*_):
            raise OSError("GitLab is down")
        cache.refresh(base, project, br, transport=down)
        old, kept = cache.stale_since(project, br), cache.get(project, br)
        cache.refresh(base, project, br, transport=gl)
        again = cache.stale_since(project, br)
    finally:
        bk.unload()
    assert fresh is None and again is None
    assert old is not None and kept["iid"] == 12, "the copy before stays, said to be old"
    row = mod("deliveries").base_row("r1", {"key": "PAY-1"})
    f = {"key": "PAY-1", "owner": "alice", "claim": {"owner": "alice"}, "run": {"mode": "fast", "intent": None},
         "approvals": [], "park": None, "events": [], "worktree": "/w", "branch": br, "tip_at": 1.0}
    mod("deliveries").classify(row, f, mr=dict(kept, pipelines=None), slot=None, cfg={}, me="alice", now=2.0,
                               fast=fastrow.FastFacts(code_commits=1, verify=None, risky=(), patrol=False, stale_s=1800.0))
    assert row["waits"]["text"] == "MR !12：等人审、合并（GitLab 这会儿读不到：这是 30 分钟前读到的）"
