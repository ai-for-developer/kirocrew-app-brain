"""TeamSnapshot: what the team server writes into GitLab every few minutes (team/relay.py), one for each of its projects
in that project itself, on the project's own branch brain/status, and what every person's app reads with their own
token (runner_view.Runners), one shape for both sides (ui/src/types.ts TeamSnapshot), each side parsing it at its
boundary: the writer what it built, before it writes; the reader what it read, before it shows it. GitLab decides who
reads it: whoever can read a project's code reads its status, and nobody else; a person's app puts together the
projects they can read.

The shape is one table (SNAPSHOT): a field is str, int, float (an int too), bool, Opt(kind) (None too), Many(kind) (a
list), Added(kind, empty) (a field added within the schema: an older writer's snapshot has none, read as empty), or a
dict of fields. parse() keeps exactly those fields, every one checked, and drops any other (a newer writer's addition
within the same schema); a field missing or of another kind is Bad, and a schema other than SCHEMA is OtherSchema. The
parts:
  project    the project it is the status of (the one it is in)
  server     the team server, the same in every project's: the app's version, team mode, its last round, and the
             brain-patrol sessions MR 巡检 and 测试环境报错 share (cap, working), each one's (held: what its card on
             运行机 counts)
  state      the project as the round left it: whether the runner takes its hand-overs (on), the work sources its
             kit.json switches on, the label 从 issue 接活 takes there ('' where it is off), and what went wrong with
             it, in words
  runner     its hand-over runs (the ledger's latest run of each requirement, as 运行机 lists them, with what each waits
             on), MR 巡检's and 测试环境报错's items of the project as their views give them, each one's sessions in the
             project that no item shows at work, with what holds each (patrol_hidden, staging_hidden: their views'
             hidden), and the labelled issues 从 issue 接活 has not started a run of yet, with why (its view's items: on
             their way, or refused)
  delivery   every requirement of the project, open or merged or closed in the last 14 days, from GitLab only: its stage
             in 交付总览's words, its MR with its CI, whether the staging VERSION holds its merge, what it waits on;
             delivery_at: when those rows were read from GitLab whole (a round that ran out of time carries the last)
Waits is what the app says a row waits on (deliveries.waits: who, the step, the sentence, since when) with *on*, the
GitLab users who take that step now ([]: nobody in particular, or no person), so a reader can put first what waits on
them.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

SCHEMA = 1
FILE, BRANCH = "snapshot.json", "brain/status"      # where it is in each project: a branch of its own, no parent
SKIP_CI = "[skip ci] [ci-skip]"     # in every commit on BRANCH: GitLab CI's marker, and Jenkins' gitlab-plugin's
FLOOR_S = 5 * 60        # the team server builds one, and writes it when it changed, at most this often
HEARTBEAT_S = 30 * 60   # and writes an unchanged one again this old, to say it is alive
# one this old says the team server may have stopped: two heartbeats missed, each found up to a floor late (it is only
# looked at when a snapshot is built), and a floor more for the round, the read and the clocks of two machines
STALE_S = 2 * (HEARTBEAT_S + FLOOR_S) + FLOOR_S


class Bad(ValueError):
    """Not a TeamSnapshot: where, and what is wrong there."""


class OtherSchema(Bad):
    """A TeamSnapshot of another schema: a newer (or older) team server's."""

    def __init__(self, schema: Any) -> None:
        super().__init__("schema %r, not %d" % (schema, SCHEMA))
        self.schema = schema


@dataclass(frozen=True)
class Opt:
    kind: Any


@dataclass(frozen=True)
class Many:
    kind: Any


@dataclass(frozen=True)
class Added:
    """A field added within SCHEMA: a snapshot an older team server wrote has none, read as *empty*."""
    kind: Any
    empty: Any


WAITS = {"who": str, "do": str, "text": str, "since": Opt(float), "on": Many(str)}
SERVER = {"version": str, "team_mode": bool, "last_round_at": Opt(float), "cap": int, "working": int,
          "held": Added(Opt({"patrol": int, "staging": int}), None)}
