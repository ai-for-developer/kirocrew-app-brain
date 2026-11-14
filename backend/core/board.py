"""需求看板: one requirement as the reference team board shows a run: AI-DLC phase bands with a node per stage Lean
ran (code generation per unit), each node's rounds (第 N 轮) and gates, who did each round (this machine's session, a
run on the team runner, a small requirement of a split), each hand-over's steps, the run branch's commits on a
conveyor, a check wall, the events placed on the graph, and 验收 (the sample's 签字单).

Built from what is already recorded, never guessed: the Lean ledger (aidlc-docs/audit, read by repo.py), the run trace
(brainkit.trace: commits, the hand-over, the bridge's ledger and vouchers when this machine has them, park.json's
history, the merge and the 14 days after it), the MR (person mode: mrinfo.py), the bridge ledger (team mode), this
app's own events and the Kiro Crew sessions it opened. stages.py cuts the rounds and writes each node's status (and
its history, so the page can play the requirement again: 回放). runs.py says who did them and what each run does now.
This module places every event on the graph and reads the check wall and 验收 off it.
Pure: no I/O here (the service reads and passes everything in).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

from . import lean_view, problems, runs, stages
from . import outputs as outputs_mod
from .deliveries import ASK_NAME, ASK_WHO, CAT_LABEL, CAT_ORDER, KIND_LABEL, category, owns

RUNNER_NO_DESC = "运行机还没开始做：它做完提交以后才有变更说明"
# Lean's approval kinds the board says, in words (CR-<team>-<n> is left out)
GATE_KIND_LABEL = {"REQUIREMENTS": "需求", "INCEPTION": "需求和设计", "DESIGN": "设计", "PLAN": "代码生成计划",
                   "RELEASE": "发布"}
FINAL = ("merged", "skipped", "failed")
MAX_EVENTS = 400
MAX_BELT = 80
TICKER_TEXT = 200   # characters of the agent's last line a ticker carries
NOT_NEEDED = "这次不需要"
KIT_LABEL = "kit 文件"   # a kit row's short name on the check wall, as the button 重装 kit 文件 says it
# brainkit.trace's `missing` sources (trace.py): the ones a repo done only on this machine, or a requirement not handed
# over yet, has nothing to read from are not missing
MISSING = (("bridge ledger", "runner", "桥的账本：这台机器上读不到，交接以后桥的核对和转的问题看不全"),
           ("vouchers", "runner", "签名凭证：这台机器上读不到，看不到签名服务签了什么"),
           ("pushes", "runner", "推送记录：git 不记推送，交接以后人的提交当作推送"),
           ("CI pipelines", "remote", "CI 流水线：在 GitLab 上，这里读不到"),
           ("credits", None, "花费：KiroCrew 还没报 credits"),
           ("Lean audit", None, "Lean 的记录：这个需求的分支上没有，阶段和审批都看不到"),
           ("run branch", None, "需求的分支：本机、远端、默认分支上都没找到"))


def _epoch(x: Any) -> float | None:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return lean_view.epoch(x) if isinstance(x, str) else None


def _clip(s: Any, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _stage_label(d: dict[str, Any]) -> str:
    label = stages.label(d.get("stage"))
    return "%s（%s）" % (label, d["unit"]) if d.get("unit") else label


def _who(you: str) -> str:
    """The requirement's person as Waits.who: you, or someone else (people)."""
    return "you" if you == "你" else "people"


def _asked(you: str, ask: Any = None, kind: Any = None, lean: Any = None) -> tuple[str, str]:
    """(Waits.who, the name the text says) of what the runner asks on the MR, by the row's rule (deliveries.classify):
    an acceptance or the release approval is the product's; else park.json's `ask`, the owner being 你 only to the
    owner (*you*: the requirement's person as the reader calls them)."""
    if kind == "acceptance" or str(lean or "").split(" ")[0] == "RELEASE":
        return "product", ASK_NAME["product"]
    who = ASK_WHO.get(str(ask or ""), "people")
    return (_who(you), you) if who == "you" else (who, ASK_NAME.get(who, "人"))


def _park_for(parks: list[dict[str, Any]], at: float | None, *, kind: Any = None,
              lean: tuple[str, ...] = ()) -> dict[str, Any]:
    """The park.json question (the trace's, waiting) a question on the MR or a Lean request on the runner was asked
    from: of that kind, or asking one of the Lean approvals *lean*; the nearest in time."""
    hits = [q for q in parks if (not kind or q.get("kind") == kind)
            and (not lean or str(q.get("lean_request") or "").split(" ")[0] in lean)]
    return min(hits, key=lambda q: abs((_epoch(q.get("at")) or 0) - (at or 0)), default={})


def _you(me: str | None, owner: str | None) -> str:
    """Who the requirement's person is to the one reading the board: 你, or their name (UI问题清单 BOARD-11)."""
    return "你" if owns(me, owner) else " %s " % owner


def _first(*xs: Any) -> float | None:
    """The first of *xs* that is a moment."""
    for x in xs:
        t = _epoch(x)
        if t is not None:
            return t
    return None


def decided_by(humans: list[dict[str, Any]], kind: str, at: float | None, local: bool, you: str) -> str:
    """Who approved or sent back: on this machine's run the person typed the line in their session (*you*); on the
    runner's, whoever answered on the MR (their user name when brain-kit read it, never a pseudonym)."""
    if local:
        return you
    h = min((h for h in humans if h.get("what") in (kind, "acceptance") and _epoch(h.get("at")) is not None
             and at is not None and abs(_epoch(h.get("at")) - at) < 3600), default=None,
            key=lambda h: abs(_epoch(h.get("at")) - at))
    who = str((h or {}).get("who") or "")
    if re.match(r"^[a-z0-9][a-z0-9._-]*$", who):
        return " %s " % who
    return "产品在 MR 上" if kind == "RELEASE" else "有人在 MR 上"   # the acceptance is asked of the product


def missing_sources(raw: list[Any], *, local_only: bool, handed: bool) -> list[str]:
    """brainkit.trace's `missing`, in plain Chinese, without the sources this requirement has no use for: a repo done
    only here has no runner, bridge, signer or GitLab CI; before the hand-over the runner's half has nothing yet."""
    out = []
    for x in raw:
        s = str(x)
        hit = next((m for m in MISSING if s.startswith(m[0])), None)
        if hit:
            if (hit[1] == "runner" and (local_only or not handed)) or (hit[1] == "remote" and local_only):
                continue
            text = hit[2]
        elif "decisions.md" in s:
            text = "决定记录：AI 还没写下决定"
        elif s.endswith("(ignored)"):
            text = "开工记录里有一项格式不对，没用上"
        else:
            text = "还有一个来源没读到"
        if text not in out:
            out.append(text)
    return out


