"""交付总览's three numbers (Overview.cto), from brainkit.evaluate's data: how many hand-over runs ran unattended, how
many times a person stepped in (at the judgment points and outside them), credits per requirement, and the merges per
week by how they went. Pure: evaluate's data is made here in its own shape (brainkit/evaluate.py collect)."""

import time

from appkit import mod

D = mod("deliveries")


def trace(key, *, handed=True, merged_at=None, humans=(), credits=None):
    return {"key": key, "outcome": {"handed_over": {"value": handed}, "merged_at": {"value": merged_at}},
            "humans": [{"kind": k, "what": w, "judgment_point": p} for k, w, p in humans],
            "cost": {"credits": credits}}


def data():
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))  # noqa: E731
    return {
        "ref": "origin/main",
        "autonomy": {"value": [2, 3], "runs": [
            {"key": "PAY-1", "status": "counted", "autonomous": True, "correct": "correct", "beyond": []},
            {"key": "PAY-2", "status": "counted", "autonomous": True, "correct": "correct", "beyond": []},
            {"key": "PAY-3", "status": "counted", "autonomous": False, "correct": "correct",
             "beyond": ["push_after_handover code"]},
            {"key": "PAY-4", "status": "pending", "correct": "pending", "beyond": []},
            {"key": "PAY-5", "status": "local only"}]},
        "judgment": {"value": [5, 6], "by_kind": {"approval（判断点）": 4, "answer（判断点）": 1,
                                                  "push_after_handover（判断点之外）": 1}},
        "traces": [
            trace("PAY-1", merged_at=iso(now - 20 * 86400), humans=[("approval", "REQUIREMENTS", True)], credits=21.5),
            trace("PAY-2", merged_at=iso(now - 3 * 86400), humans=[("approval", "RELEASE", True)], credits=14.25),
            trace("PAY-3", merged_at=iso(now - 2 * 86400), humans=[("approval", "REQUIREMENTS", True),
                                                                   ("push_after_handover", "code", False)]),
            trace("PAY-4", merged_at=iso(now - 1 * 86400)),
            trace("PAY-5", handed=False, merged_at=iso(now - 9 * 86400)),
            trace("PAY-6", handed=True)],
    }


def test_one_repos_part_says_how_each_run_went():
    p = D.cto_part(data())
    assert p["unattended"] == [2, 3] and p["judgment"] == [5, 6]
    runs = {r["key"]: r for r in p["runs"]}
    assert runs["PAY-1"]["how"] == "unattended" and runs["PAY-3"]["how"] == "people"
    assert runs["PAY-3"]["beyond"] == ["交接后推代码"] and runs["PAY-3"]["outside"] == 1 and runs["PAY-3"]["at_points"] == 1
    assert runs["PAY-4"]["how"] == "pending" and runs["PAY-5"]["how"] == "local" and runs["PAY-6"]["how"] == "pending"
    assert runs["PAY-1"]["credits"] == 21.5 and runs["PAY-6"]["merged_at"] is None
    # the acts by kind, the judgment points first
    assert p["kinds"][0] == {"label": "批准", "n": 4, "point": True}
    assert {"label": "交接后推代码", "n": 1, "point": False} in p["kinds"]


