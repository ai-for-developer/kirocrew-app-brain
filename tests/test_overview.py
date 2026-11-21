"""交付总览 and 需求现状 on real brain-kit projects: claimed, local (with a pending Lean approval), handed over,
merged; overlaps; the approval line is shown, never sent."""

import json
import subprocess

from appkit import BRAINKIT, mod
from brainfix import (filled_notes, install_brain_dev, lean, make_project, make_service, needs_brainkit, request_requirements, run,
                      sh, start_in_worktree, brain)

pytestmark = needs_brainkit


def rows_by_key(view):
    return {r["key"]: r for r in view["rows"]}


def test_every_requirement_shows_where_it_is_and_what_it_waits_on(tmp_path, home):
    repo = make_project(tmp_path)
    wt_root = home / "workplace" / "brain-app" / "pay"
    wt1 = start_in_worktree(repo, wt_root / "refund", "refund", "退款", "--paths", "src/pay")
    line = request_requirements(wt1, "261108-pay-1-refund") if (wt1 / "aidlc-docs" / "261108-pay-1-refund").exists() else None
    intent1 = json.loads((wt1 / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    if line is None:
        line = request_requirements(wt1, intent1)
    wt2 = start_in_worktree(repo, wt_root / "report", "report", "对账报表", "--paths", "src/pay/report,src/ui")
    brain(repo, "claim", "--title", "只认领")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.overview("all")
        finally:
            await svc.stop()

    view = run(go())
    rows = rows_by_key(view)
    assert set(rows) == {"PAY-1", "PAY-2", "PAY-3"}
    one = rows["PAY-1"]
    assert one["state"] == "local" and one["where"] == "worktree" and one["worktree"] == str(wt1)
    assert one["waits"]["who"] == "you" and one["waits"]["line"] == line and line.startswith("approve REQUIREMENTS ")
    assert one["lean"]["stage"] == "requirements" and one["lean"]["stage_label"] == "需求分析" and one["lean"]["total"] == 11
    assert one["lean"]["stage_n"] == "2.3" and one["lean"]["stage_state"] == "started"
    # brain start's `lean new` skips Ideation as a whole: out of the count, said once
    assert one["stage"] == "做完 0 · 跳过 1 · 在做：需求分析 · 构思整段不需要" and one["lean"]["next"]["label"] == "需求分析"
    assert (one["waits"]["do"], one["waits"]["where"]) == ("approve", "session") and one["waits"]["since"]
    assert rows["PAY-2"]["state"] == "local" and rows["PAY-2"]["waits"]["line"] is None
    assert rows["PAY-3"]["state"] == "claimed" and rows["PAY-3"]["title"] == "只认领"
    # a claim not started waits for its owner to start it, here in the app, since the claim (not since now)
    w3 = rows["PAY-3"]["waits"]
    assert (w3["who"], w3["do"], w3["where"], w3["text"]) == ("you", "start", "app", "已认领，还没开工") and w3["since"]
    assert all(r["local_only"] for r in rows.values())      # no remote: done only on this machine
    # overlap: src/pay contains src/pay/report
    assert view["overlaps"] == [{"keys": ["PAY-1", "PAY-2"], "paths": ["src/pay", "src/pay/report"]}] or \
        view["overlaps"][0]["keys"] == ["PAY-1", "PAY-2"]
    assert rows["PAY-1"]["overlap"] == ["PAY-2"] and rows["PAY-2"]["overlap"] == ["PAY-1"]
    assert view["counts"]["local"] == 2 and view["counts"]["claimed"] == 1
    assert view["funnel"]["rows"] and view["eval"]["metrics"] is not None
    assert "token" not in json.dumps(view).lower()
    # the team view: what its one person has open, and the overlap as a stuck line
    team = view["roster"]
    owner = rows["PAY-1"]["owner"]
    assert team["people"] == [{"owner": owner, "you": True, "open": 3, "limit": 3, "keys": ["PAY-1", "PAY-2", "PAY-3"]}]
    assert team["stuck"] == [{"repo": "r1", "key": "PAY-1", "kind": "overlap", "since": None, "other_key": "PAY-2",
                              "text": "和 PAY-2 都要改 src/pay：都是 %s 的，先合一个再做另一个" % owner}]
    assert team["counts"] == {"overdue": 0, "stale": 0, "overlap": 1, "verify_fails": 0}
    assert [r["verify_fails"] for r in view["rows"]] == [0, 0, 0]


def test_a_handed_over_run_and_a_merged_one(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(), "Status: Construction step 2 of 4"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    wt2 = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "done", "done", "做完了")
    sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge PAY-2", "ai/alice/PAY-2-done")
    sh("git", "-C", repo, "worktree", "remove", "--force", wt2)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.overview("r1"), await svc.requirement("r1", "PAY-1"), await svc.requirement("r1", "PAY-2")
        finally:
            await svc.stop()

    view, d1, d2 = run(go())
    rows = rows_by_key(view)
    assert rows["PAY-1"]["state"] == "handed_over" and rows["PAY-1"]["waits"]["who"] == "bridge"
    assert rows["PAY-2"]["state"] == "merged" and rows["PAY-2"]["where"] == "main"
    assert d1["run"]["handoff"]["by"] == "alice" and d1["lean"]["status"] == "Construction step 2 of 4"
    assert d1["timeline"]["items"] and d1["timeline"]["source"] in ("trace", "app")
    assert any(i["kind"] == "handover" for i in d1["timeline"]["items"]) or d1["timeline"]["source"] == "app"
    assert d2["row"]["state"] == "merged" and d2["where_label"].startswith("默认分支")


BOT = {"GIT_AUTHOR_NAME": "brain-bot", "GIT_AUTHOR_EMAIL": "bot@example.com", "GIT_COMMITTER_NAME": "brain-bot",
       "GIT_COMMITTER_EMAIL": "bot@example.com"}


def test_after_the_hand_over_the_page_follows_the_runner_on_origin_and_keeps_the_persons_own_edits(tmp_path, home):
    repo = make_project(tmp_path, origin=True, kit={"bot_emails": ["bot@example.com"]})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(), "Status: Construction step 2 of 4"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    branch = sh("git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    sh("git", "-C", wt, "push", "-q", "origin", branch)
    # the runner's clone: it worked on, and stopped for the product's acceptance of the release
    runner = tmp_path / "runner"
    sh("git", "clone", "-q", "-b", branch, tmp_path / "pay-origin.git", runner)
    (runner / "runs" / "PAY-1" / "park.json").write_text(json.dumps(
        {"status": "waiting", "kind": "acceptance", "lean_request": "RELEASE 0c9a7e21"}))
    sh("git", "-C", runner, "add", "-A")
    sh("git", "-C", runner, "commit", "-qm", "PAY-1: ask for the acceptance", env=BOT)
    sh("git", "-C", runner, "push", "-q", "origin", branch)
    sh("git", "-C", repo, "fetch", "-q", "origin")      # progress.py's loop, every few minutes
    # alice wrote a line in her worktree after the hand-over and has not committed it
    dec = wt / "aidlc-docs" / intent / "decisions.md"
    dec.write_text(dec.read_text() + "| D-9 | 2026-11-08 | release | 交接后补一句 | 我自己记的 | owner |\n")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.overview("r1"), await svc.requirement("r1", "PAY-1"), await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    view, d, b = run(go())
    row = rows_by_key(view)["PAY-1"]
    assert (row["state"], row["waits"]["who"], row["waits"]["do"]) == ("acceptance", "product", "accept"), \
        "the runner's commit on origin is read: no longer 等桥核对交接"
    assert row["worktree"] == str(wt)
    assert d["row"]["state"] == b["row"]["state"] == "acceptance" and not d["timeline"]["error"]
    assert any(c["text"].endswith("ask for the acceptance") for c in b["belt"]), "the runner's commit is on the belt"
    assert any(x["id"] == "D-9" for x in d["decisions"]), "her own edit, not committed, is still in her view"


def test_an_approval_the_runner_asks_for_is_answered_on_the_mr_not_in_a_session(tmp_path, home):
    """UI-23: after the hand-over the runner stops for a Lean approval (park.json with a Lean request, asked as a
    clarify). It is answered on the MR, approve / reject, once the bridge posts it: 需求现状's question is the runner's
    and names the approval (never 澄清, never 「在会话里打那一行」), and 运行过程's step points at the MR with no
    session line."""
    repo = make_project(tmp_path, origin=True, kit={"bot_emails": ["bot@example.com"]})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(), "Status: Construction step 1 of 4"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    branch = sh("git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    sh("git", "-C", wt, "push", "-q", "origin", branch)
    runner = tmp_path / "runner"
    sh("git", "clone", "-q", "-b", branch, tmp_path / "pay-origin.git", runner)
    (runner / "runs" / "PAY-1" / "park.json").write_text(json.dumps(
        {"status": "waiting", "kind": "clarify", "ask": "owner", "lean_request": "PLAN 0c9a7e21",
         "question": "请批准 app 的代码生成计划"}, ensure_ascii=False))
    sh("git", "-C", runner, "add", "-A")
    sh("git", "-C", runner, "commit", "-qm", "PAY-1: ask for the plan's approval", env=BOT)
    sh("git", "-C", runner, "push", "-q", "origin", branch)
    sh("git", "-C", repo, "fetch", "-q", "origin")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.overview("r1"), await svc.requirement("r1", "PAY-1"), await svc.board("r1", "PAY-1")
        finally:
            await svc.stop()

    view, d, b = run(go())
    w = rows_by_key(view)["PAY-1"]["waits"]
    assert (w["who"], w["do"], w["where"]) == ("you", "answer", "mr") and "会话" not in w["text"]
    [q] = d["questions"]
    assert (q["source"], q["lean_request"], q["answerable_here"]) == ("runner", "PLAN 0c9a7e21", False), \
        "the runner's park.json: answered on the MR, not in this machine's session"
    assert q["kind_label"] == "代码生成计划审批"
    step = b["accept"]["step"]
    assert step["text"] == "等桥把它贴到 MR 上，再在那里回答（approve 通过 / reject 退回）：桥会把回答带回运行机"
    assert (step["line"], step["session_url"], step["comment"]) == (None, None, None), "no D-n yet: nothing to write"


def test_detail_lists_stages_approvals_decisions_with_brain_citations(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    line = request_requirements(wt, intent)
    dec = wt / "aidlc-docs" / intent / "decisions.md"
    # Who in brain-kit's vocabulary (9935b11): agent; owner (the run's owner answered in the local chat); a person's
    # pseudonym and voucher (an answer through the bridge); brain <entry>; anything else is unclassified
    dec.write_text(dec.read_text() + "| D-2 | 2026-11-08 | requirements | 退款单号按 RF+9 位 | 见 T-PAY-1-2 | agent |\n"
                   "| D-3 | 2026-11-08 | requirements | 退款上限是原单金额 | 产品确认 | owner |\n"
                   "| D-4 | 2026-11-08 | requirements | 退款要人工审核 | MR 上回答 | P-1A2B V-0123456789 |\n"
                   "| D-5 | 2026-11-08 | requirements | 对账按自然日 | Brain | brain T-PAY-1-2 |\n"
                   "| D-6 | 2026-11-08 | requirements | 先不做导出 | 产品说的 | product |\n")
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await svc.requirement("r1", "PAY-1")
        finally:
            await svc.stop()

    d = run(go())
    st = {s["id"]: s["state"] for s in d["lean"]["stages"]}
    assert st["reverse-engineering"] == "skipped" and st["requirements"] == "started" and st["release"] == "todo"
    ap = d["lean"]["approvals"]
    assert ap == [dict(ap[0], kind="REQUIREMENTS", state="pending", line=line)]
    by = {x["id"]: x for x in d["decisions"]}
    assert by["D-2"]["cites"] == ["T-PAY-1-2"] and by["D-2"]["by"] == "agent"
    assert (by["D-3"]["by"], by["D-3"]["who"]) == ("person", "owner")
    assert (by["D-4"]["by"], by["D-4"]["who"]) == ("person", "P-1A2B")
    assert (by["D-5"]["by"], by["D-5"]["who"]) == ("brain", "T-PAY-1-2")
    assert (by["D-6"]["by"], by["D-6"]["who"]) == ("unclassified", None)     # not a person: not in the vocabulary
    assert d["credits"]["note"].startswith("KiroCrew 只报 credits")
    # editing the requirements after the request voids the line
    (wt / "aidlc-docs" / intent / "inception" / "requirements" / "requirements.md").write_text("# changed\n")
    svc2 = make_service(tmp_path / "again", [repo])

    async def again():
        await svc2.start()
        try:
            return await svc2.requirement("r1", "PAY-1")
        finally:
            await svc2.stop()

    d2 = run(again())
    assert d2["lean"]["approvals"][0]["state"] == "stale" and d2["lean"]["approvals"][0]["line"] is None
    assert d2["row"]["waits"]["line"] is None


def test_lean_approval_by_inception_answers_the_requirements_request(tmp_path, home):
    mod_lv = __import__("appkit").mod("lean_view")
    files = {"aidlc-docs/i/inception/requirements/requirements.md": b"R", "aidlc-docs/i/inception/application-design/application-design.md": b"D"}
    import hashlib
    h = {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}
    req = "aidlc-docs/i/inception/requirements/requirements.md"
    evs = [{"event": "APPROVAL_REQUESTED", "ts": "2026-11-08T01:00:00Z", "data": {"kind": "REQUIREMENTS", "path": req, "sha256": h[req]}},
           {"event": "APPROVED", "ts": "2026-11-08T02:00:00Z", "data": {"kind": "INCEPTION", "paths": list(files), "files": h, "sha256": "ab" * 32}}]
    out = mod_lv.approvals(evs, files.get)
    assert {a["kind"]: a["state"] for a in out} == {"REQUIREMENTS": "approved", "INCEPTION": "approved"}
    plan = "aidlc-docs/i/construction/plans/app-code-generation-plan.md"
    body = b"- [ ] 1. a\n- [ ] 2. b\n"
    evs = [{"event": "APPROVAL_REQUESTED", "ts": "2026-11-08T01:00:00Z",
            "data": {"kind": "PLAN", "unit": "app", "path": plan, "sha256": hashlib.sha256(body).hexdigest()}}]
    ticked = {plan: b"- [x] 1. a\n- [ ] 2. b\n"}
    assert mod_lv.approvals(evs, ticked.get)[0]["state"] == "pending"  # ticking a step keeps the request
    assert mod_lv.approvals(evs, {plan: b"- [ ] 1. other\n"}.get)[0]["state"] == "stale"


def test_unused_imports_are_fine():
    assert lean and install_brain_dev


def test_team_mode_lists_each_requirement_once_the_runners_facts_winning(tmp_path, home):
    """Team mode: a requirement handed over is listed once, as the runner's; one the runner has only is the runner's;
    a merge by both counts stays its repo's (the merge, the 14 days and the write-back are read there); 待办 too."""
    from types import SimpleNamespace
    repo = make_project(tmp_path, kit={"gitlab": {"base": "https://gitlab.example.com", "project": "shop/pay"}})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    state = wt / "aidlc-docs" / intent / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(), "Status: Construction step 2 of 4"))
    sh("git", "-C", wt, "commit", "-qam", "notes")
    brain(wt, "handoff", "--no-push")
    wt2 = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "done", "done", "做完了")
    sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge PAY-2", "ai/alice/PAY-2-done")
    sh("git", "-C", repo, "worktree", "remove", "--force", wt2)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "local", "local", "本地")
    svc = make_service(tmp_path, [repo], role="team_server")          # team mode is the team server's
    deliveries = __import__("appkit").mod("deliveries")

    def runner_row(key, state, text, project="shop/pay"):
        row = deliveries.base_row("runner", {"key": key, "where": "runner"})
        row.update(where="runner", state=state, state_label=deliveries.STATE_LABEL[state],
                   waits=deliveries.waits("product" if state == "parked" else "nobody", "none", text))
        return row, {"key": key, "project": project, "decisions_text": None}, []

    async def go():
        await svc.start()
        try:
            svc.team = SimpleNamespace(host=object())
            svc._team_rows = lambda snap: [runner_row("PAY-1", "parked", "等产品在 MR 上回答 D-1"),
                                           runner_row("PAY-2", "merged", "MR 已合并，运行结束"),
                                           runner_row("PAY-3", "running", "运行机在跑"),
                                           runner_row("PAY-9", "running", "运行机在跑"),
                                           runner_row("WEB-1", "running", "运行机在跑", project="shop/web")]
            return await svc.overview("all"), await svc.overview("r1"), await svc.overview("runner"), await svc.todo()
        finally:
            svc.team = None
            await svc.stop()

    every, r1, runner, todo = run(go())
    ids = sorted((r["repo"], r["key"]) for r in every["rows"])
    # PAY-1 handed over: the runner's; PAY-2 merged by both: r1's; PAY-3 never handed over here: r1's; PAY-9 the runner's
    assert ids == [("r1", "PAY-2"), ("r1", "PAY-3"), ("runner", "PAY-1"), ("runner", "PAY-9"), ("runner", "WEB-1")], ids
    # r1's view has what the runner does for r1's project (shop/pay), each row saying its repo; a project this machine
    # does not list (shop/web) is in 全部 and the runner's view only
    assert sorted((r["repo"], r["key"]) for r in r1["rows"]) == [("r1", "PAY-2"), ("r1", "PAY-3"), ("runner", "PAY-1"),
                                                                 ("runner", "PAY-9")]
    assert {r["key"]: r["home"] for r in every["rows"]} == {"PAY-1": "r1", "PAY-2": None, "PAY-3": None, "PAY-9": "r1",
                                                            "WEB-1": None}
    assert next(r for r in r1["rows"] if r["key"] == "PAY-1")["state"] == "parked"
    assert sorted(r["key"] for r in runner["rows"]) == ["PAY-1", "PAY-9", "WEB-1"]
    assert sum(every["counts"].values()) == len(every["rows"]) == len(r1["rows"]) + 1
    assert next(r for r in every["rows"] if r["key"] == "PAY-1")["state"] == "parked"
    assert not any(i["key"] == "PAY-1" and i["repo"] == "r1" for i in todo["items"]), todo["items"]


def test_the_numbers_say_they_are_being_worked_out_until_evaluate_is_done(tmp_path, home):
    """Overview 待确认-2: while brainkit.evaluate computes a repo's numbers the page is told so (正在算), then not."""
    import asyncio
    repo = make_project(tmp_path)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            first = await svc.overview("r1")
            for _ in range(300):
                later = await svc.overview("r1")
                if not later["eval"].get("computing"):
                    return first, later
                await asyncio.sleep(0.1)
            return first, later
        finally:
            await svc.stop()

    first, later = run(go())
    assert first["eval"]["computing"] is True and first["eval"]["available"] is False
    assert later["eval"]["computing"] is False


def test_a_repo_that_cannot_be_read_is_said_in_words_with_the_original_under_details(tmp_path, home):
    """Overview 待确认-1: an exception while reading a repo's requirements is not shown; the banner says what to do."""
    repo = make_project(tmp_path)
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            def broken(*a, **k):
                raise OSError("[Errno 13] Permission denied: '.git/refs/heads/claim'")
            svc.repos["r1"].scan = broken
            return await svc.overview("all")
        finally:
            await svc.stop()

    view = run(go())
    (e,) = view["errors"]
    assert e["error"] == "读不了这个仓库里的需求：在「导入仓库」里检查它，那里会说哪里不对"
    assert e["raw"].startswith("[Errno 13] Permission denied")
    assert (e["name"], e["path"]) == (repo.name, str(repo.resolve()))


def filed_for_anyone(repo, key, title):
    """A claim filed for others by alice (owner null, as brain split files a small requirement), pushed: claim.json in
    a commit of its own, as brain-kit's claims write one."""
    doc = {"schema": 1, "key": key, "owner": None, "title": title, "created_at": "2026-11-20T08:00:00Z", "filed_by": "alice"}
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=json.dumps(doc) + "\n",
                          text=True, capture_output=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(repo), "mktree"], input="100644 blob %s\tclaim.json\n" % blob, text=True,
                          capture_output=True, check=True).stdout.strip()
    c = sh("git", "-C", repo, "commit-tree", tree, "-m", "file %s by alice (unclaimed)" % key).stdout.strip()
    sh("git", "-C", repo, "push", "-q", "origin", "%s:refs/heads/claim/%s" % (c, key))


