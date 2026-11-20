"""A fast run's row and board (快速): whose turn it is and the step, from its commits, its newest verify, its risk paths
and its own MR, through deliveries.classify as every row goes; and the board's five steps."""

from appkit import mod

deliveries, fastrow, fastboard, lean_view = mod("deliveries"), mod("fastrow"), mod("fastboard"), mod("lean_view")
FastFacts, Verify = fastrow.FastFacts, fastrow.Verify
CFG = {"default_branch": "main", "rejected_label": "ai-handoff-rejected", "running_label": "ai-running",
       "handoff_label": "ai-handoff"}
RUN = {"mode": "fast", "intent": None, "branch": "ai/alice/PAY-17-coupon", "owner": "alice"}
IDLE = {"busy": False, "awaiting": False, "last_activity": 1794186428.5}
GREEN = Verify(ok=True, at=1794186400.0, failed=())
HEAD = "c" * 40


def facts(**kw):
    return dict({"key": "PAY-17", "owner": "alice", "claim": {"owner": "alice", "title": "订单优惠码"}, "run": RUN,
                 "approvals": [], "park": None, "events": [], "stages": lean_view.stages([]), "worktree": "/w",
                 "branch": RUN["branch"], "tip_at": 1794186300.0}, **kw)


def fast(**kw):
    return FastFacts(**dict({"code_commits": 2, "verify": GREEN, "risky": (), "patrol": False}, **kw))


def mr(state="opened", pipelines=None):
    return {"iid": 12, "url": "https://gitlab.example.com/shop/pay/-/merge_requests/12", "state": state, "sha": HEAD,
            "labels": [], "questions": [], "pipelines": pipelines}


def pipe(status, sha=HEAD):
    return {"id": 901, "status": status, "sha": sha, "at": 1794186450.0,
            "url": "https://gitlab.example.com/shop/pay/-/pipelines/901"}


def row_of(f=None, *, me="alice", slot=IDLE, m=None, ff=None):
    f = f or facts()
    row = deliveries.base_row("r1", f)
    deliveries.classify(row, f, mr=m, slot=slot, cfg=CFG, me=me, now=1794186500.0, fast=ff or fast())
    w = row["waits"]
    return row["state"], w["who"], w["do"], w["where"], w["text"]


def test_a_fast_row_waits_on_whoever_takes_its_next_step():
    assert row_of(slot=dict(IDLE, busy=True)) == ("local", "agent", "none", None, "AI 在写代码")
    assert row_of() == ("local", "you", "open_mr", "app", fastrow.READY)
    assert row_of(ff=fast(verify=None)) == ("local", "you", "open_mr", "app", fastrow.READY_UNCHECKED)
    assert row_of(ff=fast(code_commits=0, verify=None)) == (
        "local", "you", "reply", "session", "会话停下了：看它最后说了什么，回它")
    assert row_of(slot=None, ff=fast(code_commits=0, verify=None)) == (
        "local", "you", "reply", "app", "没有开着的会话：打开会话，跟 AI 说要做什么")
    red = fast(verify=Verify(ok=False, at=1.0, failed=("pytest",)))
    assert row_of(ff=red) == ("local", "you", "reply", "session", "自动检查没过（pytest）：看会话，回它")
    assert row_of(slot=None, ff=red)[3:] == ("app", "自动检查没过（pytest）：打开会话让 AI 修")
    assert row_of(f=facts(worktree=None), slot=None)[2:] == (
        "reply", None, "这台机器上没有它的工作区：先检出分支 ai/alice/PAY-17-coupon，再打开会话接着做")


def test_a_risk_path_sends_the_person_to_the_full_flow_before_and_after_the_mr():
    risky = fast(risky=("src/pay/refund.py",))
    want = ("local", "you", "full", "app", "改到了风险目录（src/pay/refund.py）：快速需求不能合并这个改动。点「转完整流程」")
    assert row_of(ff=risky) == want
    assert row_of(ff=risky, m=mr(pipelines=[pipe("failed")])) == want
    many = fast(risky=("a.py", "b.py", "c.py", "d.py", "e.py"))
    assert "a.py、b.py、c.py等 5 个文件" in row_of(ff=many)[4]


