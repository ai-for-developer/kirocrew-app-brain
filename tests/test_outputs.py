"""产出 (GET /outputs/{repo}/{key}?node&round and /outputs/{repo}/{key}/file): what each round of a stage handed in,
read on real git repositories. The routes are the dashboard owner's only; a path is a repo-relative name; only the
requirement's own commits are read, a symlink never; the one worktree read is pinned to the sha256 Lean recorded at
the stage table's document path; a diff runs no diff driver the repo names; Lean's ledgers are never shown; an email
never reaches the page."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from appkit import commit_file, git_repo, mod, sh
from brainfix import lean, make_project, make_service, needs_brainkit, request_requirements, run, start_in_worktree

outputs = mod("outputs")
stages = mod("stages")
board = mod("board")
Git = mod("repo").Git
AppError = mod("httpkit").AppError

INTENT = "261111-refund-sms"
RELEASE = "aidlc-docs/%s/operations/release.md" % INTENT
DESIGN = "aidlc-docs/%s/inception/application-design/application-design.md" % INTENT
REQS = "aidlc-docs/%s/inception/requirements/requirements.md" % INTENT


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def rnd(n, commits=(), outputs_=(), run_="local", ask=None, started=0.0, ended=1.0):
    return stages.Round(n=n, run=run_, started=started, ended=ended, end=stages.RoundEnd(stages.Outcome.DONE),
                        why=stages.Why("first"), note=None, outputs=tuple(outputs_), commits=tuple(commits), ask=ask)


def node(stage_id, rounds, unit=None):
    return stages.Node(stages.node_id(stage_id, unit), stages.BY_ID[stage_id], unit, list(rounds), [],
                       stages.NodeStatus("done", "", None))


def fold(repo, nodes, shas, worktree=None, units=()):
    """The board's fold as Service._board_sync keeps it, its commits and changes read by the board's own parser."""
    git = Git(repo)
    log = git.run(*board.GIT_LOG, *shas).stdout
    changes = board.changes_from_log(log)
    commits = tuple({"sha": s, "at": float(i), "paths": [c["path"] for c in changes.get(s) or []]} for i, s in enumerate(shas))
    return outputs.Fold(reader=outputs.GitReader(git), intent=INTENT, branch="ai/alice/PAY-9-refund-sms", nodes=tuple(nodes),
                        commits=commits, changes=changes, units=tuple(units), worktree=worktree)


def view(f, **q):
    return outputs.file_view(f, outputs.file_query(dict({"view": "source"}, **q)))


def refused(fn, status, code):
    with pytest.raises(AppError) as e:
        fn()
    assert (e.value.status, e.value.code) == (status, code), str(e.value)
    return str(e.value)


@pytest.fixture
def pay9(tmp_path):
    """A run branch: the release asked for twice (round 1 refused, round 2 now), a code change between them."""
    repo = git_repo(tmp_path / "pay")
    base = commit_file(repo, "README.md", "shop\n", "main")
    v1 = "# Release\n\n| what | by |\n|---|---|\n| INCEPTION | alice@example.com |\n"
    r1 = commit_file(repo, RELEASE, v1, "请批发布")
    code = commit_file(repo, "services/api/notify.py", "def send(order):\n    return True\n", "api 的短信通知")
    v2 = v1 + "| RELEASE | Bob.Smith@Example.COM |\n\n旧订单不发短信。\n"
    r2 = commit_file(repo, RELEASE, v2, "请批发布（第 2 轮）")
    rel = node("release", [rnd(1, [r1], [(RELEASE, h(v1))]), rnd(2, [code, r2], [(RELEASE, h(v2))],
                                                                   ask=stages.Ask("RELEASE", ((RELEASE, h(v2)),), 1.0, "local"))])
    return SimpleNamespace(repo=repo, base=base, r1=r1, r2=r2, code=code, v1=v1, v2=v2,
                           fold=fold(repo, [rel], [r1, code, r2]))


# ---------------------------------------------------------------- the routes: the owner's only, paths checked first

def routes(tmp_path, owner, fold_=None):
    """The two route handlers as the host calls them; *fold_*: the fold they read in place of the board's."""
    import backend.routes as br
    from http_fakes import FakeWeb

    class Route:
        def __init__(self, method, path, handler):
            self.method, self.path, self.handler = method, path, handler

    svc = br.service.Service(SimpleNamespace(name="brain", logger=None, data_dir=str(tmp_path / "data")))
    if fold_ is not None:
        svc._fold = lambda rid, key, snap: fold_
    r = {(x.method, x.path): x.handler for x in br.handlers.build_routes(svc, Route, web=FakeWeb(), is_owner=lambda _r: owner)}
    return r[("GET", "/outputs/{repo}/{key}")], r[("GET", "/outputs/{repo}/{key}/file")]


