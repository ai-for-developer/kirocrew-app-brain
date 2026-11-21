"""The run record: `runs/<KEY>/run.json`, committed on the run branch.

It carries numbers and hashes only (no requirement text). Lean's
`aidlc-docs/<intent>/aidlc-state.md` is the hand-over note; run.json points to it.
KEY is a Jira key (tracker "jira") or a claimed <req_prefix>-<n> (tracker "none").

A split child's run (split.start_child) continues its parent's Lean intent and adds, all optional for other runs:
  parent         the parent's key            unit          its unit in the parent's unit-of-work.md
  target_branch  where its MR goes: the parent's run branch
  lean_team      Lean's team in its worktree (the unit)     lean_clone  the random part of that Lean clone's id
                 (its ledger file is aidlc-docs/audit/<host>-<lean_clone>.jsonl)
  built_on       a dependency whose branch it started from, before that one merged (`brain start --on`)
  mr             {requested_at, by, local_head}: `brain handoff` asked to merge it into target_branch. The child
                 counts as merged once this record and its branch tip are on the parent's branch (intents.child_merged)

A fast run (flows.FAST) has "mode": "fast" and "intent": null: no Lean intent, never handed over, never a split child.
A Lean run's record has no "mode" key, as every run before fast mode had.
"""
from __future__ import annotations

import os
import re

from . import flows
from .common import JIRA_RE, SHA_RE, USER_RE, BrainError, parse_branch

