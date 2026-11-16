"""A rejection's words, typed in the session, are on no page: every read route over a real Lean worktree."""

import json
import time

from appkit import mod, sh
from brainfix import lean, make_project, make_service, needs_brainkit, request_requirements, run, start_in_worktree
from test_outputs_turns import hook

outputs = mod("outputs")
AppError = mod("httpkit").AppError

MARK = "TYPEDWORDS老板张三"


async def every_read(svc, rid, key):
    got = {"overview": await svc.overview("all"), "requirement": await svc.requirement(rid, key),
           "board": await svc.board(rid, key), "todo": await svc.todo(), "status": await svc.status()}
    for n in got["board"]["nodes"]:
        rounds = [None] + [str(r["n"]) for r in n.get("rounds") or []]
        for rn in rounds:
            try:
                listed = await svc.outputs(rid, key, outputs.node_query(n["id"], rn))
            except AppError as e:
                listed = {"error": [e.status, e.code]}
            got["outputs %s %s" % (n["id"], rn)] = listed
            for f in listed.get("files") or []:
                q = {"ref": f["ref"], "path": f["path"], **({"pin": f["pin"]} if f.get("pin") else {})}
                try:
                    got["file %s %s" % (f["path"], f["ref"])] = await svc.outputs_file(rid, key, outputs.file_query(q))
                except AppError as e:
                    got["file %s %s" % (f["path"], f["ref"])] = [e.status, e.code]
    return got


@needs_brainkit
def test_a_rejections_words_are_on_no_page(tmp_path, home):
    repo = make_project(tmp_path)
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    intent = json.loads((wt / "runs" / "PAY-1" / "run.json").read_text())["intent"]
    request_requirements(wt, intent)
    hook(wt, "驳回 REQUIREMENTS %s 金额按分算" % MARK)
    lean(wt, "audit", "pack")
    assert MARK in (wt / "aidlc-docs" / intent / "aidlc-state.md").read_text(encoding="utf-8")
    (wt / "aidlc-docs" / intent / "inception" / "requirements" / "requirements.md").write_text(
        "# Requirements\n\nSize: small\n\n- AC-1 退款，金额按分算\n", encoding="utf-8")
    lean(wt, "request", "REQUIREMENTS")
    sh("git", "-C", wt, "add", "-A")
    later = "@%d +0000" % (time.time() + 2)      # inside round 2: its other changes hold aidlc-state.md and audit.md
    sh("git", "-C", wt, "commit", "-q", "-m", "requirements: revise after rejection",
       env={"GIT_AUTHOR_DATE": later, "GIT_COMMITTER_DATE": later})
    time.sleep(3)
    hook(wt, "reject REQUIREMENTS %s 还是不对" % MARK)      # round 2 sent back too: the gate, the banner, 待办
    svc = make_service(tmp_path, [repo])

    async def go():
        await svc.start()
        try:
            return await every_read(svc, "r1", "PAY-1")
        finally:
            await svc.stop()

    got = run(go())
    text = json.dumps(got, ensure_ascii=False)
    req = next(n for n in got["board"]["nodes"] if n["id"] == "requirements")
    assert [r["outcome"]["kind"] for r in req["rounds"]] == ["sent_back", "sent_back"], req["rounds"]
    assert any(e["kind"] == "reject" for e in got["board"]["events"]), got["board"]["events"]
    assert {a["kind"]: a["state"] for a in got["requirement"]["lean"]["approvals"]}["REQUIREMENTS"] == "rejected"
    assert MARK not in text, [k for k, v in got.items() if MARK in json.dumps(v, ensure_ascii=False)]
