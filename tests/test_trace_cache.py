"""需求现状 and 运行过程 poll every 2 to 5 s: brain-kit's trace of a requirement (a dozen git calls and more) is made
again only when something it is made from changed, not every 15 s."""

import subprocess
import time
from types import SimpleNamespace

from appkit import git_calls, mod, sh
from brainfix import lean, make_project, make_service, needs_brainkit, run, start_in_worktree


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


@needs_brainkit
def test_a_trace_that_failed_once_is_made_again_on_a_later_poll(tmp_path, home, monkeypatch):
    """A failed read (git timed out under load) is not the answer while nothing it reads moves: within 15 s the
    需求现状 says brain-kit could not read the run, after that it shows the trace."""
    repo = make_project(tmp_path)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款导出")
    svc = make_service(tmp_path, [repo])
    svc.reload_sync()
    t0 = time.monotonic()
    clock = [t0]
    shim = SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")})
    shim.monotonic = lambda: clock[0]
    monkeypatch.setattr(mod("service"), "time", shim)
    trace = mod("detail").bk.m("trace")
    real = trace.build
    fail = [True]

    def build(*a, **k):
        if fail[0]:
            raise RuntimeError("git for-each-ref timed out")
        return real(*a, **k)

    monkeypatch.setattr(trace, "build", build)

    def error():
        return run(svc.requirement("r1", "PAY-1"))["timeline"]["error"]

    assert "git for-each-ref timed out" in error()
    fail[0] = False
    clock[0] = t0 + 5
    assert "git for-each-ref timed out" in error(), "a failed read is kept for a few seconds, not made on every poll"
    clock[0] = t0 + 30
    assert error() == ""


@needs_brainkit
def test_a_trace_made_while_a_quiet_git_call_failed_is_made_again_within_a_minute(tmp_path, home, monkeypatch):
    """brain-kit reads some git calls without a check (trace.py _g, _show, _ancestor): one that failed reads as "not
    there" and its trace has no error. The trace cache cannot tell, so a kept trace lives at most a minute."""
    repo = make_project(tmp_path)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款导出")
    svc = make_service(tmp_path, [repo])
    svc.reload_sync()
    t0 = int(svc.now() // 60) * 60 + 5
    clock = [t0]
    svc.now = lambda: clock[0]
    trace = mod("detail").bk.m("trace")
    fail = [True]
    for name, none in (("_g", None), ("_show", None), ("_ancestor", False)):
        real = getattr(trace, name)
        monkeypatch.setattr(trace, name, (lambda real, none: lambda *a, **k: none if fail[0] else real(*a, **k))(real, none))

    def page(s):
        t = run(s.requirement("r1", "PAY-1"))["timeline"]
        return {k: t.get(k) for k in ("items", "missing", "error")}

    first = page(svc)
    fail[0] = False
    clock[0] = t0 + 60
    later = page(svc)
    cold = make_service(tmp_path, [repo])
    cold.reload_sync()
    fresh = page(cold)
    assert first["error"] == "" and first != fresh, "the failed calls change the trace and leave no error"
    assert later == fresh


def test_a_git_config_that_failed_once_is_read_again(tmp_path, home, monkeypatch):
    """`git config brain.intake` timed out once (rc 124): that is not "not set", so the trace still reads the
    configured intake's vouchers (hand-overs, bridge answers) on the next poll."""
    monkeypatch.delenv("BRAIN_INTAKE_DIR", raising=False)
    repo = make_project(tmp_path)
    intake = tmp_path / "runner-intake"
    (intake / "vouchers").mkdir(parents=True)
    sh("git", "-C", repo, "config", "brain.intake", intake)
    svc = make_service(tmp_path, [repo])
    svc.reload_sync()
    r = svc.repos["r1"]
    Git = mod("repo").Git
    real = Git.run
    fail = [True]

    def git_run(self, *args, **kw):
        if fail[0] and args[:1] == ("config",):
            return subprocess.CompletedProcess(args, 124, "", "timed out")
        return real(self, *args, **kw)

    monkeypatch.setattr(Git, "run", git_run)
    assert svc._vouchers_dir(r) is None
    fail[0] = False
    assert [svc._vouchers_dir(r) for _ in range(2)] == [str(intake / "vouchers")] * 2
