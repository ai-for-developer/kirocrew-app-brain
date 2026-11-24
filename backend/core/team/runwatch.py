"""What the app adds to brain-kit's watch of the runs its bridge dispatched, each round right after brain-kit's steps,
from brain-kit's ledger, the slot as KiroCrew reads it (crewadapter.activity) and the MR.

brain-kit's watchdog (delivery.tell_if_stalled, bridge.json stall_minutes) tells the MR about a session at work that
went quiet, or one KiroCrew cannot show; an idle session it leaves alone, since a session that parked or finished is
idle too. A turn KiroCrew stopped half-way (a restart, a provider shutdown) leaves one idle that is neither: its work
in the workspace, uncommitted, and the bridge waiting for a push that never comes (RUN-REQ-2-1, 20 hours). The slot
tells such a turn apart (crewadapter.cut_off: KiroCrew wrote no end to it) and turn() whose move the run is; a session
whose turn was cut off and that owes a push, with nothing queued or on its way to it, quiet for stall_minutes, is told
to go on (RESUME, through brain-kit's outbox as a notice about the MR's head: sent once, settled as brain-kit settles
its own); once more if that turn is cut off too; then the MR hears once that a person should look. A session that
ended its turn without a push (brain-run's 「…不继续」 lines) or that someone stopped is left alone, and so is a run
whose newest answer the signer refused: it waits for a person. brain-kit's own watchdog is where this belongs
(BRAINKIT_REQUESTS 49).

A run the product accepted (brain-kit's _accepted) whose session is idle with its records pushed after (turn: DONE)
is done: the MR hears once that it can be merged and who merges it (the run's owner), and its label is MERGE_LABEL in
place of kit.json running_label. brain-kit's reconcile marks a run merged or closed and leaves the MR's labels: those
two are taken off once, within RECENT_S of the run's end. brain-kit has neither (BRAINKIT_REQUESTS 50).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Mapping

from .. import bk
from ..constants import MERGE_LABEL

RESUMES = 2             # messages to go on at one MR head, before a person is told
RECENT_S = 14 * 86400   # a run that ended longer ago is not looked at again for its labels
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
READY = ("%s 做完了：产品在提交 %s 上验收通过（%s，凭证 %s），运行机把记录也推上来了（提交 %s），以后不再改这个 MR。可以合并了：请 %s "
         "在 GitLab 上合并（审批和流水线照 GitLab 的规则）。")
RELABELLED = "标签换成了 %s，合并以后去掉。"
READY_KEY, UNLABELLED_KEY = "ready:%s", "unlabelled:%s"     # brain-kit's ledger notices, by run id


def accepted(decisions: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The run's acceptance, as brain-kit's DecisionLoop._accepted finds it: its newest acceptance decision that was
    delivered, when the product's answer was approve; else None. Lean closes the intent then."""
    acc = [d for d in decisions if d.get("kind") == "acceptance" and d.get("state") == "delivered"]
    d = max(acc, key=lambda x: str(x.get("created") or ""), default=None)
    return d if d is not None and d.get("answer") == "approve" else None


