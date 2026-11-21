"""状态中转's 全队交付 (team/relay.py Deliveries), offline: alice's and bob's real brain-kit requirements (brain start, Lean's
records, brain handoff, merges) pushed to a bare origin that a fake GitLab serves (relayfix.RepoGitLab), read with the
bridge's token as the team server reads them: from GitLab only.

Every requirement open, or merged within 14 days, is one row in 交付总览's words: its stage, what it waits on and who
takes that step, its MR with the CI at its head, and whether the staging VERSION holds its merge. A merge older than
that, and a closed claim, are not rows. A branch's records are read once per head, an MR's notes once per change, a
pipeline until it is over, a deploy's merge base once; a project GitLab does not answer keeps its last rows."""

import hashlib
import json
import subprocess
import time

import pytest

from appkit import BRAINKIT, mod, sh
from brainfix import brain, filled_notes, make_project, needs_brainkit, person, request_requirements, start_in_worktree
from relayfix import BASE, RepoGitLab
from teamfix import BOT_ENV

pytestmark = needs_brainkit
P = "shop/pay"
KIT = {"gitlab": {"base": BASE, "project": P}, "bridge_user": "brain-bridge"}
ROLES = {"product": ["carol", "erin"], "builder": ["bob"]}
DAY = 86400


def publish(path):
    """Every claim and run branch of the checkout, to origin."""
    sh("git", "-C", path, "push", "-q", "origin", "refs/heads/claim/*:refs/heads/claim/*", "refs/heads/ai/*:refs/heads/ai/*")


def intent_of(wt, key):
    return json.loads((wt / "runs" / key / "run.json").read_text(encoding="utf-8"))["intent"]


def commit(path, files, msg, bot=False):
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), encoding="utf-8")
    sh("git", "-C", path, "add", "-A")
    sh("git", "-C", path, "commit", "-qm", msg, env=BOT_ENV if bot else None)
    return sh("git", "-C", path, "rev-parse", "HEAD").stdout.strip()


def unowned_claim(repo, key, title):
    """A claim filed for others (brain split's, owner null), pushed: brain-kit's claim_doc in a commit of its own, as
    brain-kit's claims write one."""
    doc = mod("bk").m("claims").claim_doc(key, None, title, {"filed_by": "alice"})
    path = repo.parent / ("claim-%s.json" % key)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    blob = sh("git", "-C", repo, "hash-object", "-w", path).stdout.strip()
    tree = subprocess.run(["git", "-C", str(repo), "mktree"], input="100644 blob %s\tclaim.json\n" % blob, text=True,
                          capture_output=True, check=True).stdout.strip()
    c = sh("git", "-C", repo, "commit-tree", tree, "-m", "file %s by alice (unclaimed)" % key).stdout.strip()
    sh("git", "-C", repo, "push", "-q", "origin", "%s:refs/heads/claim/%s" % (c, key))


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """shop/pay on GitLab, made once for the module (each test reads it with a new Deliveries): PAY-1 alice asks for
    her requirements' approval; PAY-2 alice handed over, the runner parked it on a question to product (carol), its
    pipeline running; PAY-3 and PAY-4 merged 2 and 1 days ago, the staging VERSION at PAY-3's merge; PAY-5 merged 30
    days ago; PAY-6 filed for anyone; PAY-7 bob just started."""
    bk = mod("bk")
    tmp_path = tmp_path_factory.mktemp("relay")
    home = tmp_path / "home"
    (home / ".kiro" / "agents").mkdir(parents=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))
        for k in ("BRAIN_USER", "GITLAB_TOKEN", "BRAIN_GITLAB_TOKEN_FILE", "BRAIN_INTAKE_DIR", "BRAINKIT_HOME"):
            mp.delenv(k, raising=False)
        assert bk.load(str(BRAINKIT))["ok"]
        try:
            yield made(tmp_path, home)
        finally:
            bk.unload()


@pytest.fixture
def world(shared):
    """The module's world, read with a Deliveries of the test's own."""
    bk = mod("bk")
    gl = shared["gl"]
    yield dict(shared, deployed=dict(shared["deployed"]),
               d=mod("team.relay").Deliveries(bk.m("rest").GitLab(BASE, "bridge-tok", gl), BASE, lambda p: shared["kit"]))
    gl.down.clear()
    gl.pipes[(P, 5)][0]["status"] = "running"


