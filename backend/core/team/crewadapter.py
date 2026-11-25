"""brain-kit's KiroCrew client surface (bridge/kirocrew.py: create_slot, set_project, send, slot, activity, stop),
served by the app's own session runner instead of brain-kit's HTTP client: the runner's sessions are opened like every
other session of the app (gateway.py, the app token, sessionApproval), sit in the sidebar under 「Brain 运行机」, and
count their credits. MR 巡检 (team/patrol.py) has a surface of its own (open_patrol … patrol_stop) on its own slot
names, and 测试环境报错's fix sessions (team/staging.py) use it on theirs (staging-…): patrol_project and patrol_send
raise patrol.Refused when KiroCrew answered no (nothing went in), BrainError when it did not answer (a message may then
have landed).

The bridge loop runs in a worker thread and calls these synchronously; each call runs on the gateway's event loop
(``run_coroutine_threadsafe``) and waits for its answer. A failure is raised as brain-kit's BrainError, which the
bridge already handles (setup failures are retried, a send whose outcome is unknown is never repeated).

What a turn does: brain-kit's ``slot()`` answers the newest messages only, without ``running`` or the queue, so
brain-kit's bridge sees a send that landed but never one that did not. ``activity()`` is brain-kit's own (KiroCrew's
read of the slot with its newest row, through brain-kit's slot_activity), with ``queued`` and ``cut`` added:
brain-kit's stall watchdog (delivery.tell_if_stalled) reads it, and so does team/runwatch.py for a session whose turn
KiroCrew cut off before its push (cut_off). ``send()`` sets the slot to trust before each message, as brain-kit's
client does: KiroCrew keeps trust in memory, so a restart ends it, and nobody is at the runner to approve a tool call.
patrol_turn() answers from the session runner instead: every message sent through it is a turn it polls
(sessions.py), which ends once the slot is idle twice in a row with the reply in.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from typing import Any

from .. import bk
from ..constants import RUNNER_FOLDER
from ..gateway import GatewayError
from .patrol import Refused

SEND_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RUN_SLOT_RE = re.compile(r"^run-[a-z][a-z0-9]{0,9}-[1-9][0-9]{0,6}-[1-9][0-9]{0,5}$")
PATROL_SLOT_RE = re.compile(r"^(?:patrol|staging)-[1-9][0-9]{0,11}-[1-9][0-9]{0,6}(?:-[2-9]|-[1-9][0-9]{1,3})?$")
# KiroCrew's answer to an app without permissions.sessionApproval granted (or turned off) when it sets a slot to trust
APPROVAL_REFUSED = (403, "session_approval_not_granted")
# rows KiroCrew writes after a turn, not part of it: a compaction, the notice that its restart turned trust off
AFTER_TURN = (("assistant", "compaction"), ("system", "trust_reset_notice"))


def cut_off(rows: list[Any]) -> bool:
    """Did KiroCrew stop the newest turn of these rows (a slot's newest, oldest first) before it ended? KiroCrew ends a
    turn it finished with a reply that carries the turn's ``meta.turn_stats``, and one someone stopped with a system
    row ``{"kind": "stop_event"}``. A turn its restart cut off ends on whatever it wrote last (RUN-REQ-2-1: a tool
    call), one its error ended on that error (role ``error``). Looked past: the rows of AFTER_TURN, and the empty
    assistant row KiroCrew writes when a turn stops, a stop_event before it or not (its file_changes, no turn_stats).
    No rows: nothing was cut."""
    for m in reversed([r for r in rows if isinstance(r, dict)]):
        role, meta = m.get("role"), m.get("meta") if isinstance(m.get("meta"), dict) else {}
        if (role, meta.get("kind")) in AFTER_TURN:
            continue
        if role == "assistant":
            if "turn_stats" not in meta and not str(m.get("content") or "").strip():
                continue
            return "turn_stats" not in meta
        if role == "system":
            try:
                return (json.loads(str(m.get("content") or "")) or {}).get("kind") != "stop_event"
            except (ValueError, AttributeError):
                return True
        return True
    return False


class KiroCrewAdapter:
    def __init__(self, loop: asyncio.AbstractEventLoop, mgr: Any, brain_error: type, *, timeout: float = 90.0) -> None:
        self.loop, self.mgr, self.BrainError, self.timeout = loop, mgr, brain_error, timeout
        self.app = getattr(mgr, "app", "brain")

    def _run(self, coro: Any, refused: type[Exception] | None = None, only: tuple[int, str] | None = None) -> Any:
        """*coro*'s answer on the event loop; a failure as BrainError, or as *refused* when KiroCrew answered no (with
        *only*: that answer alone, its status and code)."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(self.timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise self.BrainError("KiroCrew %d 秒内没有回应" % self.timeout) from None
        except self.BrainError:
            raise
        except Exception as exc:  # noqa: BLE001 - GatewayError and friends: brain-kit only knows BrainError
            if refused is not None and isinstance(exc, GatewayError) and exc.refused \
                    and only in (None, (exc.status, exc.code)):
                raise refused("KiroCrew 没收下：%s" % str(exc)[:300]) from None
            raise self.BrainError("KiroCrew：%s" % str(exc)[:300]) from None

    def create_slot(self, name: str, agent: str, model: str, memory_mode: str = "incognito") -> dict[str, Any]:
        if not RUN_SLOT_RE.match(name or ""):
            raise self.BrainError("slot name %r is not a run id" % name)
        run_id = name.upper()
        return self._run(self.mgr.adapter_create(name, agent=agent, model=model or "", memory_mode=memory_mode,
                                                 title="%s · 运行机" % run_id, folder=run_id,
                                                 parent="%s-run:%s" % (self.app, run_id), key=run_id,
                                                 root_folder=RUNNER_FOLDER))

    def _ours(self, name: str) -> None:
        """A run's slot name only; the session runner itself refuses a slot it did not open for the bridge (checked on
        the event loop: the ledger's slot column is writable by the agents' OS user)."""
        if not RUN_SLOT_RE.match(name or ""):
            raise self.BrainError("slot name %r is not a run id" % name)

    def set_project(self, name: str, path: str) -> dict[str, Any]:
        self._ours(name)
        return self._run(self.mgr.adapter_set_project(name, path))

    def send(self, name: str, message: str, send_id: str) -> dict[str, Any]:
        """Trust for the slot, then the message. KiroCrew refusing the app sessionApproval (APPROVAL_REFUSED) is
        brain-kit's TrustRefused: its delivery holds the message and tells the MR to grant it. Any other failure of the
        trust call (a slot this app did not open for the bridge, one KiroCrew does not have, KiroCrew busy) is an
        ordinary error, which the delivery settles by reading the slot. Nothing is sent after either."""
        if not SEND_ID_RE.match(send_id or ""):
            raise self.BrainError("sendId must match [A-Za-z0-9_-]{1,128}")
        self._ours(name)
        self._run(self.mgr.adapter_trust(name), bk.m("bridge.kirocrew").TrustRefused, only=APPROVAL_REFUSED)
        return self._run(self.mgr.adapter_send(name, message, send_id))

    def slot(self, name: str) -> dict[str, Any]:
        self._ours(name)
        return self._run(self.mgr.adapter_slot(name))

    def activity(self, name: str) -> dict[str, Any] | None:
        """brain-kit's slot_activity of the slot's newest row (running, last_at, last_role, last_id); queued: a
        message waits behind the turn, or a tool call for a click; cut: KiroCrew stopped its newest turn before it
        ended (cut_off), and nobody stopped it in the app. None when KiroCrew's read does not say."""
        self._ours(name)
        data = self._run(self.mgr.adapter_activity(name))
        a = bk.m("bridge.kirocrew").slot_activity(data)
        if a is not None:
            a["queued"] = bool(data.get("queue") or data.get("queue_depth") or data.get("pending_approval"))
            a["cut"] = cut_off(data.get("messages") or []) and not data.get("user_stopped")
        return a

    def stop(self, name: str) -> dict[str, Any]:
        self._ours(name)
        return self._run(self.mgr.adapter_stop(name))

    # ---- MR 巡检's sessions: patrol slot names only, never a run's ------------------------------------------ #

    def _patrol(self, name: str) -> None:
        if not PATROL_SLOT_RE.match(name or ""):
            raise self.BrainError("slot name %r is not a patrol slot" % name)

    def open_patrol(self, name: str, agent: str, model: str, label: str) -> None:
        """The slot *name* booting the template *agent*, in the folder of the MR *label* (grp/repo!17) under
        「Brain 运行机」; no message yet."""
        self._patrol(name)
        self._run(self.mgr.adapter_create(name, agent=agent, model=model or "", memory_mode="incognito",
                                          title="%s 巡检 · 运行机" % label, folder=("巡检 " + label)[:60],
                                          parent="%s-patrol:%s" % (self.app, label), key="patrol:" + label,
                                          root_folder=RUNNER_FOLDER))

    def patrol_project(self, name: str, path: str) -> None:
        """Its project directory, and trust (set again before each message: a KiroCrew restart ends it)."""
        self._patrol(name)
        self._run(self.mgr.adapter_set_project(name, path), Refused)

    def patrol_send(self, name: str, message: str, send_id: str) -> None:
        if not SEND_ID_RE.match(send_id or ""):
            raise self.BrainError("sendId must match [A-Za-z0-9_-]{1,128}")
        self._patrol(name)
        self._run(self.mgr.adapter_send(name, message, send_id), Refused)

    def patrol_turn(self, name: str, send_id: str) -> str:
        """running | ended | failed | unknown: the turn the message *send_id* started (sessions.adapter_turn)."""
        self._patrol(name)
        return str(self._run(self.mgr.adapter_turn(name, send_id)))

    def patrol_stop(self, name: str) -> None:
        self._patrol(name)
        self._run(self.mgr.adapter_stop(name))