def test_a_persons_overview_lists_their_own_requirements_and_the_team_servers_everyones(tmp_path, home):
    """交付总览 on alice's machine: her requirement, and PAY-3 that alice filed for anyone (nobody's yet: whoever starts
    it has it). bob's run, which her app fetched from origin, is the team's (团队运行机's 全队交付), not hers; that it
    may change her paths is still said on hers. 按人 has her alone. On the team server's 交付总览 every requirement is
    listed, as before."""
    from brainfix import person
    repo = make_project(tmp_path, origin=True)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款", "--paths", "src/pay")
    sh("git", "-C", repo, "push", "-q", "origin", "refs/heads/claim/*:refs/heads/claim/*", "refs/heads/ai/*:refs/heads/ai/*")
    bob = person(tmp_path, tmp_path / "pay-origin.git", "bob")
    brain(bob, "start", "report", "--title", "报表", "--paths", "src/pay/report")
    sh("git", "-C", bob, "push", "-q", "origin", "refs/heads/claim/PAY-2", "refs/heads/ai/bob/PAY-2-report")
    filed_for_anyone(repo, "PAY-3", "对账单导出")
    sh("git", "-C", repo, "fetch", "-q", "origin")

    def overview(svc):
        async def go():
            await svc.start()
            try:
                return await svc.overview("all")
            finally:
                await svc.stop()
        return run(go())

    mine = overview(make_service(tmp_path / "alice", [repo]))
    team = overview(make_service(tmp_path / "server", [repo], role="team_server"))
    assert sorted(rows_by_key(team)) == ["PAY-1", "PAY-2", "PAY-3"] and rows_by_key(team)["PAY-2"]["owner"] == "bob"
    assert sorted(rows_by_key(mine)) == ["PAY-1", "PAY-3"] and sum(mine["counts"].values()) == 2
    assert (rows_by_key(mine)["PAY-3"]["owner"], rows_by_key(mine)["PAY-3"]["waits"]["text"]) == (None, "待认领：谁开工就归谁")
    assert mine["overlaps"] == team["overlaps"] == [{"keys": ["PAY-1", "PAY-2"], "paths": ["src/pay"]}]
    assert rows_by_key(mine)["PAY-1"]["overlap"] == ["PAY-2"]
    assert [p["owner"] for p in mine["roster"]["people"]] == ["alice"]
    assert sorted(p["owner"] for p in team["roster"]["people"]) == ["alice", "bob"]