STATE = {"on": bool, "sources": Many(str), "error": str, "issue_label": Added(str, "")}
HANDOVER = {"project": str, "iid": int, "mr_url": Opt(str), "key": str, "title": str, "run_id": str, "state": str,
            "owner": Opt(str), "waits": WAITS}
PATROL_MR = {"project": str, "iid": int, "title": str, "url": Opt(str), "author": str, "state": str,
             "problems": Many({"kind": str, "text": str}), "skip": str, "skip_code": str, "note": str, "attempts": int,
             "slot": Opt(str), "updated_at": Opt(str)}
LINK = {"iid": int, "url": Opt(str), "title": str}
STAGING_ERROR = {"project": str, "signature": str, "count": int, "state": str, "issue": Opt(LINK), "origin": Opt(LINK),
                 "fix_mr": Opt(LINK), "first_seen": float, "last_seen": float, "deployed_sha": str, "attempts": int,
                 "slot": Opt(str), "note": str}
ISSUE = {"project": str, "iid": int, "title": str, "url": Opt(str), "labeler": str, "role": str, "state": str,
         "key": Opt(str), "mr_iid": Opt(int), "mr_url": Opt(str), "run_id": Opt(str), "reason": str, "note": str}
HELD = {"project": str, "text": str}
RUNNER = {"handovers": Many(HANDOVER), "patrol": Many(PATROL_MR), "staging": Many(STAGING_ERROR),
          "issues": Added(Many(ISSUE), []), "patrol_hidden": Added(Many(HELD), []),
          "staging_hidden": Added(Many(HELD), [])}
MR = {"iid": int, "url": Opt(str), "state": str, "ci": Opt(str)}
DELIVERY = {"project": str, "key": str, "title": str, "owner": Opt(str), "stage": str, "state": str,
            "branch": Opt(str), "mr": Opt(MR), "deployed": Opt(bool), "waits": WAITS, "updated_at": Opt(float)}
SNAPSHOT = {"schema": int, "generated_at": float, "project": str, "server": SERVER, "state": STATE, "runner": RUNNER,
            "delivery": Many(DELIVERY), "delivery_at": Opt(float)}


def parse(raw: Any) -> dict[str, Any]:
    """A TeamSnapshot from its JSON (bytes or text) or the object loaded from it: SNAPSHOT's fields, each checked,
    nothing else. OtherSchema for another schema, Bad for anything else that is not one."""
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise Bad("not JSON: %s" % exc) from None
    if not isinstance(raw, dict):
        raise Bad("not an object")
    if raw.get("schema") != SCHEMA or isinstance(raw.get("schema"), bool):
        raise OtherSchema(raw.get("schema"))
    return _value(SNAPSHOT, raw, "")


def dump(snap: dict[str, Any]) -> str:
    """The text snapshot.json holds: keys sorted, so the same snapshot is the same text."""
    return json.dumps(snap, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def _value(kind: Any, v: Any, at: str) -> Any:
    if isinstance(kind, dict):
        if not isinstance(v, dict):
            raise Bad("%s: want an object" % (at or "/"))
        missing = [k for k, sub in kind.items() if k not in v and not isinstance(sub, Added)]
        if missing:
            raise Bad("%s: missing %s" % (at or "/", ", ".join(missing)))
        return {k: _value(sub, v[k], "%s.%s" % (at, k) if at else k) if k in v else copy.deepcopy(sub.empty)
                for k, sub in kind.items()}
    if isinstance(kind, Added):
        return _value(kind.kind, v, at)
    if isinstance(kind, Opt):
        return None if v is None else _value(kind.kind, v, at)
    if isinstance(kind, Many):
        if not isinstance(v, list):
            raise Bad("%s: want a list" % at)
        return [_value(kind.kind, x, "%s[%d]" % (at, i)) for i, x in enumerate(v)]
    if kind is bool:
        if not isinstance(v, bool):
            raise Bad("%s: want true or false" % at)
        return v
    if kind is int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise Bad("%s: want a whole number" % at)
        return v
    if kind is float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise Bad("%s: want a number" % at)
        return float(v)
    if kind is str:
        if not isinstance(v, str):
            raise Bad("%s: want text" % at)
        return v
    raise TypeError("no such field kind: %r" % (kind,))
