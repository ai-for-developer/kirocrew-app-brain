"""需求详情 and 需求看板 poll every 2 to 5 s: brain-kit's trace of a requirement (a dozen git calls and more) is made
again only when something it is made from changed, not every 15 s."""

from appkit import git_calls, sh
from brainfix import lean, make_project, make_service, needs_brainkit, start_in_worktree


@needs_brainkit
def test_the_trace_is_made_again_only_when_what_it_reads_changed(tmp_path, home, monkeypatch):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款导出")
    svc = make_service(tmp_path, [repo])
    svc.reload_sync()
    r = svc.repos["r1"]
    f = svc._facts(r, "PAY-1")
    snap = svc._sessions_snapshot()
    clock = [svc.now()]
    svc.now = lambda: clock[0]
    calls = git_calls(monkeypatch)

    def timeline():
        calls.clear()
        tl = svc._timeline(r, f, snap, "r1:PAY-1")
        return [(i["at"], i["kind"]) for i in tl["items"]], len(calls)

    first, n = timeline()
    assert n > 0
    assert timeline() == (first, 0), "nothing changed: no git"
    lean(wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
    got, n = timeline()
    assert n > 0 and len(got) > len(first), "Lean wrote its ledger in the worktree, nothing committed: read at once"
    assert timeline() == (got, 0)
    sh("git", "-C", repo, "branch", "PAY-1-elsewhere", "main")
    assert timeline()[1] > 0, "a branch moved"
    assert timeline()[1] == 0
    clock[0] += 3600
    assert timeline()[1] > 0, "the 14 days after a merge are judged at most an hour late"
