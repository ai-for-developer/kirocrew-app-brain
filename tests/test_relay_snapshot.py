"""The team server's state relay, its pure parts: a requirement's stage in 交付总览's words (deliveries.flow_stage, held to
ui/src/cto/Flow.tsx colOf by one cases file both suites run), and the TeamSnapshot both sides parse (snapshot.py)."""

import copy
import json

import pytest

from appkit import ROOT, mod

deliveries, snapshot = mod("deliveries"), mod("snapshot")
CASES = json.loads((ROOT / "ui" / "src" / "test" / "stage-cases.json").read_text(encoding="utf-8"))["cases"]
WAITS = {"who": "product", "do": "accept", "text": "等产品在 MR 上验收 D-3", "since": 1794135600.0, "on": ["carol"]}


def a_snapshot():
    """Every part of a project's TeamSnapshot with one row each, as a team server writes it on shop/pay's brain/status."""
    link = {"iid": 61, "url": "https://gitlab.example.com/shop/pay/-/issues/61", "title": "测试环境报错：KeyError"}
    return {
        "schema": 1, "generated_at": 1794146400.0, "project": "shop/pay",
        "server": {"version": "0.4.2", "team_mode": True, "last_round_at": 1794146390.5, "cap": 2, "working": 1},
        "state": {"on": True, "sources": ["mr-handoff", "mr-patrol"], "error": ""},
        "runner": {
            "handovers": [{"project": "shop/pay", "iid": 41, "mr_url": "https://gitlab.example.com/shop/pay/-/merge_requests/41",
                           "key": "PAY-4", "title": "退款审核", "run_id": "RUN-PAY-4-1", "state": "parked", "owner": "alice",
                           "waits": copy.deepcopy(WAITS)}],
            "patrol": [{"project": "shop/pay", "iid": 52, "title": "折扣", "url": None, "author": "bob", "state": "working",
                        "problems": [{"kind": "ci_failed", "text": "流水线 #9 没过：test"}], "skip": "", "skip_code": "",
                        "note": "", "attempts": 1, "slot": "patrol-7-52", "updated_at": "2026-11-08T13:00:00Z"}],
            "staging": [{"project": "shop/pay", "signature": "KeyError at pay/refund.py:refund", "count": 3, "state": "issued",
                         "issue": link, "origin": None, "fix_mr": None, "first_seen": 1794140000, "last_seen": 1794146000,
                         "deployed_sha": "a" * 40, "attempts": 0, "slot": None, "note": ""}]},
        "delivery": [{"project": "shop/pay", "key": "PAY-4", "title": "退款审核", "owner": "alice", "stage": "发布 · 验收",
                      "state": "acceptance", "branch": "ai/alice/PAY-4-refund",
                      "mr": {"iid": 41, "url": "https://gitlab.example.com/shop/pay/-/merge_requests/41", "state": "opened",
                             "ci": "success"},
                      "deployed": None, "waits": copy.deepcopy(WAITS), "updated_at": 1794146000.0}],
        "delivery_at": 1794146380.0,
    }


def test_a_snapshot_reads_back_as_it_was_written():
    snap = snapshot.parse(a_snapshot())
    assert snapshot.parse(snapshot.dump(snap)) == snap == snapshot.parse(snapshot.dump(snap).encode())
    assert snap["runner"]["staging"][0]["first_seen"] == 1794140000.0      # a whole number of seconds is a time too
    assert snapshot.dump(snap) == snapshot.dump(json.loads(snapshot.dump(snap)))


def test_what_a_newer_server_adds_is_left_out_and_what_one_lacks_is_said_where():
    newer = a_snapshot()
    newer["server"]["region"] = "cn-north"
    newer["delivery"][0]["mr"]["approvals"] = 2
    assert snapshot.parse(newer) == snapshot.parse(a_snapshot())
    for path, value, said in ((("delivery", 0, "waits", "on"), "carol", "delivery[0].waits.on: want a list"),
                              (("server", "cap"), True, "server.cap: want a whole number"),
                              (("state", "sources"), "mr-handoff", "state.sources: want a list"),
                              (("server", "team_mode"), 1, "server.team_mode: want true or false"),
                              (("delivery", 0, "mr", "iid"), "41", "delivery[0].mr.iid: want a whole number"),
                              (("generated_at",), None, "generated_at: want a number")):
        bad = a_snapshot()
        at = bad
        for k in path[:-1]:
            at = at[k]
        at[path[-1]] = value
        with pytest.raises(snapshot.Bad) as got:
            snapshot.parse(bad)
        assert str(got.value) == said
    lacking = a_snapshot()
    del lacking["runner"]["handovers"][0]["waits"]
    with pytest.raises(snapshot.Bad, match=r"^runner\.handovers\[0\]: missing waits$"):
        snapshot.parse(lacking)


@pytest.mark.parametrize("schema", [2, 0, "1", True, None])
def test_another_schema_is_not_read_as_this_one(schema):
    other = dict(a_snapshot(), schema=schema)
    with pytest.raises(snapshot.OtherSchema) as got:
        snapshot.parse(other)
    assert got.value.schema == schema


@pytest.mark.parametrize("raw", [b"{", b"[]", "\"text\"", b"\xff\xfe"])
def test_what_is_not_a_snapshot_is_bad(raw):
    with pytest.raises(snapshot.Bad):
        snapshot.parse(raw)


def test_parse_keeps_the_input_as_it_was():
    raw = a_snapshot()
    kept = copy.deepcopy(raw)
    snapshot.parse(raw)
    assert raw == kept


@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_a_requirements_stage_is_the_column_its_overview_puts_it_in(case):
    assert deliveries.flow_stage(case["row"]) == case["stage"]


def test_the_cases_name_every_stage_the_overview_has():
    assert {c["stage"] for c in CASES} - {None} == set(deliveries.FLOW_STAGES)