def test_a_persons_own_rows_are_theirs_the_ones_nobody_has_and_the_parts_of_their_big_ones():
    """The reviewer's own_rows run: alice keeps hers, the claim filed for anyone, the small one nobody took (of bob's big
    one), and carol's small one of alice's big one; bob's are the team's. A repo where she is not known keeps all."""
    d = mod("deliveries")
    rows = [
        {"repo": "r1", "key": "PAY-1", "owner": "alice", "family": None},
        {"repo": "r1", "key": "PAY-6", "owner": None, "family": None},
        {"repo": "r1", "key": "PAY-8", "owner": None, "family": {"role": "child", "parent": "PAY-5"}},
        {"repo": "r1", "key": "PAY-5", "owner": "bob", "family": {"role": "parent", "parent": "PAY-5"}},
        {"repo": "r1", "key": "PAY-9", "owner": "bob", "family": {"role": "child", "parent": "PAY-5"}},
        {"repo": "r1", "key": "PAY-11", "owner": "alice", "family": {"role": "parent", "parent": "PAY-11"}},
        {"repo": "r1", "key": "PAY-12", "owner": "carol", "family": {"role": "child", "parent": "PAY-11"}},
        {"repo": "r2", "key": "WEB-1", "owner": "bob", "family": None},
    ]
    keys = lambda got: [r["key"] for r in got]  # noqa: E731
    assert keys(d.own_rows(rows, {"r1": "alice", "r2": "alice"})) == ["PAY-1", "PAY-6", "PAY-8", "PAY-11", "PAY-12"]
    assert keys(d.own_rows(rows, {"r1": "alice", "r2": None})) == ["PAY-1", "PAY-6", "PAY-8", "PAY-11", "PAY-12", "WEB-1"]
    assert keys(d.own_rows(rows, {"r1": None, "r2": None})) == keys(rows)


