"""交付总览's team view (backend/core/roster.py): working hours, each kind of stuck requirement (a hit and a miss),
what each person has open, the order 全队卡点 lists them in, and teammates' rows as the real classify makes them."""

from datetime import datetime, timezone

from appkit import mod

roster = mod("roster")
deliveries = mod("deliveries")
family = mod("family")
LIMITS = {k: v[0] for k, v in mod("settings").LIMITS.items()}     # 设置's defaults: 3, 4, 8, 3
CFG = {"default_branch": "main"}


def at(day: int, hour: int, minute: int = 0) -> float:
    """A moment of the week of Monday 2026-11-09, local time (Friday is the 13th, Monday after it the 16th)."""
    return datetime(2026, 11, day, hour, minute).timestamp()


def row(key, owner, state, who="nobody", do="none", text="", since=None, *, repo="r1", paths=(), verify=(), where=None,
        family_=None):
    """A row as the overview has it (deliveries.base_row and its waits); *verify*: Lean's verify runs, oldest first,
    each a run of one check that passed or not."""
    r = deliveries.base_row(repo, {"key": key, "owner": owner, "run": {"expected_paths": list(paths)},
                                   "events": [{"event": "VERIFY", "data": {"ok": ok}} for ok in verify]})
    r.update(state=state, state_label=deliveries.STATE_LABEL[state], family=family_)
    r["waits"] = deliveries.waits(who, do, text, since=since, where=where)
    return r


def view(rows, now, me="alice", **limits):
    return roster.roster(rows, me=me, now=now, settings=dict(LIMITS, **limits))


def design_wait(key, owner, since):
    """Someone else's run on their machine, waiting for its DESIGN approval (classify's words for it)."""
    return row(key, owner, "local", "people", "approve", deliveries.approve_text({"kind": "DESIGN"}, owner), since)


def kinds(got, kind):
    return [s for s in got["stuck"] if s["kind"] == kind]


def test_working_hours_are_monday_to_friday_nine_to_six_local_time():
    h = roster.work_hours_between
    assert h(at(13, 10), at(13, 16)) == 6
    assert h(at(13, 17), at(13, 22)) == 1                   # the evening does not count
    assert h(at(13, 19), at(13, 23)) == 0
    assert h(at(12, 23), at(13, 9, 30)) == 0.5              # the night neither
    assert h(at(13, 16), at(16, 10)) == 3                   # Friday's last two hours, Monday's first
    assert h(at(14, 10), at(15, 20)) == 0                   # a weekend
    assert h(at(11, 15), at(12, 11)) == 5                   # across days
    assert h(at(9, 9), at(16, 9)) == 45                     # a whole week
    assert h(at(13, 16), at(13, 10)) == 0                   # the end before the start


def test_a_human_gate_waiting_past_its_timeout_is_overdue_with_how_long_in_working_hours():
    acceptance = row("PAY-5", "alice", "acceptance", "product", "accept",
                     deliveries.mr_ask("product", "验收", "D-3", approval=True), at(13, 13, 30))
    refused = row("PAY-9", "carol", "parked", "people", "answer",
                  deliveries.STUCK_DECISION["refused"] % "D-1", at(13, 9), where="mr")
    got = view([acceptance, design_wait("PAY-15", "carol", at(13, 12)), refused], at(13, 18, 30))
    assert got["stuck"] == [
        {"repo": "r1", "key": "PAY-9", "kind": "overdue", "since": at(13, 9), "other_key": None,
         "text": "D-1 的回答签名服务不签：看运行机页的拒签原因，已等 9 个工作小时（超时）"},     # its reason kept
        {"repo": "r1", "key": "PAY-15", "kind": "overdue", "text": "等 carol 批准设计，已等 6 个工作小时（超时）",
         "since": at(13, 12), "other_key": None},
        {"repo": "r1", "key": "PAY-5", "kind": "overdue", "text": "等产品在 MR 上验收 D-3，已等 4 个工作小时（超时）",
         "since": at(13, 13, 30), "other_key": None}]


