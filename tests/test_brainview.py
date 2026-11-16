"""Brain: entries by file and section with sources and usage, lint, reflect preview (nothing written) and apply
(brain-kit's own command: a new brain/<KEY>-reflect-<n> branch)."""

import asyncio
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

from appkit import mod
from brainfix import brain, make_project, make_service, needs_brainkit, run, sh, start_in_worktree

pytestmark = needs_brainkit

PROPOSAL = """
## Brain proposals

```brain-proposal
file: TECH
type: pitfall
rel: new
section: 外部系统
src: D-2
---
退款单号以产品确认为准：样例文件里的位数是旧格式。
```
"""


def test_entries_usage_lint_and_reflect(tmp_path, home):
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--section", "外部系统",
          "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    dec = wt / "aidlc-docs" / intent / "decisions.md"
    dec.write_text(dec.read_text() + "| D-2 | 2026-11-08 | requirements | 按 T-PAY-9-1 切分 | Brain | agent |\n" + PROPOSAL)
    sh("git", "-C", wt, "commit", "-qam", "decisions")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            view = await svc.brain("r1")
            item = await svc.requirement("r1", "PAY-1")
            prev = await svc.reflect_preview("r1", "PAY-1")
            applied = await svc.reflect_apply("r1", "PAY-1", push=False)
            again = await svc.reflect_preview("r1", "PAY-1")
            return view, item, prev, applied, again
        finally:
            await svc.stop()

    view, item, prev, applied, again = run(go())
    tech = next(f for f in view["files"] if f["key"] == "TECH")
    assert tech["count"] == 1 and tech["sections"][0]["title"] == "外部系统"
    e = tech["sections"][0]["entries"][0]
    assert e["id"] == "T-PAY-9-1" and e["type"] == "pitfall" and e["src"] == ["D-1"] and "自然日" in e["text"]
    assert view["lint"]["errors"] == [] and view["ref"] == "main"
    # the run is not merged: the Brain's numbers (merged runs only) leave it out, and it is listed on its own with the
    # numbers 需求详情 shows for it
    assert view["usage"]["runs"] == [] and view["usage"]["useful"] == [0, 1]
    assert view["usage"]["unmerged"] == [{"key": "PAY-1", "loaded": 1, "cited": ["T-PAY-9-1"], "where": "worktree"}]
    assert (item["brain"]["loaded"], item["brain"]["cited"]) == (1, ["T-PAY-9-1"])
    assert [r["key"] for r in view["reflect"]] == ["PAY-1"]
    assert prev["error"] == "" and prev["problems"] == [] and prev["substantive"] is True
    assert [(o["kind"], o["file"], o["type"], o["section"]) for o in prev["ops"]] == [("new", "TECH", "pitfall", "外部系统")]
    assert applied["ok"] and applied["branch"] == "brain/PAY-1-reflect-1", applied["output"]
    assert sh("git", "-C", repo, "rev-parse", "--verify", "-q", "brain/PAY-1-reflect-1").returncode == 0
    # live regression 2026-11-09: the reply names the worktree brain-kit wrote the branch in (it printed it)
    wt_reflect = applied["worktree"]
    assert wt_reflect and wt_reflect in applied["output"], applied
    assert sh("git", "-C", wt_reflect, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "brain/PAY-1-reflect-1"
    assert wt_reflect.startswith(str(home / ".brain" / "worktrees")) and "brain-PAY-1-reflect-1" in wt_reflect
    assert sh("git", "-C", repo, "status", "--porcelain", "--untracked-files=no").stdout.strip() == ""
    # the same proposal is not written twice; brain-kit's English is said in Chinese, its own words kept for 细节
    assert again["ops"] == [] and again["notes"] == ["第 13 行的提议已经在回写分支 brain/PAY-1-reflect-1 上了（MR 还没合并），跳过"]
    assert again["raw"] == ["proposal at line 13: already on brain/PAY-1-reflect-1 (MR not merged yet), skipped"]
    assert applied["mr_url"] is None and applied["pushed"] is False


def logged_git(tmp_path, monkeypatch):
    """Every git the app and brain-kit start, logged: brain-kit's (git on PATH) and the app's (repo.GIT_BIN)."""
    log = tmp_path / "git-calls"
    shim = tmp_path / "gitbin" / "git"
    shim.parent.mkdir()
    shim.write_text("#!/bin/sh\necho \"$*\" >> '%s'\nexec '%s' \"$@\"\n" % (log, mod("repo").GIT_BIN), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (shim.parent, os.environ["PATH"]))
    monkeypatch.setattr(mod("repo"), "GIT_BIN", str(shim))
    return lambda: len(log.read_text().splitlines()) if log.exists() else 0


async def fresh(svc, rid="r1"):
    """The Brain page once the Brain read behind it is there (``stale`` None), as the page asks again."""
    for _ in range(600):
        got = await svc.brain(rid)
        if got["stale"] is None:
            return got
        await asyncio.sleep(0.1)
    raise AssertionError("the Brain was not read again within 60 s")


def test_the_brain_page_is_made_once_until_main_or_a_runs_records_change(tmp_path, home, monkeypatch):
    """The Brain page read the Brain on main four times and every run's records (40 git calls, 3 to 4 s): it is made
    again only when the default branch moves or a run not merged yet changes (its branch, or its worktree's files)."""
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    dec = wt / "aidlc-docs" / intent / "decisions.md"
    before = dec.read_text()
    dec.write_text(before + "| D-2 | 2026-11-08 | requirements | 按 T-PAY-9-1 切分 | Brain | agent |\n")
    svc = make_service(tmp_path, [repo])
    gits = logged_git(tmp_path, monkeypatch)

    async def go():
        await svc.start()
        try:
            n0 = gits()
            first = await svc.brain("r1")
            n1 = gits()
            again = await svc.brain("r1")
            n2 = gits()
            dec.write_text(before)                      # the run no longer cites the entry
            uncited = await svc.brain("r1")
            brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-8", "--src", "D-1", "--text", "退款按原路返回。")
            sh("git", "-C", repo, "add", "-A")
            sh("git", "-C", repo, "commit", "-qm", "another entry")
            moved = await fresh(svc)
            return first, again, uncited, moved, n1 - n0, n2 - n1
        finally:
            await svc.stop()

    first, again, uncited, moved, made, kept = run(go())
    assert first["usage"]["unmerged"] == [{"key": "PAY-1", "loaded": 1, "cited": ["T-PAY-9-1"], "where": "worktree"}]
    assert again == first
    assert made >= 15 and kept <= 6, (made, kept)
    assert uncited["usage"]["unmerged"] == [{"key": "PAY-1", "loaded": 1, "cited": [], "where": "worktree"}]
    tech = next(f for f in moved["files"] if f["key"] == "TECH")
    assert tech["count"] == 2


def test_once_main_moves_the_page_shows_the_brain_read_before_at_once_and_reads_the_new_one_behind(tmp_path, home):
    """A merge moves the default branch: the page does not wait the seconds the Brain takes to read again. It gets the
    Brain read before at once, with when that was (``stale``), while the new one is read behind it."""
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    svc = make_service(tmp_path, [repo])
    count = lambda v: next(f for f in v["files"] if f["key"] == "TECH")["count"]  # noqa: E731

    async def go():
        await svc.start()
        try:
            first = await svc.brain("r1")
            brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-8", "--src", "D-1", "--text", "退款按原路返回。")
            sh("git", "-C", repo, "add", "-A")
            sh("git", "-C", repo, "commit", "-qm", "another entry")
            moved = await svc.brain("r1")
            return first, moved, await fresh(svc)
        finally:
            await svc.stop()

    first, moved, now = run(go())
    assert first["stale"] is None and count(first) == 1
    assert count(moved) == 1 and isinstance(moved["stale"], float) and moved["stale"] <= time.time()
    assert now["stale"] is None and count(now) == 2


def test_a_brain_that_cannot_be_read_after_main_moved_is_said_not_shown_stale_for_ever(tmp_path, home, monkeypatch):
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    svc = make_service(tmp_path, [repo])

    def broken(*a, **k):
        raise RuntimeError("usage.collect: cannot read the runs")

    async def go():
        await svc.start()
        try:
            await svc.brain("r1")
            sh("git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "main moves")
            monkeypatch.setattr(mod("bk").m("usage"), "collect", broken)
            return await svc.brain("r1"), await fresh(svc)
        finally:
            await svc.stop()

    moved, now = run(go())
    assert moved["stale"] is not None and moved["error"] == ""
    assert now["error"] == mod("brainview").NO_USAGE and now["raw"] == "usage.collect: cannot read the runs"



def test_an_empty_brain_is_kept_only_when_main_has_no_brain_files(tmp_path, home, monkeypatch):
    """brain-kit reads a Brain whose git listing failed as an empty Brain with no error: the page kept 0 entries until
    main moved again. An empty Brain is kept only when main's tree has no Brain files."""
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    svc = make_service(tmp_path, [repo])
    flag, log = tmp_path / "fail-next-ls-tree", tmp_path / "brainkit-git-calls"
    shim = tmp_path / "gitbin" / "git"                  # brain-kit's git (on PATH), not the app's
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\necho "$*" >> "%s"\nif [ -f "%s" ] && [ "$3" = ls-tree ]; then rm -f "%s"; exit 128; fi\n'
                    'exec \'%s\' "$@"\n' % (log, flag, flag, mod("repo").GIT_BIN), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (shim.parent, os.environ["PATH"]))
    count = lambda v: next(f for f in v["files"] if f["key"] == "TECH")["count"]  # noqa: E731
    calls = lambda: len(log.read_text().splitlines())  # noqa: E731

    async def go():
        await svc.start()
        try:
            first = await fresh(svc)
            sh("git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "main moves")
            flag.write_text("1")
            after = [count(await fresh(svc)) for _ in range(3)]
            failed = not flag.exists()
            sh("git", "-C", repo, "rm", "-rq", "brain")
            sh("git", "-C", repo, "commit", "-qm", "no Brain on main")
            gone = await fresh(svc)
            n = calls()
            again = await fresh(svc)
            return first, after, failed, gone, again, calls() - n
        finally:
            await svc.stop()

    first, after, failed, gone, again, more = run(go())
    assert count(first) == 1 and failed
    assert after == [1, 1, 1]                           # read again, not kept as 0
    assert count(gone) == 0 and gone["error"] == "" and again == gone and more == 0   # really none: kept


def test_a_brain_that_cannot_be_read_lists_no_runs_and_reads_none(tmp_path, home, monkeypatch):
    """When brain-kit cannot read the Brain on main the page says so and nothing else, as it always did: the runs not
    merged yet were read all the same and listed in the answer."""
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    svc = make_service(tmp_path, [repo])
    log = tmp_path / "brainkit-git-calls"
    shim = tmp_path / "gitbin" / "git"                  # brain-kit's git: main is not a commit in the repo
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\necho "$*" >> "%s"\nif [ "$2" = "%s" ] && [ "$3" = rev-parse ]; then exit 128; fi\n'
                    'exec \'%s\' "$@"\n' % (log, repo, mod("repo").GIT_BIN), encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (shim.parent, os.environ["PATH"]))

    async def go():
        await svc.start()
        try:
            return await svc.brain("r1")
        finally:
            await svc.stop()

    v = run(go())
    assert v["error"] == mod("brainview").UNREADABLE and v["files"] == []
    assert v["usage"]["unmerged"] == [] and str(wt) not in log.read_text()


class ChangingRepo:
    """A repo whose three runs not merged yet move to other branches on every scan."""

    def __init__(self, i):
        self.path, self.cfg, self.n = "/repos/%d" % i, {"brain_dir": "brain", "runs_dir": "runs"}, 0

    def scan(self):
        self.n += 1
        return [{"key": "PAY-%d" % (k + 1), "run": {"intent": "i%d" % k}, "ref": "refs/heads/ai/a/PAY-%d" % ((self.n + k) % 7 + 1),
                 "tip": "%d-%d" % (self.n, k), "worktree": None, "merged": False} for k in range(3)]


def test_runs_not_merged_are_read_by_many_pages_at_once(monkeypatch):
    """Each /brain runs in a thread of its own: two at once pruned the shared run records while the other one changed
    them (dictionary changed size during iteration, KeyError)."""
    bv = mod("brainview")
    monkeypatch.setattr(bv, "_kit", lambda: ())
    monkeypatch.setattr(bv.bk, "m", lambda name: SimpleNamespace(collect=lambda src, cfg, ref=None: {"runs": []}))
    errors = []
    repos = [ChangingRepo(i) for i in range(4)]

    def pages(repo):
        for _ in range(2000):
            try:
                bv.unmerged_usage(repo)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=pages, args=(repos[i % 4],)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        sys.setswitchinterval(was)
    assert errors == []


def test_a_bad_proposal_is_a_problem_in_the_preview(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "x", "x", "x")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    dec = wt / "aidlc-docs" / intent / "decisions.md"
    dec.write_text(dec.read_text() + PROPOSAL.replace("type: pitfall", "type: fantasy"))
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.reflect_preview("r1", "PAY-1")
        finally:
            await svc.stop()

    prev = run(go())
    assert prev["problems"] and prev["problems"][0]["text"].startswith("第 ") and \
        "类型不对：技术只收 原则、" in prev["problems"][0]["text"] and prev["problems"][0]["level"] == "FAIL", prev
    assert "fantasy" in prev["raw"][-1]


def test_reflect_says_brainkit_in_chinese_and_any_other_english_points_to_the_details():
    bv = mod("brainview")
    assert bv.reflect_says("proposal at line 27: already in the Brain as P-DEMO-1-1, skipped") == \
        "第 27 行的提议 Brain 里已经有了（P-DEMO-1-1），跳过"
    assert bv.reflect_says("D-3 (V-1a2b) was rejected, but no correction or repeats proposal cites it") == \
        "D-3 被否了（凭证 V-1a2b），可是没有一条纠正或「又出现」的提议提到它"
    assert bv.reflect_says("T-PAY-2-1 只加来源，不改文字") == "T-PAY-2-1 只加来源，不改文字"
    assert bv.reflect_says("a sentence nobody translated") == "brain-kit 说了一句这里还没翻译的话：原话在「细节」里"


def test_a_lint_line_in_the_reflect_dry_run_says_what_the_brain_check_card_says():
    """REST-4: brain-kit's dry run lints the Brain with the proposals written; its English line quotes a Chinese word
    and is said the way the Brain 检查 card says it (one rule for both), 做什么 included; English quoting Chinese is
    not a Chinese sentence."""
    bv, pr = mod("brainview"), mod("problems")
    line = ("TECH.md:20: source '见群聊' is not a reference (D01#S003, PKG-…, D-3, V-<10 hex>, RUN-…, an entry id, "
            "an intent, a key)")
    row, card = bv.reflect_row(line), pr.lint("FAIL", line)
    assert (row["level"], row["text"], row["todo"]) == ("FAIL", "brain/TECH.md 第 20 行：来源「见群聊」不是引用", card["todo"])
    assert card["text"] == row["text"] and card["todo"].startswith("来源写成查得到的引用")
    assert bv.reflect_row(line, brain_dir="docs/brain")["text"].startswith("docs/brain/TECH.md 第 20 行")
    assert bv.reflect_row("proposal at line 27: looks like a secret (AKIA…)")["text"] == "第 27 行的提议：像是有密钥或密码"
    assert bv.reflect_says("TECH.md:3: 见群聊 is wrong here") == bv.UNTRANSLATED
    assert bv.reflect_says("T-PAY-2-1 只加来源，不改文字") == "T-PAY-2-1 只加来源，不改文字"


def test_brain_kit_failing_to_read_the_use_is_said_in_words_its_own_in_raw(tmp_path, home, monkeypatch):
    """REST-29: 「Brain 用量读不出来」 says what still shows; brain-kit's exception is kept for 细节, not in the sentence."""
    repo = make_project(tmp_path)
    brain(repo, "entry", "add", "TECH", "pitfall", "--key", "PAY-9", "--src", "D-1", "--text", "对账文件按自然日切分。")
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-qm", "brain entry")
    svc = make_service(tmp_path, [repo])

    def broken(*a, **k):
        raise RuntimeError("usage.collect: cannot read runs/PAY-1/run.json at refs/heads/main")

    async def go():
        await svc.start()
        try:
            monkeypatch.setattr(mod("bk").m("usage"), "collect", broken)
            return await svc.brain("r1")
        finally:
            await svc.stop()

    view = run(go())
    assert view["error"] == mod("brainview").NO_USAGE and "run.json" not in view["error"]
    assert view["raw"] == "usage.collect: cannot read runs/PAY-1/run.json at refs/heads/main"
    assert [e["id"] for f in view["files"] for s in f["sections"] for e in s["entries"]] == ["T-PAY-9-1"]
