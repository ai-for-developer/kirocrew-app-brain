"""产出 of a fast run, by its commits (GET /outputs/{repo}/{key}/commits): each commit of the run branch with the code
it changed, and all of it added up, read and compared at the run's own commits as the Lean drawer reads a round's."""

from appkit import mod, sh
from brainfix import lean, needs_brainkit, run
from test_fast_flow import START, idle, project, service as fast_service

pytestmark = needs_brainkit
outputs = mod("outputs")


def commit(wt, files, subject):
    for path, text in files.items():
        p = wt / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    lean(wt, "verify")
    sh("git", "-C", wt, "add", "-A")
    sh("git", "-C", wt, "commit", "-qm", subject)
    return sh("git", "-C", wt, "rev-parse", "HEAD").stdout.strip()


def test_a_fast_runs_drawer_lists_each_commits_code_and_all_of_it(tmp_path, home):
    repo = project(tmp_path)
    svc, gw = fast_service(tmp_path, home, repo)

    async def go():
        await svc.start()
        try:
            out = await svc.intake_start(dict(START))
            await idle(svc, gw, out["slot"])
            wt = home / "workplace" / "brain-app" / "pay" / "coupon"
            one = commit(wt, {"shop/coupon.py": "RATE = 0.9\nMIN = 100\n",
                              "tests/test_coupon.py": "from shop import coupon\n\n\ndef test_rate():\n"
                                                      "    assert coupon.RATE == 0.9\n"}, "coupon codes")
            two = commit(wt, {"shop/coupon.py": "RATE = 0.85\nMIN = 100\n"}, "a better rate")
            svc._repo("r1").invalidate()
            await svc.board("r1", out["key"])
            got = await svc.outputs_commits("r1", out["key"])
            q = {"ref": two, "base": one, "path": "shop/coupon.py", "view": "diff"}
            diff = await svc.outputs_file("r1", out["key"], outputs.file_query(q))
            source = await svc.outputs_file("r1", out["key"], outputs.file_query(dict(q, view="source", base="")))
            return out, one, two, got, diff, source
        finally:
            await svc.stop()

    out, one, two, got, diff, source = run(go())
    by = {c["sha"]: c for c in got["commits"]}
    assert [c["sha"] for c in got["commits"]][-2:] == [one, two] and got["head"] == two
    assert got["branch"] == out["branch"] and got["why"] is None
    assert {(f["path"], f["status"], f["ref"], f["base"]) for f in by[one]["files"] if not f["path"].startswith("runs/")} \
        == {("shop/coupon.py", "A", one, None), ("tests/test_coupon.py", "A", one, None)}
    assert [(f["path"], f["status"], f["added"], f["removed"], f["ref"], f["base"]) for f in by[two]["files"]] == [
        ("shop/coupon.py", "M", 1, 1, two, one)]
    whole = {f["path"]: f for f in got["files"]}
    assert (whole["shop/coupon.py"]["status"], whole["shop/coupon.py"]["ref"], whole["shop/coupon.py"]["added"]) == (
        "A", two, 3), "added up: added by the first commit, read at the last"
    assert not [f for c in got["commits"] for f in c["files"] if "aidlc-docs/audit" in f["path"]], "Lean's ledger never"
    assert "-RATE = 0.9" in diff["text"] and "+RATE = 0.85" in diff["text"]
    assert source["text"] == "RATE = 0.85\nMIN = 100\n"


def test_a_run_with_no_commit_of_its_own_says_why():
    fold = outputs.Fold(reader=outputs.GitReader(mod("repo").Git("/nowhere")), intent=None, branch="ai/a/X-1-y",
                        nodes=(), commits=(), changes={})
    got = outputs.commits_output(fold)
    assert (got["commits"], got["files"], got["head"], got["why"]) == ([], [], None, outputs.NO_COMMITS)
