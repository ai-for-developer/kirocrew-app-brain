"""证据 in the app: what `lean verify` sealed, read through brain-kit's proof module (bk's optional "proof").

The 产出 drawer: a build-and-test round lists the files its VERIFY sealed, found by the output_sha256 Lean recorded
and labelled by plan row, never another verify's; a PNG or a JPEG shows as a picture (view image, a data: URL) and
nothing else does. With a brain-kit older than the proof module the drawer lists no evidence and keeps working.

The sets are written by the kit's own render_set (proof.py shots drives Chrome and has its own tests in brain-kit)."""

import base64
import hashlib
import json
import shlex
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from appkit import BRAINKIT, git_repo, mod, sh
from brainfix import lean, make_project, make_service, needs_brainkit, run, start_in_worktree

outputs = mod("outputs")
stages = mod("stages")
board = mod("board")
bk = mod("bk")
Git = mod("repo").Git
AppError = mod("httpkit").AppError

INTENT = "261118-refund"
CLONE = "alice-mbp-1a2b3c"
SETS = "aidlc-docs/%s/construction/build-and-test/proof/%s" % (INTENT, CLONE)
SHOTS = "python3 .kiro/brain/proof.py shots"
TESTS = "python3 .kiro/brain/proof.py run tests -- pytest -q"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def png(rgb=(200, 30, 30), w=4, h=3) -> bytes:
    """A real PNG of one colour."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def K():
    """The kit's read and write API of the brain-kit under test (brainkit.proof.kit())."""
    if not bk.load(str(BRAINKIT))["ok"] or not bk.has("proof"):
        pytest.skip("this brain-kit has no proof module")
    return bk.m("proof").kit()


def commit_all(repo: Path, msg: str) -> str:
    sh("git", "-C", repo, "add", "-A")
    sh("git", "-C", repo, "commit", "-q", "-m", msg)
    return sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()


def code(repo: Path, text: str, msg: str) -> str:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "refund.py").write_text(text, encoding="utf-8")
    return commit_all(repo, msg)


def row(K, rid, ac, what, data, result="ok", note=""):
    """A plan row's item and its screenshot (none when nothing was taken)."""
    name = "app-%s.png" % rid if data else None
    item = K.Item("app", rid, (ac,), what, ("open /orders/123", "wait %s" % what), name, sha(data) if data else None,
                  len(data or b""), K.Result(result), note, 0.4, None)
    return item, ({name: data} if data else {})


def write_set(K, repo: Path, name: str, rows, body: str, at: str, on_disk=None, clone: str = CLONE) -> str:
    """A set folder as the kit writes it; returns sha256(output.txt), which Lean's VERIFY records for its check.
    *on_disk*: bytes written in place of a file's (an edit after the capture)."""
    d = repo / K.set_path(INTENT, clone, name)
    d.mkdir(parents=True, exist_ok=True)
    for _, files in rows:
        for f, data in files.items():
            (d / f).write_bytes((on_disk or {}).get(f, data))
    kind = K.Kind.SHOTS if name == "ui" else K.Kind.RUN
    s = K.ProofSet(K.SCHEMA, name, kind, INTENT, clone, 0, K.CodeState(at, ()), tuple(i for i, _ in rows), (),
                   "Chrome 157.0.0.0 · 1280×800" if kind is K.Kind.SHOTS else "", ())
    text = K.render_set(body, s).encode("utf-8")
    (d / K.OUTPUT).write_bytes(text)
    return sha(text)


def verify_round(n: int, checks, commits) -> "stages.Round":
    return stages.Round(n=n, run="local", started=float(n), ended=n + .5,
                        end=stages.RoundEnd(stages.Outcome.PASSED, n + .5, checks=tuple(
                            stages.CheckResult(cmd, True, 1.0, h) for cmd, h in checks)),
                        why=stages.Why("first"), note=None, outputs=(), commits=tuple(commits))


def fold(repo: Path, rounds, shas) -> "outputs.Fold":
    git = Git(repo)
    changes = board.changes_from_log(git.run(*board.GIT_LOG, *shas).stdout)
    bt = stages.Node(stages.node_id("build-and-test", None), stages.BY_ID["build-and-test"], None, list(rounds), [],
                     stages.NodeStatus("done", "", None))
    commits = tuple({"sha": s, "at": float(i), "paths": [c["path"] for c in changes.get(s) or []]} for i, s in enumerate(shas))
    return outputs.Fold(reader=outputs.GitReader(git), intent=INTENT, branch="ai/alice/PAY-1-refund", nodes=(bt,),
                        commits=commits, changes=changes)