def test_what_is_not_a_human_gate_or_not_late_yet_is_not_overdue():
    now = at(16, 8)
    rows = [design_wait("PAY-15", "carol", at(13, 15)),                                 # 3 working hours
            design_wait("PAY-16", "carol", at(13, 17)),                                 # Friday 17:00, Monday 8:00: 1
            row("PAY-6", "carol", "claimed", "people", "start", "等 carol 开工", at(9, 9)),  # her next step, no gate
            row("PAY-1", "alice", "local", "you", "reply", "会话停下了：看它最后说了什么，回它", at(9, 9)),
            row("PAY-7", "alice", "rejected", "you", "redo_handoff", "接手被拒：标签是 bob 打的", at(9, 9)),
            row("PAY-10", "alice", "running", "people", "none", "流水线一直没过：验收问题没发，请负责人看一下", at(9, 9)),
            row("PAY-4", "carol", "running", "runner", "none", "运行机在跑", at(9, 9)),
            row("PAY-14", None, "unclaimed", "people", "start", "待认领：谁做都可以认领", at(9, 9)),
            row("ORD-9", "erin", "merged", "people", "write_back", "已合并，有 1 条新知识等 erin 回写 Brain", at(9, 9))]
    assert kinds(view(rows, now), "overdue") == []
    # the timeout is 设置's: at 1 working hour the Friday-afternoon approvals are late too, and nothing else
    assert [(s["key"], s["text"]) for s in kinds(view(rows, now, wait_timeout_h=1), "overdue")] == [
        ("PAY-15", "等 carol 批准设计，已等 3 个工作小时（超时）"), ("PAY-16", "等 carol 批准设计，已等 1 个工作小时（超时）")]


def test_someone_elses_run_with_nothing_new_pushed_for_long_is_stale():
    since = at(11, 15)      # bob's last push: Wednesday 15:00; Friday 11:00 is 3 + 9 + 2 working hours later
    got = view([row("PAY-13", "bob", "local", "people", "none", "bob 在自己的机器上做", since)], at(13, 11))
    assert got["stuck"] == [{"repo": "r1", "key": "PAY-13", "kind": "stale",
                             "text": "bob 在自己的机器上做，14 个工作小时没有推上新进度：问问 bob 卡在哪", "since": since,
                             "other_key": None}]


def test_a_run_that_moved_lately_yours_or_waiting_on_someone_else_is_not_stale():
    now = at(13, 11)
    long_ago = at(9, 9)
    parent = {"role": "parent", "family_state": "in_progress"}
    rows = [row("PAY-13", "bob", "local", "people", "none", "bob 在自己的机器上做", at(12, 13)),    # 7 working hours
            row("PAY-11", "alice", "local", "people", "none", "等小需求合进来：已合并 1/3", long_ago, family_=parent),
            row("PAY-21", "bob", "local", "people", "none", "bob 在自己的机器上做", long_ago, family_=parent),
            row("PAY-22", "bob", "local", "people", "none", "交了 MR：等大需求的负责人合进 b", long_ago, where="mr"),
            row("PAY-23", "alice", "local", "people", "none", "alice 在自己的机器上做", long_ago),          # yours
            design_wait("PAY-15", "carol", long_ago)]                                                  # a gate
    assert kinds(view(rows, now), "stale") == []


def test_two_open_requirements_that_could_change_one_file_are_one_line():
    rows = [row("PAY-3", "alice", "local", paths=["src/refund/", "src/export/csv.py"]),
            row("PAY-4", "carol", "parked", paths=["src/refund/ledger.py"]),
            row("PAY-1", "alice", "local", paths=["src/stats/"]),
            row("PAY-9", "alice", "claimed", paths=["src/stats/daily.py"]),
            row("PAY-12", "bob", "local", paths=["services/api/**"]),         # brain split writes a unit's paths as globs
            row("PAY-17", "carol", "local", paths=["services/api/handlers.py"]),
            row("PAY-2", "alice", "merged", paths=["src/refund/id.py"]),      # merged: changes nothing any more
            row("ORD-1", "erin", "local", repo="r3", paths=["src/refund/"])]  # another repo
    got = kinds(view(rows, at(13, 11)), "overlap")
    assert got == [{"repo": "r1", "key": "PAY-3", "kind": "overlap", "text": "和 PAY-4 都要改 src/refund：先商量好谁先合",
                    "since": None, "other_key": "PAY-4"},
                   {"repo": "r1", "key": "PAY-1", "kind": "overlap",
                    "text": "和 PAY-9 都要改 src/stats：都是 alice 的，先合一个再做另一个", "since": None,
                    "other_key": "PAY-9"},
                   {"repo": "r1", "key": "PAY-12", "kind": "overlap",
                    "text": "和 PAY-17 都要改 services/api/**：先商量好谁先合", "since": None, "other_key": "PAY-17"}]
    assert [deliveries.path_base(p) for p in ("services/api/**", "src/*.py", "./src/refund/", "*.md")] == [
        "services/api", "src", "src/refund", ""]


