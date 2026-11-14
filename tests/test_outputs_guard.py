"""产出 shows the requirement's own files and nothing else: the review's checks of README's 「产出 reads only what the
fold names」, each on real git repositories, most of them through the route handlers and the Service (owner check,
query check, scrubber)."""

import json
import os
import re
import subprocess
import time
from types import SimpleNamespace

from appkit import BRAINKIT, commit_file, git_repo, mod, sh
from brainfix import needs_brainkit
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


# ---------------------------------------------------------------- folders and Lean's ledgers

def test_a_folder_lean_recorded_for_a_round_is_never_read_nor_diffed(tmp_path):
    repo = git_repo(tmp_path / "pay")
    base = commit_file(repo, "README.md", "x\n")
    c1 = commit_file(repo, "apps/pay/notes.md", "draft\n", "notes")
    c2 = commit_file(repo, "apps/pay/" + LEDGER, '{"data":{"words":"%s"}}\n' % BOB_WORDS, "records")
    f = fold(repo, [node("review", [rnd(1, [c1, c2], [("apps/pay", h("draft\n"))])])], [c1, c2])
    d = drawer(tmp_path, f)
    for q in ({"view": "source"}, {"view": "diff", "base": base}, {"view": "diff", "base": c1}, {"view": "diff"}):
        resp = d.file(ref=c2, path="apps/pay", **q)
        assert (resp.status, resp.body.get("code")) == (404, "missing"), (q, resp.body)


def test_lean_ledgers_are_never_listed_nor_read_at_any_depth_with_or_without_a_slash(tmp_path):
    repo = git_repo(tmp_path / "pay")
    commit_file(repo, "README.md", "x\n")
    nested = "apps/pay/" + LEDGER
    c1 = commit_file(repo, nested, '{"data":{"words":"%s"}}\n' % BOB_WORDS, "records")
    c2 = commit_file(repo, LEDGER, '{"data":{"words":"%s"}}\n' % BOB_WORDS, "records")
    rounds = [rnd(1, [c1, c2], [("aidlc-docs/audit", h("x")), ("apps/pay/aidlc-docs/audit", h("x"))])]
    f = fold(repo, [node("review", rounds)], [c1, c2])
    d = drawer(tmp_path, f)
    listed = d.node(node="review").body
    assert listed["files"] == [] and BOB_WORDS not in json.dumps(listed, ensure_ascii=False), listed["files"]
    for p in (nested, "aidlc-docs/audit", "apps/pay/aidlc-docs/audit"):
        for v in ("source", "diff"):
            resp = d.file(ref=c2, path=p, view=v)
            assert (resp.status, resp.body.get("code")) == (403, "not_shown"), (p, v, resp.body)


# ---------------------------------------------------------------- emails

def test_a_file_well_under_4_mb_answers_in_a_second_whatever_its_characters(tmp_path):
    """One 60 000-character run of letters (a long hash, a generated id list) and no @: the email pattern tried each
    of its characters as the start of an address to the end of the run (4 s)."""
    repo = git_repo(tmp_path / "pay")
    r1 = commit_file(repo, "ids.txt", "a" * 60_000 + "\n", "ids")
    f = fold(repo, [node("review", [rnd(1, [r1])])], [r1])
    t = time.perf_counter()
    view(f, ref=r1, path="ids.txt")
    took = time.perf_counter() - t
    assert took < 1.0, "a 60 kB file took %.1f s" % took


@needs_brainkit
def test_an_email_becomes_the_pseudonym_brain_kit_gives_it():
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    trace = bk.m("trace")
    text = ("| RELEASE | Bob.Smith@Example.COM |\nalice+pay@mail.example.co.uk,carol@x;dave@host\n"
            "a@b..c@d e@f+g@h -x@y.z %s@example.com 邮箱：zhang_san@公司.cn\n" % ("q" * 64))
    want = trace.EMAIL_RE.sub(lambda m: trace.pseudonym(m.group(0)), text)
    assert outputs.pseudonymize(text) == want and "@" not in want.replace("@公司", ""), want


# ---------------------------------------------------------------- what a diff answers

def test_a_diff_of_a_file_that_is_not_utf8_answers_its_change_not_a_crash(tmp_path):
    repo = git_repo(tmp_path / "pay")
    a = commit_file(repo, "README.md", "x\n")
    (repo / "gbk.txt").write_bytes("退款\n".encode("gbk"))
    sh("git", "-C", repo, "add", "gbk.txt")
    sh("git", "-C", repo, "commit", "-qm", "gbk")
    b = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "gbk.txt").write_bytes("退款成功\n".encode("gbk"))
    sh("git", "-C", repo, "commit", "-qam", "gbk 2")
    c = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    f = fold(repo, [node("review", [rnd(1, [b, c])])], [a, b, c])
    try:
        resp = drawer(tmp_path, f).file(ref=c, base=b, path="gbk.txt", view="diff")
        got = (resp.status, resp.body.get("text", "").splitlines()[:1], resp.body.get("binary"))
    except Exception as exc:  # noqa: BLE001  (what the host would answer as a 500)
        got = ("raised out of the route", type(exc).__name__)
    assert got == (200, ["diff --git a/gbk.txt b/gbk.txt"], False), got