def events_of(f: dict[str, Any], t: dict[str, Any] | None, *, mr: dict[str, Any] | None, app_events: list[dict[str, Any]],
              team_decisions: list[dict[str, Any]], subjects: dict[str, str], reflect_refs: list[tuple[str, float, float | None]],
              default_branch: str, you: str = "你") -> list[dict[str, Any]]:
    """Every recorded event of the requirement, oldest first, in the board's words (BoardEvent without seq and without
    its place on the graph, which stage_graph gives it). A question or an answer keeps the side it is on (_side: this
    machine's run, or the runner's), an answer the bridge delivered says so (_relay). *you*: the requirement's person
    as the reader calls them (你, or their name)."""
    run = f.get("run") or {}
    handoff = run.get("handoff") if isinstance(run.get("handoff"), dict) else None
    hand_at = _epoch((handoff or {}).get("requested_at"))
    out: list[dict[str, Any]] = []
    order = [0]

    def add(at: float | None, kind: str, text: str, *, detail: bool = False, src: str | None = None, **data: Any) -> None:
        if at is None:
            return
        order[0] += 1
        out.append({"at": at, "kind": kind, "text": _clip(text, 200), "detail": detail, "src": src, "_n": order[0], **data})

    def side(at: float | None) -> str:
        return "runner" if hand_at is not None and at is not None and at > hand_at else "local"

    parks = [q for q in ((t or {}).get("actions") or {}).get("questions") or []
             if isinstance(q, dict) and q.get("status") == "waiting"]
    claim = f.get("claim") or {}
    add(_epoch(claim.get("created_at")), "claim", "认领 %s：%s" % (f["key"], _clip(claim.get("title"), 40)),
        src="认领记录 claim/%s" % f["key"])
    add(_epoch(run.get("created_at")), "start", "开工：在它自己的工作目录里", src="开工记录 runs/%s/run.json" % f["key"])
    humans = [h for h in (t or {}).get("humans") or [] if isinstance(h, dict)]

    def approver(kind: str, at: float | None, s: str) -> str:
        return decided_by(humans, kind, at, s == "local", you)

    # Lean's ledger
    evs = [e for e in f.get("events") or [] if isinstance(e, dict)]
    sessions_seen: set[str] = set()
    for i, e in enumerate(evs):
        ev, d, at = e.get("event"), e.get("data") if isinstance(e.get("data"), dict) else {}, e.get("_at")
        s = side(at)
        if ev == "HUMAN_TURN" and s == "local":
            # the person typed in the session: the session's first prompt is its task, a prompt Lean read as an
            # approval line is the approval itself (the next event); any other is the person stepping in (BOARD-16)
            first = str(d.get("session") or "") not in sessions_seen
            sessions_seen.add(str(d.get("session") or ""))
            nxt = evs[i + 1] if i + 1 < len(evs) else {}
            line = nxt.get("event") in ("APPROVED", "REJECTED", "APPROVAL_REFUSED") and \
                (_epoch(nxt.get("_at")) or 0) - (at or 0) < 5
            if not first and not line:
                add(at, "answer", "%s允许这一轮改受保护的文件" % you if d.get("allow_protected") else "%s在会话里回话了" % you,
                    _side="local", _turn=True)
            continue
        if ev == "STAGE_STARTED":
            add(at, "stage", "开始「%s」" % _stage_label(d), detail=True, _rec=e)
        elif ev == "STAGE_COMPLETED":
            add(at, "stage", "做完「%s」" % _stage_label(d), detail=True, _rec=e)
        elif ev == "STAGE_SKIPPED":
            add(at, "stage", "跳过「%s」%s" % (_stage_label(d), "：%s" % _clip(d.get("reason"), 60) if d.get("reason") else ""),
                detail=True, _rec=e)
        elif ev == "PHASE_SKIPPED":
            why = lean_view.say_note(d.get("reason"))
            add(at, "stage", "跳过「%s」整段%s" % (lean_view.phase_name(d.get("phase")), "：%s" % _clip(why, 60) if why else ""),
                detail=True, _rec=e)
        elif ev in ("APPROVAL_REQUESTED", "APPROVED", "REJECTED", "APPROVAL_REFUSED"):
            kind = str(d.get("kind") or "")
            name = GATE_KIND_LABEL.get(kind)
            if not name:
                continue
            if ev == "APPROVAL_REQUESTED":
                # after the hand-over the bridge asks it on the MR (an INCEPTION as its two approvals), of whom the
                # park.json written for it asks
                asker = you if s == "local" else _asked(you, _park_for(
                    parks, at, lean=("REQUIREMENTS", "DESIGN") if kind == "INCEPTION" else (kind,)).get("ask"), lean=kind)[1]
                add(at, "request", "请%s批准%s" % (you, name) if s == "local" else "请%s在 MR 上批准%s" % (asker, name), _rec=e)
            elif ev == "APPROVED":
                add(at, "approve", "%s批准了%s" % (approver(kind, at, s), name), _rec=e)
            elif ev == "REJECTED":
                add(at, "reject", "%s退回了%s" % (approver(kind, at, s), name), _rec=e)
            else:
                why = lean_view.refused_why(d.get("check"))
                add(at, "refused", "%s（%s）%s" % (lean_view.REFUSED, name, "：%s" % why if why else ""), _rec=e)
        elif ev == "BLOCKED":
            what = "写 %s" % _clip(d.get("path"), 50) if d.get("path") else "一次%s" % lean_view.BLOCK_TOOL.get(str(d.get("tool") or ""), "工具调用")
            add(at, "blocked", "Lean 拦下了%s：%s" % (what, lean_view.block_rule(d.get("gate"))), detail=True, _rec=e)
        elif ev == "VERIFY":
            res = [r for r in d.get("results") or [] if isinstance(r, dict)] if isinstance(d.get("results"), list) else []
            bad = [stages.check_name(r.get("cmd")) for r in res if r.get("exit") not in (0, None)]
            ok = bool(d.get("ok"))
            add(at, "verify", ("自动检查通过（%d 项）" % len(res) if res else "自动检查通过") if ok else
                "自动检查没过：%s" % ("、".join(bad[:3]) or "有检查失败"), ok=ok, _rec=e)
    # the trace: commits, the hand-over and the bridge, questions, the merge
    commits = ((t or {}).get("actions") or {}).get("commits") or []
    hand_sha = None
    hands = ((t or {}).get("actions") or {}).get("handovers") or []
    for h in hands:
        if h.get("kind") == "requested" and h.get("commit"):
            hand_sha = h["commit"]
    seen_hand = False
    for c in commits:
        at, sha = _epoch(c.get("at")), str(c.get("sha") or "")
        subj = subjects.get(sha, "")
        kept = {"sha": sha, "files": c.get("files") or 0, "records": bool(c.get("records_only")), "_commit": c}
        if sha and sha == hand_sha:
            seen_hand = True
            add(at, "handover", "交给运行机（提交 %s）" % sha[:8], by="person", subject=subj or "交接", **kept)
        elif c.get("by") == "bot":
            add(at, "commit", "运行机提交 %s%s" % (sha[:8], "：%s" % _clip(subj, 60) if subj else ""), by="bot",
                subject=subj, **kept)
        elif c.get("after_handover"):
            add(at, "commit", "交接以后有人推了%s（%s）" % ("记录" if c.get("records_only") else "代码", sha[:8]),
                by="person", subject=subj, **kept)
        else:
            add(at, "commit", "提交 %s%s" % (sha[:8], "：%s" % _clip(subj, 60) if subj else ""), by="person", subject=subj,
                **kept)
    if hand_at is not None and not seen_hand:
        add(hand_at, "handover", "交给运行机", by="person", sha=(handoff or {}).get("local_head") or "")
    for h in hands:
        at, kind = _epoch(h.get("at")), str(h.get("kind") or "")
        if kind == "bridge accepted":
            add(at, "accepted", "桥接手了%s" % ("（MR !%s）" % h["mr_iid"] if h.get("mr_iid") else ""))
        elif kind == "bridge rejected":
            why = problems.reasons_text(str(h.get("reason") or "").split("; "), 90)[0]   # the bridge's English, said as the MR's is
            add(at, "rejected", "桥拒绝接手%s" % ("：%s" % why if why else ""))
        elif kind == "voucher":
            rid = h.get("run_id")
            add(at, "signed", "签名服务签了接手合同%s" % ("（%s）" % run_words([rid]) if rid else ""),
                src="运行号 %s" % rid if rid else None)
    for q in ((t or {}).get("actions") or {}).get("questions") or []:
        at, st = _epoch(q.get("at")), q.get("status")
        s = side(at)
        label = KIND_LABEL.get(str(q.get("kind")), q.get("kind") or "问题")
        if st == "waiting":
            asker = you if s == "local" else _asked(you, q.get("ask"), q.get("kind"), q.get("lean_request"))[1]
            add(at, "question", "停下问%s：%s" % (asker.rstrip(), label), _side=s)
        elif st == "answered":
            add(at, "answer", "%s的问题回答了，接着做" % label, _side=s)

    def on_mr(did: Any, kind: Any, lean: Any, at: float | None, ask: Any = None) -> None:
        """A question the bridge put on the MR for the runner's run: of whom its park.json asks."""
        asker = _asked(you, ask or _park_for(parks, at, kind=kind).get("ask"), kind, lean)[1]
        add(at, "question", "%s 在 MR 上问%s：%s" % (did, asker.rstrip(), KIND_LABEL.get(str(kind), kind or "问题")),
            _side="runner")

    decisions = [d for d in (t or {}).get("decisions") or [] if d.get("source") == "bridge"] or [
        dict(d, source="bridge", asked_at=d.get("created"), at=d.get("updated") if d.get("answer") else None, choice=d.get("answer"))
        for d in team_decisions]
    for d in decisions:
        on_mr(d.get("id"), d.get("kind"), d.get("lean_request"), _epoch(d.get("asked_at")), d.get("ask"))
        if d.get("choice"):
            add(_epoch(d.get("at")), "answer", "%s 回答了：%s" % (d.get("id"), _clip(d.get("choice"), 20)), _side="runner")
            if d.get("state") == "delivered":
                add((_epoch(d.get("at")) or 0) + 1 if _epoch(d.get("at")) else None, "answer",
                    "桥把 %s 的回答签好送回运行机" % d.get("id"), _side="runner", _relay=True)
    if not decisions:   # person mode without the ledger: the MR's questions, at the time they were posted
        for q in (mr or {}).get("questions") or []:
            on_mr(q.get("id"), q.get("kind"), q.get("lean_request"), _epoch(q.get("at")))
    m = ((t or {}).get("outcome") or {}).get("merged_at") or {}
    if m.get("value"):
        how = {"merge commit": "合并提交", "fast-forward": "快进"}.get(str(m.get("how") or ""), "")
        add(_epoch(m.get("value")), "merge", "合并进 %s%s" % (default_branch, "（%s）" % how if how else ""),
            sha=str(m.get("commit") or ""))
    elif f.get("merged") and not m.get("value"):
        add(_epoch(f.get("tip_at")) or None, "merge", "已在 %s 上" % default_branch)
    # this app's own record
    for e in app_events:
        at, label = _epoch(e.get("at")), str(e.get("label") or "")
        if "会话" in label:
            add(at, "session", label)
        elif "回答" in label:
            add(at, "answer", label, _side="local" if "澄清" in label else "runner")
        elif label.startswith("回写 Brain"):
            add(at, "reflect", "回写 Brain：%s" % label.split("：")[-1], ref=e.get("detail") or None)
        elif "开工" in label:
            continue   # run.created_at says it already
        else:
            add(at, "app", label)
    applied = [e for e in out if e["kind"] == "reflect"]
    for ref, at, merged in reflect_refs:   # the branch this app's write-back made is the same write-back: one event (BOARD-32)
        hit = next((e for e in applied if e.get("ref") == ref or (not e.get("ref") and abs(e["at"] - at) < 120)), None)
        if hit:
            hit["text"] = _clip("%s（回写分支 %s）" % (hit["text"], ref), 200)
            hit["ref"] = ref
        else:
            add(at, "reflect", "写好了回写分支 %s" % ref, ref=ref)
        if merged is not None and merged > at + 60:     # merged later (a merge commit): that moment is an event too
            add(merged, "reflect", "回写分支 %s 已合并" % ref, ref=ref)
    out.sort(key=lambda x: (x["at"], x["_n"]))
    return out