def test_on_the_team_runner_a_row_pairs_by_the_repo_it_is_in():
    def on_runner(key, home, paths):
        r = row(key, "carol", "running", repo="runner", paths=paths)
        r["home"] = home
        return r

    rows = [row("PAY-3", "alice", "local", paths=["src/refund/"]),
            on_runner("PAY-4", "r1", ["src/refund/ledger.py"]),       # shop-pay's, on the runner
            on_runner("ORD-5", "r3", ["src/refund/"]),                 # shop-order's
            on_runner("ORD-6", None, ["src/refund/"])]                 # a project this machine does not list
    assert [(s["key"], s["other_key"]) for s in kinds(view(rows, at(13, 11)), "overlap")] == [("PAY-3", "PAY-4")]


def test_one_check_failing_verify_round_after_round_says_stop_and_ask():
    def runs(*rounds):
        """Lean's VERIFY records, oldest first: each round {check: exit code}."""
        return [{"event": "VERIFY", "data": {"ok": all(e == 0 for e in rd.values()),
                                             "results": [{"cmd": c, "exit": e} for c, e in rd.items()]}} for rd in rounds]

    def streak(*rounds):
        return mod("lean_view").verify_fails(runs(*rounds))

    assert streak({"pytest -q": 1, "ruff": 0}, {"pytest -q": 1, "ruff": 0}, {"pytest -q": 1, "ruff": 0}) == 3
    assert streak({"pytest -q": 1, "ruff": 0}, {"pytest -q": 0, "ruff": 1}, {"pytest -q": 1, "ruff": 0}) == 1   # a new check each round
    assert streak({"pytest -q": 1}, {"pytest -q": 1}, {"pytest -q": 0}) == 0                                  # passed at last
    assert streak({"pytest -q": 1}, {"pytest -q": 1}, {"pytest -q": 1}, {"pytest -x": 0}) == 0                # not run any more
    assert streak() == 0
    rows = [row("ORD-12", "erin", "local", verify=[False, True, False, False, False]),
            row("ORD-14", "erin", "local", verify=[False, False]),                # under the limit
            row("ORD-8", "dave", "merged", verify=[False, False, False])]         # done
    assert [r["verify_fails"] for r in rows] == [3, 2, 3]
    assert view(rows, at(13, 11))["stuck"] == [{"repo": "r1", "key": "ORD-12", "kind": "verify_fails",
                                                "text": "同一项检查 verify 连续失败 3 次，该停下来问人了", "since": None,
                                                "other_key": None}]
    assert [s["key"] for s in view(rows, at(13, 11), verify_fail_limit=2)["stuck"]] == ["ORD-12", "ORD-14"]


def test_each_person_counts_what_they_have_open_you_first_then_the_most():
    rows = [row("PAY-3", "alice", "local"), row("PAY-8", "alice", "claimed"), row("PAY-10", "alice", "handed_over"),
            row("PAY-2", "alice", "merged"),
            row("PAY-11", "alice", "local", family_={"role": "parent", "family_state": "in_progress"}),   # its parts count
            row("PAY-13", "bob", "local"),
            row("PAY-4", "carol", "parked"), row("PAY-5", "carol", "acceptance", repo="runner"),   # on the runner
            row("ORD-8", "dave", "merged"), row("PAY-6", "carol", "closed"),
            row("PAY-14", None, "unclaimed"), row("PAY-17", None, "claimed")]                    # nobody's
    got = view(rows, at(13, 11), wip_limit=2)["people"]
    assert got == [{"owner": "alice", "you": True, "open": 3, "limit": 2, "keys": ["PAY-3", "PAY-8", "PAY-10"]},
                   {"owner": "carol", "you": False, "open": 2, "limit": 2, "keys": ["PAY-4", "PAY-5"]},
                   {"owner": "bob", "you": False, "open": 1, "limit": 2, "keys": ["PAY-13"]}]
    assert [p["owner"] for p in view(rows, at(13, 11), me="bob")["people"]] == ["bob", "alice", "carol"]


