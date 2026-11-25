"""Projects that left the runner: the runs a ledger still holds of a project bridge.json no longer lists.

A runner pointed at other projects (一键建运行机 run again without one, or bridge.json edited) keeps its ledgers, and
brain-kit's bridge goes through every open run of its ledger each round (watch, collect, reconcile: GitLab for each),
whatever bridge.json lists; the bridge's account may not see that project any more (a project token sees its own
project only), so every round failed on them. Leaving marks each open run of such a project with brain-kit's
``Ledger.set_run`` (compare-and-set: run again, nothing more happens): in the bridge's ledger failed, a run this
runner will not finish, so brain-kit closes its answers on its way; in the signer's closed, as the signer's own close
command does. Its open questions are closed the way brain-kit closes those of a run that ended (collect would read the
MR before it looked at the run), and its session is stopped when *stop* is given; a session that does not stop leaves
the run left all the same, and its caller says so (unstopped_said). The ledger file is copied first (``backup``).
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Callable, Iterable

from .. import bk

BRIDGE_TO, SIGNER_TO = "failed", "closed"
QUESTIONS = ("open", "posting", "asking")      # brain-kit's decision states its collect reads the MR for
UNSTOPPED = "%s 的会话 %s 没停下"
UNSTOPPED_TODO = "它可能还在 KiroCrew 里做：在 KiroCrew 里打开它，停掉"


def kept(projects: Iterable[Any]) -> set[str]:
    """bridge.json's (or signer.json's) projects, as paths compared without case (GitLab's paths have none)."""
    return {str(p["project"] if isinstance(p, dict) else p).lower() for p in projects}


def stray(ledger: Any, keep: set[str], signer: bool = False) -> list[dict[str, Any]]:
    """The open runs of *ledger* whose project is not in *keep*; the signer's reservations count as open runs."""
    states = tuple(bk.m("bridge.ledger").OPEN_RUN_STATES) + ((bk.m("bridge.ledger").RESERVED,) if signer else ())
    return [dict(r) for r in ledger.runs_in(*states) if str(r["project"]).lower() not in keep]


def backup(path: str) -> str:
    """A copy of the sqlite file *path* beside it, as one moment of it (another process may be writing): the copy's
    path."""
    to = "%s.left-%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
    fd = os.open(to, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    src, dst = sqlite3.connect(path), sqlite3.connect(to)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return to


def leave(ledger: Any, path: str, keep: set[str], to: str, *, signer: bool = False,
          stop: Callable[[str], Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Every open run in *ledger* (its file at *path*) of a project not in *keep* marked *to*, after a backup of the
    file: (the runs marked, the backup's path); ([], "") when there is none. A run whose session *stop* could not stop
    has stop_error, what stopping it raised."""
    runs = stray(ledger, keep, signer)
    if not runs:
        return [], ""
    copy = backup(path)
    now = bk.m("common").iso(bk.m("common").utcnow())
    marked = []
    for r in runs:
        if not ledger.set_run(r["run_id"], r["state"], to):
            continue                                    # it moved meanwhile (a round of the bridge): left as it is
        marked.append(r)
        if ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone():
            ledger.db.execute("UPDATE decisions SET state='closed', updated=? WHERE run_id=? AND state IN (%s)"
                              % ",".join("?" * len(QUESTIONS)), (now, r["run_id"]) + QUESTIONS)
        if stop is not None and r.get("slot") and r["state"] in ("dispatched", "send_unknown"):
            try:
                stop(str(r["slot"]))
            except Exception as exc:  # noqa: BLE001 - KiroCrew's, or a bug: the run is left either way
                r["stop_error"] = " ".join(str(exc).split())[:300] or type(exc).__name__
    return marked, copy


def unstopped_said(runs: Iterable[dict[str, Any]], why: bool = True) -> str:
    """The runs of *runs* whose session did not stop, each with its slot (and *why*: what stopping it raised), in one
    line; '' when none."""
    return "；".join(UNSTOPPED % (r["run_id"], r["slot"]) + ("（%s）" % r["stop_error"] if why else "")
                    for r in runs if r.get("stop_error"))


def leave_file(path: str, keep: set[str], to: str, *, signer: bool = False,
               stop: Callable[[str], Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """leave() on the ledger file *path* (brain-kit's Ledger opened on it for this, then closed); nothing when there
    is no such file."""
    if not os.path.isfile(path):
        return [], ""
    led = bk.m("bridge.ledger").Ledger(path)
    try:
        return leave(led, path, keep, to, signer=signer, stop=stop)
    finally:
        led.close()