def call(h_, **req):
    from http_fakes import FakeRequest
    return asyncio.run(h_(FakeRequest(match_info={"repo": "r1", "key": "PAY-9"}, **req), None))


def test_the_drawer_routes_are_the_owners_only(tmp_path):
    for h_ in routes(tmp_path, owner=False):
        assert call(h_, user=None).status == 401
        assert call(h_, app="brain").status == 403          # an app token, this app's included
        resp = call(h_)
        assert resp.status == 403 and resp.body["code"] == "owner_only"


@pytest.mark.parametrize("path", ["../etc/passwd", "aidlc-docs/../../x", "/etc/passwd", "./a", "a//b", "a\\b", "a\nb", "x" * 401])
def test_a_path_that_is_not_a_repo_relative_name_is_400_before_anything_is_read(tmp_path, path):
    _, file_ = routes(tmp_path, owner=True)
    resp = call(file_, query={"ref": "a" * 40, "path": path})
    assert resp.status == 400 and resp.body["code"] == "invalid_path", resp


def test_a_bad_node_round_ref_or_pin_is_400(tmp_path):
    node_, file_ = routes(tmp_path, owner=True)
    assert call(node_, query={"node": "../x"}).body["code"] == "invalid_node"
    assert call(node_, query={"node": "release", "round": "0"}).body["code"] == "invalid_round"
    for q, code in (({"ref": "HEAD"}, "invalid_ref"), ({"ref": "a" * 40, "base": "main"}, "invalid_ref"),
                    ({"ref": "worktree", "pin": "abc"}, "invalid_pin"), ({"ref": "a" * 40, "view": "raw"}, "invalid_view")):
        resp = call(file_, query=dict({"path": RELEASE}, **q))
        assert (resp.status, resp.body["code"]) == (400, code), q


def test_lean_ledgers_are_never_read(tmp_path):
    _, file_ = routes(tmp_path, owner=True)
    resp = call(file_, query={"ref": "a" * 40, "path": "aidlc-docs/audit/alice-mbp-3f2a1c.jsonl"})
    assert resp.status == 403 and resp.body["code"] == "not_shown" and "有人在会话里打的字" in resp.body["error"]


# ---------------------------------------------------------------- what a round handed in

def test_a_round_lists_what_it_declared_first_at_the_version_lean_recorded_then_its_other_changes(pay9):
    got = outputs.node_output(pay9.fold, outputs.node_query("release", "2"))
    assert (got["round"], got["head"]) == (2, pay9.r2)
    rel, code = got["files"]
    assert (rel["path"], rel["output"], rel["ref"], rel["status"]) == (RELEASE, True, pay9.r2, "M")
    # 和第 1 轮比: the version round 1 handed in, the one Lean recorded for it
    assert (rel["base"], rel["base_round"], rel["vs"]) == (pay9.r1, 1, [{"round": 1, "ref": pay9.r1}])
    assert (code["path"], code["output"], code["status"], code["ref"], code["base"]) == (
        "services/api/notify.py", False, "A", pay9.code, None)
    first = outputs.node_output(pay9.fold, outputs.node_query("release", "1"))["files"]
    assert [(f["path"], f["ref"], f["status"], f["vs"]) for f in first] == [(RELEASE, pay9.r1, "A", [])]