def listed(f, n):
    return outputs.node_output(f, outputs.node_query("build-and-test", str(n)))["files"]


def view(f, **q):
    return outputs.file_view(f, outputs.file_query(dict({"view": "source"}, **q)))


def refused(fn):
    with pytest.raises(AppError) as e:
        fn()
    return e.value.status, e.value.code


def two_verifies(K, tmp_path):
    """Two verifies of a refund page: round 1 shot P-1 (ok), P-2 (did not see its text), P-3 (no Chrome) and kept the
    tests' whole output with a JPEG the test left; its records commit falls inside round 2, which shot all three again
    after a code change."""
    repo = git_repo(tmp_path / "pay")
    sh("git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "main")
    c1 = code(repo, "def refund(order):\n    return True\n", "退款")
    p1, p2 = png((40, 160, 60)), png((200, 30, 30))
    tests1 = write_set(K, repo, "tests", [(K.Item("", "AC-2-refund.jpg", ("AC-2",), "", (), "AC-2-refund.jpg", sha(JPEG),
                                                   len(JPEG), K.Result.OK, "", 0.0, None), {"AC-2-refund.jpg": JPEG})],
                       "3 passed in 0.2s\n", c1)
    ui1 = write_set(K, repo, "ui", [row(K, "P-1", "AC-1", "已付款订单有「退款」按钮", p1),
                                    row(K, "P-2", "AC-2", "点退款以后状态是「已退款」", p2, "failed", "没等到「已退款」（10 秒）"),
                                    row(K, "P-3", "AC-3", "退款以后显示退款单号", None, "skipped", "没有 Chrome（设 CHROME）")],
                    "shots: 3 rows\n", c1)
    r1 = commit_all(repo, "verify 1 的记录")
    c2 = code(repo, "def refund(order):\n    return order.paid\n", "退款：只退已付款的")
    p3 = png((20, 20, 220))
    tests2 = write_set(K, repo, "tests", [], "4 passed in 0.3s\n", c2)
    ui2 = write_set(K, repo, "ui", [row(K, "P-1", "AC-1", "已付款订单有「退款」按钮", p3)], "shots: 1 row\n", c2)
    r2 = commit_all(repo, "verify 2 的记录")
    rounds = [verify_round(1, [(TESTS, tests1), (SHOTS, ui1), ("ruff check .", sha(b"ruff"))], [c1]),
              verify_round(2, [(TESTS, tests2), (SHOTS, ui2)], [r1, c2])]
    return dict(repo=repo, fold=fold(repo, rounds, [c1, r1, c2, r2]), c1=c1, r1=r1, c2=c2, r2=r2, p1=p1, p3=p3,
                rounds=rounds)


# ---------------------------------------------------------------- the checks on the wall

@needs_brainkit
@pytest.mark.parametrize("cmd,name", [(TESTS, "pytest"), (SHOTS, "截图"), ("python3 -m pytest -q", "pytest"),
                                      ("python3 tests/fake_shots.py .kiro/brain/proof.py shots", "截图"),
                                      ("python3 ./.kiro/brain/proof.py run web -- npm run test", "npm test"),
                                      ("python3 tools/proof.py run x -- make", "python3")])
def test_an_evidence_check_is_named_by_what_it_wraps_as_the_kit_tells_one(K, cmd, name):
    assert stages.check_name(cmd) == name
    assert (stages.PROOF_CHECK_RE.search(cmd) is not None) is K.is_proof_check(cmd)


# ---------------------------------------------------------------- the 产出 drawer

@needs_brainkit
def test_a_verify_round_lists_what_it_sealed_labelled_by_plan_row(K, tmp_path):
    w = two_verifies(K, tmp_path)
    files = listed(w["fold"], 1)
    got = [(f["path"].replace(SETS + "/", ""), f["ref"], f["output"], f["proof"]) for f in files if f["output"]]
    r1 = w["r1"]
    assert [f["path"] for f in files if not f["output"]] == ["src/refund.py"]
    assert got == [
        ("tests/output.txt", r1, True, {"set": "tests", "label": "命令输出 tests：pytest -q", "result": None, "mark": "", "note": ""}),
        ("tests/AC-2-refund.jpg", r1, True, {"set": "tests", "label": "AC-2-refund.jpg · AC-2", "result": "ok", "mark": "", "note": ""}),
        ("ui/output.txt", r1, True, {"set": "ui", "label": "截图", "result": None, "mark": "", "note": ""}),
        ("ui/app-P-1.png", r1, True, {"set": "ui", "label": "P-1 · AC-1 · 已付款订单有「退款」按钮", "result": "ok", "mark": "",
                                     "note": ""}),
        ("ui/app-P-2.png", r1, True, {"set": "ui", "label": "P-2 · AC-2 · 点退款以后状态是「已退款」", "result": "failed",
                                     "mark": "没看到", "note": "没等到「已退款」（10 秒）"}),
    ]


@needs_brainkit
def test_a_round_never_lists_another_verifys_evidence_as_its_own(K, tmp_path):
    """Round 1's records commit is inside round 2: round 2 lists its own sets as handed in and none of round 1's
    files among its other changes."""
    w = two_verifies(K, tmp_path)
    files = listed(w["fold"], 2)
    assert [(f["path"].replace(SETS + "/", ""), f["ref"]) for f in files if f["output"]] == [
        ("tests/output.txt", w["r2"]), ("ui/output.txt", w["r2"]), ("ui/app-P-1.png", w["r2"])]
    assert [f["path"] for f in files if not f["output"]] == ["src/refund.py"]


@needs_brainkit
def test_a_file_changed_after_its_set_was_sealed_does_not_count(K, tmp_path):
    repo = git_repo(tmp_path / "pay")
    c1 = code(repo, "x = 1\n", "代码")
    ui = write_set(K, repo, "ui", [row(K, "P-1", "AC-1", "有退款按钮", png())], "shots: 1 row\n", c1,
                   on_disk={"app-P-1.png": png((0, 0, 0))})
    r1 = commit_all(repo, "记录（截图被换过）")
    f = listed(fold(repo, [verify_round(1, [(SHOTS, ui)], [c1])], [c1, r1]), 1)
    shot = next(x for x in f if x["path"].endswith("app-P-1.png"))
    assert shot["ref"] is None and shot["why"].startswith(shot["path"] + " 不是 lean verify 留下的样子")
    assert "不算证据" in shot["why"]


@needs_brainkit
def test_a_png_or_a_jpeg_shows_as_a_picture_and_nothing_else_does(K, tmp_path):
    w = two_verifies(K, tmp_path)
    f, r1 = w["fold"], w["r1"]
    got = view(f, ref=r1, path=SETS + "/ui/app-P-1.png", view="image")
    assert got["image"].startswith("data:image/png;base64,") and got["binary"] and got["size"] == len(w["p1"])
    assert base64.b64decode(got["image"].split(",", 1)[1]) == w["p1"]
    assert view(f, ref=r1, path=SETS + "/tests/AC-2-refund.jpg", view="image")["image"] == \
        "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    assert refused(lambda: view(f, ref=r1, path=SETS + "/ui/output.txt", view="image")) == (415, "not_an_image")
    assert "image" not in view(f, ref=r1, path=SETS + "/ui/app-P-1.png")      # its source: a binary file, no bytes
    # by its first bytes only: an SVG named .png is no picture, and one over the drawer's 4 MB is not read at all
    (w["repo"] / "web").mkdir()
    (w["repo"] / "web" / "logo.png").write_bytes(SVG)
    (w["repo"] / "web" / "big.png").write_bytes(png() + b"\0" * (4 * 1024 * 1024))
    c3 = commit_all(w["repo"], "图")
    f = fold(w["repo"], [verify_round(1, [], [c3])], [w["c1"], r1, w["c2"], w["r2"], c3])
    assert refused(lambda: view(f, ref=c3, path="web/logo.png", view="image")) == (415, "not_an_image")
    assert refused(lambda: view(f, ref=c3, path="web/big.png", view="image")) == (413, "too_big")


def older_brainkit(tmp_path: Path) -> Path:
    """brainkit/ as it was before brainkit/proof.py came: the newest commit without it that has every module the app
    requires (the parent of the commit that added proof.py has no flows.py when fast mode came on another branch)."""
    def has(names: set, name: str) -> bool:
        base = "brainkit/" + name.replace(".", "/")
        return base + ".py" in names or base + "/__init__.py" in names
    for sha in sh("git", "-C", BRAINKIT, "rev-list", "--max-count=400", "HEAD").stdout.split():
        names = set(sh("git", "-C", BRAINKIT, "ls-tree", "-r", "--name-only", sha, "brainkit").stdout.split())
        if "brainkit/proof.py" not in names and all(has(names, n) for n in bk.REQUIRED):
            break
    else:
        pytest.skip("no commit of this brain-kit has the modules the app requires and no brainkit/proof.py")
    old = tmp_path / "older-brain-kit"
    old.mkdir()
    tar = subprocess.run(["git", "-C", str(BRAINKIT), "archive", sha, "brainkit"], capture_output=True,
                         check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(old)], input=tar, check=True)
    return old