def read_overview(svc, *, mrs=False):
    """GET /overview once the service has started; *mrs*: after a read of the MRs its rows asked for (refresh_mrs)."""
    async def go():
        await svc.start()
        try:
            if mrs:
                await svc.overview("all")
                await svc.refresh_mrs()
                for r in svc.repos.values():
                    r.invalidate()
            return await svc.overview("all")
        finally:
            await svc.stop()
    return run(go())


def test_a_persons_overview_has_their_own_fast_run_and_not_a_teammates(tmp_path, home):
    """alice's fast run (`brain start --fast`) is on her 交付总览 as a fast run: its row says its flow, it waits on her
    to open its session, and 需求流动 has it in 构建 until its MR is open. bob's fast run, fetched from origin, is the
    team's: on the team server's 交付总览, waiting on him on his machine, and not on hers."""
    from brainfix import person
    repo = make_project(tmp_path, origin=True)
    start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "coupon", "coupon", "优惠码", "--fast")
    sh("git", "-C", repo, "push", "-q", "origin", "refs/heads/claim/*:refs/heads/claim/*", "refs/heads/ai/*:refs/heads/ai/*")
    bob = person(tmp_path, tmp_path / "pay-origin.git", "bob")
    brain(bob, "start", "report", "--title", "报表", "--fast")
    sh("git", "-C", bob, "push", "-q", "origin", "refs/heads/claim/PAY-2", "refs/heads/ai/bob/PAY-2-report")
    sh("git", "-C", repo, "fetch", "-q", "origin")
    deliveries = mod("deliveries")
    mine = rows_by_key(read_overview(make_service(tmp_path / "alice", [repo])))
    team = rows_by_key(read_overview(make_service(tmp_path / "server", [repo], role="team_server")))
    assert sorted(mine) == ["PAY-1"] and sorted(team) == ["PAY-1", "PAY-2"]
    one = mine["PAY-1"]
    assert (one["owner"], one["flow"]["id"], one["state"]) == ("alice", "fast", "local")
    assert (one["waits"]["who"], one["waits"]["do"], one["waits"]["text"]) == ("you", "reply", "没有开着的会话：打开会话，跟 AI 说要做什么")
    assert deliveries.flow_stage(one) == "构建"
    two = team["PAY-2"]
    assert (two["owner"], two["flow"]["id"], two["waits"]["text"]) == ("bob", "fast", "bob 在自己的机器上做")


