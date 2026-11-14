"""产出 shows the requirement's own files and nothing else: the review's checks of README's 「产出 reads only what the
fold names」, each on real git repositories, most of them through the route handlers and the Service (owner check,
query check, scrubber)."""

import json
import os
from types import SimpleNamespace

from appkit import commit_file, git_repo, mod, sh
from test_outputs import RELEASE, REQS, call, fold, h, node, rnd, routes, view, waiting  # noqa: F401  (waiting: a fixture)

outputs = mod("outputs")
AppError = mod("httpkit").AppError

LEDGER = "aidlc-docs/audit/bob-mbp-111111.jsonl"
BOB_WORDS = "bob 在会话里打的字：这个方案太慢"


def drawer(tmp_path, f):
    """The two routes over fold *f*, called as the page calls them."""
    node_, file_ = routes(tmp_path, True, f)
    return SimpleNamespace(node=lambda **q: call(node_, query=q), file=lambda **q: call(file_, query=q))


def outcome(fn):
    """What the page would get: the text, or the refusal's (status, code)."""
    try:
        return fn()["text"]
    except AppError as e:
        return (e.status, e.code)


def test_case_variants_of_a_ledger_or_a_document_are_not_them(tmp_path, waiting):
    repo = git_repo(tmp_path / "led")
    sha = commit_file(repo, LEDGER, '{"words":"%s"}\n' % BOB_WORDS, "records")
    f = fold(repo, [node("review", [rnd(1, [sha])])], [sha])
    for p in ("AIDLC-DOCS/audit/bob-mbp-111111.jsonl", "aidlc-docs/Audit/bob-mbp-111111.jsonl"):
        got = outcome(lambda: view(f, ref=sha, path=p))
        assert not isinstance(got, str), (p, got)
    # macOS opens AIDLC-DOCS/... as aidlc-docs/...: the pinned read still asks for the stage table's exact path
    upper = REQS.replace("aidlc-docs", "AIDLC-DOCS")
    assert (waiting.repo / upper).exists()
    got = outcome(lambda: view(waiting.fold, ref="worktree", pin=h(waiting.text), path=upper))
    assert not isinstance(got, str) and got[0] == 404, got


def test_config_cannot_colour_or_reprefix_the_diff_nor_an_env_diff_tool_run(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "pay")
    hit = tmp_path / "ran"
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\necho ran > %s\n" % hit, encoding="utf-8")
    tool.chmod(0o755)
    for k, v in (("color.ui", "always"), ("color.diff", "always"), ("diff.noprefix", "true"),
                 ("diff.mnemonicPrefix", "true"), ("diff.relative", "true")):
        sh("git", "-C", repo, "config", k, v)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(tool))
    b = commit_file(repo, "doc.md", "one\n", "v1")
    c = commit_file(repo, "doc.md", "one\ntwo\n", "v2")
    f = fold(repo, [node("review", [rnd(1, [b, c])])], [b, c])
    got = view(f, ref=c, base=b, path="doc.md", view="diff")["text"]
    assert got.startswith("diff --git a/doc.md b/doc.md\n") and "\x1b" not in got and "+two" in got and not hit.exists()


def test_a_diff_whose_old_side_is_a_symlink_is_refused(tmp_path):
    repo = git_repo(tmp_path / "pay")
    (tmp_path / "secret.txt").write_text("top secret\n", encoding="utf-8")
    os.symlink(str(tmp_path / "secret.txt"), str(repo / "notes.md"))
    sh("git", "-C", repo, "add", "notes.md")
    sh("git", "-C", repo, "commit", "-qm", "a link")
    c1 = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    os.unlink(str(repo / "notes.md"))
    c2 = commit_file(repo, "notes.md", "a real file now\n", "no link")
    f = fold(repo, [node("review", [rnd(1, [c1, c2])])], [c1, c2])
    assert outcome(lambda: view(f, ref=c2, base=c1, path="notes.md", view="diff")) == (403, "symlink")


