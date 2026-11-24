"""Rules of AI-DLC Lean the app restates instead of running Lean, held to the Lean brain-kit vendors by running that
Lean on the same files: a disagreement fails here, not on the team runner."""

import json

import pytest

from appkit import BRAINKIT, mod
from brainfix import lean, make_project, needs_brainkit

pytestmark = needs_brainkit


@pytest.fixture(autouse=True)
def bk():
    b = mod("bk")
    assert b.load(str(BRAINKIT))["ok"]
    yield b
    b.unload()


@pytest.mark.parametrize("text", [
    None,                                                       # Lean's template: "checks": []
    json.dumps({"version": 1}),                                 # no checks key: Lean's DEFAULT_CONFIG has []
    json.dumps({"version": 1, "checks": None}),
    json.dumps({"version": 1, "checks": ["python3 -c pass"]}),
    json.dumps({"version": 1, "checks": ["python3 -c 'import sys; sys.exit(3)'"]}),     # fails, but it can pass
    "{not json",
])
def test_issue_intake_holds_exactly_where_lean_verify_cannot_pass(tmp_path, home, text):
    repo = make_project(tmp_path)
    if text is not None:
        (repo / "aidlc-docs" / "lean.json").write_text(text, encoding="utf-8")
    p = lean(repo, "verify", check=False)
    said = p.stdout + p.stderr
    cannot = p.returncode != 0 and ("No checks configured" in said or "not valid JSON" in said)
    assert bool(mod("team.issues").verify_problem(str(repo), "main")) == cannot, said