def test_a_run_started_from_an_issue_is_its_labelers_on_the_runner(tmp_path, home, monkeypatch):
    """A requirement 从 issue 接活 started on the runner for alice's label (the issue source's own code pushed its claim
    and start commit to origin), fetched by alice's app and bob's. alice's 交付总览 lists it as hers and the runner's:
    its MR, read with her token, has the running label, so 运行机在跑 in 交给运行机, not a run waiting on her machine.
    bob's leaves it out: it is alice's (团队运行机's 全队交付 has the team's)."""
    from brainfix import person
    from issuefix import started_from_issue
    from test_kitswitch import BASE, GITLAB, TOKEN, GitLab
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    origin = tmp_path / "pay-origin.git"
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]         # the issue source's code runs here, outside any app
    try:
        key, branch, sha = started_from_issue(origin, tmp_path / "issues", GITLAB["gitlab"]["project"], "alice",
                                              base=BASE, iid=7)
    finally:
        bk.unload()
    gl = GitLab((repo / ".brain" / "kit.json").read_text(encoding="utf-8"))
    gl.mrs.append({"iid": 31, "source_branch": branch, "target_branch": "main", "state": "opened", "sha": sha,
                   "labels": ["ai-running"], "web_url": "%s/grp/pay/-/merge_requests/31" % BASE})
    bob = person(tmp_path, origin, "bob")
    sh("git", "-C", repo, "fetch", "-q", "origin")
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)
    monkeypatch.setattr(mod("mrinfo"), "TTL_S", 0.0)
    mine = rows_by_key(read_overview(make_service(tmp_path / "alice", [repo], transport=gl), mrs=True))
    theirs = rows_by_key(read_overview(make_service(tmp_path / "bob", [bob], transport=gl), mrs=True))
    assert key in mine and key not in theirs
    row = mine[key]
    assert (row["owner"], row["state"], row["waits"]["who"], row["waits"]["text"]) == ("alice", "running", "runner", "运行机在跑")
    assert (row["mr"]["iid"], row["mr"]["state"]) == (31, "opened")
    assert mod("deliveries").flow_stage(row) == "交给运行机"
