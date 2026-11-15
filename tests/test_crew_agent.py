"""KiroCrew's agent for brain-dev sessions is the one brain-kit names (crewcheck.crew_agent: cli.CREW_AGENT,
brain-dev-crew, so a project's .kiro/agents/brain-dev.md cannot run in its place). The KiroCrew check, its fix and the
session the app opens all name that agent, with the brain-dev.json of before the rename still on this machine."""

import json
import os

from appkit import mod
from brainfix import FakeGateway, make_project, make_service, needs_brainkit, run

pytestmark = needs_brainkit

NEW = "brain-dev-crew"


def renamed(monkeypatch, tmp_path):
    """The loaded brain-kit as brain-kit d8c5ffe made it: CREW_AGENT, crew/brain-dev-crew.json, crew_agent_path(name)."""
    cli = mod("bk").m("cli")
    with open(cli.CREW_AGENT_SRC, encoding="utf-8") as f:
        spec = dict(json.load(f), name=NEW)
    src = tmp_path / "crew" / ("%s.json" % NEW)
    src.parent.mkdir()
    src.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(cli, "CREW_AGENT", NEW, raising=False)
    monkeypatch.setattr(cli, "CREW_AGENT_SRC", str(src))
    monkeypatch.setattr(cli, "crew_agent_path",
                        lambda name=NEW: os.path.join(os.path.expanduser("~"), ".kiro", "agents", name + ".json"))


def body(tmp_path):
    d = tmp_path / "需求文档"
    d.mkdir()
    (d / "退款需求.md").write_text("# 退款\n\n- AC-1 能退款\n", encoding="utf-8")
    return {"repo": "r1", "slug": "refund", "title": "退款", "docs": [str(d)], "consent": True,
            "classification": "internal", "business": "", "exclude": [], "paths": [], "open_session": True, "trust": True}


def start(tmp_path, monkeypatch, gw, legacy=None):
    """*legacy*: write brain-kit's spec there first, as the brain-dev.json of before the rename (which a project's
    .kiro/agents/brain-dev.md runs in place of)."""
    svc = make_service(tmp_path, [make_project(tmp_path)], gw=gw)

    async def go():
        await svc.start()
        try:
            if legacy:
                with open(mod("bk").m("cli").CREW_AGENT_SRC, encoding="utf-8") as f:
                    legacy.write_text(f.read(), encoding="utf-8")
            renamed(monkeypatch, tmp_path)
            first = await svc.crew_checks()
            fixed = await svc.crew_fix("agent")
            return first, fixed, await svc.intake_start(body(tmp_path))
        finally:
            await svc.stop()

    return run(go())


def test_after_brain_kit_renamed_its_agent_the_check_its_fix_and_the_session_are_brain_dev_crew(tmp_path, home,
                                                                                                    monkeypatch):
    gw = FakeGateway()
    gw.templates = {"brain-dev", NEW}
    first, fixed, out = start(tmp_path, monkeypatch, gw, legacy=home / ".kiro" / "agents" / "brain-dev.json")
    agent = next(r for r in first["rows"] if r["fix"] == "agent")
    assert agent["text"] == "KiroCrew 里还没有 brain-dev-crew" and "点「装上 brain-dev-crew」" in agent["todo"], agent
    assert agent["raw"] == "没有 ~/.kiro/agents/brain-dev-crew.json", agent
    assert fixed["ok"] and fixed["text"].startswith("装好了 brain-dev-crew"), fixed
    assert json.loads((home / ".kiro" / "agents" / "brain-dev-crew.json").read_text())["name"] == NEW
    assert any(r["level"] == "OK" and r["text"] == "brain-dev-crew 的设置里 Lean 的拦截都在" for r in fixed["rows"])
    assert out["slot"] and not out["session_error"], out
    assert [c[1:] for c in gw.calls if c[0] == "agent"] == [(NEW, "template")]


def test_a_kirocrew_without_brain_dev_crew_names_the_button_that_installs_it(tmp_path, home, monkeypatch):
    gw = FakeGateway()
    gw.templates = {"brain-dev"}
    _, _, out = start(tmp_path, monkeypatch, gw)
    assert out["slot"] is None and not gw.slot_rows
    assert "KiroCrew 里没有 brain-dev-crew：在设置里点「装上 brain-dev-crew」，再试一次" in out["session_error"], out