SCHEMA = 1
ORIGINS = ("local", "remote")
PKG_RE = re.compile(r"^PKG-[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}-[1-9][0-9]{0,4}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
INTENT_RE = re.compile(r"^[0-9]{6}-[a-z0-9][a-z0-9-]{0,80}$")   # Lean: yymmdd-slug
PATH_RE = re.compile(r"^[A-Za-z0-9_./*-][A-Za-z0-9_./* -]{0,199}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")      # Lean's UNIT_NAME_RE
TEAM_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")                     # a team as `lean init --team` stores it
CLONE_RE = re.compile(r"^[0-9a-f]{6}$")
CHILD_KEYS = ("parent", "unit", "target_branch", "lean_team", "lean_clone", "built_on", "mr")   # a split child's


MAX_PATHS = 50


def path(runs_dir: str, key: str) -> str:
    return os.path.join(runs_dir, key, "run.json")


def path_problem(p) -> str:
    """Why `p` cannot be one of expected_paths (the rule validate applies), or ''."""
    if isinstance(p, str) and PATH_RE.match(p) and ".." not in p:
        return ""
    return ("%r is not a path run.json can hold: ASCII letters, digits, _ . / * - and spaces only (no @ ( ) [ ] + or "
            "non-ASCII characters; write * in their place, e.g. packages/*acme/pay), no '..', at most 200 characters"
            % (p,))


def package_key(pkg) -> str:
    """The requirement key a material package id PKG-<KEY>-<n> is for ('' when it is not one)."""
    return pkg[len("PKG-"):].rsplit("-", 1)[0] if PKG_RE.match(str(pkg or "")) else ""


def new(key, owner, branch, intent, brain_commit, kit_version, expected_paths, created_at, claim_checked,
        tracker="none", materials=(), flow=flows.DEFAULT):
    """A new run record. "mode" is written only for a flow other than flows.DEFAULT, so a Lean run.json is what it
    was before fast mode."""
    rec = {
        "schema": SCHEMA,
        "key": key,
        "tracker": tracker,
        "owner": owner,
        "branch": branch,
        "intent": intent,
        "brain_commit": brain_commit,
        "kit_version": kit_version,
        "origin": "local",
        "expected_paths": list(expected_paths),
        "materials": list(materials),
        "created_at": created_at,
        "claim_checked": bool(claim_checked),
        "handoff": None,
    }
    if flow is not flows.DEFAULT:
        rec["mode"] = flow.value
    return rec


def validate(rec, branch=None, prefix="ai") -> list:
    """Problems with a run record; with `branch`, also check it matches the branch name."""
    if not isinstance(rec, dict):
        return ["run.json is not an object"]
    p = []
    if rec.get("schema") != SCHEMA:
        p.append("schema must be %d" % SCHEMA)
    if not JIRA_RE.match(str(rec.get("key", ""))):
        p.append("key is not like OMS-12")
    if not USER_RE.match(str(rec.get("owner", ""))):
        p.append("owner is not a username")
    if not SHA_RE.match(str(rec.get("brain_commit", ""))):
        p.append("brain_commit is not a full commit sha")
    p += flow_problems(rec)
    if rec.get("origin") not in ORIGINS:
        p.append("origin must be one of %s" % (ORIGINS,))
    eps = rec.get("expected_paths", [])
    if not isinstance(eps, list) or len(eps) > MAX_PATHS or any(path_problem(x) for x in eps):
        p.append("expected_paths must be a list of up to 50 repo paths")
    mats = rec.get("materials", [])
    if not isinstance(mats, list):
        p.append("materials must be a list")
    else:
        for m in mats:
            if not isinstance(m, dict) or not PKG_RE.match(str(m.get("package", ""))) \
                    or not HEX64_RE.match(str(m.get("manifest_sha256", ""))):
                p.append("materials entries need package PKG-<KEY>-<n> and manifest_sha256")
            elif not m["package"].startswith("PKG-%s-" % rec.get("key")):
                p.append("material %s is not for %s" % (m["package"], rec.get("key")))
    b = parse_branch(str(rec.get("branch", "")), prefix)
    if not b:
        p.append("branch is not %s/<owner>/<KEY>-<slug>" % prefix)
    elif b["owner"] != rec.get("owner") or b["key"] != rec.get("key"):
        p.append("branch does not match owner/key")
    if branch is not None and rec.get("branch") != branch:
        p.append("run.json is for branch %r, not %r" % (rec.get("branch"), branch))
    p += child_problems(rec)
    h = rec.get("handoff")
    if h is not None:
        if not isinstance(h, dict) or not h.get("requested_at") or h.get("by") != rec.get("owner"):
            p.append("handoff must have requested_at and by == owner")
    return p


def flow_problems(rec) -> list:
    """The record's flow rules. A Lean run names its intent folder (INTENT_RE: the intent is copied into the runner's
    message and the app's kickoff, so nothing looser gets in). A run without an intent has intent null, is no split
    child (a child continues its parent's intent), and has no hand-over when its flow has none."""
    try:
        flow = flows.of(rec)
    except BrainError as e:
        return [str(e)]
    if flow.intent:
        return [] if INTENT_RE.match(str(rec.get("intent", ""))) else [
            "intent must be a Lean intent folder name (yymmdd-slug)"]
    p = []
    if rec.get("intent", "") is not None:
        p.append("a %s run has no Lean intent: intent must be null" % flow.value)
    if any(k in rec for k in CHILD_KEYS):
        p.append("a %s run is not part of a split: it has no %s" % (flow.value, ", ".join(
            k for k in CHILD_KEYS if k in rec)))
    if flows.Action.HANDOFF not in flow.actions and rec.get("handoff") is not None:
        p.append("a %s run is not handed over to the team runner: handoff must be null" % flow.value)
    return p


def child_problems(rec) -> list:
    """The split-child fields of a run record (none of them: not a child, nothing to check)."""
    p = []
    if not any(k in rec for k in CHILD_KEYS):
        return p
    if not JIRA_RE.match(str(rec.get("parent", ""))) or rec.get("parent") == rec.get("key"):
        p.append("parent must be another requirement key")
    if not UNIT_RE.match(str(rec.get("unit", ""))):
        p.append("unit must be a Lean unit name")
    t = parse_branch(str(rec.get("target_branch", "")), str(rec.get("target_branch", "")).split("/", 1)[0])
    if not t or t["key"] != rec.get("parent"):
        p.append("target_branch must be the parent's run branch <prefix>/<owner>/<parent>-<slug>")
    if "lean_team" in rec and not TEAM_RE.match(str(rec["lean_team"])):
        p.append("lean_team must be a Lean team name (letters, digits, _)")
    if "lean_clone" in rec and not CLONE_RE.match(str(rec["lean_clone"])):
        p.append("lean_clone must be 6 hex digits")
    if "built_on" in rec and (not JIRA_RE.match(str(rec["built_on"])) or rec["built_on"] in (rec.get("key"),
                                                                                            rec.get("parent"))):
        p.append("built_on must be a sibling's requirement key")
    m = rec.get("mr")
    if m is not None and (not isinstance(m, dict) or not isinstance(m.get("requested_at"), str)
                          or m.get("by") != rec.get("owner")):
        p.append("mr must have requested_at and by == owner")
    return p