def test_the_version_asked_for_is_said_and_emails_become_pseudonyms_everywhere(pay9):
    got = view(pay9.fold, ref=pay9.r2, path=RELEASE)
    assert got["asked"] == {"kind": "RELEASE", "sha8": h(pay9.v2)[:8]} and got["lang"] == "markdown"
    assert "G-" + hashlib.sha256(b"alice@example.com").hexdigest()[:6].upper() in got["text"]
    assert "G-" + hashlib.sha256(b"bob.smith@example.com").hexdigest()[:6].upper() in got["text"]
    diff = view(pay9.fold, ref=pay9.r2, base=pay9.r1, path=RELEASE, view="diff")
    assert diff["text"].startswith("diff --git a/") and "+旧订单不发短信。" in diff["text"]
    old = view(pay9.fold, ref=pay9.r1, path=RELEASE)
    assert old["asked"] is None                       # round 1's version was never the one asked now
    for payload in (got, diff, old, outputs.node_output(pay9.fold, outputs.node_query("release", None))):
        s = json.dumps(payload, ensure_ascii=False)
        assert "@" not in s.replace("@@", ""), s       # no email of any form left in what the page gets


def test_a_commit_outside_the_requirement_is_404_never_read(pay9):
    other = sh("git", "-C", pay9.repo, "commit-tree", "-m", "x", sh("git", "-C", pay9.repo, "write-tree").stdout.strip()).stdout.strip()
    refused(lambda: view(pay9.fold, ref=other, path=RELEASE), 404, "unknown_commit")
    refused(lambda: view(pay9.fold, ref=pay9.r2, base=other, path=RELEASE, view="diff"), 404, "unknown_commit")
    # the commit before the branch's first is the version before it: a file of the requirement is compared with it
    assert "+| INCEPTION |" in view(pay9.fold, ref=pay9.r1, base=pay9.base, path=RELEASE, view="diff")["text"]
    # a file only that commit has is not the requirement's: never read
    refused(lambda: view(pay9.fold, ref=pay9.base, path="README.md"), 404, "unknown_file")


def test_a_symlink_in_a_commit_is_refused_not_followed(tmp_path):
    repo = git_repo(tmp_path / "pay")
    (tmp_path / "secret.txt").write_text("top secret\n", encoding="utf-8")
    os.symlink(str(tmp_path / "secret.txt"), str(repo / "notes.md"))
    sh("git", "-C", repo, "add", "notes.md")
    sh("git", "-C", repo, "commit", "-qm", "a link")
    sha = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    f = fold(repo, [node("review", [rnd(1, [sha])])], [sha])
    for v in ("source", "diff"):
        msg = refused(lambda: view(f, ref=sha, path="notes.md", view=v), 403, "symlink")
        assert "符号链接" in msg


def test_a_diff_never_runs_the_driver_or_textconv_the_repo_names(tmp_path):
    repo = git_repo(tmp_path / "pay")
    hit = tmp_path / "driver-ran"
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\necho ran > %s\necho DRIVER OUTPUT\n" % hit, encoding="utf-8")
    tool.chmod(0o755)
    for k, v in (("diff.evil.command", str(tool)), ("diff.evil.textconv", str(tool)), ("diff.external", str(tool))):
        sh("git", "-C", repo, "config", k, v)
    a = commit_file(repo, ".gitattributes", "*.md diff=evil\n", "attributes")
    b = commit_file(repo, "doc.md", "one\n", "v1")
    c = commit_file(repo, "doc.md", "one\ntwo\n", "v2")
    f = fold(repo, [node("review", [rnd(1, [b, c])])], [a, b, c])
    got = view(f, ref=c, base=b, path="doc.md", view="diff")
    assert "+two" in got["text"] and "DRIVER OUTPUT" not in got["text"] and not hit.exists()


