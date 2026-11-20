"""What a fast run reads of its MR on GitLab, in the background and on its row: only while the MR can still change
(a teammate's open one, the person's own once it is opened, a first look at app start; never a merged or closed one),
and a copy GitLab cannot refresh said to be old."""

from appkit import BRAINKIT, mod
from brainfix import needs_brainkit, run
from test_fast_flow import START, idle, project, service as fast_service
from test_kitswitch import TOKEN, GitLab

pytestmark = needs_brainkit
fastrow = mod("fastrow")


def gets(gl, branch):
    return [c for c in gl.calls if c[0] == "GET" and c[1].endswith("/merge_requests") and c[2].get("source_branch") == branch]


def test_a_fast_runs_mr_is_read_while_it_can_change_and_no_longer(tmp_path, home, monkeypatch):
    repo = project(tmp_path)
    gl = GitLab((repo / ".brain" / "kit.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(mod("mrinfo"), "TTL_S", 0.0)
    svc, gw = fast_service(tmp_path, home, repo)
    svc.transport = gl

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            monkeypatch.setenv("GITLAB_TOKEN", TOKEN)      # after the start: brain start would ask GitLab for the key
            br, seen = out["branch"], {}
            await svc.overview("all")
            await svc.refresh_mrs()
            seen["first look"] = len(gets(gl, br))
            await svc.overview("all")
            await svc.refresh_mrs()
            seen["no MR, read once"] = len(gets(gl, br)) - seen["first look"]
            gl.mrs.append({"iid": 12, "source_branch": br, "target_branch": "main", "state": "opened", "sha": "d" * 40,
                           "labels": [], "web_url": "https://gitlab.example.com/grp/pay/-/merge_requests/12"})
            svc.mr.refresh("https://gitlab.example.com", "grp/pay", br, transport=gl)     # as 开 MR does
            await svc.overview("all")
            before = len(gets(gl, br))
            await svc.refresh_mrs()
            seen["opened"] = len(gets(gl, br)) - before
            gl.mrs[-1]["state"] = "merged"
            await svc.refresh_mrs()
            await svc.overview("all")
            before = len(gets(gl, br))
            await svc.refresh_mrs()
            seen["merged"] = len(gets(gl, br)) - before
            return seen
        finally:
            await svc.stop()

    assert run(go()) == {"first look": 1, "no MR, read once": 0, "opened": 1, "merged": 0}


def test_polls_mr_follows_who_can_still_change_it():
    f = {"merged": False}
    opened, merged, closed = {"state": "opened"}, {"state": "merged"}, {"state": "closed"}
    assert fastrow.polls_mr(f, None, mine=True, read=False), "a first look"
    assert not fastrow.polls_mr(f, None, mine=True, read=True), "no MR yet: 开 MR reads it when it opens one"
    assert fastrow.polls_mr(f, opened, mine=True, read=True)
    assert fastrow.polls_mr(f, None, mine=False, read=True), "a teammate opens it on their machine"
    assert not any(fastrow.polls_mr(f, m, mine=mine, read=True) for m in (merged, closed) for mine in (True, False))
    assert not fastrow.polls_mr({"merged": True}, opened, mine=False, read=True)


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