def made(tmp_path, home):
    bk = mod("bk")
    repo = make_project(tmp_path, origin=True, kit=KIT)
    bare = tmp_path / "pay-origin.git"
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    gl = RepoGitLab()
    gl.project(P, 7, bare)
    places = home / "workplace" / "brain-app" / "pay"
    now = time.time()

    wt1 = start_in_worktree(repo, places / "refund", "refund", "退款")
    request_requirements(wt1, intent_of(wt1, "PAY-1"))
    commit(wt1, {}, "requirements asked for")

    wt2 = start_in_worktree(repo, places / "notify", "notify", "退款通知")
    state = wt2 / "aidlc-docs" / intent_of(wt2, "PAY-2") / "aidlc-state.md"
    state.write_text(filled_notes(state.read_text(encoding="utf-8"), "Status: Construction step 2 of 4"), encoding="utf-8")
    commit(wt2, {}, "notes")
    brain(wt2, "handoff", "--no-push")
    park = {"schema": 1, "status": "waiting", "kind": "clarify", "ask": "product", "question": "短信要不要带金额？",
            "options": [{"id": "A", "text": "要"}, {"id": "B", "text": "不要"}], "recommend": "A"}
    parked = commit(wt2, {"runs/PAY-2/park.json": park}, "park", bot=True)

    merges = {}
    for key, slug, title in (("PAY-3", "receipt", "收据"), ("PAY-4", "invoice", "发票"), ("PAY-5", "old", "旧的")):
        wt = start_in_worktree(repo, places / slug, slug, title)
        commit(wt, {"src/%s.py" % slug: "x = 1\n"}, slug)
        sh("git", "-C", repo, "merge", "-q", "--no-ff", "-m", "merge " + key, "ai/alice/%s-%s" % (key, slug))
        merges[key] = sh("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    publish(repo)
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    unowned_claim(repo, "PAY-6", "对账单导出")
    bob = person(tmp_path, bare, "bob")
    brain(bob, "start", "report", "--title", "报表")
    sh("git", "-C", bob, "push", "-q", "origin", "refs/heads/claim/PAY-7", "refs/heads/ai/bob/PAY-7-report")

    gl.mr(P, 5, "ai/alice/PAY-2-notify", labels=["ai-running"], updated=now - 3600)
    raw_park = (wt2 / "runs" / "PAY-2" / "park.json").read_bytes()
    gl.note(P, 5, "brain-bridge", bk.m("bridge.decisions").question_text(
        "D-1", "RUN-PAY-2-1", park, ["carol"], "clarify", parked, hashlib.sha256(raw_park).hexdigest()))
    gl.pipeline(P, 5, "running")
    for iid, key, slug, ago in ((8, "PAY-3", "receipt", 2), (9, "PAY-4", "invoice", 1), (4, "PAY-5", "old", 30)):
        branch = "ai/alice/%s-%s" % (key, slug)
        gl.mr(P, iid, branch, state="merged", updated=now - ago * DAY, sha=gl.head(P, branch),
              merge_commit_sha=merges[key])
        gl.pipeline(P, iid, "success")
    return {"gl": gl, "kit": bk.m("config").load(str(repo)), "now": now, "bob": bob, "merges": merges,
            "deployed": {P: merges["PAY-3"]}}


def build(world):
    return world["d"].build([P], deployed=world["deployed"], roles=ROLES)


def by_key(rows):
    return {r["key"]: r for r in rows}


def test_every_open_or_lately_merged_requirement_is_a_row_in_the_overviews_words(world):
    rows, errors = build(world)
    assert errors == {}
    got = by_key(rows)
    assert list(got) == ["PAY-1", "PAY-2", "PAY-3", "PAY-4", "PAY-6", "PAY-7"]      # PAY-5 merged 30 days ago
    mr = lambda iid, state, ci: {"iid": iid, "url": "%s/%s/-/merge_requests/%d" % (BASE, P, iid), "state": state, "ci": ci}  # noqa: E731
    one = got["PAY-1"]
    assert {k: one[k] for k in ("project", "title", "owner", "stage", "state", "branch", "mr", "deployed")} == {
        "project": P, "title": "退款", "owner": "alice", "stage": "需求", "state": "local",
        "branch": "ai/alice/PAY-1-refund", "mr": None, "deployed": None}
    assert {k: one["waits"][k] for k in ("who", "do", "text", "on")} == {
        "who": "people", "do": "approve", "text": "等 alice 批准需求：读完再在会话里自己打那一行", "on": ["alice"]}
    two = got["PAY-2"]
    assert (two["stage"], two["state"], two["mr"], two["deployed"]) == ("交给运行机", "parked", mr(5, "opened", "running"), None)
    assert {k: two["waits"][k] for k in ("who", "do", "text", "on")} == {
        "who": "product", "do": "answer", "text": "等产品在 MR 上回答 D-1（澄清）", "on": ["carol"]}
    for key, iid, deployed, ago in (("PAY-3", 8, True, 2), ("PAY-4", 9, False, 1)):
        r = got[key]
        merged_at = r["updated_at"]
        assert abs(merged_at - (world["now"] - ago * DAY)) < 2
        assert (r["stage"], r["state"], r["owner"], r["mr"], r["deployed"]) == ("已合并", "merged", "alice", mr(iid, "merged", "success"), deployed)
        end = time.strftime("%m-%d", time.localtime(merged_at + 14 * DAY))
        assert (r["waits"]["who"], r["waits"]["do"], r["waits"]["text"], r["waits"]["on"]) == (
            "nobody", "watch", "已合并，在观察合并后的 14 天（到 %s）" % end, [])
    free = got["PAY-6"]
    assert (free["title"], free["owner"], free["stage"], free["state"], free["branch"]) == ("对账单导出", None, "构建", "unclaimed", None)
    assert (free["waits"]["do"], free["waits"]["on"]) == ("start", [])
    bob = got["PAY-7"]
    assert (bob["owner"], bob["stage"], bob["state"], bob["waits"]["text"], bob["waits"]["on"]) == (
        "bob", "需求", "local", "bob 在自己的机器上做", [])
    snapshot = mod("snapshot")
    assert all(snapshot._value(snapshot.DELIVERY, r, "row") == r for r in rows)


def test_a_branchs_records_are_read_once_per_head_and_a_running_pipeline_until_it_is_over(world):
    gl = world["gl"]
    build(world)
    first = len(gl.calls)
    assert gl.asked("raw") and gl.asked("tree") and gl.asked("notes") and gl.asked("merge_base")
    again, _ = build(world)
    for what in ("raw", "tree", "commits", "notes", "merge_base"):
        assert gl.asked(what, since=first) == [], what
    assert len(gl.asked("pipelines", since=first)) == 1               # PAY-2's, still running; the merged ones are over
    # bob pushes a commit: only his new head is read
    sh("git", "-C", world["bob"], "commit", "-q", "--allow-empty", "-m", "progress")
    sh("git", "-C", world["bob"], "push", "-q", "origin", "HEAD")
    head = sh("git", "-C", world["bob"], "rev-parse", "HEAD").stdout.strip()
    second = len(gl.calls)
    third, _ = build(world)
    raws = gl.asked("raw", since=second)
    assert raws and {c[2]["ref"] for c in raws} == {head}
    assert by_key(third)["PAY-7"]["updated_at"] >= by_key(again)["PAY-7"]["updated_at"]
    # the pipeline is over: read once more, then kept
    gl.pipes[(P, 5)][0]["status"] = "success"
    fourth = len(gl.calls)
    rows, _ = build(world)
    assert by_key(rows)["PAY-2"]["mr"]["ci"] == "success" and len(gl.asked("pipelines", since=fourth)) == 1
    fifth = len(gl.calls)
    build(world)
    assert gl.asked("pipelines", since=fifth) == []
    # a new note moves the MR: its notes are read again
    gl.note(P, 5, "carol", "answer D-1 A")
    sixth = len(gl.calls)
    build(world)
    assert len(gl.asked("notes", since=sixth)) == 1


def test_a_merge_older_than_14_days_is_no_row_however_lately_its_mr_moved(world):
    gl = world["gl"]
    old = gl.mrs[(P, 4)]
    was = old["updated_at"]
    old["updated_at"] = gl.mrs[(P, 5)]["updated_at"]          # someone commented on PAY-5's merged MR today
    try:
        rows, _ = build(world)
    finally:
        old["updated_at"] = was
    assert any(c[2].get("state") == "merged" for c in gl.asked("mrs")) and "PAY-5" not in by_key(rows)


def test_a_project_gitlab_does_not_answer_keeps_its_last_rows_and_says_why(world):
    rows, _ = build(world)
    world["gl"].down.add(P)
    kept, errors = build(world)
    assert kept == rows and "502" in errors[P]
    world["gl"].down.clear()
    back, errors = build(world)
    assert errors == {} and back == rows


def test_without_a_staging_version_nothing_is_said_deployed(world):
    world["deployed"] = {}
    rows, _ = build(world)
    assert {r["key"]: r["deployed"] for r in rows if r["state"] == "merged"} == {"PAY-3": None, "PAY-4": None}
