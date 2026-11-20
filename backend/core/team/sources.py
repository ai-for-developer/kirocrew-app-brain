"""The kinds of work the team runner takes (SOURCES), and the one claim record each kind makes of what it works on.

kit.json ``work_sources`` on a project's default branch switches kinds on for that project (brain-kit's Bridge.kit
reads it; brain-kit's checks let a key it does not know through). A project that does not name it takes DEFAULT, the
hand-over alone: the bridge's round as it was.

Before a kind dispatches anything it turns the GitLab object into a Claim. Its key is the requirement key when the work
has one (a Brain branch ai/<owner>/<KEY>-<slug>, a hand-over's run), else ``<kind>:<project>:<type>!<iid>``. A kind
does not dispatch work whose key another kind holds, so work is claimed once. What a kind holds is read from its own
ledger (Source.held) and merged only here. mr-handoff is brain-kit's bridge, which asks no one: it holds the
requirement keys of its open runs, and every other kind yields to it. issue-label (team/issues.py) holds an issue from
its label until its run starts; from then on the run is the bridge's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Mapping, TypedDict

from .. import bk

HANDOFF, PATROL, ISSUE = "mr-handoff", "mr-patrol", "issue-label"
# GitLab's access levels (a member's role in a project) by number, as Claim.role names them
LEVEL_NAME = {10: "Guest", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}
MAINTAINER, DEVELOPER, OWNER = 40, 30, 50


class Ref(TypedDict):
    type: Literal["mr", "issue"]
    iid: int


class Claim(TypedDict):
    key: str
    kind: str
    project: str
    link: str | None
    asked_by: str
    role: str               # asked_by's GitLab role in the project, when the kind reads it ("" when it does not)
    title: str
    ref: Ref


def claim_key(kind: str, project: str, ref: Ref, req: str | None = None) -> str:
    return req or "%s:%s:%s!%d" % (kind, project, ref["type"], ref["iid"])


def make_claim(kind: str, project: str, ref: Ref, *, link: str | None, asked_by: str, title: str,
               req: str | None = None, role: str = "") -> Claim:
    return {"key": claim_key(kind, project, ref, req), "kind": kind, "project": project, "link": link,
            "asked_by": asked_by, "role": role, "title": title, "ref": ref}


def mr_claim(kind: str, project: str, mr: Mapping[str, Any], branch_prefix: str) -> Claim:
    """An MR (GitLab's MR object) as *kind*'s claim: keyed by its requirement when its source branch is a Brain one."""
    b = bk.m("common").parse_branch(str(mr.get("source_branch") or ""), branch_prefix)
    author = mr.get("author") if isinstance(mr.get("author"), dict) else {}
    return make_claim(kind, project, {"type": "mr", "iid": int(mr["iid"])}, link=mr.get("web_url") or None,
                      asked_by=str(author.get("username") or ""), title=str(mr.get("title") or ""),
                      req=b["key"] if b else None)


@dataclass(frozen=True)
class Source:
    kind: str
    words: str                              # what the page calls it
    held: Callable[[Any], list[Claim]]      # what it holds now, read from its own ledger (arg: the TeamRuntime)


def _handoff_held(rt: Any) -> list[Claim]:
    """The bridge's open runs (brain-kit's ledger: reserved, queued for a workspace or at work), by requirement."""
    host = getattr(rt, "host", None)
    if host is None:
        return []
    led = bk.m("bridge.ledger")
    return [make_claim(HANDOFF, str(r["project"]), {"type": "mr", "iid": int(r["iid"])},
                       link=rt.mr_url(r["project"], r["iid"]), asked_by=str(r["owner"] or ""), title=str(r["run_id"]),
                       req=str(r["req"]))
            for r in host.ledger.runs_in(*(tuple(led.OPEN_RUN_STATES) + (led.RESERVED,)))]


def _patrol_held(rt: Any) -> list[Claim]:
    p = getattr(rt, "patrol", None)
    return p.held() if p is not None else []


def _issues_held(rt: Any) -> list[Claim]:
    s = getattr(rt, "issues", None)
    return s.held() if s is not None else []


SOURCES: dict[str, Source] = {s.kind: s for s in (
    Source(HANDOFF, "交接过来的 MR", _handoff_held),
    Source(PATROL, "巡检开着的 MR", _patrol_held),
    Source(ISSUE, "打了标签的 issue", _issues_held),
)}
DEFAULT: tuple[str, ...] = (HANDOFF,)


def enabled(kit: Mapping[str, Any]) -> tuple[str, ...]:
    """The kinds kit.json's work_sources switches on: the ones SOURCES has, each once, in its order. DEFAULT when it
    names no list."""
    raw = kit.get("work_sources")
    if not isinstance(raw, list):
        return DEFAULT
    return tuple(dict.fromkeys(k for k in raw if isinstance(k, str) and k in SOURCES))


def held(rt: Any) -> list[Claim]:
    """Every kind's claims, as each kind's own ledger has them now."""
    return [c for s in SOURCES.values() for c in s.held(rt)]


def holder(c: Claim, claims: Iterable[Claim]) -> Claim | None:
    """Another kind's claim on *c*'s key, or None: *c* is free for its kind."""
    return next((x for x in claims if x["key"] == c["key"] and x["kind"] != c["kind"]), None)