@needs_brainkit
def test_with_a_brain_kit_older_than_proof_the_drawer_lists_no_evidence(K, tmp_path):
    w = two_verifies(K, tmp_path)
    try:
        info = bk.load(str(older_brainkit(tmp_path)))
        assert info["ok"] and info["modules"]["proof"] is False, info
        files = listed(w["fold"], 2)
        assert [f["proof"] for f in files] == [None] * len(files)
        assert [f["path"] for f in files if f["output"]] == []
        assert SETS + "/ui/app-P-1.png" in [f["path"] for f in files]      # just a file round 2's commits changed
    finally:
        assert bk.load(str(BRAINKIT))["ok"]


# ---------------------------------------------------------------- the 证据 row: bind() at the commit under review

evidence = mod("evidence")
RUNNER = "runner-1-3f9c01"


def record(seq: int, checks, clone: str = CLONE) -> dict:
    """A VERIFY record as Lean writes it (the app reads it with _file, as lean_view.events gives it)."""
    return {"event": "VERIFY", "ts": "2026-11-18T05:%02d:00.000Z" % seq, "_at": 1794000000.0 + seq, "intent": INTENT,
            "_file": "aidlc-docs/audit/%s.jsonl" % clone, "seq": seq,
            "data": {"ok": True, "fingerprint": "f" * 64, "results": [{"cmd": c, "exit": 0, "output_sha256": h}
                                                                      for c, h in checks]}}


