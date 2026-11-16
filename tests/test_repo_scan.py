"""A repo's scan finds what changed without starting git when git's refs and worktrees stayed the same: every page's
first step is a scan, and its three git calls per repo were most of /status (a real brain-kit project here)."""

from appkit import commit_file, mod
from brainfix import brain, make_project, make_service, needs_brainkit, sh, start_in_worktree

pytestmark = needs_brainkit


def logged_git(tmp_path, monkeypatch):
    log = tmp_path / "git-calls"
    shim = tmp_path / "gitbin" / "git"
    shim.parent.mkdir()
    shim.write_text("#!/bin/sh\necho \"$*\" >> '%s'\nexec '%s' \"$@\"\n" % (log, mod("repo").GIT_BIN), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setattr(mod("repo"), "GIT_BIN", str(shim))
    return lambda: len(log.read_text().splitlines()) if log.exists() else 0


def keys(repo):
    return {f["key"]: f for f in repo.scan(ttl=0)}


def test_a_scan_that_finds_nothing_new_starts_no_git_and_sees_each_change_at_once(tmp_path, home, monkeypatch):
    repo_dir = make_project(tmp_path, origin=True)
    brain(repo_dir, "claim", "--title", "只认领")
    sh("git", "-C", repo_dir, "push", "-q", "origin", "claim/PAY-1")
    svc = make_service(tmp_path, [repo_dir])
    svc.reload_sync()
    repo = svc.repos["r1"]
    gits = logged_git(tmp_path, monkeypatch)
    assert set(keys(repo)) == {"PAY-1"}
    n = gits()
    assert set(keys(repo)) == {"PAY-1"} and gits() == n               # nothing new: no git at all
    assert repo.has_origin() and repo.has_origin() and gits() == n + 1
    assert repo.reflected() == set() and repo.reflected() == set() and gits() == n + 2
    sh("git", "-C", repo_dir, "branch", "-q", "brain/PAY-1-reflect-1")
    assert repo.reflected() == {"PAY-1"}                               # a reflect branch: seen at once
    n = gits()
    main = sh("git", "-C", repo_dir, "rev-parse", "origin/main").stdout.strip()
    assert repo.main_sha() == main and gits() == n                     # from the refs the scan listed

    wt = start_in_worktree(repo_dir, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    got = keys(repo)                                                   # a new branch and its worktree
    assert got["PAY-2"]["worktree"] == str(wt) and got["PAY-2"]["where"] == "worktree"
    sh("git", "-C", repo_dir, "branch", "-q", "claim/PAY-1-old", "claim/PAY-1")
    sh("git", "-C", repo_dir, "pack-refs", "--all")                   # refs packed: the loose files are gone
    assert set(keys(repo)) == {"PAY-1", "PAY-2"}
    sh("git", "-C", repo_dir, "branch", "-q", "-D", "claim/PAY-1")
    sh("git", "-C", repo_dir, "push", "-q", "origin", "--delete", "claim/PAY-1")
    sh("git", "-C", repo_dir, "fetch", "-q", "--prune", "origin")
    assert set(keys(repo)) == {"PAY-2"}                                # a packed ref deleted
    commit_file(repo_dir, "runs/PAY-9/run.json", '{"key": "PAY-9", "intent": "261108-pay-9-x"}', "merged run")
    sh("git", "-C", repo_dir, "push", "-q", "origin", "main")
    assert keys(repo)["PAY-9"]["merged"] is True                       # origin's main moved
    assert repo.main_sha() == sh("git", "-C", repo_dir, "rev-parse", "origin/main").stdout.strip() != main
    sh("git", "-C", repo_dir, "remote", "remove", "origin")
    assert not repo.has_origin()                                       # the remote is gone

    monkeypatch.setattr(mod("repo"), "PLACES_TTL_S", 0.0)
    n = gits()
    keys(repo)
    assert gits() > n                                                  # asked again after PLACES_TTL_S anyway
