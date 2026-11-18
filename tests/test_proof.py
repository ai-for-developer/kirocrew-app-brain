"""证据 in the app: what `lean verify` sealed, read through brain-kit's proof module (bk's optional "proof").

The 产出 drawer: a build-and-test round lists the files its VERIFY sealed, found by the output_sha256 Lean recorded
and labelled by plan row, never another verify's; a PNG or a JPEG shows as a picture (view image, a data: URL) and
nothing else does. With a brain-kit older than the proof module the drawer lists no evidence and keeps working.

The sets are written by the kit's own render_set (proof.py shots drives Chrome and has its own tests in brain-kit)."""

import base64
import hashlib
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from appkit import BRAINKIT, git_repo, mod, sh
from brainfix import needs_brainkit

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


def write_set(K, repo: Path, name: str, rows, body: str, at: str, on_disk=None) -> str:
    """A set folder as the kit writes it; returns sha256(output.txt), which Lean's VERIFY records for its check.
    *on_disk*: bytes written in place of a file's (an edit after the capture)."""
    d = repo / K.set_path(INTENT, CLONE, name)
    d.mkdir(parents=True, exist_ok=True)
    for _, files in rows:
        for f, data in files.items():
            (d / f).write_bytes((on_disk or {}).get(f, data))
    kind = K.Kind.SHOTS if name == "ui" else K.Kind.RUN
    s = K.ProofSet(K.SCHEMA, name, kind, INTENT, CLONE, 0, K.CodeState(at, ()), tuple(i for i, _ in rows), (),
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
    return dict(repo=repo, fold=fold(repo, rounds, [c1, r1, c2, r2]), c1=c1, r1=r1, c2=c2, r2=r2, p1=p1, p3=p3)


# ---------------------------------------------------------------- the 产出 drawer

@needs_brainkit
def test_a_verify_round_lists_what_it_sealed_labelled_by_plan_row(K, tmp_path):
    w = two_verifies(K, tmp_path)
    files = listed(w["fold"], 1)
    got = [(f["path"].replace(SETS + "/", ""), f["ref"], f["output"], f["proof"]) for f in files if f["output"]]
    r1 = w["r1"]
    assert [f["path"] for f in files if not f["output"]] == ["src/refund.py"]
    assert got == [
        ("tests/output.txt", r1, True, {"set": "tests", "label": "命令输出 tests：pytest -q", "result": None, "note": ""}),
        ("tests/AC-2-refund.jpg", r1, True, {"set": "tests", "label": "AC-2-refund.jpg · AC-2", "result": "ok", "note": ""}),
        ("ui/output.txt", r1, True, {"set": "ui", "label": "截图", "result": None, "note": ""}),
        ("ui/app-P-1.png", r1, True, {"set": "ui", "label": "P-1 · AC-1 · 已付款订单有「退款」按钮", "result": "ok", "note": ""}),
        ("ui/app-P-2.png", r1, True, {"set": "ui", "label": "P-2 · AC-2 · 点退款以后状态是「已退款」", "result": "failed",
                                     "note": "没等到「已退款」（10 秒）"}),
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
    """brainkit/ as it was before brainkit/proof.py came (the parent of the commit that added it)."""
    added = sh("git", "-C", BRAINKIT, "log", "--diff-filter=A", "--format=%H", "--", "brainkit/proof.py",
               check=False).stdout.split()
    if not added:
        pytest.skip("this brain-kit's history does not say when brainkit/proof.py came")
    old = tmp_path / "older-brain-kit"
    old.mkdir()
    tar = subprocess.run(["git", "-C", str(BRAINKIT), "archive", added[-1] + "^", "brainkit"], capture_output=True,
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