def test_the_numbers_add_up_over_repos_and_the_weeks_say_how_merges_went():
    now = time.time()
    rows = [{"repo": "r1", "key": "PAY-1", "state": "merged", "credits": 21.5, "updated_at": now - 20 * 86400},
            {"repo": "r1", "key": "PAY-7", "state": "merged", "credits": None, "updated_at": now - 4 * 86400},
            {"repo": "r1", "key": "PAY-8", "state": "local", "credits": 10.0, "updated_at": now}]
    c = D.cto_numbers([("r1", D.cto_part(data())), ("r2", D.cto_part(data()))], rows, available=True, now=now)
    assert c["available"] and c["unattended"] == [4, 6]
    assert c["stepped_in"]["at_points"] == 10 and c["stepped_in"]["outside"] == 2
    assert c["credits"] == {"per_req": 15.8, "total": 31.5, "reqs": 2, "cut": 0}
    assert len(c["weekly"]) == 12 and c["weekly"][-1]["week"] <= time.strftime("%Y-%m-%d", time.gmtime(now))
    total = {k: sum(w[k] for w in c["weekly"]) for k in ("unattended", "people", "local", "pending")}
    # each repo's PAY-1 … PAY-5 merged in the last 12 weeks; PAY-7 is merged but not judged: pending
    assert total == {"unattended": 4, "people": 2, "local": 2, "pending": 2 + 1}
    assert {r["repo"] for r in c["runs"]} == {"r1", "r2"}
    # whole sentences; 人介入 says it counts the work on this machine too; credits say over how many requirements
    assert c["sentence"] == ("交给运行机的 6 个需求里，4 个交接以后没人插手就做完了。人介入了 12 次（本机做的也算），"
                             "10 次落在判断点上。有 credits 的 2 个需求，平均每个 15.8 credits。")
    assert c["ref"] == "各仓库远端的 main 分支"


def test_a_local_run_not_merged_is_not_done_and_a_local_only_world_says_so():
    """「本机做完」 is a merged local run only (OVERVIEW-3); repos done only on this machine say so instead of 「还没有
    交给运行机、已经评估完的需求」 (OVERVIEW-4); several repos' branches are said by name (OVERVIEW-24)."""
    d = data()
    d["traces"].append(trace("PAY-9", handed=False))
    d["autonomy"]["runs"].append({"key": "PAY-9", "status": "local only"})
    runs = {r["key"]: r["how"] for r in D.cto_part(d)["runs"]}
    assert runs["PAY-5"] == "local" and runs["PAY-9"] == "pending"
    rows = [{"repo": "r1", "key": "DEMO-1", "state": "local", "credits": 49.79, "updated_at": 1.0},
            {"repo": "r1", "key": "DEMO-3", "state": "local", "credits": 36.95, "updated_at": 1.0},
            {"repo": "r1", "key": "DEMO-2", "state": "claimed", "credits": None, "updated_at": None}]
    part = dict(D.cto_part(d), unattended=[0, 0], judgment=[2, 2], ref="refs/heads/main")
    other = dict(D.cto_part(d), unattended=[0, 0], judgment=[0, 0], ref="refs/heads/master")
    c = D.cto_numbers([("r1", part), ("r2", other)], rows, available=True, now=time.time(),
                      names={"r1": "demo", "r2": "oms-brain"}, repos=2, local_repos=2)
    assert c["sentence"].startswith("这 2 个仓库都只在本机做，不交给运行机。人介入了 2 次（本机做的也算），2 次落在判断点上。")
    assert c["sentence"].endswith("有 credits 的 2 个需求，平均每个 43.4 credits。")
    assert c["ref"] == "demo 的 main 分支、oms-brain 的 master 分支"
    one = D.cto_numbers([("r1", part)], rows, available=True, now=time.time(), names={"r1": "demo"}, repos=1, local_repos=1)
    assert one["sentence"].startswith("这个仓库只在本机做，不交给运行机。") and one["ref"] == "main 分支"


def test_without_evaluate_the_rows_still_give_credits_and_merges():
    now = time.time()
    rows = [{"repo": "r1", "key": "PAY-1", "state": "merged", "credits": 12.0, "updated_at": now - 86400}]
    c = D.cto_numbers([], rows, available=False, now=now)
    assert c["available"] is False and c["unattended"] is None and c["stepped_in"] is None
    assert c["credits"]["per_req"] == 12.0 and sum(w["pending"] for w in c["weekly"]) == 1
    assert c["sentence"] == "有 credits 的 1 个需求，平均每个 12.0 credits。"


