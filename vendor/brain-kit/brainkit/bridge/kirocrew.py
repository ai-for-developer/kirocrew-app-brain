"""KiroCrew gateway client for the bridge app (loopback only; app token from the app secret).

Endpoints read from KiroCrew 0.11.2 source (kiro_crew/dashboard): POST /api/apps/<app>/token with
X-App-Secret; POST /api/chat/slots; POST /api/chat/slots/<slot>/project; POST /api/chat?ws=1;
GET /api/chat/slots/<slot>; POST /api/chat/slots/<slot>/stop. Messages sent this way are recorded as
turnActor=app. Field-level behaviour is still to be confirmed on the runner (V1).
Read in KiroCrew 0.12.0 (kiro_crew/shared_crew/send_race.py, dashboard/chat_api/slot_detail.py): a send is NOT
deduplicated by meta.sendId (it is only stored on the user row); a retry with the same Idempotency-Key header is a
replay, in memory, while that KiroCrew process runs. GET /api/chat/slots/<slot> without a limit returns every message
(each user row with its meta.sendId), "running" and the "queue": the bridge reads it to tell whether a send landed.
Read in KiroCrew 0.13.0-rc.1 (dashboard/chat_api/slot_approval.py, chat_trust_restart.py): POST /api/chat/mode {"mode":
"trust", "slot"} lets the slot's tool calls run without a person's click. An app may set it only on a slot it opened,
and only with permissions.sessionApproval in its app.json, which the owner agreed to (else 403
session_approval_not_granted). Only the dashboard records that consent (cli_commands_app.py): `kirocrew app install`
leaves such an app off, and `kirocrew app enable` refuses it. KiroCrew keeps trust in memory: a restart ends it, and
the app driving the slot sets it again, so the bridge sets it before each message.
"""
from __future__ import annotations

import json
import re

from ..common import BrainError
from ..rest import HttpError, Rest, gl_time, q

SEND_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


# rows KiroCrew writes after a turn, not part of it: a compaction, the notice that its restart turned trust off
AFTER_TURN = (("assistant", "compaction"), ("system", "trust_reset_notice"))
ACTIVITY_ROWS = 20          # the newest rows activity() reads: enough to look past the rows written after a turn
# how a slot's newest turn ended (turn_end)
TURN_DONE, TURN_STOPPED, TURN_CUT = "done", "stopped", "cut"


def turn_end(rows) -> str:
    """How the newest turn of a slot's rows (oldest first) ended: TURN_DONE (KiroCrew finished it: its reply carries
    meta.turn_stats), TURN_STOPPED (someone stopped it: a system row {"kind": "stop_event"}), TURN_CUT (KiroCrew wrote
    no end to it: a restart ends it on whatever it wrote last, RUN-REQ-2-1 a tool call; an error on a row of role
    error), or "" (no rows). Looked past: the rows of AFTER_TURN, and the empty assistant row KiroCrew writes when a
    turn stops (its file_changes, no turn_stats)."""
    for m in reversed([r for r in rows or [] if isinstance(r, dict)]):
        role, meta = m.get("role"), m.get("meta") if isinstance(m.get("meta"), dict) else {}
        if (role, meta.get("kind")) in AFTER_TURN:
            continue
        if role == "assistant":
            if "turn_stats" not in meta and not str(m.get("content") or "").strip():
                continue
            return TURN_DONE if "turn_stats" in meta else TURN_CUT
        if role == "system":
            try:
                kind = (json.loads(str(m.get("content") or "")) or {}).get("kind")
            except (ValueError, AttributeError):
                kind = None
            return TURN_STOPPED if kind == "stop_event" else TURN_CUT
        return TURN_CUT
    return ""


def _last_said(rows) -> str:
    """The last line the newest reply that ended a turn says (at most 200 characters)."""
    for m in reversed([r for r in rows or [] if isinstance(r, dict)]):
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        if m.get("role") == "assistant" and "turn_stats" in meta:
            lines = [l.strip() for l in str(m.get("content") or "").splitlines() if l.strip()]
            return lines[-1][:200] if lines else ""
    return ""


def slot_activity(data):
    """{"running": a turn is in flight, "last_at": when its newest row was written (aware UTC, or None), "last_role"
    ("permission": a tool call waits for a click), "last_id", "queued": a message waits behind the turn, or a tool call
    for a click, "end": how its newest turn ended (turn_end; TURN_STOPPED too when the brain app's session runner says
    a person stopped it, user_stopped), "cut": end is TURN_CUT, "said": the last line of the reply that ended it} from a
    slot read, or None when it does not say."""
    if not isinstance(data, dict) or not isinstance(data.get("running"), bool):
        return None
    rows = [m for m in data.get("messages") or [] if isinstance(m, dict)]
    last = rows[-1] if rows else {}
    meta = last.get("meta") if isinstance(last.get("meta"), dict) else {}
    end = TURN_STOPPED if data.get("user_stopped") else turn_end(rows)
    return {"running": data["running"], "last_at": gl_time(last.get("ts")), "last_role": str(last.get("role") or ""),
            "last_id": str(meta.get("mid") or last.get("ts") or ""),
            "queued": bool(data.get("queue") or data.get("queue_depth") or data.get("pending_approval")),
            "end": end, "cut": end == TURN_CUT, "said": _last_said(rows) if end == TURN_DONE else ""}


