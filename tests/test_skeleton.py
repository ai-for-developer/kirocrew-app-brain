"""The app's frame: the manifest names it apart from aidlc-team, every route but /health is the dashboard owner's
only (never an app token), and the routes answer under plain pytest with the fake aiohttp."""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from http_fakes import FakeRequest, FakeWeb

import backend.routes as routes
from appkit import ROOT


class Route:
    def __init__(self, method, path, handler):
        self.method, self.path, self.handler = method, path, handler


def build(tmp_path, owner=True):
    ctx = SimpleNamespace(name="brain", logger=None, data_dir=str(tmp_path / "data"))
    svc = routes.service.Service(ctx)
    return svc, {(r.method, r.path): r.handler for r in routes.handlers.build_routes(svc, Route, web=FakeWeb(),
                                                                                    is_owner=lambda _r: owner)}


def call(handler, **req):
    return asyncio.run(handler(FakeRequest(**req), None))


def test_the_manifest_is_brains_own():
    m = json.loads((ROOT / "app.json").read_text())
    assert m["name"] == "brain" and m["ui"]["pages"][0]["route"] == "/apps/brain"
    assert m["permissions"]["api"] == ["/api/apps/brain", "/api/chat"] and m["permissions"]["sessionApproval"] is True
    assert "aidlc" not in json.dumps(m).lower()
    c = routes.service  # the private namespace differs from aidlc-team's
    assert c.__name__.startswith("_brainapp_backend.")
    from appkit import mod
    k = mod("constants")
    assert (k.APP_NAME, k.LOCAL_FOLDER, k.RUNNER_FOLDER) == ("brain", "Brain 需求", "Brain 运行机")
    assert "aidlc-team" not in str(k.WORK_ROOT)


def test_health_needs_no_login(tmp_path):
    _, r = build(tmp_path)
    resp = call(r[("GET", "/health")])
    assert resp.status == 200 and resp.body["ok"] is True


def test_every_other_route_is_the_owners_only(tmp_path):
    _, r = build(tmp_path, owner=False)
    for (method, path), h in r.items():
        if path == "/health":
            continue
        assert call(h, method=method, user=None).status == 401, path
        assert call(h, method=method, app="brain").status == 403, path  # an app token, this app's included
        assert call(h, method=method).status == 403, path  # signed in, but not the owner


def test_bad_ids_are_refused_before_anything_runs(tmp_path):
    _, r = build(tmp_path)
    resp = call(r[("GET", "/requirements/{repo}/{key}")], match_info={"repo": "../etc", "key": "PAY-1"})
    assert resp.status == 404 and resp.body["code"] == "no_repo"
    resp = call(r[("GET", "/requirements/{repo}/{key}")], match_info={"repo": "r1", "key": "pay-1; rm"})
    assert resp.status == 400 and resp.body["code"] == "invalid_key"
    resp = call(r[("POST", "/crew/fix")], json_body={"what": "everything"})
    assert resp.status == 400 and resp.body["code"] == "invalid_fix"
    resp = call(r[("POST", "/requirements/{repo}/{key}/answer")], match_info={"repo": "r1", "key": "PAY-1"},
                json_body={"choice": "A", "id": "approve"})
    assert resp.status == 400 and resp.body["code"] == "invalid_id"


def test_settings_are_checked_before_they_are_saved(tmp_path, home):
    svc, r = build(tmp_path)
    resp = call(r[("POST", "/settings")], json_body={"repos": ["relative/path"], "work_root": "/tmp/elsewhere",
                                                     "team": {"enabled": True}})
    assert resp.status == 400 and "绝对路径" in resp.body["error"] and "workplace" in resp.body["error"]
    assert "就要填「桥的配置文件」" in resp.body["error"]       # 设置's own label, not team.bridge_config
    assert not (tmp_path / "data" / "settings.json").exists()


# the brain-kit commit the separate-user signer names when it refuses an older checkout (its BRAINKIT_NEEDED)
SIGNER_NEEDED = re.search(r'^BRAINKIT_NEEDED = "([0-9a-f]+)"', (ROOT / "signer" / "brain_signer.py").read_text(
    encoding="utf-8"), re.M).group(1)
OLDER = [
    # 9935b11: the hand-over commit rule (a method: nothing else imports it, so the copy still imports)
    ("bridge/handoff.py", lambda t: t.replace("def handoff_commit_problems(", "def _handoff_commit_problems_gone("),
     "bridge.handoff.Bridge.handoff_commit_problems"),
    # 72b278e: the edit signal (GitLab GraphQL's lastEditedBy, through rest.GitLab.note_edits)
    ("rest.py", lambda t: t.replace("def note_edits(", "def _note_edits_gone("), "rest.GitLab.note_edits"),
]


