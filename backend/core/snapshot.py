"""TeamSnapshot: what the team server writes into GitLab every few minutes (team/relay.py) and every person's app reads
with their own token (runner_view.Relays), one shape for both sides (ui/src/types.ts TeamSnapshot), each side parsing it
at its boundary: the writer what it built, before it writes; the reader what it read, before it shows it.

The shape is one table (SNAPSHOT): a field is str, int, float (an int too), bool, Opt(kind) (None too), Many(kind) (a
list), or a dict of fields. parse() keeps exactly those fields, every one checked, and drops any other (a newer writer's
addition within the same schema); a field missing or of another kind is Bad, and a schema other than SCHEMA is
OtherSchema. The parts:
  server     the app's version, team mode, its last round, and per project whether the runner takes its hand-overs
             (on), the work sources its kit.json switches on, and what went wrong with it this round, in words
  runner     its hand-over runs (the ledger's latest run of each requirement, as 运行机 lists them, with what each waits
             on), MR 巡检's and 测试环境报错's items as their views give them, and the brain-patrol sessions: cap, working
  delivery   every requirement of its projects, open or merged in the last 14 days, from GitLab only: its stage in
             交付总览's words, its MR with its CI, whether the staging VERSION holds its merge, what it waits on
Waits is what the app says a row waits on (deliveries.waits: who, the step, the sentence, since when) with *on*, the
GitLab users who take that step now ([]: nobody in particular, or no person), so a reader can put first what waits on
them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SCHEMA = 1


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


WAITS = {"who": str, "do": str, "text": str, "since": Opt(float), "on": Many(str)}
PROJECT = {"project": str, "on": bool, "sources": Many(str), "error": str}
SERVER = {"version": str, "team_mode": bool, "last_round_at": Opt(float), "projects": Many(PROJECT)}
HANDOVER = {"project": str, "iid": int, "mr_url": Opt(str), "key": str, "title": str, "run_id": str, "state": str,
            "owner": Opt(str), "waits": WAITS}
PATROL_MR = {"project": str, "iid": int, "title": str, "url": Opt(str), "author": str, "state": str,
             "problems": Many({"kind": str, "text": str}), "skip": str, "skip_code": str, "note": str, "attempts": int,
             "slot": Opt(str), "updated_at": Opt(str)}
LINK = {"iid": int, "url": Opt(str), "title": str}
STAGING_ERROR = {"project": str, "signature": str, "count": int, "state": str, "issue": Opt(LINK), "origin": Opt(LINK),
                 "fix_mr": Opt(LINK), "first_seen": float, "last_seen": float, "deployed_sha": str, "attempts": int,
                 "slot": Opt(str), "note": str}
RUNNER = {"handovers": Many(HANDOVER), "patrol": Many(PATROL_MR), "staging": Many(STAGING_ERROR), "cap": int,
          "working": int}
MR = {"iid": int, "url": Opt(str), "state": str, "ci": Opt(str)}
DELIVERY = {"project": str, "key": str, "title": str, "owner": Opt(str), "stage": str, "state": str,
            "branch": Opt(str), "mr": Opt(MR), "deployed": Opt(bool), "waits": WAITS, "updated_at": Opt(float)}
SNAPSHOT = {"schema": int, "generated_at": float, "server": SERVER, "runner": RUNNER, "delivery": Many(DELIVERY)}


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
        missing = [k for k in kind if k not in v]
        if missing:
            raise Bad("%s: missing %s" % (at or "/", ", ".join(missing)))
        return {k: _value(sub, v[k], "%s.%s" % (at, k) if at else k) for k, sub in kind.items()}
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