def test_what_is_stuck_comes_worst_kind_first_and_is_counted_by_kind():
    now = at(13, 18)
    rows = [row("ORD-12", "erin", "local", verify=[False] * 3),
            row("PAY-3", "alice", "local", paths=["src/refund/"]), row("PAY-4", "carol", "parked", paths=["src/refund/"]),
            row("PAY-13", "bob", "local", "people", "none", "bob 在自己的机器上做", at(11, 9)),
            design_wait("PAY-15", "carol", at(13, 13)), design_wait("PAY-16", "carol", at(12, 13))]
    got = view(rows, now)
    assert [(s["kind"], s["key"]) for s in got["stuck"]] == [
        ("overdue", "PAY-16"), ("overdue", "PAY-15"), ("stale", "PAY-13"), ("overlap", "PAY-3"),
        ("verify_fails", "ORD-12")]
    assert got["counts"] == {"overdue": 2, "stale": 1, "overlap": 1, "verify_fails": 1}
    assert got["limits"] == {"wip_limit": 3, "wait_timeout_h": 4, "stale_after_h": 8, "verify_fail_limit": 3}
    quiet = view([row("PAY-3", "alice", "local")], now)
    assert quiet["stuck"] == [] and quiet["counts"] == {"overdue": 0, "stale": 0, "overlap": 0, "verify_fails": 0}


def classified(key, owner, me, fam, *, approval_at=None, tip_at=None, mr=False, claim_at=None):
    """A small requirement's row as a machine whose person is *me* has it: deliveries.classify over its facts (a run on
    its owner's machine, read from the branch they pushed, its DESIGN approval pending when *approval_at*), then
    family.classify with brain-kit's listing of it (*fam*)."""
    run = {"intent": "261109-x", "branch": "ai/%s/%s-x" % (owner, key), **({"mr": {"by": owner}} if mr else {})}
    pending = [{"kind": "DESIGN", "unit": None, "state": "pending", "line": "approve DESIGN 1234abcd", "at": approval_at}]
    f = {"key": key, "owner": owner, "where": "branch", "branch": run["branch"], "tip_at": tip_at,
         "claim": {"owner": owner, "title": key, "created_at": "2026-11-09T01:00:00Z"} if claim_at is None else
         {"owner": owner, "title": key, "created_at": datetime.fromtimestamp(claim_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
         "run": None if claim_at is not None else run, "approvals": pending if approval_at else [], "events": []}
    r = deliveries.base_row("r1", f)
    deliveries.classify(r, f, mr=None, slot=None, cfg=CFG, me=me, now=at(13, 16))
    r["family"] = fam
    family.classify(r, fam, f, me)
    return r


def test_teammates_small_and_big_requirements_as_classify_says_them():
    child = {"role": "child", "parent": "PAY-1", "unit": "web", "state": "in_progress", "owner": "bob", "branch": "b",
             "blocked_by": [], "depends_on": []}
    now = at(13, 16)
    # bob's small one waits for its DESIGN approval: on alice's machine it is bob's gate, late after 4 working hours
    approval = classified("PAY-3", "bob", "alice", child, approval_at=at(13, 10), tip_at=at(13, 10))
    assert [s["text"] for s in view([approval], now)["stuck"]] == ["等 bob 批准设计，已等 6 个工作小时（超时）"]
    # bob's claim is held by a dependency: nobody can start it, so nothing waits on bob
    held = classified("PAY-4", "bob", "alice", dict(child, state="claimed", blocked_by=["PAY-2"],
                                                    depends_on=[{"key": "PAY-2", "text": "PAY-2（api，carol 在做）"}]),
                      claim_at=at(11, 9))
    assert (held["waits"]["do"], held["waits"]["text"]) == ("none", "bob 认领了：要等 PAY-2（api，carol 在做）合进大需求才能开工")
    # alice's small one has its MR in: it waits for the big one's owner to merge it, on any machine, not for alice
    for me in ("alice", "bob"):
        merge = classified("PAY-5", "alice", me, dict(child, owner="alice"), tip_at=at(12, 10), mr=True)
        assert (merge["waits"]["text"], merge["waits"]["where"]) == ("交了 MR：等大需求的负责人合进 b", "mr")
    # alice's big one waits for its small ones: on bob's machine it is neither late nor counted
    parent = {"role": "parent", "owner": "alice", "family_state": "in_progress", "progress": {"merged": 0, "total": 2}}
    big = classified("PAY-1", "alice", "bob", parent, tip_at=at(11, 9))
    got = view([held, merge, big], now, me="bob")
    assert got["stuck"] == []
    assert got["people"] == [{"owner": "bob", "you": True, "open": 1, "limit": 3, "keys": ["PAY-4"]},
                             {"owner": "alice", "you": False, "open": 1, "limit": 3, "keys": ["PAY-5"]}]