def test_a_unit_s_code_generation_lists_only_the_files_inside_its_paths(tmp_path):
    repo = git_repo(tmp_path / "pay")
    commit_file(repo, "README.md", "x\n")
    api = commit_file(repo, "services/api/a.py", "a = 1\n", "api")
    web = commit_file(repo, "web/b.ts", "export {}\n", "web")
    cg = node("code-generation", [rnd(1, [api, web])], unit="api")
    f = fold(repo, [cg], [api, web], units=[stages.UnitPlan("api", ("services/api/**",)), stages.UnitPlan("web", ("web/**",))])
    got = outputs.node_output(f, outputs.node_query("code-generation/api", None))
    assert [x["path"] for x in got["files"]] == ["services/api/a.py"]


def test_lean_ledgers_are_never_listed(tmp_path):
    repo = git_repo(tmp_path / "pay")
    commit_file(repo, "README.md", "x\n")
    sha = commit_file(repo, "aidlc-docs/audit/alice-mbp-3f2a1c.jsonl", '{"event":"REJECTED","data":{"words":"太慢"}}\n', "records")
    f = fold(repo, [node("review", [rnd(1, [sha])])], [sha])
    assert outputs.node_output(f, outputs.node_query("review", None))["files"] == []


# ---------------------------------------------------------------- the one worktree read, pinned

@pytest.fixture
def waiting(tmp_path):
    """A local run whose requirements.md waits for approval, not committed: Lean recorded its sha256."""
    repo = git_repo(tmp_path / "pay")
    sha = commit_file(repo, "README.md", "x\n")
    text = "# 需求\n\n- AC-1 退款成功以后发短信（联系 carol@example.com）\n"
    (repo / REQS).parent.mkdir(parents=True)
    (repo / REQS).write_text(text, encoding="utf-8")
    (repo / "notes.md").write_text(text, encoding="utf-8")
    req = node("requirements", [rnd(1, [], [(REQS, h(text))], ask=stages.Ask("REQUIREMENTS", ((REQS, h(text)),), 1.0, "local"),
                                    ended=None)])
    return SimpleNamespace(repo=repo, text=text, fold=fold(repo, [req], [sha], worktree=str(repo)))


def test_a_document_waiting_for_approval_is_read_from_the_worktree_only_pinned_to_leans_sha256(waiting):
    f = outputs.node_output(waiting.fold, outputs.node_query("requirements", None))["files"][0]
    assert (f["path"], f["ref"], f["pin"]) == (REQS, "worktree", h(waiting.text))
    got = view(waiting.fold, ref="worktree", pin=f["pin"], path=REQS)
    assert got["pinned"] is True and got["asked"] == {"kind": "REQUIREMENTS", "sha8": h(waiting.text)[:8]}
    assert "AC-1 退款成功以后发短信" in got["text"] and "carol@example.com" not in json.dumps(got, ensure_ascii=False)


def test_a_pinned_read_is_refused_when_the_bytes_changed(waiting):
    (waiting.repo / REQS).write_text(waiting.text + "- AC-2 又加了一条\n", encoding="utf-8")
    msg = refused(lambda: view(waiting.fold, ref="worktree", pin=h(waiting.text), path=REQS), 409, "changed")
    assert "到会话里看" in msg


def test_a_pinned_read_is_refused_through_a_symlinked_folder(waiting, tmp_path):
    folder = waiting.repo / "aidlc-docs" / INTENT / "inception"
    elsewhere = tmp_path / "elsewhere"
    folder.rename(elsewhere)
    os.symlink(str(elsewhere), str(folder))
    assert (waiting.repo / REQS).read_text(encoding="utf-8") == waiting.text   # the same bytes, through the link
    refused(lambda: view(waiting.fold, ref="worktree", pin=h(waiting.text), path=REQS), 403, "symlink")


def test_a_pinned_read_is_refused_when_the_file_itself_is_a_symlink(waiting, tmp_path):
    real = tmp_path / "real.md"
    (waiting.repo / REQS).rename(real)
    os.symlink(str(real), str(waiting.repo / REQS))
    refused(lambda: view(waiting.fold, ref="worktree", pin=h(waiting.text), path=REQS), 403, "symlink")


