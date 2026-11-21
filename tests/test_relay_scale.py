"""全队交付's load on GitLab (team/relay.py Deliveries), offline: a project whose Lean ledger has many audit files, one
per clone that ran Lean, served from a bare repo (relayfix.RepoGitLab).

A requirement's files are read by blob id: a new head reads only the files that changed since a head read before,
however many audit files it holds. A folder of more than 2000 files, or a default branch with more than 2000 merged
requirements, is read as far as 2000 and said; the project's rows are still made."""

import json
import subprocess

import pytest

from appkit import mod, sh
from brainfix import needs_brainkit
from relayfix import BASE, RepoGitLab

pytestmark = needs_brainkit
P, KEY, INTENT, BRANCH = "shop/pay", "PAY-9", "261121-pay", "ai/bob/PAY-9-pay"


@pytest.fixture
def bk():
    from appkit import BRAINKIT
    m = mod("bk")
    assert m.load(str(BRAINKIT))["ok"]
    yield m
    m.unload()


def write(work, files):
    for rel, text in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    sh("git", "-C", work, "add", "-A")
    sh("git", "-C", work, "commit", "-qm", "x")
    return sh("git", "-C", work, "rev-parse", "HEAD").stdout.strip()


def audit(n, *, same=False):
    """Lean's ledger as n clones left it: one file each (*same*: all of them alike)."""
    return {"aidlc-docs/audit/host-%06x.jsonl" % i: json.dumps(
        {"intent": "other" if same else "other-%d" % i, "event": "STAGE_STARTED", "ts": "2026-11-01T00:00:00Z"}) + "\n"
        for i in range(n)}


def project(tmp_path, files):
    """shop/pay with main, and bob's run branch of PAY-9 holding its run.json, Lean's note and *files*."""
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    write(work, {"README.md": "x\n"})
    sh("git", "-C", work, "checkout", "-q", "-b", BRANCH)
    write(work, dict({"runs/%s/run.json" % KEY: json.dumps({"key": KEY, "intent": INTENT, "owner": "bob"}),
                      "aidlc-docs/%s/aidlc-state.md" % INTENT: "# state\n"}, **files))
    gl = RepoGitLab()
    gl.project(P, 7, work / ".git")
    return work, gl


def deliveries(bk, gl):
    kit = {"branch_prefix": "ai", "runs_dir": "runs", "default_branch": "main"}
    return mod("team.relay").Deliveries(bk.m("rest").GitLab(BASE, "bridge-tok", gl), BASE, lambda p: kit)


def blob_reads(gl, since):
    return [c for c in gl.calls[since:] if "/repository/blobs/" in c[1]]


def test_a_new_head_reads_only_the_files_that_changed_however_many_audit_files_it_holds(tmp_path, bk):
    work, gl = project(tmp_path, audit(200))
    d = deliveries(bk, gl)
    first = d.read(P, deployed="", roles={})
    assert [r["key"] for r in first.rows] == [KEY] and first.notes == []
    assert len(blob_reads(gl, 0)) == 202                  # run.json, aidlc-state.md and the 200 audit files, once each
    write(work, {"aidlc-docs/audit/host-000007.jsonl": json.dumps(
        {"intent": INTENT, "event": "STAGE_STARTED", "ts": "2026-11-02T00:00:00Z"}) + "\n"})
    since = len(gl.calls)
    again = d.read(P, deployed="", roles={})
    changed = blob_reads(gl, since)
    assert len(changed) == 1, changed                      # the one audit file that changed
    assert len(gl.calls) - since < 15                      # the folders listed again at the new head, not 200 files
    assert again.rows[0]["updated_at"] >= first.rows[0]["updated_at"]


def test_past_2000_files_or_merged_requirements_what_fits_is_read_and_said(tmp_path, bk):
    work, gl = project(tmp_path, audit(2001, same=True))
    sh("git", "-C", work, "checkout", "-q", "main")
    write(work, {"runs/PAY-%d/run.json" % n: "{}\n" for n in range(1000, 3001)})
    relay = mod("team.relay")
    got = deliveries(bk, gl).read(P, deployed="", roles={})
    assert [r["key"] for r in got.rows] == [KEY]
    assert got.notes == [relay.CUT_RUNS % "runs", relay.CUT % "aidlc-docs/audit"]
    assert got.notes == ["默认分支的 runs 里的需求超过 2000 个，只读了前 2000 个：再早合并的不在全队交付里",
                         "aidlc-docs/audit 里的文件超过 2000 个，只读了前 2000 个：Lean 的记录可能不全"]
