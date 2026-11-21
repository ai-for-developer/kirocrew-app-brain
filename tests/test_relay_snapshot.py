"""The team server's state relay, its pure parts: a requirement's stage in 交付总览's words (deliveries.flow_stage, held to
ui/src/cto/Flow.tsx colOf by one cases file both suites run)."""

import json

import pytest

from appkit import ROOT, mod

deliveries = mod("deliveries")
CASES = json.loads((ROOT / "ui" / "src" / "test" / "stage-cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_a_requirements_stage_is_the_column_its_overview_puts_it_in(case):
    assert deliveries.flow_stage(case["row"]) == case["stage"]


def test_the_cases_name_every_stage_the_overview_has():
    assert {c["stage"] for c in CASES} - {None} == set(deliveries.FLOW_STAGES)