def test_a_diff_of_a_file_over_4_mb_is_refused_like_its_source(tmp_path):
    repo = git_repo(tmp_path / "pay")
    a = commit_file(repo, "big.txt", ("y" * 99 + "\n") * 50_000, "big")
    b = commit_file(repo, "big.txt", ("w" * 99 + "\n") * 50_000, "bigger")
    f = fold(repo, [node("review", [rnd(1, [a, b])])], [a, b])
    assert outcome(lambda: view(f, ref=b, path="big.txt")) == (413, "too_big")      # 5 MB: its source is refused
    got = outcome(lambda: view(f, ref=b, base=a, path="big.txt", view="diff"))
    assert got == (413, "too_big"), "diff answered %d characters" % len(got)


def test_a_cut_at_the_size_limit_is_at_a_line_end_and_never_leaves_part_of_a_secret(tmp_path):
    repo = git_repo(tmp_path / "pay")
    line = "x" * 99 + "\n"
    pad = line * ((outputs.TEXT_MAX - 12) // len(line))
    text = pad + "y" * (outputs.TEXT_MAX - 12 - len(pad)) + " ghp_" + "A" * 36 + "\n"
    r1 = commit_file(repo, "notes.md", text, "long")
    f = fold(repo, [node("review", [rnd(1, [r1])])], [r1])
    d = drawer(tmp_path, f)
    for v in ("source", "diff"):
        body = d.file(ref=r1, path="notes.md", view=v).body
        assert body["truncated"] is True and body["text"].endswith("\n"), (v, body["text"][-12:])
        assert "ghp_" not in body["text"], (v, body["text"][-12:])


def test_a_replace_ref_cannot_swap_the_committed_bytes(tmp_path):
    repo = git_repo(tmp_path / "pay")
    sha = commit_file(repo, "doc.md", "what the commit holds\n", "v1")
    blob = sh("git", "-C", repo, "rev-parse", "%s:doc.md" % sha).stdout.strip()
    swap = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="planted by a replace ref\n",
                          capture_output=True, text=True, check=True).stdout.strip()
    sh("git", "-C", repo, "replace", blob, swap)
    f = fold(repo, [node("review", [rnd(1, [sha])])], [sha])
    assert view(f, ref=sha, path="doc.md")["text"] == "what the commit holds\n"
    assert "planted" not in view(f, ref=sha, path="doc.md", view="diff")["text"]


# ---------------------------------------------------------------- the query, and what a refusal says

def test_short_relative_named_or_newline_ended_ids_are_400(tmp_path, waiting):
    sha = waiting.fold.commits[0]["sha"]
    d = drawer(tmp_path, waiting.fold)
    got = {}
    for ref in (sha[:7], sha + "^", sha + "~1", sha.upper(), "@", "HEAD~1", "ai/alice/PAY-9-refund-sms", sha + "\n"):
        resp = d.file(ref=ref, path="README.md")
        got[repr(ref[-3:])] = (resp.status, resp.body.get("code"))
    resp = d.file(ref=sha, base=sha + "\n", path="README.md", view="diff")
    got["base"] = (resp.status, resp.body.get("code"))
    resp = d.file(ref="worktree", pin=h(waiting.text) + "\n", path=REQS)
    got["pin"] = (resp.status, resp.body.get("code"))
    assert got == dict({k: (400, "invalid_ref") for k in got if k != "pin"}, pin=(400, "invalid_pin")), got
    for q, code in (({"node": "requirements\n"}, "invalid_node"), ({"node": "requirements", "round": "1\n"}, "invalid_round")):
        resp = d.node(**q)
        assert (resp.status, resp.body.get("code")) == (400, code), (q, resp.body)


def test_each_refusal_says_why_and_what_to_do(tmp_path, waiting):
    repo = git_repo(tmp_path / "links")
    base = commit_file(repo, "README.md", "x\n")
    os.symlink("/etc/hosts", str(repo / "link.md"))
    sh("git", "-C", repo, "add", "link.md")
    sh("git", "-C", repo, "update-index", "--add", "--cacheinfo", "160000,%s,vendor/kit" % base)
    sh("git", "-C", repo, "commit", "-qm", "a link and a submodule")
    c1 = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    c2 = commit_file(repo, "big.txt", "y" * (4 * 1024 * 1024 + 1), "big")
    f = fold(repo, [node("review", [rnd(1, [c1, c2])])], [c1, c2])
    d = drawer(tmp_path, f)
    said = {code: d.file(ref=sha, path=p).body for code, sha, p in (
        ("not_shown", c2, LEDGER), ("symlink", c1, "link.md"), ("submodule", c1, "vendor/kit"), ("too_big", c2, "big.txt"))}
    (waiting.repo / REQS).unlink()
    (waiting.repo / REQS).mkdir()
    said["not_a_file"] = drawer(tmp_path, waiting.fold).file(ref="worktree", pin=h(waiting.text), path=REQS).body
    for code, body in said.items():
        why, _, todo = body.get("error", "").rpartition("：")
        assert body.get("code") == code and why and re.match(r"(到|在|从|刷新|等)", todo), (code, body)