def test_its_mr_says_what_ci_does_and_who_merges():
    assert row_of(m=mr(pipelines=[pipe("running")])) == ("local", "nobody", "none", None, "MR !12：CI 在跑")
    assert row_of(m=mr(pipelines=[pipe("success")])) == ("local", "people", "merge", "mr", "MR !12：CI 过了，等人审、合并")
    assert row_of(m=mr(pipelines=None))[4] == "MR !12：等人审、合并"
    assert row_of(m=mr(pipelines=[pipe("success", sha="d" * 40)]))[4] == "MR !12：等人审、合并"
    assert row_of(m=mr(pipelines=[pipe("failed")])) == ("local", "you", "reply", "session", "MR !12：CI 没过：回会话让 AI 修")
    assert row_of(m=mr(pipelines=[pipe("failed")]), ff=fast(patrol=True)) == (
        "local", "runner", "none", None, "MR !12：CI 没过，运行机的 MR 巡检会去修")
    assert row_of(m=mr("closed")) == ("closed", "nobody", "none", None, "MR 已关闭")
    st, who, do, _, text = row_of(m=mr("merged"))
    assert (st, who, do) == ("merged", "nobody", "watch") and text.startswith("MR 已合并")


def test_the_checks_every_row_shares_come_first():
    assert row_of(me="bob") == ("local", "people", "none", None, "alice 在自己的机器上做")
    asked = dict(IDLE, awaiting=True)
    assert row_of(slot=asked)[1:3] == ("you", "tool")
    merged = facts(merged=True, merged_at=None)
    assert row_of(f=merged)[0] == "merged"


def steps_of(f=None, ff=None, m=None, busy=False):
    return {s["id"]: (s["status"], s["text"], s["url"])
            for s in fastboard.steps(f or facts(), fast=ff or fast(), mr=m, busy=busy)}


def test_the_board_has_five_steps_in_order():
    got = fastboard.steps(facts(), fast=fast(code_commits=0, verify=None), mr=None, busy=False)
    assert [(s["id"], s["label"]) for s in got] == [("code", "写代码"), ("verify", "自动检查"), ("mr", "开 MR"),
                                                    ("ci", "CI"), ("merge", "合并")]
    assert {s["status"] for s in got} == {"waiting"}
    assert steps_of(busy=True)["code"][0] == "running"


def test_each_step_follows_the_run_to_its_merge():
    s = steps_of()
    assert s["code"][:2] == ("done", "提交了 2 次") and s["verify"][:2] == ("done", "过了")
    assert s["mr"][:2] == ("waiting", "做完了点「开 MR」") and s["ci"][0] == "waiting"
    s = steps_of(ff=fast(verify=Verify(ok=False, at=1.0, failed=("pytest", "ruff"))))
    assert s["verify"][:2] == ("failed", "没过：pytest、ruff")
    s = steps_of(m=mr(pipelines=[pipe("success")]))
    url = mr()["url"]
    assert s["mr"] == ("done", "MR !12", url) and s["ci"] == ("done", "过了", pipe("success")["url"])
    assert s["merge"] == ("waiting", "等人审、合并", url)
    assert steps_of(m=mr(pipelines=[pipe("running")]))["ci"][:2] == ("running", "在跑")
    assert steps_of(m=mr(pipelines=None))["ci"][:2] == ("waiting", "没有流水线")
    assert steps_of(m=mr("merged", pipelines=[pipe("success")]))["merge"][:2] == ("done", "合并了")


def test_a_risk_path_fails_the_step_it_stops():
    risky = fast(risky=("src/pay/refund.py",))
    assert steps_of(ff=risky)["mr"][:2] == ("failed", fastboard.RISK_STEP)
    assert steps_of(ff=risky, m=mr(pipelines=[pipe("failed")]))["ci"][:2] == ("failed", fastboard.RISK_STEP)
