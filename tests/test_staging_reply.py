"""patrol_reply.py's --issue and --open-mr (the command 测试环境报错's fix sessions comment on their Issue and open their MR
with), offline: brain-kit's GitLab client on the in-memory GitLab, the token from bridge.json's token file."""

import pytest

from appkit import BRAINKIT, mod
from brainfix import needs_brainkit
from patrolfix import BASE, Clock
from stagingfix import StagingGitLab

pytestmark = needs_brainkit
P, PID = "grp/repo", 42


@pytest.fixture
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


@pytest.fixture
def reply(tmp_path):
    from test_patrol import reply_tool
    gl = StagingGitLab(Clock())
    gl.project(P, PID)
    gl.issues[(P, 1)] = {"iid": 1, "project_id": PID, "title": "t", "description": "", "labels": ["staging-error"],
                         "state": "opened", "web_url": BASE + "/grp/repo/-/issues/1", "_notes": []}
    token = tmp_path / "bridge-gitlab-token"
    token.write_text("bridge-tok\n")
    token.chmod(0o600)
    cfg = tmp_path / "bridge.json"
    cfg.write_text('{"gitlab": {"base": "%s", "token_env": "STAGING_TEST_NO_ENV", "token_file": "%s"}}' % (BASE, token))
    args = ["--config", str(cfg), "--brainkit", str(BRAINKIT), "--project", P]
    return gl, reply_tool(), args


def test_the_reply_helper_comments_on_an_issue_and_opens_the_fix_mr_that_closes_it(bk, reply, capsys):
    gl, tool, args = reply
    branch = ["--source", "fix/staging-ab12cd34", "--target", "main", "--title", "修复：除以零"]
    assert tool.main(args + ["--issue", "1", "--note", "改了：rate 为 0 时不打折"], transport=gl) == 0
    assert tool.main(args + ["--issue", "1", "--open-mr"] + branch + ["rate 为 0 时不打折，加了测试"], transport=gl) == 0
    assert tool.main(args + ["--issue", "1", "--open-mr"] + branch + ["再开一次"], transport=gl) == 0
    assert tool.main(args + ["--issue", "1", "--open-mr", "--source", "fix/other", "--target", "main", "--title", "x",
                             "修好了。\n\ncloses #1"], transport=gl) == 0
    assert gl.notes(P, 1) == ["改了：rate 为 0 时不打折"]
    made = [c[3] for c in gl.calls if c[0] == "POST" and c[1].endswith("/merge_requests")]
    assert [(m["source_branch"], m["target_branch"], m["title"], m["description"]) for m in made] == [
        ("fix/staging-ab12cd34", "main", "修复：除以零", "rate 为 0 时不打折，加了测试\n\nCloses #1"),
        ("fix/other", "main", "x", "修好了。\n\ncloses #1")]
    out = capsys.readouterr().out.split("\n")
    assert out[1:4] == ["!1 %s/grp/repo/-/merge_requests/1" % BASE] * 2 + ["!2 %s/grp/repo/-/merge_requests/2" % BASE]
    before = len(gl.calls)
    for bad in (["--mr", "7", "--open-mr"] + branch + ["x"], ["--issue", "1", "--discussion", "ab" * 8, "x"],
                ["--issue", "1", "--open-mr", "--source", "../x", "--target", "main", "--title", "t", "x"],
                ["--issue", "1", "--open-mr", "--source", "fix/a", "--target", "main", "x"]):
        assert tool.main(args + bad, transport=gl) == 2, bad
    assert len(gl.calls) == before