def test_credits_that_leave_something_out_are_said_to_be_more():
    """E2E-14: a requirement whose credits leave something out (a subagent, the runner's sessions elsewhere) makes the
    average at least what it says."""
    rows = [{"repo": "r1", "key": "PAY-1", "state": "merged", "credits": 12.0, "credits_whole": False, "updated_at": 1.0},
            {"repo": "r1", "key": "PAY-2", "state": "local", "credits": 8.0, "credits_whole": True, "updated_at": 1.0},
            {"repo": "r1", "key": "PAY-3", "state": "claimed", "credits": None, "credits_whole": False, "updated_at": None}]
    c = D.cto_numbers([], rows, available=False, now=time.time())
    assert c["credits"] == {"per_req": 10.0, "total": 20.0, "reqs": 2, "cut": 1}
    assert c["sentence"] == "有 credits 的 2 个需求，平均每个 10.0 credits（1 个还有没算进来的，实际更多）。"


def test_a_persons_numbers_count_the_runs_their_page_lists_run_by_run_as_brain_kit_counts_the_repos():
    """A person's 交付总览 lists their own requirements (*mine*): 无人值守, 人介入 with its kinds, the weekly merges and the
    runs count those alone, so 「无人值守 1/2」 filters its list to the two rows it counts. The team server's are the repo's."""
    now = time.time()
    part = D.cto_part(data())      # PAY-1 and PAY-2 ran unattended, a person stepped in on PAY-3 (once outside a point)
    rows = [{"repo": "r1", "key": k, "state": "merged", "credits": None, "updated_at": now} for k in ("PAY-1", "PAY-3")]
    whole = D.cto_numbers([("r1", part)], rows, available=True, now=now)
    mine = D.cto_numbers([("r1", part)], rows, available=True, now=now, mine={("r1", "PAY-1"), ("r1", "PAY-3")})
    assert (whole["unattended"], whole["mine"], mine["unattended"], mine["mine"]) == ([2, 3], False, [1, 2], True)
    assert sorted(r["key"] for r in mine["runs"]) == ["PAY-1", "PAY-3"]
    st = mine["stepped_in"]
    assert (st["at_points"], st["outside"]) == (2, 1)
    assert st["kinds"] == [{"label": "批准", "n": 2, "point": True}, {"label": "交接后推代码", "n": 1, "point": False}]
    assert [sum(w[k] for w in mine["weekly"]) for k in ("unattended", "people", "pending")] == [1, 1, 0]
    assert mine["sentence"].startswith("交给运行机的 2 个需求里，1 个交接以后没人插手就做完了。人介入了 3 次")


def test_a_persons_funnel_adds_up_who_settled_their_own_runs_decisions():
    parts = [("r1", {"decided": {"PAY-1": {"person": 2, "agent": 1}, "PAY-4": {"unclassified": 1, "agent": 3}}})]
    funnel = {"source": "evaluate", "total": 7, "questions": {"asked": 0, "answered": 0, "delivered": 0},
              "rows": [{"id": "person", "label": "人", "count": 2}, {"id": "agent", "label": "AI 自己定的", "count": 4},
                       {"id": "unclassified", "label": "没归类", "count": 1}],
              "unclassified": [{"key": "PAY-4", "id": "D-3", "src": "aidlc-docs/x/decisions.md#L10"}]}
    got = D.own_funnel(funnel, parts, {("r1", "PAY-1")})
    assert [(r["id"], r["count"]) for r in got["rows"]] == [("person", 2), ("agent", 1), ("unclassified", 0)]
    assert (got["total"], got["unclassified"], got["source"]) == (3, [], "evaluate")


def test_each_runs_decisions_are_counted_by_who_settled_them_as_brain_kits_funnel_counts_them():
    from appkit import BRAINKIT
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    try:
        d = data()
        d["traces"][0]["decisions"] = [{"source": "decisions.md", "by": "person", "cites": []},
                                       {"source": "decisions.md", "by": "agent", "cites": []},
                                       {"source": "decisions.md", "by": "", "cites": ["T-1"]},
                                       {"source": "bridge", "by": "", "cites": [], "choice": None}]   # not answered
        part = D.cto_part(d)
    finally:
        bk.unload()
    assert part["decided"]["PAY-1"] == {"person": 1, "agent": 1, "brain": 1} and part["decided"]["PAY-2"] == {}
    assert part["acts"]["PAY-3"] == [("批准", True), ("交接后推代码", False)]