def checks_of(r) -> list:
    return [(c.cmd, c.output_sha256) for c in r.end.checks]


def card(repo: Path, sha: str, events, nodes) -> dict:
    return evidence.row(evidence.read(str(repo), sha, INTENT, events), nodes, sha)


@needs_brainkit
def test_the_row_says_what_verify_left_and_each_chip_opens_the_round_that_sealed_it(K, tmp_path):
    w = two_verifies(K, tmp_path)
    events = [record(r.n, checks_of(r)) for r in w["rounds"]]
    got = card(w["repo"], w["r2"], events, w["fold"].nodes)
    assert (got["available"], got["state"], got["line"], got["at"], got["late"], got["error"]) == (
        True, "current", "截图 1 张 · 测试输出 1 份 · 是这份代码的", w["r2"][:12], "", "")
    assert [(c["kind"], c["label"], c["ac"], c["state"], c["mark"], c["round"], c["path"]) for c in got["chips"]] == [
        ("shot", "P-1", ["AC-1"], "current", "", 2, SETS + "/ui/app-P-1.png"),
        ("log", "测试输出 tests", [], "current", "", 2, SETS + "/tests/output.txt")]
    assert got["chips"][1]["what"] == "命令输出 tests：pytest -q" and got["gaps"] == []
    # one more code commit: both are of the code before it, in brain-kit's own sentence
    c3 = code(w["repo"], "def refund(order):\n    return order.paid and order.amount > 0\n", "退款：金额要大于 0")
    got = card(w["repo"], c3, events, w["fold"].nodes)
    assert (got["state"], got["line"]) == ("stale", "截图 1 张 · 测试输出 1 份 · 是旧代码的")
    assert [c["mark"] for c in got["chips"]] == ["旧代码", "旧代码"]
    assert got["gaps"] == [{"kind": "old", "text": "P-1、tests 是旧代码的：拍完以后改了 src/refund.py。合并前要重跑 lean verify"}]