# ---------------------------------------------------------------- the stage graph (ui/src/types.ts StageNode, Run, Relay)

# Where a board event sits on the stage graph, by its kind: which node, whose run (types.ts BoardEvent node / run).
#   record     the Lean record it came from says both (stages.node_of; its ledger file's run)
#   record_at  its record's run, at the node that run was at then (a blocked tool call)
#   commit     the commit's run, at the node whose round holds it
#   side_at    the run that asked or was answered (this machine's before the hand-over, else the runner's), at its node
#   local_at   this machine's run, at its node then          first   this machine's run, at the first node of the way
#   reflect    the write-back                                local   this machine's run, no node (the hand-over)
#   relay      the runner run that hand-over went to, no node   none    nobody's run, no node
PLACE = {
    "stage": "record", "request": "record", "approve": "record", "reject": "record", "refused": "record",
    "verify": "record", "blocked": "record_at", "commit": "commit", "question": "side_at", "answer": "side_at",
    "session": "local_at", "start": "first", "reflect": "reflect", "handover": "local", "accepted": "relay",
    "signed": "relay", "rejected": "none", "claim": "none", "merge": "none", "app": "none",
}
# Where it travels on the graph (types.ts BoardEvent from / to), by its kind: NODE is the node PLACE gives it, RUN_AT
# the node its run was at then; the other ends are the anchors person (the owner box, with the runs' roster), branch
# (the run branch rail), checks (the check ring) and relay (the hand-over pill on the rail). An end with no node is the
# event's other end (a blip there), and an answer the bridge delivered comes from the relay.
NODE, RUN_AT = "node", "run_at"
ANCHORS: Mapping[str, Tuple[str, str]] = {
    "claim": ("person", "person"), "app": ("person", "person"), "start": ("person", NODE), "stage": (NODE, NODE),
    "request": (NODE, "person"), "approve": ("person", NODE), "reject": ("person", NODE), "refused": (NODE, NODE),
    "blocked": (NODE, NODE), "commit": (NODE, "branch"), "handover": (RUN_AT, "relay"), "accepted": ("relay", "relay"),
    "signed": ("relay", "relay"), "rejected": ("relay", "person"), "question": (NODE, "person"),
    "answer": ("person", NODE), "verify": ("branch", "checks"), "merge": ("branch", "branch"),
    "reflect": ("person", NODE), "session": ("person", NODE),
}
MARKS = {"request": "request", "approve": "approved", "reject": "rejected", "refused": "refused", "blocked": "blocked",
         "commit": "commit", "question": "question", "answer": "answer", "reflect": "reflect", "session": "session"}
# a question's kind on the MR, by the Lean approval it asks (an acceptance asks the release's)
ASKS = {"RELEASE": "acceptance", "DESIGN": "design", "INCEPTION": "design", "PLAN": "plan", "REQUIREMENTS": "requirements"}


