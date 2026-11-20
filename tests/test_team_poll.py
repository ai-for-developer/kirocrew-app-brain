"""The hosted bridge's hand-over poll (bridgehost.TeamRuntime._poll inside loop_once), project by project, on a stand-in
for brain-kit's Bridge: a project whose kit.json or labelled MRs cannot be read does not stop the hand-overs of the
projects after it, as in brain-kit's own poll; the round's error names it, and the status says, project by project,
which one was not read and which one leaves mr-handoff out of its kit.json work_sources."""

from types import SimpleNamespace

import pytest

from appkit import BRAINKIT, mod
from brainfix import needs_brainkit

pytestmark = needs_brainkit
HANDOFF = {"work_sources": ["mr-handoff"], "handoff_label": "ai-handoff"}


@pytest.fixture
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


def test_a_project_that_cannot_be_read_does_not_stop_the_hand_overs_of_the_others(bk):
    common = bk.m("common")
    processed = []
    kits = {"grp/a": None, "grp/b": HANDOFF,
            "grp/c": dict(HANDOFF, work_sources=["staging-errors"])}      # written by hand: the hand-over left out

    def kit(project):
        if kits[project] is None:
            raise common.BrainError("grp/a: kit config: default_branch 'main' but GitLab says 'trunk'")
        return kits[project]

    host = SimpleNamespace(cfg={"projects": ["grp/a", {"project": "grp/b"}, "grp/c"]}, kit=kit, out=lambda *_: None,
                           gl=SimpleNamespace(mrs_with_label=lambda p, label: [{"iid": 7, "sha": "a" * 40}]),
                           process=lambda p, k, mr: processed.append((p, mr["iid"])) or "accepted",
                           dispatch=lambda: 0, watch=lambda: 0, collect=lambda: 0, reconcile=lambda: 0)
    rt = mod("team.bridgehost").TeamRuntime({"enabled": True, "bridge_config": "/nonexistent"}, None, None, None)
    rt.host = host
    try:
        rt.loop_once()
        status, polled, handovers = rt.status(), list(processed), rt.handovers()
        kits["grp/a"] = HANDOFF                                         # its kit.json is fixed
        rt.loop_once()
        fixed = rt.status()
    finally:
        rt.executor.shutdown(wait=False)
    assert polled == [("grp/b", 7)] and [(h["project"], h["result"]) for h in handovers] == [("grp/b", "accepted")]
    assert status["loop_error"].startswith("poll: not done this round: grp/a: grp/a: kit config: default_branch")
    a, c = status["checks"]
    assert (a["level"], a["text"]) == ("FAIL", "grp/a 上一轮没读成：它的交接没看，别的项目照常")
    assert "default_branch 'main' but GitLab says 'trunk'" in a["raw"] and a["todo"].startswith("下一轮会自动再试")
    assert (c["level"], c["text"]) == ("WARN", "grp/c 的 kit.json 里 work_sources 没写 mr-handoff：运行机不接这个项目的交接")
    assert '["mr-handoff", "mr-patrol", "staging-errors"]' in c["todo"]
    assert processed[len(polled):] == [("grp/a", 7), ("grp/b", 7)] and fixed["loop_error"] == ""
    assert [x["text"] for x in fixed["checks"]] == [c["text"]]