@pytest.mark.parametrize("path,cut,lacks", OLDER, ids=["9935b11", "72b278e"])
def test_an_older_brainkit_is_refused(tmp_path, path, cut, lacks):
    """The app (and its signer) need brain-kit 72b278e's interfaces: a checkout without one of them is refused with a
    message naming what it lacks and the commit needed (the app's: bk.VERSION_NEEDED; the signer's: its own
    BRAINKIT_NEEDED), not half used. (A copy of the checkout with one taken out.)"""
    import shutil
    from appkit import BRAINKIT, mod
    if not (BRAINKIT / "brainkit" / "__init__.py").is_file():
        pytest.skip("brain-kit checkout not found")
    old = tmp_path / "old-brain-kit"
    shutil.copytree(BRAINKIT / "brainkit", old / "brainkit", ignore=shutil.ignore_patterns("__pycache__"))
    f = old / "brainkit" / path
    f.write_text(cut(f.read_text(encoding="utf-8")), encoding="utf-8")
    bk = mod("bk")
    try:
        got = bk.load(str(old))
        assert got["ok"] is False and lacks in got["error"] and bk.VERSION_NEEDED in got["error"], got
        assert bk.load(str(BRAINKIT))["ok"] is True
    finally:
        bk.unload()
    # the signer refuses it too (its own process, as it runs)
    import subprocess
    import sys
    probe = "import sys; sys.path.insert(0, %r); import brain_signer; brain_signer.load_brainkit(sys.argv[1])" % str(
        ROOT / "signer")
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(old)], capture_output=True, text=True)
    assert p.returncode != 0 and lacks in p.stderr and SIGNER_NEEDED in p.stderr, p.stderr
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(BRAINKIT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_the_signer_needs_the_brain_kit_that_asks_q_n(tmp_path):
    """The signer reads which question a reply names by brain-kit 81380eb's decisions.same_question (its bridge asks
    Q-<n>, and `answer D-<n>` still answers it): a checkout without it is refused, naming it and the brain-kit the
    signer needs (its BRAINKIT_NEEDED, 81380eb or later). The app reads the same rule itself (mrinfo.same_question) and
    still runs on that checkout."""
    import shutil
    import subprocess
    import sys
    from appkit import BRAINKIT, mod
    old = tmp_path / "old-brain-kit"
    shutil.copytree(BRAINKIT / "brainkit", old / "brainkit", ignore=shutil.ignore_patterns("__pycache__"))
    f = old / "brainkit" / "bridge" / "decisions.py"
    f.write_text(f.read_text(encoding="utf-8").replace("def same_question(", "def _same_question_gone("), encoding="utf-8")
    probe = "import sys; sys.path.insert(0, %r); import brain_signer; brain_signer.load_brainkit(sys.argv[1])" % str(
        ROOT / "signer")
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(old)], capture_output=True, text=True)
    assert p.returncode != 0 and "bridge.decisions.same_question" in p.stderr and SIGNER_NEEDED in p.stderr, p.stderr
    bk = mod("bk")
    try:
        assert bk.load(str(old))["ok"] is True
    finally:
        bk.unload()


def test_the_signer_needs_the_brain_kit_whose_check_answer_takes_the_ci_line(tmp_path):
    """The bridge (brain-kit d333ec9) puts a line of its own on what CI did into an acceptance question, and the
    signer's check_answer must take the question with it: an older one voids it as edited, so every acceptance answer
    would be refused. A checkout without decisions.ci_line (the rule that line follows) is refused at the start,
    naming it and d333ec9."""
    import shutil
    import subprocess
    import sys
    from appkit import BRAINKIT
    f = BRAINKIT / "brainkit" / "bridge" / "decisions.py"
    if "def ci_line(" not in f.read_text(encoding="utf-8"):
        pytest.skip("brain-kit checkout before d333ec9")
    old = tmp_path / "old-brain-kit"
    shutil.copytree(BRAINKIT / "brainkit", old / "brainkit", ignore=shutil.ignore_patterns("__pycache__"))
    g = old / "brainkit" / "bridge" / "decisions.py"
    g.write_text(g.read_text(encoding="utf-8").replace("def ci_line(", "def _ci_line_gone("), encoding="utf-8")
    probe = "import sys; sys.path.insert(0, %r); import brain_signer; brain_signer.load_brainkit(sys.argv[1])" % str(
        ROOT / "signer")
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(old)], capture_output=True, text=True)
    assert p.returncode != 0 and "bridge.decisions.ci_line" in p.stderr and "d333ec9" in p.stderr, p.stderr
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(BRAINKIT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_a_brainkit_without_flows_is_refused_as_too_old(tmp_path):
    """Fast mode's rules live in brain-kit's flows: a checkout from before it is too old, said so, never half used."""
    import shutil
    from appkit import BRAINKIT, mod
    if not (BRAINKIT / "brainkit" / "flows.py").is_file():
        pytest.skip("brain-kit checkout without flows")
    old = tmp_path / "old-brain-kit"
    shutil.copytree(BRAINKIT / "brainkit", old / "brainkit", ignore=shutil.ignore_patterns("__pycache__"))
    (old / "brainkit" / "flows.py").unlink()
    bk = mod("bk")
    try:
        got = bk.load(str(old))
        assert got["ok"] is False and got["error"] == "brain-kit 太旧（没有 flows）：要 %s 或更新的版本" % bk.VERSION_NEEDED
    finally:
        bk.unload()
