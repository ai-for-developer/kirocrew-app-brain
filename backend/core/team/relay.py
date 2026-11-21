"""状态中转 on the team server: every requirement of the runner's projects as GitLab shows it, for the TeamSnapshot
(snapshot.py) the server writes into GitLab for each person's app to read.

The team view's rows come from GitLab only, read with the bridge's token: per project its claim/ and <prefix>/ branches
(and brain/<KEY>-reflect-<n>, the write-backs made), its default branch's runs_dir (the requirements merged there), its
open MRs and those merged in the last MERGED_S. A requirement's records (run.json, park.json, aidlc-state.md,
decisions.md and the Lean ledger, read_facts as a person's app reads a teammate's branch) are read at its branch's head
through the files API (GitLabSource), once per head: a head that did not move is not read again. A merged one is read at
its merge. The row is then what 交付总览 says of a teammate's requirement (deliveries.classify with no one as "you"): its
stage in 交付总览's words (deliveries.flow_stage), its MR and the CI of its head (kept once the pipeline is over), whether
the staging VERSION holds its merge (GitLab's merge base; kept per merge and deploy), what it waits on and who takes
that step (Waits.on). An MR's notes are read only for a hand-over's open MR, again only when GitLab moved its
updated_at or head. A project GitLab does not answer for keeps its last rows, with why.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from .. import bk, deliveries, lean_view, mrinfo
from ..constants import KEY_RE
from ..repo import MAX_FILE, REFLECT_RE, Source, empty_facts, parse_branch, read_facts

MERGED_S = 14 * 86400           # a merged requirement stays this long after its merge
FINAL_CI = ("success", "failed", "canceled", "skipped")
# the steps (waits.do) its owner takes: a row waiting on one waits on them
OWNER_STEPS = ("approve", "reply", "tool", "start", "redo_handoff", "label_handoff", "push_handoff", "write_back")
ROLE_OF = {"product": "product", "builder": "builder"}      # waits.who -> the identities role that takes the step


class GitLabSource(Source):
    """The files of one commit on GitLab, read the way repo.Source reads a git ref (read_facts reads a requirement's
    records through it): each file once."""

    def __init__(self, gl: Any, project: str, sha: str) -> None:
        super().__init__(None, ref=sha)
        self.gl, self.project, self.sha = gl, project, sha
        self._files: dict[str, bytes | None] = {}

    def read(self, rel: str) -> bytes | None:
        if rel not in self._files:
            raw = self.gl.file_raw(self.project, rel, self.sha)
            self._files[rel] = raw[:MAX_FILE] if raw is not None else None
        return self._files[rel]

    def ls(self, prefix: str) -> list[str]:
        try:
            rows = self.gl.pages("/projects/%s/repository/tree" % bk.m("rest").q(self.project),
                                 {"path": prefix.rstrip("/"), "ref": self.sha, "recursive": "true"}, 100, 20,
                                 "files under %s" % prefix)
        except bk.m("rest").HttpError as exc:
            if exc.status == 404:           # no such folder at this commit
                return []
            raise
        return sorted(str(r["path"]) for r in rows if isinstance(r, dict) and r.get("type") == "blob")

    def changed_at(self, rel: str) -> float | None:
        rows = self.gl.call("GET", "/projects/%s/repository/commits" % bk.m("rest").q(self.project),
                            {"ref_name": self.sha, "path": rel, "per_page": 1}) or []
        return epoch(rows[0].get("committed_date")) if rows and isinstance(rows[0], dict) else None


def epoch(stamp: Any) -> float | None:
    t = bk.m("rest").gl_time(stamp)
    return t.timestamp() if t else None


def on_names(row: Mapping[str, Any], mr: Mapping[str, Any] | None, roles: Mapping[str, Any]) -> list[str]:
    """Who takes the step *row* waits on now (Waits.on), GitLab usernames: those the bridge's open question names
    (an answer, an acceptance), the people of the role it waits on (identities.json), or its owner for a step of
    theirs; [] when it waits on no one in particular (AI, the runner, the bridge, anyone who claims it)."""
    w = row["waits"]
    if w["do"] in deliveries.GATES and mr:
        asked = [q for q in mr.get("questions") or [] if q.get("state") == "open" and q.get("who")]
        if asked:
            return list(asked[-1]["who"])
    role = ROLE_OF.get(w["who"])
    if role:
        return [str(n) for n in roles.get(role) or []]
    if w["do"] in OWNER_STEPS and w["who"] in ("people", "you") and row.get("owner"):
        return [str(row["owner"])]
    return []


def waits_of(row: Mapping[str, Any], on: list[str]) -> dict[str, Any]:
    w = row["waits"]
    return {"who": w["who"], "do": w["do"], "text": w["text"], "since": w["since"], "on": on}


def key_order(key: str) -> tuple[str, int]:
    pre, _, n = key.rpartition("-")
    return pre, int(n)


def _said(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300] or type(exc).__name__


class Deliveries:
    """The delivery part of the snapshot: build() each time the relay builds one (on the bridge's worker thread)."""

    def __init__(self, gl: Any, base: str, kit: Callable[[str], Mapping[str, Any]],
                 now: Callable[[], float] = time.time) -> None:
        self.gl, self.base, self.kit, self.now = gl, base.rstrip("/"), kit, now
        self._facts: dict[tuple, dict[str, Any]] = {}       # (project, key, sha) -> read_facts at that commit
        self._claims: dict[tuple, Any] = {}                 # (project, sha) -> its claim.json
        self._notes: dict[tuple, tuple] = {}                # (project, iid) -> ((updated_at, head), parse_notes)
        self._ci: dict[tuple, str | None] = {}              # (project, iid, head) -> its head pipeline's status
        self._deployed: dict[tuple, bool] = {}              # (project, merge, deployed) -> the deploy holds the merge
        self._merged: dict[str, tuple[str, frozenset[str]]] = {}   # project -> (default branch head, keys merged)
        self.last: dict[str, list[dict[str, Any]]] = {}     # project -> its rows of the last build that read it

    def build(self, projects: list[str], *, deployed: Mapping[str, str], roles: Mapping[str, Any]
              ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """(the rows of every project, the projects that could not be read this time with why): one of those keeps
        the rows of its last read. *deployed*: project -> the commit its staging VERSION names ('' or none: no
        staging read); *roles*: identities.json's roles."""
        rows: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        seen: set[tuple] = set()
        caches = (self._facts, self._claims, self._notes, self._ci, self._deployed)
        for project in projects:
            try:
                got = self._project(project, deployed.get(project) or "", roles, seen)
            except (bk.m("common").BrainError, OSError, ValueError) as exc:
                errors[project] = _said(exc)
                got = self.last.get(project, [])
                seen |= {k for c in caches for k in c if k[0] == project}     # kept for the next try
            else:
                self.last[project] = got
            rows += got
        for c in caches:
            for k in [k for k in c if k not in seen]:
                del c[k]
        for p in [p for p in list(self.last) + list(self._merged) if p not in projects]:
            self.last.pop(p, None)
            self._merged.pop(p, None)
        return rows, errors

    def _project(self, project: str, deployed: str, roles: Mapping[str, Any], seen: set[tuple]) -> list[dict[str, Any]]:
        kit = self.kit(project)
        pre, runs_dir, now = str(kit.get("branch_prefix") or "ai"), str(kit.get("runs_dir") or "runs"), self.now()
        bridge_user = str(kit.get("bridge_user") or "") or None
        claims = {b["name"][len("claim/"):]: b for b in self._branches(project, "claim/")
                  if KEY_RE.match(b["name"][len("claim/"):])}
        runs: dict[str, dict[str, Any]] = {}
        for b in self._branches(project, pre + "/"):
            got = parse_branch(b["name"], pre)
            if got:
                runs.setdefault(got["key"], dict(b, owner=got["owner"]))
        reflected = {m.group("key") for m in (REFLECT_RE.match("refs/heads/" + b["name"])
                                               for b in self._branches(project, "brain/")) if m}
        merged_keys = self._merged_keys(project, str(kit.get("default_branch") or "main"), runs_dir)
        mrs: dict[str, dict[str, Any]] = {}
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - MERGED_S))
        for query in ({"state": "opened"}, {"state": "merged", "updated_after": since}):
            for m in self.gl.pages("/projects/%s/merge_requests" % bk.m("rest").q(project), query, 100, 20, "MRs"):
                got = parse_branch(str(m.get("source_branch") or ""), pre) if isinstance(m, dict) else None
                if got and (got["key"] not in mrs or int(m["iid"]) > int(mrs[got["key"]]["iid"])):
                    mrs[got["key"]] = m
        out = []
        for key in sorted(set(claims) | set(runs) | set(mrs), key=key_order):
            row = self._row(project, key, kit, claims.get(key), runs.get(key), mrs.get(key), key in merged_keys,
                            key in reflected, bridge_user, deployed, roles, now, seen)
            if row is not None:
                out.append(row)
        return out

    def _row(self, project: str, key: str, kit: Mapping[str, Any], claim_b: dict | None, run_b: dict | None,
             mr: dict | None, on_main: bool, reflected: bool, bridge_user: str | None, deployed: str,
             roles: Mapping[str, Any], now: float, seen: set[tuple]) -> dict[str, Any] | None:
        merged_at = epoch(mr.get("merged_at")) if mr and mr.get("state") == "merged" else None
        if on_main or merged_at is not None:
            if merged_at is None or now - merged_at > MERGED_S:
                return None                     # merged before the last 14 days, or not through an MR GitLab lists
            sha = str(mr.get("merge_commit_sha") or mr.get("squash_commit_sha") or mr.get("sha") or "")
            where, tip_at = "main", merged_at
        elif run_b is not None:
            sha, where, tip_at = run_b["commit"]["id"], "branch", epoch(run_b["commit"].get("committed_date"))
        else:
            sha, where, tip_at = "", "claim", None
        claim = self._claim(project, claim_b, seen) if claim_b else None
        if isinstance(claim, dict) and claim.get("closed"):
            return None
        got = self._facts_at(project, key, sha, str(kit.get("runs_dir") or "runs"), seen) if sha else empty_facts()
        run = got["run"] or {}
        branch = (run_b or {}).get("name") or (mr or {}).get("source_branch") or run.get("branch")
        f = dict(got, key=key, claim=claim, claim_ref="claim/" + key if claim_b else None, where=where, ref=sha or None,
                 worktree=None, branch=branch, owner=(run_b or {}).get("owner") or run.get("owner") or
                 (claim or {}).get("owner"), local=None, origin=branch, local_sha=None, origin_sha=sha or None,
                 tip=sha or None, tip_at=tip_at, merged=where == "main", merged_at=merged_at,
                 stages=lean_view.stages(got["events"]))
        info = self._mr_info(project, mr, run, bridge_user, seen) if mr else None
        row = deliveries.base_row(project, f)
        handover = ((run.get("handoff") or {}).get("local_head")) if isinstance(run.get("handoff"), dict) else None
        parent = ((run_b or {}).get("commit") or {}).get("parent_ids") or []
        deliveries.classify(row, f, mr=info, slot=None, cfg=dict(kit), at_handover=bool(info) and bool(handover)
                            and list(parent) == [handover], me=None, reflected=reflected, now=now, pushed=True)
        if bk.m("claims").unowned(claim) and row["state"] == "claimed":     # filed for others: anyone may take it
            row["state"], row["state_label"] = "unclaimed", deliveries.STATE_LABEL["unclaimed"]
        row["mr"] = info and {"iid": info["iid"], "url": info["url"], "state": info["state"]}
        stage = deliveries.flow_stage(row)
        if stage is None or row["state"] == "closed":
            return None
        return {"project": project, "key": key, "title": row["title"], "owner": row["owner"] or None, "stage": stage,
                "state": row["state"], "branch": branch,
                "mr": info and {"iid": info["iid"], "url": info["url"], "state": str(info["state"] or ""),
                                "ci": self._ci_of(project, mr, seen)},
                "deployed": self._deployed_of(project, sha, deployed, seen) if where == "main" else None,
                "waits": waits_of(row, on_names(row, info, roles)),
                "updated_at": deliveries.updated_at(f, None) if where != "main" else merged_at}

    # ---- GitLab, read again only where something moved ------------------------------------------------------------ #

    def _branches(self, project: str, prefix: str) -> list[dict[str, Any]]:
        rows = self.gl.pages("/projects/%s/repository/branches" % bk.m("rest").q(project), {"search": "^" + prefix},
                             100, 20, "branches")
        return [b for b in rows if isinstance(b, dict) and str(b.get("name") or "").startswith(prefix)
                and isinstance(b.get("commit"), dict) and b["commit"].get("id")]

    def _merged_keys(self, project: str, default: str, runs_dir: str) -> frozenset[str]:
        """The keys with a folder in runs_dir on the default branch: listed again when its head moves."""
        b = self.gl.call("GET", "/projects/%s/repository/branches/%s" % (bk.m("rest").q(project),
                                                                      bk.m("rest").q(default))) or {}
        head = str((b.get("commit") or {}).get("id") or "")
        got = self._merged.get(project)
        if got is None or got[0] != head:
            try:
                rows = self.gl.pages("/projects/%s/repository/tree" % bk.m("rest").q(project),
                                     {"path": runs_dir, "ref": head}, 100, 20, "runs") if head else []
            except bk.m("rest").HttpError as exc:
                if exc.status != 404:
                    raise
                rows = []
            got = self._merged[project] = (head, frozenset(str(r.get("name")) for r in rows if isinstance(r, dict)
                                                           and r.get("type") == "tree" and KEY_RE.match(str(r.get("name")))))
        return got[1]

    def _claim(self, project: str, b: dict[str, Any], seen: set[tuple]) -> Any:
        k = (project, b["commit"]["id"])
        seen.add(k)
        if k not in self._claims:
            raw = self.gl.file_raw(project, "claim.json", b["commit"]["id"])
            try:
                self._claims[k] = json.loads(raw.decode("utf-8")) if raw else None
            except (ValueError, UnicodeDecodeError):
                self._claims[k] = None
        return self._claims[k]

    def _facts_at(self, project: str, key: str, sha: str, runs_dir: str, seen: set[tuple]) -> dict[str, Any]:
        k = (project, key, sha)
        seen.add(k)
        if k not in self._facts:
            self._facts[k] = read_facts(GitLabSource(self.gl, project, sha), key, runs_dir)
        return self._facts[k]

    def _mr_info(self, project: str, mr: dict[str, Any], run: Mapping[str, Any], bridge_user: str | None,
                 seen: set[tuple]) -> dict[str, Any]:
        """The MR as the overview reads it (mrinfo's shape): its notes parsed only for a hand-over's open MR."""
        iid, head = int(mr["iid"]), str(mr.get("sha") or "")
        parsed: dict[str, Any] = {"questions": [], "rejected_reason": None, "rejected_todo": None, "rejected_raw": None,
                                  "rejected_at": None, "runner": None}
        if mr.get("state") == "opened" and run.get("handoff"):
            k = (project, iid)
            seen.add(k)
            stamp = (str(mr.get("updated_at") or ""), head)
            got = self._notes.get(k)
            if got is None or got[0] != stamp:
                got = self._notes[k] = (stamp, mrinfo.parse_notes(self.gl.mr_notes(project, iid), bridge_user))
            parsed = got[1]
        return {"iid": iid, "url": mr.get("web_url") or mrinfo.mr_url(self.base, project, iid),
                "state": mr.get("state"), "labels": list(mr.get("labels") or []), "sha": head or None, **parsed}

    def _ci_of(self, project: str, mr: dict[str, Any], seen: set[tuple]) -> str | None:
        """The status of the pipeline at the MR's head, kept once it is over (GitLab does not move the MR when a
        pipeline ends, so a running one is read again)."""
        k = (project, int(mr["iid"]), str(mr.get("sha") or ""))
        seen.add(k)
        if k not in self._ci or self._ci[k] not in FINAL_CI:
            rows = mrinfo.pipelines(self.gl, self.base, project, int(mr["iid"]), None) or []
            self._ci[k] = next((p["status"] for p in rows if p["sha"] == k[2]), None)
        return self._ci[k]

    def _deployed_of(self, project: str, merge: str, deployed: str, seen: set[tuple]) -> bool | None:
        """Whether the commit the staging VERSION names holds *merge* (None: no staging read for the project)."""
        if not deployed or not merge:
            return None
        if merge.startswith(deployed) or deployed.startswith(merge):
            return True
        k = (project, merge, deployed)
        seen.add(k)
        if k not in self._deployed:
            try:
                base = self.gl.call("GET", "/projects/%s/repository/merge_base" % bk.m("rest").q(project),
                                    {"refs[]": [merge, deployed]}) or {}
            except bk.m("rest").HttpError as exc:
                if exc.status in (400, 404):    # a commit GitLab does not have (yet): not deployed, asked again later
                    return False
                raise
            self._deployed[k] = str(base.get("id") or "") == merge
        return self._deployed[k]