def turn(run: Mapping[str, Any], head: str, decisions: list[Mapping[str, Any]],
         outbox: list[Mapping[str, Any]]) -> str:
    """Whose move a dispatched run is at its MR's *head*, from brain-kit's ledger. OTHERS while a message is on its
    way to the session, a question at the head waits for a person or the bridge, or the newest question's answer was
    refused by the signer, whatever its head (the run was told it is blocked, and pushes that). SESSION when the newest
    message that reached the session is about this head (the hand-over, an answer, a notice: each asks for a push) and
    none came after, or when the head is a push of the session's the bridge read, with no question there, and the run
    is not accepted. DONE when the product accepted it and its records came after. OTHERS otherwise: the bridge has not
    read the head yet (an acceptance question waiting for CI, say)."""
    if any(o["state"] in IN_FLIGHT for o in outbox):
        return OTHERS
    at = [d for d in decisions if d.get("sha") == head]
    newest = max(decisions, key=lambda d: str(d.get("created") or ""), default=None)
    if any(d["state"] in ASKED for d in at) or (newest is not None and newest["state"] == "refused"):
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
        """Every dispatched run looked at, then the labels of every run that ended within RECENT_S. -> how many
        sessions were told to go on. A run whose slot or MR cannot be read holds up none of the others: the errors are
        raised together at the end."""
        common, led = bk.m("common"), self.host.ledger
        told, errors = 0, []
        since = common.iso(common.utcnow() - timedelta(seconds=RECENT_S))
        for run in led.runs_in("dispatched"):
            try:
                told += self._one(dict(run))
            except Exception as exc:  # noqa: BLE001 - KiroCrew, GitLab, the ledger: looked at again next round
                errors.append("%s: %s" % (run["run_id"], " ".join(str(exc).split())[:200]))
        for run in [r for r in led.runs_in("merged", "closed") if str(r["updated"]) >= since]:
            try:
                self._unlabel(dict(run))
            except Exception as exc:  # noqa: BLE001 - GitLab: looked at again next round
                errors.append("%s: %s" % (run["run_id"], " ".join(str(exc).split())[:200]))
        if errors:
            raise common.BrainError("not done this round: " + "; ".join(errors))
        return told

    def _one(self, run: dict[str, Any]) -> int:
        common, read = bk.m("common"), getattr(self.host.kc, "activity", None)
        if read is None or not run.get("slot"):
            return 0
        try:
            a = read(run["slot"])
        except common.BrainError:           # brain-kit's watchdog tells the MR once the slot stays unreadable
            return 0
        if not a or a["running"] or a.get("queued") or a["last_role"] == "permission":
            return 0
        db, rid = self.host.ledger.db, run["run_id"]
        outbox = [dict(r) for r in db.execute("SELECT send_id, what, state, sha, created FROM outbox WHERE run_id=?",
                                              (rid,))]
        if any(o["state"] in IN_FLIGHT for o in outbox):
            return 0
        decisions = [dict(r) for r in db.execute("SELECT id, sha, kind, state, answer, voucher_id, created FROM "
                                                 "decisions WHERE run_id=?", (rid,))]
        acc, stall = accepted(decisions), float(self.host.stall_s)
        due = acc is not None and not self._told(READY_KEY % rid)
        cut = a["cut"] and bool(stall) and a["last_at"] is not None and \
            (common.utcnow() - a["last_at"]).total_seconds() >= stall
        if not (due or cut):
            return 0
        mr = self.host.gl.mr(run["project"], run["iid"]) or {}
        head = str(mr.get("sha") or "")
        if mr.get("state") != "opened" or not head:
            return 0
        move = turn(run, head, decisions, outbox)
        if move == DONE and due:
            self._ready(run, mr, acc, head)
        elif move == SESSION and cut:
            return self._resume(run, head, a["last_at"].strftime("%Y-%m-%d %H:%M"), outbox)
        return 0

    def _told(self, key: str) -> bool:
        n = self.host.ledger.notice(key)
        return n is not None and n["state"] == "told"

    def _ready(self, run: dict[str, Any], mr: Mapping[str, Any], acc: Mapping[str, Any], head: str) -> None:
        """The MR's labels (MERGE_LABEL on, running_label off), then READY on it once. A label GitLab will not let the
        bridge's account change does not hold up the comment."""
        project, iid, rid = run["project"], int(run["iid"]), run["run_id"]
        running = str(self.host.kit(project).get("running_label") or "")
        labels = set(mr.get("labels") or [])
        add, remove = [MERGE_LABEL] if MERGE_LABEL not in labels else [], [running] if running in labels else []
        relabelled = self._labels(project, iid, add, remove) if add or remove else True
        owner = str(run.get("owner") or "")
        who = "@" + owner if bk.m("common").USER_RE.match(owner) else "需求的负责人"
        text = READY % (rid, str(acc["sha"])[:12], acc["id"], acc.get("voucher_id") or "?", head[:12], who)
        self.host.tell_mr_once(READY_KEY % rid, project, iid, text + (RELABELLED % MERGE_LABEL if relabelled else ""),
                               run_id=rid)

    def _unlabel(self, run: dict[str, Any]) -> None:
        """A run that ended (merged, closed): running_label and MERGE_LABEL off its MR, once. An MR GitLab does not
        show (deleted: 404) or will not let the bridge's account read (403) ends it too, said in the log."""
        project, iid, rid, led = run["project"], int(run["iid"]), run["run_id"], self.host.ledger
        key = UNLABELLED_KEY % rid
        if self._told(key) or not led.claim_notice(key, rid, project, iid):
            return
        try:
            labels = set((self.host.gl.mr(project, iid) or {}).get("labels") or [])
            gone = [x for x in (str(self.host.kit(project).get("running_label") or ""), MERGE_LABEL) if x in labels]
            if gone:
                self._labels(project, iid, [], gone)
        except bk.m("rest").HttpError as exc:
            if exc.status not in (403, 404):
                led.release_notice(key)
                raise
            self.out("%s: %s!%d: labels not taken off, and not tried again: GitLab answers %d (the MR is gone, or the "
                     "bridge's account may not read it)" % (rid, project, iid, exc.status))
        except BaseException:
            led.release_notice(key)
            raise
        led.notice_told(key)

    def _labels(self, project: str, iid: int, add: list[str], remove: list[str]) -> bool:
        """-> False when GitLab does not let the bridge's account change the MR's labels."""
        try:
            self.host.gl.edit_labels(project, iid, add=add, remove=remove)
        except bk.m("rest").HttpError as exc:
            if exc.status != 403:
                raise
            self.out("%s!%d: labels not changed: GitLab does not let the bridge's account change them" % (project, iid))
            return False
        return True

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