def _branch_commits(t: dict[str, Any] | None, subjects: dict[str, str],
                    changes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The run branch's commits as the stage graph reads them, oldest first: when, by whom, records only or not, after
    the hand-over or not, the subject, the files changed."""
    out = []
    for c in ((t or {}).get("actions") or {}).get("commits") or []:
        at, sha = _epoch(c.get("at")), str(c.get("sha") or "")
        if at is not None and sha:
            out.append({"sha": sha, "at": at, "by": c.get("by"), "records_only": bool(c.get("records_only")),
                        "after_handover": bool(c.get("after_handover")), "subject": subjects.get(sha) or "",
                        "paths": [x["path"] for x in changes.get(sha) or []]})
    return sorted(out, key=lambda c: c["at"])


def _mr_questions(t: dict[str, Any] | None, team_decisions: list[dict[str, Any]],
                  mr: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The questions the bridge put on the MR (D-n): the bridge ledger's, as the trace or team mode read it, else the
    MR's own."""
    ds = [d for d in (t or {}).get("decisions") or [] if d.get("source") == "bridge"] or [
        dict(d, asked_at=d.get("created"), at=d.get("updated") if d.get("answer") else None, choice=d.get("answer"))
        for d in team_decisions]
    if ds:
        out = [{"id": d.get("id"), "kind": d.get("kind"), "lean": str(d.get("lean_request") or ""),
                "asked": _epoch(d.get("asked_at")), "answered": _epoch(d.get("at")) if d.get("choice") else None}
               for d in ds]
    else:
        out = [{"id": q.get("id"), "kind": q.get("kind"), "lean": str(q.get("lean_request") or ""),
                "asked": _epoch(q.get("at")), "answered": None if q.get("state") == "open" else _epoch(q.get("at"))}
               for q in (mr or {}).get("questions") or []]
    return [q for q in out if q["id"]]


def _sub(stage: stages.StageDef, unit: str | None) -> str:
    """What a node hands in, said short: requirements.md · api 的代码生成计划 · lean verify · review.md · release.md."""
    if stage.id == "code-generation":
        return "%s 的代码生成计划" % unit
    if stage.id == "build-and-test":
        return "lean verify"
    if stage.id == "reflect":
        return "回写分支"
    if unit:
        return "%s 的%s" % (unit, stage.label)
    return (stage.doc or "").rsplit("/", 1)[-1]


def _round_wire(r: stages.Round) -> dict[str, Any]:
    e, w, k = r.end, r.why, r.end.outcome
    O = stages.Outcome
    return {"n": r.n, "run": r.run, "started": r.started, "ended": r.ended,
            "outcome": {"kind": k.value, "at": e.at if k in (O.APPROVED, O.SENT_BACK, O.REFUSED, O.SUPERSEDED) else None,
                        "gate": e.gate if k in (O.APPROVED, O.SENT_BACK, O.REFUSED) else None,
                        "by": (e.by or "").strip() or None if k in (O.APPROVED, O.SENT_BACK) else None,
                        "decision": e.decision if k in (O.APPROVED, O.SENT_BACK) else None,
                        "text": (lean_view.refused_why(e.check) or lean_view.REFUSED) if k is O.REFUSED else
                        _clip(lean_view.say_note(e.reason), 160) or None if k is O.SKIPPED and e.reason else None,
                        "sha": e.sha if k is O.SUPERSEDED else None,
                        "subject": _clip(e.subject, 80) or None if k is O.SUPERSEDED and e.subject else None,
                        "checks": [{"name": stages.check_name(c.cmd), "ok": c.ok, "secs": c.secs} for c in e.checks]},
            "why": {"kind": w.kind, "round": w.round, "pipeline": w.pipeline, "sha": w.sha,
                    "subject": _clip(w.subject, 80) or None if w.subject else None, "at": w.at},
            "note": (lean_view.say_note(r.note) or "").strip()[:2000] or None,
            "outputs": [{"path": path, "sha8": h[:8]} for path, h in r.outputs if h],
            "commits": [c[:12] for c in r.commits]}


def _gate_wire(g: stages.GateView) -> dict[str, Any]:
    return {"kind": g.kind, "state": g.state, "round": g.round, "at": g.at, "who": g.who,
            "by": (g.by or "").strip() or None, "line": g.line,
            "mr": {"decision": g.mr[0], "url": g.mr[1]} if g.mr is not None else None, "text": g.text, "docs": list(g.docs)}


def _node_wire(n: stages.Node, marks: list[dict[str, Any]]) -> dict[str, Any]:
    st = n.status or n.waits
    return {"id": n.id, "stage": n.stage.id, "n": n.stage.n, "unit": n.unit, "phase": n.stage.phase,
            "title": n.stage.label, "sub": _sub(n.stage, n.unit), "icon": n.stage.icon, "status": st.status,
            "doing": _clip(st.doing, 80), "since": st.since, "history": n.history, "spans": n.spans, "marks": marks,
            "rounds": [_round_wire(r) for r in n.rounds], "gates": [_gate_wire(g) for g in n.gates],
            "run": n.rounds[-1].run if n.rounds else None,
            "child": {"req": n.child[0], "state": n.child[1]} if n.child is not None else None}


def ticker(tail: Any) -> dict[str, Any] | None:
    """The working box's ticker of a run whose session is here: its last call, and the last line the agent wrote, cut
    at a line end and its emails pseudonyms (outputs.shown_text, as the drawer's replies): a line longer than
    TICKER_TEXT is left out, not cut through. The route scrubs both (SCRUB_KEYS text, detail)."""
    if not isinstance(tail, dict):
        return None
    call = tail.get("call") if isinstance(tail.get("call"), dict) else None
    said = outputs_mod.shown_text(str(tail.get("said") or ""), TICKER_TEXT)[0].strip() or None
    if call is None and said is None:
        return None
    ats = [x for x in ((call or {}).get("at"), tail.get("said_at") if said else None) if isinstance(x, (int, float))]
    return {"at": max(ats) if ats else None, "tool": call["tool"] if call else None,
            "detail": call.get("detail") if call else None, "text": said}


def _run_wire(r: runs.RunView) -> dict[str, Any]:
    now = r.now or runs.RunNow("waiting", "", None)
    return {"key": r.key, "kind": r.kind, "label": r.label, "readable": r.readable, "n": r.n, "run_id": r.run_id,
            "req": r.req, "unit": r.unit, "now": {"status": now.status, "doing": _clip(now.doing, 80), "since": now.since,
                                                  "who": now.who, "node": now.node, "do": now.do},
            "credits": r.credits, "sessions": r.sessions, "ticker": ticker(r.tail) if r.readable == "here" else None}


def _over(f: dict[str, Any], row: dict[str, Any], merged_at: float | None, now: float) -> tuple[str, float] | None:
    """When the requirement's way ended, and how: closed; merged into the default branch (at the merge the events
    read, else as the row says); a small requirement merged into its big one's branch (its `brain handoff` there)."""
    st, waits = row.get("state"), row.get("waits") or {}
    closed = (f.get("claim") or {}).get("closed")
    if st == "closed" or closed:
        return "closed", _first(closed.get("at") if isinstance(closed, dict) else None, waits.get("since"), now)
    if merged_at is not None:
        return "merged", merged_at
    if st == "merged":
        return "merged", _first(waits.get("since"), f.get("merged_at"), f.get("tip_at"), now)
    if st == "merged_into_parent":
        return "merged", _first((run_of(f).get("mr") or {}).get("requested_at"), f.get("tip_at"), now)
    return None


def _quiet(evs: list[dict[str, Any]], records: list[dict[str, Any]], who: runs.Attribution,
           rs: list[runs.RunView]) -> dict[str, list[tuple[float, float | None]]]:
    """When each run's session sat waiting for its person: before each reply the person typed in this machine's
    session, since that run's last record (a Lean record, a commit) before it; and now, since its state says it waits
    on a person (a quiet session, a tool call, a question)."""
    seen = sorted(x.get("_at", x.get("at")) for x in records if who.run_of(x) == runs.LOCAL)
    out: dict[str, list[tuple[float, float | None]]] = {}
    for e in evs:
        last = next((x for x in reversed(seen) if x < e["at"]), None) if e.get("_turn") else None
        if last is not None:
            out.setdefault(runs.LOCAL, []).append((last, e["at"]))
    for r in rs:
        if r.now is not None and r.now.status == "awaiting_approval" and r.now.since is not None:
            out.setdefault(r.key, []).append((r.now.since, None))
    return out


def stage_graph(f: dict[str, Any], row: dict[str, Any], t: dict[str, Any] | None, evs: list[dict[str, Any]], *,
                mr: dict[str, Any] | None, sessions: list[dict[str, Any]], team_decisions: list[dict[str, Any]],
                subjects: dict[str, str], changes: dict[str, list[dict[str, Any]]],
                reflect_refs: list[tuple[str, float, float | None]], you: str, me: str | None, hand_at: float | None,
                merged_at: float | None, proposals: int, units: Any, now: float) -> dict[str, Any]:
    """The board's stage graph: phases, nodes, runs, relays, other_runs, and each event (*evs*, events_of's) placed
    on it (node, run, round, and where it travels: from, to), live. Rounds and gates are stages', who did them runs'.
    The words of a gate's decider (decided_by) and of whom a request waits on (the park.json ask, the MR question)
    are the board's."""
    run = run_of(f)
    lean = sorted((e for e in f.get("events") or [] if isinstance(e, dict) and isinstance(e.get("_at"), (int, float))),
                  key=lambda e: e["_at"])
    family = runs.family_of(row.get("family"), run)
    commits = _branch_commits(t, subjects, changes)
    waits = row.get("waits") or {}
    bounds = runs.boundaries(f, t)
    if not bounds and runner_blind(f, row):   # the team runner has the hand-over in its ledger, nothing else yet
        bounds = [_first(waits.get("since"), row.get("updated_at"), now)]
    hands = ((t or {}).get("actions") or {}).get("handovers") or []
    others = {str(o.get("run_id")) for o in (t or {}).get("other_runs") or [] if isinstance(o, dict)}
    ids = {str(x) for x in list((t or {}).get("run_ids") or []) + [h.get("run_id") for h in hands if isinstance(h, dict)]
           + list(row.get("run_ids") or []) if x} - others
    who = runs.attribute(lean, runs.file_roles(lean, family), bounds, sorted(ids), commits)
    humans = [h for h in (t or {}).get("humans") or [] if isinstance(h, dict)]
    owners = {c.key: c.owner for c in family.children} if family is not None else {}
    qs = _mr_questions(t, team_decisions, mr)
    parks = [q for q in ((t or {}).get("actions") or {}).get("questions") or []
             if isinstance(q, dict) and q.get("status") == "waiting"]

    def by(e: dict[str, Any]) -> str:
        r, d = who.run_of(e), e.get("data") or {}
        if r.startswith("child-"):
            return _you(me, owners.get(r[6:]))
        if r.startswith("parent-"):
            return "大需求的负责人"
        return decided_by(humans, str(d.get("kind") or ""), e.get("_at"), r == runs.LOCAL, you)

    def decision(e: dict[str, Any]) -> str | None:
        kind, at = str((e.get("data") or {}).get("kind") or ""), e.get("_at")
        hits = [q for q in qs if q["answered"] is not None and at is not None and -300 <= at - q["answered"] < 7200
                and (q["kind"] == ASKS.get(kind) or q["lean"].split(" ")[0] == kind)]
        return max(hits, key=lambda q: q["answered"])["id"] if hits and who.run_of(e) != runs.LOCAL else None

    def pending(ask: stages.Ask) -> stages.Pending:
        """Whom a request waits on; after the hand-over, the MR question that asks it: the first of its kind the
        bridge put up after the request and before the next one."""
        if ask.run == runs.LOCAL:
            return stages.Pending(_who(you), you, line_ok=hand_at is None)
        lean_kinds = ("REQUIREMENTS", "DESIGN") if ask.kind == "INCEPTION" else (ask.kind,)
        w, asker = _asked(you, _park_for(parks, ask.at, lean=lean_kinds).get("ask"),
                          "acceptance" if ask.kind == "RELEASE" else None, ask.kind)
        nxt = min((e["_at"] for e in lean if e.get("event") == "APPROVAL_REQUESTED" and e["_at"] > ask.at
                   and (e.get("data") or {}).get("kind") == ask.kind), default=float("inf"))
        q = min((q for q in qs if ask.at - 60 <= (q["asked"] or 0) < nxt
                 and (q["kind"] == ASKS.get(ask.kind) or q["lean"].split(" ")[0] == ask.kind)),
                key=lambda q: q["asked"] or 0, default=None)
        return stages.Pending(w, asker, mr=(q["id"] if q else None, (row.get("mr") or {}).get("url")))

    over = _over(f, row, merged_at, now)
    way = stages.Way(child=family is not None and family.role == "child",
                     children={c.unit: (c.key, c.state, c.owner) for c in (family.children if family else ())
                               if c.unit and c.state != "merged"},
                     over=over, proposals=proposals, you=you, blind=runner_blind(f, row))
    if family is not None and family.role == "child":     # unit-of-work.md is its big one's: it builds one unit
        units = [u for u in units if u.name == family.unit]
    planned = list(units) + [stages.UnitPlan(c.unit) for c in (family.children if family else ()) if c.unit]
    rec = stages.Records(lean, run.get("intent"), who.run_of, commits, list(reflect_refs), by, decision)
    nodes = stages.graph(rec, stages.units_of(lean, planned), way, f.get("approvals") or [], pending)
    last_at: dict[str, float] = {}
    starts: dict[str, float] = {}
    for x in lean + commits:
        k, at = who.run_of(x), x.get("_at", x.get("at"))
        last_at[k], starts[k] = max(last_at.get(k, at), at), min(starts.get(k, at), at)
    rs = runs.runs(nodes, who, started=bool(run), family=family, sessions=sessions, owner=f.get("owner"),
                   mine=you == "你", team=row.get("where") == "runner")
    runs.overlay(rs, nodes, row=row, sessions=sessions, hand_at=hand_at, last_at=last_at, over=over, you=you,
                 approvals=f.get("approvals") or [], now=now)
    stages.with_status(nodes, rec, {r.key: (stages.NodeStatus(r.now.status, r.now.doing, r.now.since, r.now.who),
                                            r.now.node) for r in rs if r.now is not None}, way, pending,
                       _quiet(evs, lean + commits, who, rs))
    by_id = {n.id: n for n in nodes}
    first = next((n for n in nodes if n.rounds[-1:] and n.rounds[-1].end.outcome is not stages.Outcome.SKIPPED
                  or not n.rounds and n.waits.status == "waiting"), None)
    marks: dict[str, list[dict[str, Any]]] = {n.id: [] for n in nodes}

    def run_at(r: str | None, at: float) -> tuple[stages.Node | None, str | None]:
        """The node *r* was at then: its round open at *at*, else the last one it began before."""
        hit = (stages.round_at(nodes, r, at) or stages.round_at(nodes, r, at, began=True)) if r else None
        return (hit[0] if hit else None), r

    def place(e: dict[str, Any]) -> tuple[stages.Node | None, str | None]:
        how, at = PLACE[e["kind"]], e["at"]
        if how == "record":
            return stages.node_of(nodes, e["_rec"], run.get("intent")), who.run_of(e["_rec"])
        if how == "record_at":
            return run_at(who.run_of(e["_rec"]), at)
        if how == "commit":
            c = e["_commit"]
            return run_at(who.run_of({"by": c.get("by"), "at": at, "after_handover": c.get("after_handover")}), at)
        if how == "side_at":
            return run_at(runs.LOCAL if e["_side"] == "local" else who.runner_at(at), at)
        if how == "local_at":
            return run_at(runs.LOCAL, at)
        if how == "first":
            return first, runs.LOCAL
        if how == "reflect":
            return by_id.get(stages.node_id("reflect", None)), runs.LOCAL
        if how == "local":
            return None, runs.LOCAL
        if how == "relay":
            return None, who.runner_at(at)
        return None, None

    def anchor(end: str, other: str, e: dict[str, Any], n: stages.Node | None) -> str | None:
        if end == RUN_AT:
            n = run_at(e["run"], e["at"])[0]
        if end not in (NODE, RUN_AT):
            return end
        return n.id if n is not None else other if other not in (NODE, RUN_AT) else None

    for e in evs:
        n, r = place(e)
        opens = e.get("_rec", {}).get("event") in ("STAGE_STARTED", "APPROVAL_REQUESTED")
        rnd = stages.round_on(n, r, e["at"], opens) if n is not None else None
        e["node"], e["run"], e["round"] = n.id if n is not None else None, r, rnd.n if rnd is not None else None
        frm, to = ANCHORS[e["kind"]]
        frm = "relay" if e.get("_relay") else frm
        e["from"], e["to"] = anchor(frm, to, e, n), anchor(to, frm, e, n)
        kind = "verify-ok" if e["kind"] == "verify" and e.get("ok") else "verify-fail" if e["kind"] == "verify" else \
            MARKS.get(e["kind"])
        if n is not None and kind:
            marks[n.id].append({"at": e["at"], "kind": kind, "title": _clip(e["text"], 160)})
    # a way that is over (merged, merged into its big one, closed) has every run over (runs.overlay): never live
    live = any(r.now is not None and r.now.status in ("running", "fixing") for r in rs)
    return {"phases": stages.phases(nodes, way), "nodes": [_node_wire(n, marks[n.id]) for n in nodes],
            "runs": [_run_wire(r) for r in rs], "relays": runs.relays(bounds, t, row, who, starts),
            "other_runs": [{"branch": o.get("branch"), "state": o.get("state")} for o in (t or {}).get("other_runs") or []
                           if isinstance(o, dict)],
            "live": bool(live), "_nodes": nodes, "_runs": rs, "_way": way, "_commits": commits,
            "_turns": stages.turns_of(rec)}


def build(f: dict[str, Any], row: dict[str, Any], t: dict[str, Any] | None, *, mr: dict[str, Any] | None,
          sessions: list[dict[str, Any]], app_events: list[dict[str, Any]], team_decisions: list[dict[str, Any]],
          subjects: dict[str, str], reflect_refs: list[tuple[str, float, float | None]], default_branch: str, local_only: bool,
          kit_rows: list[dict[str, Any]], decisions: list[dict[str, Any]], brain: dict[str, Any] | None,
          me: str | None, now: float, changes: dict[str, list[dict[str, Any]]] | None = None,
          units: Any = (), fold: dict[str, Any] | None = None) -> dict[str, Any]:
    """The board's graph part (the stage graph: phases, nodes, runs, relays, other_runs; the events placed on it, belt,
    checks, accept, person, live, missing, and Lean's progress at each of its stage records for 回放: lean_view.steps);
    the service adds the requirement's detail (decisions, questions, 变更说明, sessions, credits). *changes*:
    changes_from_log of the commits (GIT_LOG), by full sha. *units*: the planned units (stages.UnitPlan), when the
    service read unit-of-work.md. *fold*, when given, gets the stage graph's nodes and runs, the run branch's commits
    and Lean's session turns: what the 产出 drawer reads (outputs.Fold)."""
    run = f.get("run") or {}
    handoff = run.get("handoff") if isinstance(run.get("handoff"), dict) else None
    hand_at = _epoch((handoff or {}).get("requested_at"))
    local_only = bool(local_only or row.get("local_only"))
    you = _you(me, f.get("owner"))
    evs = events_of(f, t, mr=mr, app_events=app_events, team_decisions=team_decisions, subjects=subjects,
                    reflect_refs=reflect_refs, default_branch=default_branch, you=you)
    proposals = ((brain or {}).get("proposals") or 0) if isinstance(brain, dict) else 0
    merged_at = next((e["at"] for e in evs if e["kind"] == "merge"), None)
    sg = stage_graph(f, row, t, evs, mr=mr, sessions=sessions, team_decisions=team_decisions, subjects=subjects,
                     changes=changes or {}, reflect_refs=reflect_refs, you=you, me=me, hand_at=hand_at,
                     merged_at=merged_at, proposals=proposals, units=units, now=now)
    wait_who = str((row.get("waits") or {}).get("who"))
    asked = you if wait_who == "you" else ASK_NAME.get(wait_who, "人")
    checks = _checks(sg["_nodes"], sg["_runs"], sg["relays"], kit_rows, t, local_only, hand_at, merged_at, sg["_way"],
                     you, asked, handed=hand_at is not None or runner_blind(f, row))
    for i, e in enumerate(evs, 1):
        e["seq"] = i
    if fold is not None:
        fold.update(nodes=sg["_nodes"], commits=sg["_commits"], turns=sg["_turns"], runs=sg["_runs"])
    return {"events": [{k: e[k] for k in ("seq", "at", "kind", "from", "to", "text", "detail", "src", "node", "run", "round")}
                       for e in evs][-MAX_EVENTS:],
            "belt": _belt(evs, changes or {}), "checks": checks, "person": _person(row, me, f, you),
            "accept": _accept(f, row, sg["_nodes"], checks, decisions, t, sessions, merged_at, proposals, you, mr,
                              team_decisions),
            "live": sg["live"], "started_at": evs[0]["at"] if evs else None, "ended_at": merged_at,
            "lean_steps": lean_view.steps(f.get("events") or []),
            "phases": sg["phases"], "nodes": sg["nodes"], "runs": sg["runs"], "relays": sg["relays"],
            "other_runs": sg["other_runs"],
            "missing": missing_sources((t or {}).get("missing") or [], local_only=local_only, handed=hand_at is not None)}


def _belt(evs: list[dict[str, Any]], changes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The conveyor: each commit and the hand-over with the files it changed (BOARD-21's 改动 block), then the merge."""
    out = []
    for e in evs:
        if e["kind"] == "commit" and e.get("sha"):
            out.append({"kind": "commit", "at": e["at"], "sha": e["sha"][:12], "by": e.get("by") or "person",
                        "text": _clip(e.get("subject") or ("运行机的提交" if e.get("by") == "bot" else "提交"), 80),
                        "files": int(e.get("files") or 0), "records_only": bool(e.get("records")), "run": e.get("run"),
                        "node": e.get("node"), "changes": list(changes.get(e["sha"]) or [])})
        elif e["kind"] == "handover":   # brain-kit's own commit ("<KEY>: hand off to team runner"): said in words
            out.append({"kind": "handover", "at": e["at"], "sha": str(e.get("sha") or "")[:12], "by": "person",
                        "text": "交给运行机", "files": int(e.get("files") or 0), "records_only": bool(e.get("records")),
                        "run": e.get("run"), "node": e.get("node"),
                        "changes": list(changes.get(str(e.get("sha") or "")) or [])})
        elif e["kind"] == "merge":
            out.append({"kind": "merge", "at": e["at"], "sha": str(e.get("sha") or "")[:12], "by": "person",
                        "text": e["text"], "files": 0, "records_only": False, "run": e.get("run"), "node": e.get("node"),
                        "changes": []})
    return out[-MAX_BELT:]


# (Lean's approval kind, the cell's label and name, the stage whose node it gates)
GATE_CELLS = (("REQUIREMENTS", "需求", "需求审批", "requirements"), ("DESIGN", "设计", "设计审批", "application-design"),
              ("PLAN", "计划", "代码生成计划审批", "code-generation"), ("RELEASE", "发布", "发布审批", "release"))
# the stages a design approval would be about
DESIGN_STAGES = ("user-stories", "application-design", "units", "functional-design", "nfr-design", "infrastructure-design")


def _last_gate(nodes: list[stages.Node], kind: str) -> tuple[stages.GateView | None, stages.Node | None]:
    """The approval's last word on *nodes*: their latest gate of *kind*, and its node; for the requirements or the
    design, an INCEPTION one when there is none of its own kind (one INCEPTION approval covers both documents)."""
    for k in (kind, "INCEPTION") if kind in ("REQUIREMENTS", "DESIGN") else (kind,):
        hits = [(g.at or 0, g.round, i, g, n) for i, n in enumerate(nodes) for g in n.gates if g.kind == k]
        if hits:
            _, _, _, g, n = max(hits, key=lambda x: x[:3])
            return g, n
    return None, None


def _no_design(nodes: list[stages.Node], over: Optional[Tuple[str, float]]) -> str | None:
    """Why a design approval is not on this requirement's way, when Lean went past code generation without asking
    for one or for a code-generation plan, or the requirement merged first; None while one may still come."""
    if over is not None and over[0] == "closed":
        return None
    if any(r.ask is not None for n in nodes if n.stage.id in ("application-design", "code-generation") for r in n.rounds):
        return None
    past = any(n.rounds for n in nodes if n.stage.id in ("build-and-test", "release")) or any(
        r.end.outcome is stages.Outcome.DONE for n in nodes if n.stage.id == "code-generation" for r in n.rounds)
    if past:
        skipped = all(r.end.outcome is stages.Outcome.SKIPPED for n in nodes if n.stage.id in DESIGN_STAGES
                      for r in n.rounds)
        return "%s：Lean %s" % (NOT_NEEDED, "跳过了设计" if skipped else "没有请人批设计和代码计划")
    return NOT_NEEDED if over is not None else None


def _checks(nodes: list[stages.Node], rs: list[runs.RunView], relays: list[dict[str, Any]], kit_rows: list[dict[str, Any]],
            t: dict[str, Any] | None, local_only: bool, hand_at: float | None, merged_at: float | None,
            way: stages.Way, you: str, asked: str = "人", handed: bool = False) -> dict[str, Any]:
    """The check wall, read off the stage graph. It counts what has happened (pass, fail, and wait: asked of a person);
    what is still to come on this requirement's way is `todo`, by name; what is not on its way (a design Lean skipped,
    the hand-over of a repo done only here) is not_needed or left out; the 14 days after the merge are watched,
    nobody's turn. A gate cell is its approval's last word (_last_gate), the verify cells build-and-test's latest round,
    桥 and 签名 the latest hand-over's steps. A small requirement is never handed over and its big one releases it, so
    its wall has no 桥 or 签名 and its release is not_needed. *asked*: whom the requirement waits on, in words;
    *handed*: it was handed over (the team runner's page of one it cannot read yet knows it was)."""
    blind = handed and hand_at is None   # the runner's page, nothing before the hand-over readable: not said
    by_stage: dict[str, list[stages.Node]] = {}
    for n in nodes:
        by_stage.setdefault(n.stage.id, []).append(n)
    release = (by_stage.get("release") or [None])[0]
    runner = max((r for r in rs if r.kind == "runner"), key=lambda r: r.n or 0, default=None)
    cells: list[dict[str, Any]] = []
    todo: list[str] = []
    ahead = merged_at is None and (release is None or release.status is None or release.status.status not in FINAL)

    def cell(cid: str, group: str, label: str, name: str, status: str, at: float | None, detail: str,
             node: stages.Node | None = None, run: str | None = None, later: str | None = None) -> None:
        cells.append({"id": cid, "group": group, "label": label, "name": name, "status": status, "at": at,
                      "detail": _clip(detail, 160), "node": node.id if node is not None else None,
                      "run": run or (node.rounds[-1].run if node is not None and node.rounds else None)})
        if later and status == "skip" and ahead and later not in todo:
            todo.append(later)

    for lean, label, name, sid in GATE_CELLS:
        mine = by_stage.get(sid) or []
        g, n = _last_gate(mine, lean)
        n = n or (mine[0] if mine and lean != "PLAN" else None)
        no_design = _no_design(nodes, way.over) if lean == "DESIGN" and g is None else None
        if g is None and blind and lean != "RELEASE":
            continue
        if lean == "RELEASE" and way.child:
            cell("gate-RELEASE", "gate", label, name, "not_needed", None, stages.CHILD_LEFT_OUT["release"])
        elif g is None and no_design:
            cell("gate-DESIGN", "gate", label, "设计和代码计划审批", "not_needed", None, no_design, n)
        elif g is None and lean in ("REQUIREMENTS", "RELEASE"):
            cell("gate-" + lean, "gate", label, name, "skip", None, "还没到这一步", n, later=name)
        elif g is None:
            continue
        elif g.state == "approved":
            cell("gate-" + lean, "gate", label, name, "pass", g.at, "%s批准了" % (g.by or you), n)
        elif g.state == "sent_back":
            cell("gate-" + lean, "gate", label, name, "fail", g.at, "%s退回了：等它改好再批" % (g.by or you), n)
        elif g.kind == lean and g.state in ("pending", "queued"):
            cell("gate-" + lean, "gate", label, name, "wait", None,
                 "等%s在会话里打这一行批准：%s" % (you, g.line) if hand_at is None and g.line
                 else "等%s在 MR 上回答（approve 通过 / reject 退回）" % asked, n)
        else:
            here = hand_at is None or (g.at or 0) <= hand_at
            cell("gate-" + lean, "gate", label, name, "wait", None,
                 "等%s批准" % (you if here else "%s在 MR 上" % (g.asker or "人")), n)
    # verify: build-and-test's latest round
    bt = (by_stage.get("build-and-test") or [None])[0]
    rounds = bt.rounds if bt is not None else []
    history = [{"at": r.ended, "passed": sum(1 for c in r.end.checks[:40] if c.ok),
                "failed": sum(1 for c in r.end.checks[:40] if not c.ok)} for r in rounds]
    if rounds:
        r = rounds[-1]
        ok = r.end.outcome is stages.Outcome.PASSED
        if not r.end.checks:
            cell("verify", "verify", "自动检查", "自动检查", "pass" if ok else "fail", r.ended,
                 "自动检查通过" if ok else "自动检查没过：有检查失败", bt)
        seen: dict[str, int] = {}
        for c in r.end.checks[:40]:
            name = stages.check_name(c.cmd)
            k = seen[name] = seen.get(name, 0) + 1
            cell("verify-%s-%d" % (name, k), "verify", name, "自动检查：%s" % name, "pass" if c.ok else "fail", r.ended,
                 "通过" if c.ok else "没过：在会话里看自动检查的输出", bt)
    else:
        cell("verify", "verify", "自动检查", "自动检查", "skip", None, "还没跑过自动检查（构建和测试阶段跑）", bt, later="自动检查")
    # the hand-over: the bridge and the signer (not on the way of a repo done only here, of one done here and merged, or
    # of a small requirement)
    if not local_only and not way.child and not (not handed and merged_at is not None):
        steps = {x["key"]: x for x in relays[-1]["steps"]} if relays else {}
        hand, bridge, signer = steps.get("handover") or {}, steps.get("bridge") or {}, steps.get("signer") or {}
        mine = runner.key if runner is not None else None
        refused = bridge.get("status") == "failed"
        if hand.get("status") == "stalled":
            # pushed without the hand-over label: nothing reached the bridge and nothing is checked; the person labels it
            cell("bridge", "handover", "标签", "补上交接标签", "wait", None,
                 bridge.get("text") or "MR 上没有交接标签：桥不会接", run=mine)
        else:
            st = "pass" if bridge.get("status") == "merged" else "fail" if refused else "wait" if handed else "skip"
            said = {"pass": "接手了", "fail": bridge.get("text") or "接手被拒", "wait": "在核对", "skip": "还没交接"}
            cell("bridge", "handover", "桥", "桥核对交接", st, bridge.get("at") if st in ("pass", "fail") else None,
                 said[st], run=mine)
        signed = signer.get("status") == "merged"
        cell("signer", "handover", "签名", "签名服务签接手合同", "pass" if signed else "skip",
             signer.get("at") if signed else None,
             "签了" if signed else "在签" if signer.get("status") == "running" else "还没签", run=mine,
             later="签接手合同" if handed and not refused else None)
    # the repo's kit (what the team runner needs committed): a ! reminder matters once a requirement is handed over, so
    # for a repo done only on this machine it is not on the way
    for i, k in enumerate(kit_rows):
        warn = k.get("level") == "WARN"
        cell("kit-%d" % i, "brain", k.get("label") or KIT_LABEL, k.get("text") or "", "fail" if k.get("level") == "FAIL" else
             ("not_needed" if local_only else "wait") if warn else "pass", None,
             "只在本机做，用不上：交给运行机时才要紧" if warn and local_only else (k.get("todo") or k.get("text") or ""))
    # the Brain the run used: a citation that is stale or unknown fails; no citation is no check at all
    b = (t or {}).get("brain") or {}
    if b.get("cited") or b.get("unknown") or b.get("stale"):
        bad = list(b.get("unknown") or [])
        stale = list(b.get("stale") or [])
        cell("brain-cite", "brain", "引用", "引用的 Brain 条目", "fail" if bad else "wait" if stale else "pass", None,
             ("引用了 Brain 里没有的 %s" % "、".join(bad[:3])) if bad else ("引用了过时的 %s" % "、".join(stale[:3])) if stale
             else "引用了 %d 条，都在" % len(b.get("cited") or []))
    # the 14 days after the merge: watched until the window ends
    r14 = ((t or {}).get("outcome") or {}).get("reverted_or_hotfixed_within_14d") or {}
    if r14.get("window_end"):
        v = r14.get("value")
        cell("after-14d", "after", "14 天", "合并后 14 天没有回滚或热修", "fail" if v is True else "pass" if v is False else "watch",
             _epoch(r14.get("window_end")) if v is not None else None,
             "有回滚或热修" if v is True else "14 天里没有回滚、热修或缺陷" if v is False else "观察中：到 %s" % str(r14["window_end"])[:10])
    passed = sum(1 for c in cells if c["status"] == "pass")
    failed = sum(1 for c in cells if c["status"] == "fail")
    total = sum(1 for c in cells if c["status"] in ("pass", "fail", "wait"))
    ats = [c["at"] for c in cells if c["at"] is not None]
    return {"cells": cells, "passed": passed, "failed": failed, "total": total, "todo": todo,
            "at": max(ats) if ats else None, "history": history}


def _person(row: dict[str, Any], me: str | None, f: dict[str, Any], you: str = "你") -> dict[str, Any]:
    """The owner's node: it asks for the person only when the requirement waits on them (waits.who `you`); a question
    for the product or someone else is said, not flashed at the owner (BOARD-5). `you`: the board calls the owner 你,
    so the page offers them 开工 and says 你 where everything else on it does (BOARD-11)."""
    waits = row.get("waits") or {}
    owner = f.get("owner")
    st = row.get("state")
    who = waits.get("who")
    if st in ("merged", "closed"):
        status, doing = ("merged", waits.get("text") if waits.get("do") == "write_back" and waits.get("text") else "已合并") \
            if st == "merged" else ("skipped", "已关闭")
    elif who == "you":
        status, doing = "awaiting_approval", waits.get("text") or "轮到你了"
    elif who in ("agent", "runner", "bridge"):
        status, doing = "running", waits.get("text") or "AI 在做"
    else:
        status, doing = "waiting", waits.get("text") or "没有等你的事"
    return {"status": status, "doing": _clip(doing, 80), "owner": owner, "me": bool(me) and me == owner, "you": you == "你"}


def _lines(s: str) -> str:
    return "\n".join(" ".join(x.split()) for x in s.split("\n") if x.strip())


def _do_of(row: dict[str, Any], f: dict[str, Any], nodes: list[stages.Node], hand: bool, local: list[dict[str, Any]],
           merged: bool) -> str:
    """The step the requirement waits for (Waits.do); a row from before the contract had it gets it from its state."""
    waits = row.get("waits") or {}
    if waits.get("do"):
        return str(waits["do"])
    st, who = row.get("state"), waits.get("who")
    if merged:
        return "write_back" if any(n.stage.id == "reflect" and n.status is not None
                                   and n.status.status == "awaiting_approval" for n in nodes) else "none"
    if st == "claimed":
        return "start"
    if not hand and any(a.get("state") == "pending" and a.get("line") for a in f.get("approvals") or []):
        return "approve"
    return {"acceptance": "accept", "parked": "answer", "rejected": "redo_handoff"}.get(str(st)) or (
        "label_handoff" if st == "handed_over" and who == "you" else
        ("tool" if local and local[-1].get("awaiting") else "reply") if st == "local" and who == "you" else "none")


def _open_question(mr: dict[str, Any] | None, team_decisions: list[dict[str, Any]], t: dict[str, Any] | None) -> dict[str, Any]:
    """The question on the MR that waits for its answer (the newest): its D-n, kind and options."""
    qs = [q for q in (mr or {}).get("questions") or [] if q.get("state") == "open"]
    qs += [d for d in team_decisions if d.get("state") == "open"]
    qs += [d for d in (t or {}).get("decisions") or [] if d.get("source") == "bridge" and not d.get("choice")
           and d.get("state") in (None, "open")]
    return qs[-1] if qs else {}


def _said(note: str, you: str) -> tuple[str, str | None]:
    """Lean's own next step from its Session notes (aidlc-state.md, 「Next」), in plain words: what it says to do, and
    the words it says to type (「…」 after 说：) as the line to copy."""
    note = re.sub(r"\bowner\b\s*", you.strip() + ("" if you == "你" else " "), note.replace("`", ""))
    m = re.search(r"说[：:]\s*「(.+?)」", note)
    if not m:
        return _clip(note, 200), None
    return _clip(note[:m.start()] + "说下面这一行", 200), _clip(m.group(1), 400)


def _reply_step(row: dict[str, Any], f: dict[str, Any], you: str) -> tuple[str, str | None]:
    """An idle local session (BOARD-4): where Lean is, its next stage, and what to say to the session."""
    rows = f.get("stages") or []
    lean = row.get("lean") or {}
    nxt = lean["next"] if "next" in lean else lean_view.summary(rows)["next"]
    done = [r for r in rows if r.get("state") == "done"]
    head = "会话空着，轮到%s回话。" % you
    if done:
        head += "Lean 做完了「%s」，" % done[-1]["label"]
    head += "下一步是「%s」。" % nxt["label"] if nxt else "Lean 的阶段都做完了。"
    # before the release, the review in a new session: the line to say is Lean's own (the one 开审查会话 sends), never
    # one the session that wrote the code worded for its reviewer
    w = row.get("waits") or {}
    needed = w["review"] if "review" in w else bool(nxt and nxt.get("id") == "release" and not lean_view.review_current(
        (f.get("review") or {}).get("at"), bool(f.get("review")), f.get("events") or []))
    review = lean_view.review_line(run_of(f).get("intent")) if needed else None
    # an English note (Lean's notes are the agent's words) is not shown: the stage names say it in Chinese
    notes = [x for x in lean_view.notes(f.get("state_text")).get("next") or [] if re.search(r"[\u4e00-\u9fff]", x)]
    if notes:
        text, line = _said(notes[0], you)
        return _lines("%s\nLean 留的下一步：%s" % (head, text)), review or line
    if review:
        return _lines("%s\n先开一个新会话做审查（这个会话写了代码，不能自己审），审查写好再回这个会话接着发布" % head), review
    if nxt:
        tail = "（做完会请%s批准发布）" % you if nxt.get("id") == "release" else ""
        return _lines("%s\n在会话里让它接着做「%s」%s" % (head, nxt["label"], tail)), "接着做下一步：%s" % nxt["label"]
    return head, None


def _accept(f: dict[str, Any], row: dict[str, Any], nodes: list[stages.Node], checks: dict[str, Any],
            decisions: list[dict[str, Any]], t: dict[str, Any] | None, sessions: list[dict[str, Any]],
            merged_at: float | None, proposals: int, you: str, mr: dict[str, Any] | None,
            team_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """验收: where the release's approval is (its last word on the 发布 node), and the one step a person takes now."""
    g, _ = _last_gate([n for n in nodes if n.stage.id == "release"], "RELEASE")
    word = g.state if g is not None else None
    st = row.get("state")
    merged = merged_at is not None or st == "merged"
    state = "merged" if merged else "waiting" if word in ("pending", "queued", "refused") or st == "acceptance" \
        else "accepted" if word == "approved" else "rejected" if word == "sent_back" else "not_yet"
    waits = row.get("waits") or {}
    local = [s for s in sessions if s.get("kind", "local") == "local" and not s.get("missing")]
    session_url = local[-1]["url"] if local else None
    mr_url = (row.get("mr") or {}).get("url")
    hand = isinstance(run_of(f).get("handoff"), dict)
    do = _do_of(row, f, nodes, hand, local, merged)
    where = waits.get("where") or ("app" if do == "start" else "mr" if hand else "session")
    who = {"product": "产品", "people": "别人", "builder": "开发的人"}.get(str(waits.get("who")), you.strip())
    step = {"text": "", "who": "", "line": None, "mr_url": None, "session_url": None}
    q = _open_question(mr, team_decisions, t)
    on_mr = "在 MR 上回答%s（%s）：桥会把回答带回运行机" % (
        " " + q["id"] if q.get("id") else "",
        " / ".join(str(o.get("id")) for o in q.get("options") or [] if isinstance(o, dict) and o.get("id"))
        or ("approve 通过 / reject 退回" if do in ("accept", "approve") or q.get("kind") == "acceptance" else "选一个答案"))
    if do == "approve" and where != "mr":
        pend = next((a for a in f.get("approvals") or [] if a.get("state") == "pending" and a.get("line")), None)
        if pend:
            step = {"text": "在会话里自己打这一行，批准%s" % GATE_KIND_LABEL.get(pend["kind"], pend["kind"]), "who": who,
                    "line": pend["line"], "mr_url": None, "session_url": session_url}
    elif do in ("approve", "accept") or (do == "answer" and where == "mr"):
        step = {"text": on_mr, "who": who, "line": None, "mr_url": mr_url, "session_url": None}
    elif do == "answer":
        step = {"text": "在会话里回答它的问题（%s）" % KIND_LABEL.get(str((f.get("park") or {}).get("kind")), "问题"),
                "who": who, "line": None, "mr_url": None, "session_url": session_url}
    elif do == "reply":
        text, line = _reply_step(row, f, you)
        step = {"text": text, "who": who, "line": line, "mr_url": None, "session_url": session_url}
    elif do == "tool":
        step = {"text": "会话里有一个工具调用等%s批准：打开会话，批准或拒绝它" % you, "who": who, "line": None, "mr_url": None,
                "session_url": session_url}
    elif do == "start":
        step = {"text": "认领了，还没开工：在开需求里开工 %s" % f["key"] if you == "你" else "%s认领的，等%s开工" % (you, you),
                "who": you.strip(), "line": None, "mr_url": None, "session_url": None}
    elif do == "redo_handoff":
        why = re.sub(r"^接手被拒[：:]\s*", "", str(waits.get("text") or "")) or "看 MR 上桥写的原因"
        step = {"text": _lines("接手被拒：%s\n改好后，在这个需求的分支上重新交接（交接会提交、推送、打标签）" % why),
                "who": who, "line": None, "mr_url": mr_url, "session_url": None}
    elif do == "label_handoff":
        step = {"text": waits.get("text") or "在 MR 上补上交接标签", "who": who, "line": None, "mr_url": mr_url,
                "session_url": None}
    elif do == "write_back" and proposals:
        step = {"text": "去 Brain 页回写这次的 %d 条新知识：先预览，再写到新分支" % proposals, "who": who, "line": None,
                "mr_url": None, "session_url": None}
    step["text"] = _lines(step["text"]) if step["text"] else ""
    counts = {c: 0 for c in CAT_ORDER}
    for d in decisions:
        counts[category(d)] = counts.get(category(d), 0) + 1
    by = [{"id": c, "label": CAT_LABEL[c], "count": counts[c]} for c in CAT_ORDER if counts.get(c)]
    done = None
    if state == "merged":
        m = ((t or {}).get("outcome") or {}).get("merged_at") or {}
        h = next((h for h in reversed((t or {}).get("humans") or []) if h.get("what") in ("RELEASE", "acceptance")
                  and h.get("kind") == "approval"), None) or {}
        name = str(h.get("who") or "")
        # a user name brain-kit read is said, never a pseudonym (G-…, P-…): on the owner's own run it was the owner,
        # after the hand-over the product accepted it on the MR
        who_done = name if re.match(r"^[a-z0-9][a-z0-9._-]*$", name) else ("产品" if hand else f.get("owner")) if h else None
        done = {"at": merged_at or _epoch(f.get("tip_at")) or 0, "who": who_done,
                "how": {"merge commit": "合并提交", "fast-forward": "快进"}.get(str(m.get("how") or ""), "已合并")}
    elif state == "accepted":
        done = {"at": g.at or 0, "who": None, "how": "发布已批准，等合并"}
    return {"state": state, "step": step, "by": by, "checks": checks_line(checks), "done": done}


def checks_line(checks: dict[str, Any]) -> str:
    """The checks in one line: 「目前 3 项都过 · 还剩 发布审批」, 「检查 2/4 过 · 没过：pytest · 等人：发布审批」."""
    cells, todo = checks["cells"], checks.get("todo") or []
    bad = [c["name"] for c in cells if c["status"] == "fail"]
    wait = [c["name"] for c in cells if c["status"] == "wait"]
    watched = sum(1 for c in cells if c["status"] == "watch")
    later = (" · 还剩 %s" % "、".join(todo) if todo else "") + (" · %d 项在观察" % watched if watched else "")
    if not checks["total"]:
        return "还没有检查结果" + later
    if not bad and not wait:
        return ("目前 %d 项都过" % checks["passed"] + later) if later else "全部 %d 项都过" % checks["passed"]
    return "检查 %d/%d 过%s%s%s" % (checks["passed"], checks["total"], " · 没过：%s" % "、".join(bad[:3]) if bad else "",
                                  " · 等人：%s" % "、".join(wait[:3]) if wait else "", later)


NUMBERS = ("stages_done", "verify_rounds", "verify_failed", "commits_bot", "commits_person", "blocked",
           "human_at_judgment", "human_outside_judgment", "decisions", "questions", "credits")


def _numbers(x: Any) -> dict[str, Any] | None:
    if not isinstance(x, dict):
        return None
    return {k: (x[k] if isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) else None) for k in NUMBERS}


def rollup(t: dict[str, Any] | None) -> dict[str, Any] | None:
    """A big requirement's family numbers from its trace (brain-kit 0d52e97 trace `family.rollup`: its own, and with
    every small one added; each small one's): FamilyRollup, or None for any other requirement (or an older brain-kit)."""
    fam = (t or {}).get("family")
    if not isinstance(fam, dict) or fam.get("role") != "parent":
        return None
    r = fam.get("rollup") if isinstance(fam.get("rollup"), dict) else {}
    return {"own": _numbers(r.get("own")), "total": _numbers(r.get("total")),
            "unknown": [str(u)[:80] for u in r.get("unknown") or []][:20],
            "children": [{"key": str(c["key"]), "unit": str(c.get("unit") or "") or None,
                          "merged": bool((c.get("merged_into_parent") or {}).get("value")), "numbers": _numbers(c.get("numbers"))}
                         for c in fam.get("children") or [] if isinstance(c, dict) and c.get("key")][:50]}


def run_words(ids: list[Any]) -> str:
    """brain-kit's run ids (RUN-<KEY>-<n>) as the 运行机 page says them: 「第 1 次运行」, 「第 1、2 次运行」 (the ids
    themselves only on hover: the event's src, the lane's tooltip)."""
    ns = [m.group(1) for m in (re.match(r"^RUN-.+-([0-9]+)$", str(i or "")) for i in ids) if m]
    return "第 %s 次运行" % "、".join(ns) if ns else ("运行 %d 次" % len(ids) if ids else "")


def runner_blind(f: dict[str, Any], row: dict[str, Any]) -> bool:
    """The team runner's page of a requirement it has no working directory for yet: handed over (the bridge took it),
    nothing of its records readable there."""
    return row.get("where") == "runner" and not f.get("run") and row.get("state") not in ("claimed", "merged", "closed")


def run_of(f: dict[str, Any]) -> dict[str, Any]:
    return f.get("run") if isinstance(f.get("run"), dict) else {}


# the one git call the board's commits are read with (read only): each commit's subject, and the files it changed with
# their status (--raw) and lines added and removed (--numstat), renames found (-M)
GIT_LOG = ("log", "--no-walk=unsorted", "-M", "--raw", "--numstat", "--format=%x01%H%x00%s")
MAX_CHANGES = 40


def subjects_from_log(text: str) -> dict[str, str]:
    """`git log` output in GIT_LOG's format (or `--format=%H%x00%s`) → {sha: subject}."""
    out = {}
    for line in (text or "").splitlines():
        sha, _, subj = line.lstrip("\x01").partition("\0")
        if re.match(r"^[0-9a-f]{40}$", sha) and "\0" in line:
            out[sha] = subj.strip()[:200]
    return out


def changes_from_log(text: str) -> dict[str, list[dict[str, Any]]]:
    """`git log` output in GIT_LOG's format → {sha: [{path, added, removed, status}]}: status A, M, D or R (a copy is
    an A, a type change an M); a binary file's lines are 0. git prints the --raw and the --numstat lines of a commit
    in the same order, so the n-th of one is the n-th of the other (no rename paths to parse)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for block in (text or "").split("\x01")[1:]:
        head, _, body = block.partition("\n")
        sha = head.partition("\0")[0]
        if not re.match(r"^[0-9a-f]{40}$", sha):
            continue
        raw, nums = [], []
        for line in body.splitlines():
            if line.startswith(":"):
                meta, *paths = line.split("\t")
                letter = (meta.split() or ["M"])[-1][:1]
                raw.append(({"C": "A", "T": "M"}.get(letter, letter) if letter in "AMDRCT" else "M", paths[-1] if paths else ""))
            elif line.count("\t") >= 2:
                a, r, _ = line.split("\t", 2)
                nums.append((int(a) if a.isdigit() else 0, int(r) if r.isdigit() else 0))
        out[sha] = [{"path": path[:300], "added": n[0], "removed": n[1], "status": st}
                    for (st, path), n in zip(raw, nums + [(0, 0)] * (len(raw) - len(nums)))][:MAX_CHANGES]
    # Lean names its audit file after the clone (the host): shown by its label, as everywhere else
    audit = lean_view.audit_labels(sorted({c["path"] for cs in out.values() for c in cs
                                           if re.match(r"^aidlc-docs/audit/[^/]+\.jsonl$", c["path"])}))
    for cs in out.values():
        for c in cs:
            c["path"] = audit.get(c["path"], c["path"])
    return out


def kit_cells(rows: list[dict[str, Any]], repo_path: str) -> list[dict[str, Any]]:
    """The KiroCrew rows about this repo (what the team runner needs committed), as the check wall's kit cells."""
    out = []
    for r in rows:
        if r.get("repo") and repo_path and r.get("repo") != repo_path:
            continue
        if not r.get("repo") and "仓库" not in str(r.get("text") or ""):
            continue
        txt, raw = str(r.get("text") or ""), str(r.get("raw") or "")
        # its short name on the wall: brain-kit's own line (raw) says which file it is about
        label = "适配" if ("适配" in txt or "adapter" in txt + raw) else "Lean" if "Lean" in txt and "提交" in txt else \
            "记录" if "runs/" in txt + raw else KIT_LABEL
        out.append({"level": r.get("level"), "label": label, "text": txt, "todo": r.get("todo") or ""})
    return out

