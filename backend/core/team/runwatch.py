"""What the app adds to brain-kit's watch of the runs its bridge dispatched, each round right after brain-kit's steps,
from brain-kit's ledger, the slot as KiroCrew reads it (crewadapter.activity) and the MR.

brain-kit's watchdog (delivery.tell_if_stalled, bridge.json stall_minutes) tells the MR about a session at work that
went quiet, or one KiroCrew cannot show; an idle session it leaves alone, since a session that parked or finished is
idle too. A turn KiroCrew stopped half-way (a restart, a provider shutdown) leaves one idle that is neither: its work
in the workspace, uncommitted, and the bridge waiting for a push that never comes (RUN-REQ-2-1, 20 hours). turn() tells
them apart, and a session that owes a push, with nothing queued or on its way to it, quiet for stall_minutes, is told
to go on (RESUME, through brain-kit's outbox as a notice about the MR's head: sent once, settled as brain-kit settles
its own); once more if it is quiet again; then the MR hears once that a person should look. brain-kit's own watchdog is
where this belongs (BRAINKIT_REQUESTS 49).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .. import bk

RESUMES = 2             # messages to go on at one MR head, before a person is told
# brain-kit's outbox: a message on its way to the session (its delivery sends it again, or waits for KiroCrew)
IN_FLIGHT = ("sending", "interrupted", "unknown", "held")
# brain-kit's decision states of a question someone else holds: being posted, asked, an answer on its way or refused
ASKED = ("posting", "asking", "open", "held", "answered", "delivery_unknown", "refused")
SESSION, DONE, OTHERS = "session", "done", "others"
RESUME = ("会话中断：你上一轮没做完就停了（多半是 KiroCrew 重启打断了它）：会话从 %s UTC 起没有新动静，MR 上还是提交 %s，也没有问题在等"
          "人回答。你做的改动还在工作区里：先 `git status`、`git log --oneline -3` 看做到哪了，再接着做：按 brain-run 的规矩提交并"
          "推送，然后照常停下（要问人就写 park.json，以 `等待决策，不继续` 结束这一轮；做完了就结束这一轮）。")
STOPPED = ("%s 的会话停住了：KiroCrew 会话 %s 从 %s UTC 起没在跑，也没有推送（MR 上还是提交 %s），没有问题在等人回答。桥让它接着做了 "
           "%d 次（多半是 KiroCrew 重启打断了它那一轮），都没有动静。请负责人到 KiroCrew 看一下这个会话：运行机的工作区里可能有它没提交"
           "的改动。它再推送以后，桥照常接着看。")


def accepted(decisions: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The run's acceptance, as brain-kit's DecisionLoop._accepted finds it: its newest acceptance decision that was
    delivered, when the product's answer was approve; else None. Lean closes the intent then."""
    acc = [d for d in decisions if d.get("kind") == "acceptance" and d.get("state") == "delivered"]
    d = max(acc, key=lambda x: str(x.get("created") or ""), default=None)
    return d if d is not None and d.get("answer") == "approve" else None


def turn(run: Mapping[str, Any], head: str, decisions: list[Mapping[str, Any]],
         outbox: list[Mapping[str, Any]]) -> str:
    """Whose move a dispatched run is at its MR's *head*, from brain-kit's ledger. OTHERS while a message is on its
    way to the session or a question at the head waits for a person or the bridge. SESSION when the newest message
    that reached the session is about this head (the hand-over, an answer, a notice: each asks for a push) and none
    came after, or when the head is a push of the session's the bridge read, with no question there, and the run is
    not accepted. DONE when the product accepted it and its records came after. OTHERS otherwise: the bridge has not
    read the head yet (an acceptance question waiting for CI, say)."""
    if any(o["state"] in IN_FLIGHT for o in outbox):
        return OTHERS
    at = [d for d in decisions if d.get("sha") == head]
    if any(d["state"] in ASKED for d in at):
        return OTHERS
    sent = [o for o in outbox if o["state"] == "sent"]
    if sent and max(sent, key=lambda o: str(o["created"]))["sha"] == head:
        return SESSION
    if head != run.get("last_sha") or at:
        return OTHERS
    return DONE if accepted(decisions) else SESSION


class RunWatch:
    """round() on the bridge's worker thread, after brain-kit's steps. *host* is brain-kit's Bridge: its ledger, its
    KiroCrew client (the app's adapter), GitLab client, stall_s, outbox and MR notices."""

    def __init__(self, host: Any, out: Callable[[str], None] = print) -> None:
        self.host, self.out = host, out

    def round(self) -> int:
        """Every dispatched run looked at. -> how many sessions were told to go on. A run whose slot or MR cannot be
        read holds up none of the others: the errors are raised together at the end."""
        common = bk.m("common")
        told, errors = 0, []
        for run in self.host.ledger.runs_in("dispatched"):
            try:
                told += self._one(dict(run))
            except Exception as exc:  # noqa: BLE001 - KiroCrew, GitLab, the ledger: looked at again next round
                errors.append("%s: %s" % (run["run_id"], " ".join(str(exc).split())[:200]))
        if errors:
            raise common.BrainError("not done this round: " + "; ".join(errors))
        return told

    def _one(self, run: dict[str, Any]) -> int:
        stall = float(self.host.stall_s)
        read = getattr(self.host.kc, "activity", None)
        if not stall or read is None or not run.get("slot"):
            return 0
        try:
            a = read(run["slot"])
        except bk.m("common").BrainError:   # brain-kit's watchdog tells the MR once the slot stays unreadable
            return 0
        if not a or a["running"] or a.get("queued") or a["last_role"] == "permission" or a["last_at"] is None:
            return 0
        if (bk.m("common").utcnow() - a["last_at"]).total_seconds() < stall:
            return 0
        db, rid = self.host.ledger.db, run["run_id"]
        outbox = [dict(r) for r in db.execute("SELECT send_id, what, state, sha, created FROM outbox WHERE run_id=?",
                                              (rid,))]
        if any(o["state"] in IN_FLIGHT for o in outbox):
            return 0
        decisions = [dict(r) for r in db.execute("SELECT id, sha, kind, state, answer, created FROM decisions "
                                                 "WHERE run_id=?", (rid,))]
        mr = self.host.gl.mr(run["project"], run["iid"]) or {}
        head = str(mr.get("sha") or "")
        if mr.get("state") != "opened" or not head or turn(run, head, decisions, outbox) != SESSION:
            return 0
        return self._resume(run, head, a["last_at"].strftime("%Y-%m-%d %H:%M"), outbox)

    def _resume(self, run: dict[str, Any], head: str, since: str, outbox: list[dict[str, Any]]) -> int:
        """RESUME once more at *head*, or, RESUMES sent there already, the MR told once (STOPPED)."""
        rid, mark = run["run_id"], "notice:resume:%s:" % head[:12]
        n = sum(1 for o in outbox if str(o["what"]).startswith(mark))
        if n >= RESUMES:
            self.host.tell_mr_once("stopped:%s:%s" % (rid, head[:12]), run["project"], run["iid"],
                                   STOPPED % (rid, run["slot"], since, head[:12], n), run_id=rid)
            return 0
        d, key = bk.m("bridge.delivery"), "resume:%s:%d" % (head[:12], n + 1)
        # notify_run takes only brain-kit's own notice words (NOTICE_PREFIXES): its send, minus that check
        got = self.host._send_once(rid, run["slot"], d.notice_send_id(rid, key), "notice:" + key,
                                   d.inert(RESUME % (since, head[:12])), sha=head)
        self.out("%s: its session was idle owing a push since %s UTC: told to go on (%s): %s" % (rid, since, key, got))
        return 1