def test_a_pinned_read_is_refused_at_a_path_that_is_not_the_stage_tables_document(waiting):
    # the same bytes, the same sha256, another path: never read
    refused(lambda: view(waiting.fold, ref="worktree", pin=h(waiting.text), path="notes.md"), 404, "unknown_file")
    refused(lambda: view(waiting.fold, ref="worktree", pin=h("x\n"), path="README.md"), 404, "not_recorded")
    refused(lambda: view(waiting.fold, ref="worktree", pin=h("other"), path=REQS), 404, "not_recorded")


def test_a_runner_round_is_never_read_from_this_machines_worktree(waiting):
    r = waiting.fold.nodes[0].rounds[0]
    runner = node("requirements", [stages.Round(**dict(r.__dict__, run="runner-1"))])
    f = outputs.Fold(**dict(waiting.fold.__dict__, nodes=(runner,)))
    listed = outputs.node_output(f, outputs.node_query("requirements", None))["files"][0]
    assert listed["ref"] is None and "等运行机把它推上来" in listed["why"]
    refused(lambda: view(f, ref="worktree", pin=h(waiting.text), path=REQS), 404, "not_recorded")


# ---------------------------------------------------------------- on a real brain-kit project, through the service

@needs_brainkit
def test_the_service_reads_the_boards_own_fold_for_the_drawer(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款导出")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    request_requirements(wt, intent, text="# Requirements\n\n- AC-1 退款（找 dave@example.com）\n")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            listed = await svc.outputs("r1", "PAY-1", outputs.node_query("requirements", None))
            f = listed["files"][0]
            got = await svc.outputs_file("r1", "PAY-1", outputs.file_query({"ref": f["ref"], "pin": f["pin"], "path": f["path"]}))
            return listed, got
        finally:
            await svc.stop()

    listed, got = run(go())
    path = "aidlc-docs/%s/inception/requirements/requirements.md" % intent
    assert (listed["files"][0]["path"], listed["files"][0]["ref"]) == (path, "worktree")
    assert got["pinned"] and got["asked"]["kind"] == "REQUIREMENTS" and "AC-1 退款" in got["text"]
    assert "dave@example.com" not in json.dumps([listed, got], ensure_ascii=False)


@needs_brainkit
def test_code_generation_has_the_code_its_run_committed_after_lean_recorded_it_done(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款短信")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    plan = "aidlc-docs/%s/construction/plans/app-code-generation-plan.md" % intent
    lean(wt, "stage", "start", "code-generation")
    (wt / plan).write_text("# Code generation plan\n\n- [x] Step 1: services/refund.py sends the SMS\n", encoding="utf-8")
    (wt / "services").mkdir()
    (wt / "services" / "refund.py").write_text("def notify(order):\n    return True\n", encoding="utf-8")
    lean(wt, "stage", "done", "code-generation", "--note", "services/refund.py")
    shas = []
    for secs, rel, msg in ((20, None, "PAY-1: refund SMS"), (40, "runs/PAY-1/notes.md", "PAY-1: notes")):
        if rel:
            (wt / rel).write_text("next: review\n", encoding="utf-8")
        later = "@%d +0000" % (time.time() + secs)
        sh("git", "-C", wt, "add", "-A")
        sh("git", "-C", wt, "commit", "-qm", msg, env={"GIT_AUTHOR_DATE": later, "GIT_COMMITTER_DATE": later})
        shas.append(sh("git", "-C", wt, "rev-parse", "HEAD").stdout.strip())
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return (await svc.outputs("r1", "PAY-1", outputs.node_query("code-generation/app", None)),
                    await svc.board("r1", "PAY-1"))
        finally:
            await svc.stop()

    got, b = run(go())
    work = shas[0]
    assert [(f["path"], f["output"], f["ref"]) for f in got["files"]] == [
        (plan, True, work), ("services/refund.py", False, work)]
    assert got["head"] == work
    cg = next(n for n in b["nodes"] if n["id"] == "code-generation/app")
    assert [r["commits"] for r in cg["rounds"]] == [[work[:12]]]