# KiroCrew 0.13's `cause` of 403 session_approval_not_granted (dashboard/chat_api/slot_approval.py): not_declared, the
# installed app.json lacks permissions.sessionApproval (an update is needed); consent_pending, nobody agreed to it on
# the dashboard yet; disabled, the app is turned off; unavailable, KiroCrew cannot tell now (try again)
TRUST_CAUSES = ("not_declared", "consent_pending", "disabled", "unavailable")


class TrustRefused(BrainError):
    """KiroCrew would not set a run's slot to trust: the bridge's app may not (permissions.sessionApproval). `cause`:
    KiroCrew's reason, one of TRUST_CAUSES, or "" when it gave none (before 0.13, or a client that does not say)."""

    def __init__(self, msg="", cause=""):
        super().__init__(msg)
        self.cause = cause if cause in TRUST_CAUSES else ""


class KiroCrew:
    def __init__(self, base, app, secret, transport=None):
        if not re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", base or ""):
            raise BrainError("kirocrew.base must be a loopback URL (the dashboard is never exposed): %r" % base)
        self.rest = Rest(base, transport=transport)
        self.app = app
        self.secret = secret
        self._token = None

    def _tok(self):
        if not self._token:
            status, payload = self.rest.transport("POST", self.rest.base + "/api/apps/%s/token" % q(self.app),
                                                  {"X-App-Secret": self.secret}, b"")
            if status != 200:
                raise BrainError("KiroCrew app token exchange failed: HTTP %d" % status)
            try:
                tok = (json.loads(payload.decode("utf-8")) or {}).get("token")
            except ValueError:
                tok = None
            if not tok:
                raise BrainError("KiroCrew app token exchange returned no token")
            self._token = tok
        return self._token

    def _call(self, method, path, body=None, query=None, headers=None):
        for attempt in (1, 2):
            qq = dict(query or {})
            qq["token"] = self._tok()
            try:
                return self.rest.call(method, path, query=qq, body=body, ok=(200, 201, 202, 204), headers=headers)
            except HttpError as e:
                if e.status in (401, 403) and attempt == 1:
                    self._token = None  # expired (5 min for ?token=) or gateway restarted
                    continue
                raise

    def create_slot(self, name, agent, model, memory_mode="incognito"):
        return self._call("POST", "/api/chat/slots",
                          body={"name": name, "agent": agent, "model": model, "memory_mode": memory_mode})

    def set_project(self, name, path):
        """The slot's project, then trust for it: nobody is at the runner to approve a tool call (the app does the
        same for the runner sessions it opens)."""
        out = self._call("POST", "/api/chat/slots/%s/project" % q(name), body={"project": path})
        self.trust(name)
        return out

    def trust(self, name):
        try:
            return self._call("POST", "/api/chat/mode", body={"mode": "trust", "slot": name})
        except HttpError as e:
            if e.status == 403 and "session_approval_not_granted" in str(e):
                m = re.search(r'"cause"\s*:\s*"([a-z_]+)"', str(e))
                cause = m.group(1) if m else ""
                raise TrustRefused("KiroCrew refuses to set slot %s to trust: app %s needs permissions.sessionApproval "
                                   "(cause: %s)" % (name, self.app, cause or "not given"), cause=cause)
            raise

    def send(self, name, message, send_id, idempotency_key=None):
        if not SEND_ID_RE.match(send_id):
            raise BrainError("sendId must match [A-Za-z0-9_-]{1,128}")
        self.trust(name)            # a KiroCrew restart since the last message ended it
        # KiroCrew does not deduplicate by meta.sendId; its Idempotency-Key makes a retry of the same send (one
        # KiroCrew process) a replay. The bridge also reads the slot before it sends a message again (delivery.py).
        key = idempotency_key or send_id
        return self._call("POST", "/api/chat", query={"ws": "1"},
                          body={"slot": name, "message": message, "meta": {"sendId": send_id}},
                          headers={"Idempotency-Key": key} if len(key) >= 8 else None)

    def send_again(self, name, message, send_id, attempt):
        """The same message (same meta.sendId) once more, after the slot showed that the earlier send did not land.
        It gets its own Idempotency-Key: under the first one KiroCrew would replay the earlier send's answer (an
        error) instead of sending it."""
        return self.send(name, message, send_id, idempotency_key="%s-r%d" % (send_id, int(attempt)))

    def slot(self, name):
        return self._call("GET", "/api/chat/slots/%s" % q(name))

    def activity(self, name):
        """What the slot is doing, from its newest rows (?limit=ACTIVITY_ROWS): see slot_activity."""
        return slot_activity(self._call("GET", "/api/chat/slots/%s" % q(name), query={"limit": ACTIVITY_ROWS}))

    def stop(self, name):
        return self._call("POST", "/api/chat/slots/%s/stop" % q(name), body={})