def test_a_secret_in_a_file_goes_through_the_scrubber_on_the_route(tmp_path):
    repo = git_repo(tmp_path / "pay")
    text = "# Release\n\ntoken: glpat-ABCDEFGHIJKLMNOPQRST\n部署用 alice@example.com\n"
    r1 = commit_file(repo, RELEASE, text, "请批发布")
    f = fold(repo, [node("release", [rnd(1, [r1], [(RELEASE, h(text))])])], [r1])
    body = drawer(tmp_path, f).file(ref=r1, path=RELEASE).body
    assert "[已隐去]" in body["text"] and "glpat-" not in body["text"] and "alice@" not in body["text"]


def test_a_file_over_4_mb_is_413_and_a_long_text_is_cut_at_text_max(tmp_path):
    repo = git_repo(tmp_path / "pay")
    big = commit_file(repo, "big.txt", "y" * (4 * 1024 * 1024 + 1), "big")
    long = commit_file(repo, "long.txt", ("z" * 99 + "\n") * (outputs.TEXT_MAX // 100 + 5), "long")
    f = fold(repo, [node("review", [rnd(1, [big, long])])], [big, long])
    assert outcome(lambda: view(f, ref=big, path="big.txt")) == (413, "too_big")
    got = view(f, ref=long, path="long.txt")
    assert (got["truncated"], len(got["text"])) == (True, outputs.TEXT_MAX)


def test_refusals_are_plain_chinese_and_carry_no_file_content(tmp_path, waiting):
    (waiting.repo / REQS).write_text(waiting.text + "- AC-2 秘密的新一条\n", encoding="utf-8")
    d = drawer(tmp_path, waiting.fold)
    sha = waiting.fold.commits[0]["sha"]
    for q in ({"ref": "worktree", "pin": h(waiting.text), "path": REQS},      # 409 changed
              {"ref": sha, "path": "no/such.md"},                             # 404 missing
              {"ref": "f" * 40, "path": "README.md"}):                        # 404 not ours
        resp = d.file(**q)
        assert set(resp.body) == {"error", "code"} and "：" in resp.body["error"], resp.body
        assert "秘密" not in resp.body["error"] and "AC-1" not in resp.body["error"]


# ---------------------------------------------------------------- the requirement's files only

def test_a_file_no_commit_of_the_requirement_touched_is_not_shown(tmp_path):
    repo = git_repo(tmp_path / "pay")
    other = "aidlc-docs/260101-points/inception/requirements/requirements.md"
    base = commit_file(repo, other, "# 会员积分\n\n- carol 的需求：积分翻倍\n", "carol 的需求，早就合进 main 了")
    r1 = commit_file(repo, RELEASE, "# Release\n", "请批发布")
    f = fold(repo, [node("release", [rnd(1, [r1], [(RELEASE, h("# Release\n"))])])], [r1])
    d = drawer(tmp_path, f)
    got = {ref[:7]: d.file(ref=ref, path=other) for ref in (r1, base)}
    assert {k: (v.status, v.body.get("code")) for k, v in got.items()} == {r1[:7]: (404, "unknown_file"), base[:7]: (404, "unknown_file")}
    assert all("carol" not in json.dumps(v.body, ensure_ascii=False) for v in got.values())
    assert d.file(ref=r1, path=RELEASE).body["text"] == "# Release\n"


def test_a_folder_s_diff_never_shows_a_ledger(tmp_path):
    repo = git_repo(tmp_path / "pay")
    base = commit_file(repo, "README.md", "x\n")
    c1 = commit_file(repo, LEDGER, '{"event":"REJECTED","data":{"words":"%s"}}\n' % BOB_WORDS, "records")
    c2 = commit_file(repo, RELEASE, "# Release\n", "release")
    f = fold(repo, [node("release", [rnd(1, [c1, c2], [(RELEASE, h("# Release\n"))])])], [c1, c2])
    d = drawer(tmp_path, f)
    leaked = {}
    for p in ("aidlc-docs", "aidlc-docs/audit"):
        resp = d.file(ref=c2, base=base, path=p, view="diff")
        if BOB_WORDS in json.dumps(resp.body, ensure_ascii=False):
            leaked[p] = (resp.status, [x for x in resp.body["text"].splitlines() if "bob" in x])
    assert leaked == {}