@needs_brainkit
def test_a_screenshot_that_did_not_show_what_it_waits_for_is_said_with_brain_kits_sentence(K, tmp_path):
    repo = git_repo(tmp_path / "pay")
    c1 = code(repo, "x = 1\n", "代码")
    ui = write_set(K, repo, "ui", [row(K, "P-1", "AC-1", "已付款订单有「退款」按钮", png()),
                                   row(K, "P-2", "AC-2", "点退款以后状态是「已退款」", png((9, 9, 9)), "failed",
                                       "没等到「已退款」（10 秒）")], "shots: 2 rows\n", c1)
    r1 = commit_all(repo, "记录")
    rounds = [verify_round(1, [(SHOTS, ui)], [c1])]
    got = card(repo, r1, [record(1, checks_of(rounds[0]))], fold(repo, rounds, [c1, r1]).nodes)
    assert (got["state"], got["line"]) == ("failed", "截图 2 张（1 张没看到） · 是这份代码的")
    assert [(c["label"], c["mark"], c["round"]) for c in got["chips"]] == [("P-1", "", 1), ("P-2", "没看到", 1)]
    assert got["gaps"] == [{"kind": "missed", "text": "P-2 没看到要的：没等到「已退款」（10 秒）"}]


@needs_brainkit
def test_a_runner_without_chrome_keeps_the_owners_screenshot_and_says_whose_it_is(K, tmp_path):
    """The runner's verify (its own clone's folder) took no screenshot: the owner's from round 2 is shown, judged by
    its own code, and the row says the newest verify took none."""
    w = two_verifies(K, tmp_path)
    ui3 = write_set(K, w["repo"], "ui", [row(K, "P-1", "AC-1", "已付款订单有「退款」按钮", None, "skipped",
                                             "没有 Chrome（设 CHROME）")], "shots: 1 row\n", w["r2"], clone=RUNNER)
    r3 = commit_all(w["repo"], "运行机 verify 的记录")
    rounds = w["rounds"] + [verify_round(3, [(SHOTS, ui3)], [r3])]
    events = [record(r.n, checks_of(r)) for r in w["rounds"]] + [record(1, [(SHOTS, ui3)], clone=RUNNER)]
    got = card(w["repo"], r3, events, fold(w["repo"], rounds, [w["c1"], w["r1"], w["c2"], w["r2"], r3]).nodes)
    shot = got["chips"][0]
    assert (shot["label"], shot["state"], shot["round"], shot["path"]) == ("P-1", "current", 2, SETS + "/ui/app-P-1.png")
    assert got["late"] == "最近一次 verify 没拍截图（没有 Chrome（设 CHROME））：显示的是第 2 轮拍的"


@needs_brainkit
def test_a_project_whose_verify_keeps_no_evidence_says_so_in_brain_kits_words(K, tmp_path):
    repo = git_repo(tmp_path / "pay")
    c1 = code(repo, "x = 1\n", "代码")
    got = card(repo, c1, [record(1, [("pytest -q", sha(b"out"))])], ())
    assert (got["state"], got["line"], got["chips"]) == ("off", "不留证据", [])
    assert [g["kind"] for g in got["gaps"]] == ["no_proof_check"]
    assert got["gaps"][0]["text"].startswith("这个项目的 lean verify 不留证据：aidlc-docs/lean.json 的 checks 里没有")



# ---------------------------------------------------------------- through the service, on a real `lean verify`

FAKE_SHOTS = r"""import hashlib, importlib.util, os, struct, sys, zlib
root = os.getcwd()
spec = importlib.util.spec_from_file_location("proofkit", os.path.join(root, ".kiro", "brain", "proof.py"))
K = importlib.util.module_from_spec(spec)
sys.modules["proofkit"] = K
spec.loader.exec_module(K)


def png(rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + bytes(rgb) * 4 for _ in range(3))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


t, why = K.target(root)
new = K.begin_set(t, "ui")
start = K.code_state(root, t.is_record)
path = K.plan_path(t.intent, "app")
with open(os.path.join(root, path), "rb") as f:
    plan = K.parse_plan(f.read(), "app", path)
items = []
for n, r in enumerate(plan.rows):
    data, name = png((60 * n, 160, 60)), "app-%s.png" % r.id
    with open(os.path.join(new, name), "wb") as f:
        f.write(data)
    items.append(K.Item("app", r.id, r.ac, r.what, tuple(s.text() for s in r.steps), name,
                        hashlib.sha256(data).hexdigest(), len(data), K.Result.OK, "", 0.4, K.Page("/orders/123", "订单 123")))
s = K.ProofSet(K.SCHEMA, "ui", K.Kind.SHOTS, t.intent, t.clone, 0, start, tuple(items),
               ((plan.unit, plan.path, plan.sha256),), "Chrome 157.0.0.0 · 1280×800", ())
K.emit(K.finish_set(t, "ui", new, "shots: %d rows\n" % len(items), s))
"""
PLAN = """# 证据计划：退款

| ID | AC | 看什么 | 步骤 |
|---|---|---|---|
| P-1 | AC-1 | 已付款订单有「退款」按钮 | open /orders/123 · wait 退款 |
| P-2 | AC-2 | 点退款以后状态是「已退款」 | open /orders/123 · click 退款 · wait 已退款 |
"""


@needs_brainkit
def test_the_board_and_the_drawer_show_what_a_real_verify_sealed_and_an_older_brain_kit_says_so(K, tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    (wt / "tests").mkdir(exist_ok=True)
    (wt / "tests" / "fake_shots.py").write_text(FAKE_SHOTS, encoding="utf-8")
    plan = wt / "aidlc-docs" / intent / "construction" / "app" / "proof-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(PLAN, encoding="utf-8")
    py = shlex.quote(sys.executable)
    lj = wt / "aidlc-docs" / "lean.json"
    cfg = json.loads(lj.read_text(encoding="utf-8"))
    cfg["checks"] = ["%s .kiro/brain/proof.py run tests -- %s -c \"print('2 passed')\"" % (py, py),
                     "%s tests/fake_shots.py .kiro/brain/proof.py shots" % py]
    lj.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    commit_all(wt, "证据检查和计划")
    out = lean(wt, "verify").stdout
    assert "verify: PASS" in out, out
    tip = commit_all(wt, "verify 的记录")
    sets = "aidlc-docs/%s/construction/build-and-test/proof" % intent
    shot = next((wt / sets).glob("*/ui/app-P-1.png"))
    svc = make_service(tmp_path, [repo])
    outputs_q = outputs.node_query("build-and-test", "1")

    async def go():
        await svc.start()
        try:
            b = await svc.board("r1", "PAY-1")
            listed = await svc.outputs("r1", "PAY-1", outputs_q)
            f = next(x for x in listed["files"] if x["path"].endswith("/ui/app-P-1.png"))
            pic = await svc.outputs_file("r1", "PAY-1", outputs.file_query({"ref": f["ref"], "path": f["path"],
                                                                          "view": "image"}))
            bk.load(str(older_brainkit(tmp_path)))
            return b, listed, pic, await svc.board("r1", "PAY-1"), await svc.outputs("r1", "PAY-1", outputs_q)
        finally:
            assert bk.load(str(BRAINKIT))["ok"]
            await svc.stop()

    b, listed, pic, old_b, old_listed = run(go())
    p = b["proof"]
    assert (p["state"], p["line"], p["at"], p["gaps"]) == ("current", "截图 2 张 · 测试输出 1 份 · 是这份代码的", tip[:12], [])
    assert [(c["label"], c["round"], c["path"].split("/")[-1]) for c in p["chips"]] == [
        ("P-1", 1, "app-P-1.png"), ("P-2", 1, "app-P-2.png"), ("测试输出 tests", 1, "output.txt")]
    labels = [(x["path"].split("/")[-1], x["proof"]["label"]) for x in listed["files"] if x["proof"]]
    assert labels == [("output.txt", "命令输出 tests：%s -c \"print('2 passed')\"" % sys.executable), ("output.txt", "截图"),
                      ("app-P-1.png", "P-1 · AC-1 · 已付款订单有「退款」按钮"),
                      ("app-P-2.png", "P-2 · AC-2 · 点退款以后状态是「已退款」")]
    assert all(x["ref"] == tip for x in listed["files"] if x["proof"])
    assert base64.b64decode(pic["image"].split(",", 1)[1]) == shot.read_bytes()
    # a brain-kit from before the proof module: the row says so, the board and the drawer still answer
    assert (old_b["proof"]["available"], old_b["proof"]["state"], old_b["proof"]["error"]) == (False, None, evidence.NO_MODULE)
    assert old_b["nodes"] and not [x for x in old_listed["files"] if x["proof"]]
