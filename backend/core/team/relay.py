"""状态中转 on the team server: the TeamSnapshot (snapshot.py) it writes into GitLab for each person's app to read
(runner_view.Relays), and every requirement of the runner's projects as GitLab shows it, the snapshot's delivery.

Writing (Relay): snapshot.json on branch main of the status project (bridge.json team_status.project; 一键建运行机's
状态中转 makes it), through the commits API with the bridge's token (or team_status.token_file's). A round of the bridge
builds a snapshot at most every FLOOR_S, and writes it only when it differs from the one there apart from when it was
made and when the bridge last went round (content_key), or, the same, once the one there is HEARTBEAT_S old: so a
reader tells a server at work from a stopped one (snapshot.STALE_S). The one there is read once, when the relay starts,
so a restart writes nothing new. A file there or not is created or updated, whichever GitLab takes. What went wrong is
kept for the server's own 运行机 page; it never stops the round.

The team view's rows come from GitLab only, read with the bridge's token: per project its claim/ and <prefix>/ branches
(and brain/<KEY>-reflect-<n>, the write-backs made), its default branch's runs_dir (the requirements merged there), its
open MRs and those merged or closed in the last MERGED_S, each requirement's newest one whatever its state, as a person's
app takes a branch's (a hand-over's branch none of them is of asks GitLab for its own, once per head). A claim branch
whose claim.json is not a JSON object is left out, and said. A requirement's records (run.json, park.json,
aidlc-state.md, decisions.md and the Lean ledger, read_facts as a person's app reads a teammate's branch) are read at its
branch's head (GitLabSource), once per head: a head that did not move is not read again, and a new head reads only the
files that changed (each file by its blob id, once). A folder listed past MAX_PAGES of 100 files, and a default branch
with more merged requirements than that, are read as far as that and said. A merged one is read at its merge. The row is then what 交付总览 says of a teammate's requirement (deliveries.classify with no one
as "you", then family.classify for a big requirement and its small ones): its stage in 交付总览's words
(deliveries.flow_stage; a closed one and a small one merged into its big one's branch, in no column there, by their
state's words), its MR and the CI of its head (kept once the pipeline is over), whether the staging VERSION holds its
merge (GitLab's merge base; kept per merge and deploy), what it waits on and who takes that step (Waits.on). Only an MR
merged into the default branch counts as merged: one merged into any other branch, outside a family, says where it went
and is never deployed. An MR's notes are read only for a hand-over's open MR, again only when GitLab moved its
updated_at or head. A project GitLab does not answer for raises, its last read kept (Deliveries.last).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Mapping, NamedTuple

from .. import bk, deliveries, family, lean_view, mrinfo, snapshot
from ..constants import KEY_RE
from ..repo import MAX_FILE, REFLECT_RE, Source, empty_facts, parse_branch, read_facts

FILE, BRANCH = snapshot.FILE, snapshot.BRANCH
NO_TOKEN_ENV = "BRAIN_STATUS_NO_TOKEN_ENV"   # nobody sets it: team_status.token_file is read, never the environment
FLOOR_S, HEARTBEAT_S = snapshot.FLOOR_S, snapshot.HEARTBEAT_S
MERGED_S = 14 * 86400           # a merged requirement stays this long after its merge
FINAL_CI = ("success", "failed", "canceled", "skipped")
MAX_PAGES = 20                  # a folder of a commit is listed this many pages of 100 at most: past them, what fits
CUT = "%s 里的文件超过 %d 个，只读了前 %d 个：Lean 的记录可能不全" % ("%s", MAX_PAGES * 100, MAX_PAGES * 100)
CUT_RUNS = "默认分支的 %s 里的需求超过 %d 个，只读了前 %d 个：再早合并的不在全队交付里" % ("%s", MAX_PAGES * 100, MAX_PAGES * 100)
# the steps (waits.do) its owner takes: a row waiting on one waits on them
OWNER_STEPS = ("approve", "reply", "tool", "start", "redo_handoff", "label_handoff", "push_handoff", "write_back")
ROLE_OF = {"product": "product", "builder": "builder"}      # waits.who -> the identities role that takes the step


class GitLabSource(Source):
    """The files of one commit on GitLab, read the way repo.Source reads a git ref (read_facts reads a requirement's
    records through it). A folder is listed once (GitLab's tree: each file's blob id), and a file is read by its blob
    id once whatever commit or path holds it (*blobs*, kept by Deliveries per project), so the Lean ledger's audit files
    that did not change since another head are not read again. A listing past MAX_PAGES of 100 keeps what it read:
    *cut* says which folder."""

    def __init__(self, gl: Any, project: str, sha: str, blobs: dict[str, bytes]) -> None:
        super().__init__(None, ref=sha)
        self.gl, self.project, self.sha, self.blobs = gl, project, sha, blobs
        self._ids: dict[str, str] = {}          # path -> blob id at sha, for each folder listed
        self._listed: set[str] = set()
        self.used: set[str] = set()             # the blob ids read through it
        self.cut: list[str] = []                # the folders listed only in part

    def read(self, rel: str) -> bytes | None:
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if folder not in self._listed:
            self._list(folder, recursive=False)
        blob = self._ids.get(rel)
        if blob is None:
            return None
        self.used.add(blob)
        if blob not in self.blobs:
            raw = self.gl.call("GET", "/projects/%s/repository/blobs/%s/raw" % (bk.m("rest").q(self.project), blob),
                               raw=True) or b""
            self.blobs[blob] = raw[:MAX_FILE]
        return self.blobs[blob]

    def ls(self, prefix: str) -> list[str]:
        folder = prefix.rstrip("/")
        return sorted(path for path in self._list(folder, recursive=True) if path.startswith(folder + "/"))

    def _list(self, folder: str, recursive: bool) -> list[str]:
        """The files under *folder* (in it only, unless *recursive*), their blob ids kept; [] for a folder not
        there."""
        query = {"path": folder, "ref": self.sha, **({"recursive": "true"} if recursive else {})}
        try:
            rows, cut = listed(self.gl, "/projects/%s/repository/tree" % bk.m("rest").q(self.project), query)
        except bk.m("rest").HttpError as exc:
            if exc.status != 404:           # 404: no such folder at this commit
                raise
            rows, cut = [], False
        files = [r for r in rows if r.get("type") == "blob" and r.get("path") and r.get("id")]
        self._ids.update((str(r["path"]), str(r["id"])) for r in files)
        self._listed.add(folder)                # a folder cut short is not listed again: what it has past that is not read
        if cut:
            self.cut.append(folder)
        elif recursive:
            self._listed |= {str(r["path"]) for r in rows if r.get("type") == "tree"}
        return [str(r["path"]) for r in files]

    def changed_at(self, rel: str) -> float | None:
        rows = self.gl.call("GET", "/projects/%s/repository/commits" % bk.m("rest").q(self.project),
                            {"ref_name": self.sha, "path": rel, "per_page": 1}) or []
        return epoch(rows[0].get("committed_date")) if rows and isinstance(rows[0], dict) else None


def listed(gl: Any, path: str, query: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """(the rows of a paged GitLab list, at most MAX_PAGES of 100; whether there are more than that)."""
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        rows = gl.call("GET", path, dict(query, per_page=100, page=page)) or []
        out += [r for r in rows if isinstance(r, dict)]
        if len(rows) < 100:
            return out, False
    return out, bool(gl.call("GET", path, dict(query, per_page=1, page=MAX_PAGES * 100 + 1)))


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


class Read(NamedTuple):
    """One project's 全队交付 as GitLab showed it: its rows, and what was left out, in words (a claim branch whose
    claim.json is not an object)."""
    rows: list[dict[str, Any]]
    notes: list[str]


class Facts(NamedTuple):
    """read_facts at one commit, with the blob ids read for it and the folders listed only in part there."""
    facts: dict[str, Any]
    blobs: frozenset[str]
    cut: tuple[str, ...]


class Gathered(NamedTuple):
    """What GitLab says of one requirement before it is worded: its records where they are read (facts, as read_facts
    and repo.scan give them), its newest MR, its claim and where it is (main, its branch, or only claimed)."""
    f: dict[str, Any]
    mr: dict[str, Any] | None
    run: dict[str, Any]
    run_b: dict[str, Any] | None
    claim: dict[str, Any] | None
    sha: str
    merged_at: float | None


NOT_A_CLAIM = "%s 上的 claim.json 读不出一个 JSON 对象：跳过了这个分支"


def merge_of(mr: Mapping[str, Any] | None, default: str) -> tuple[float | None, str, str]:
    """(when *mr* merged into *default*, its merge commit, ''); (None, '', the branch it merged into) for one merged
    into another branch (a small requirement into its big one's); (None, '', '') when it is not merged."""
    if not mr or mr.get("state") != "merged":
        return None, "", ""
    target = str(mr.get("target_branch") or default)
    if target != default:
        return None, "", target
    return epoch(mr.get("merged_at")), str(mr.get("merge_commit_sha") or mr.get("squash_commit_sha") or
                                           mr.get("sha") or ""), ""


class Deliveries:
    """The delivery part of the snapshot: read() for each project the relay builds one for (on the bridge's worker
    thread)."""

    def __init__(self, gl: Any, base: str, kit: Callable[[str], Mapping[str, Any]],
                 now: Callable[[], float] = time.time) -> None:
        self.gl, self.base, self.kit, self.now = gl, base.rstrip("/"), kit, now
        self._facts: dict[tuple, Facts] = {}                # (project, key, sha) -> read_facts at that commit
        self._blobs: dict[str, dict[str, bytes]] = {}       # project -> blob id -> the file (GitLabSource)
        self._claims: dict[tuple, Any] = {}                 # (project, sha) -> its claim.json, an object or None
        self._notes: dict[tuple, tuple] = {}                # (project, iid) -> ((updated_at, head), parse_notes)
        self._ci: dict[tuple, str | None] = {}              # (project, iid, head) -> its head pipeline's status
        self._deployed: dict[tuple, bool] = {}              # (project, merge, deployed) -> the deploy holds the merge
        self._branch_mr: dict[tuple, dict | None] = {}      # (project, branch, head) -> that branch's newest MR
        self._merged: dict[str, tuple[str, frozenset[str], bool]] = {}   # project -> (default head, keys merged, cut)
        self.last: dict[str, Read] = {}                     # project -> its last read that worked

    def read(self, project: str, *, deployed: str, roles: Mapping[str, Any]) -> Read:
        """*project*'s rows as GitLab shows them now, read again only where something moved; what GitLab raised, else
        (last keeps it). *deployed*: the commit its staging VERSION names ('': no staging read); *roles*:
        identities.json's roles. What was read before is kept for the next try when it fails."""
        caches = (self._facts, self._claims, self._notes, self._ci, self._deployed, self._branch_mr)
        seen: set[tuple] = set()
        try:
            got = self._project(project, deployed, roles, seen)
        except BaseException:
            seen |= {k for c in caches for k in c if k[0] == project}
            raise
        finally:
            for c in caches:
                for k in [k for k in c if k[0] == project and k not in seen]:
                    del c[k]
            used = {b for k, x in self._facts.items() if k[0] == project for b in x.blobs}
            blobs = self._blobs.get(project, {})
            for b in [b for b in blobs if b not in used]:
                del blobs[b]
        self.last[project] = got
        return got

    def forget(self, keep: list[str]) -> None:
        """Let go of the projects not in *keep* (the bridge no longer has them)."""
        for p in [p for p in list(self.last) + list(self._merged) + list(self._blobs) if p not in keep]:
            self.last.pop(p, None)
            self._merged.pop(p, None)
            self._blobs.pop(p, None)
        for c in (self._facts, self._claims, self._notes, self._ci, self._deployed, self._branch_mr):
            for k in [k for k in c if k[0] not in keep]:
                del c[k]

    def build(self, projects: list[str], *, deployed: Mapping[str, str], roles: Mapping[str, Any]
              ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """(the rows of every project, what to say of a project: why it was not read this time, its last rows kept,
        or what its rows left out)."""
        rows: list[dict[str, Any]] = []
        said: dict[str, str] = {}
        for project in projects:
            try:
                got = self.read(project, deployed=deployed.get(project) or "", roles=roles)
            except Exception as exc:  # noqa: BLE001 - GitLab, or what it holds: this project's, said; the others go on
                said[project] = "全队交付没读成：%s" % _said(exc)
                got = self.last.get(project) or Read([], [])
            else:
                if got.notes:
                    said[project] = "全队交付：%s" % "；".join(got.notes)
            rows += got.rows
        self.forget(projects)
        return rows, said

    def _project(self, project: str, deployed: str, roles: Mapping[str, Any], seen: set[tuple]) -> Read:
        kit = self.kit(project)
        pre, runs_dir, now = str(kit.get("branch_prefix") or "ai"), str(kit.get("runs_dir") or "runs"), self.now()
        default = str(kit.get("default_branch") or "main")
        bridge_user = str(kit.get("bridge_user") or "") or None
        claims: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        bad: list[str] = []
        for b in self._branches(project, "claim/"):
            key = b["name"][len("claim/"):]
            if not KEY_RE.match(key):
                continue
            doc = self._claim(project, b, seen)
            if doc is None:
                bad.append(key)
            else:
                claims[key] = (b, doc)
        notes = [NOT_A_CLAIM % ("claim/" + key) for key in sorted(bad, key=key_order)]
        runs: dict[str, dict[str, Any]] = {}
        for b in self._branches(project, pre + "/"):
            got = parse_branch(b["name"], pre)
            if got:
                runs.setdefault(got["key"], dict(b, owner=got["owner"]))
        reflected = {m.group("key") for m in (REFLECT_RE.match("refs/heads/" + b["name"])
                                               for b in self._branches(project, "brain/")) if m}
        merged_keys = self._merged_keys(project, default, runs_dir, notes)
        mrs = self._mrs(project, pre, now)
        got: dict[str, Gathered] = {}
        cut: set[str] = set()
        for key in sorted(set(claims) | set(runs) | set(mrs), key=key_order):
            g = self._gather(project, key, runs_dir, default, claims.get(key), runs.get(key), mrs, key in merged_keys,
                             now, seen, cut)
            if g is not None:
                got[key] = g
        notes += [CUT % folder for folder in sorted(cut)]
        fams = family.families([g.f for g in got.values()], None, me=None, cfg=dict(kit), can_split=False, big=set(),
                               at=None)
        rows = [self._row(project, g, fams.get(key), kit, key in reflected, bridge_user, deployed, roles, now, seen)
                for key, g in got.items()]
        return Read(rows, notes)

    def _mrs(self, project: str, pre: str, now: float) -> dict[str, dict[str, Any]]:
        """The newest MR of each requirement among those open and those merged or closed in the last MERGED_S, as the
        person's app takes a branch's newest MR whatever its state (mrinfo.MrCache)."""
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - MERGED_S))
        mrs: dict[str, dict[str, Any]] = {}
        for query in ({"state": "opened"}, {"state": "merged", "updated_after": since},
                      {"state": "closed", "updated_after": since}):
            for m in self.gl.pages("/projects/%s/merge_requests" % bk.m("rest").q(project), query, 100, 20, "MRs"):
                got = parse_branch(str(m.get("source_branch") or ""), pre) if isinstance(m, dict) else None
                if got and (got["key"] not in mrs or int(m["iid"]) > int(mrs[got["key"]]["iid"])):
                    mrs[got["key"]] = m
        return mrs

    def _gather(self, project: str, key: str, runs_dir: str, default: str, claimed: tuple | None,
                run_b: dict | None, mrs: Mapping[str, dict[str, Any]], on_main: bool, now: float,
                seen: set[tuple], cut: set[str]) -> Gathered | None:
        """Where *key* is and its records there, read once per commit; None when it is not a row: its claim closed,
        merged before the last MERGED_S (or not through an MR GitLab lists), its hand-over's MR merged or closed
        before them."""
        claim_b, claim = claimed if claimed else (None, None)
        if claim and claim.get("closed"):
            return None
        mr = mrs.get(key)
        merged_at, sha, into = merge_of(mr, default)
        if on_main and merged_at is None and claim and claim.get("parent"):
            merged_at, sha, _ = merge_of(mrs.get(str(claim["parent"])), default)   # in its big one's merge
        if on_main or merged_at is not None:
            if merged_at is None or now - merged_at > MERGED_S:
                return None
            where, tip_at = "main", merged_at
        elif run_b is not None:
            sha, where, tip_at = run_b["commit"]["id"], "branch", epoch(run_b["commit"].get("committed_date"))
        else:
            sha, where, tip_at = "", "claim", None
        got = self._facts_at(project, key, sha, runs_dir, seen, cut) if sha else empty_facts()
        run = got["run"] or {}
        if where == "branch" and mr is None and run.get("handoff"):
            mr = self._mr_of_branch(project, str(run_b["name"]), sha, seen)
            if mr and mr.get("state") in ("merged", "closed"):
                return None                     # not in the window: it ended before the last MERGED_S
        branch = (run_b or {}).get("name") or (mr or {}).get("source_branch") or run.get("branch")
        f = dict(got, key=key, claim=claim, claim_ref="claim/" + key if claim_b else None, where=where, ref=sha or None,
                 worktree=None, branch=branch, owner=(run_b or {}).get("owner") or run.get("owner") or
                 (claim or {}).get("owner"), local=None, origin=branch, local_sha=None, origin_sha=sha or None,
                 tip=sha or None, tip_at=tip_at, merged=where == "main", merged_at=merged_at, merged_into=into or None,
                 stages=lean_view.stages(got["events"]))
        return Gathered(f, mr, run, run_b, claim, sha, merged_at)

    def _row(self, project: str, g: Gathered, fam: dict[str, Any] | None, kit: Mapping[str, Any], reflected: bool,
             bridge_user: str | None, deployed: str, roles: Mapping[str, Any], now: float,
             seen: set[tuple]) -> dict[str, Any]:
        """The row in 交付总览's words: deliveries.classify with no one as "you", then family.classify for a big
        requirement or one of its small ones. A closed one, and a small one merged into its big one, are in no column
        of 需求流动: their state's words stand for the stage. An MR merged into a branch that is not the default one,
        outside a family, is not merged: it says where it went."""
        f, mr, run = g.f, g.mr, g.run
        info = self._mr_info(project, mr, run, bridge_user, seen) if mr else None
        row = deliveries.base_row(project, f)
        handover = ((run.get("handoff") or {}).get("local_head")) if isinstance(run.get("handoff"), dict) else None
        parent = ((g.run_b or {}).get("commit") or {}).get("parent_ids") or []
        deliveries.classify(row, f, mr=info, slot=None, cfg=dict(kit), at_handover=bool(info) and bool(handover)
                            and list(parent) == [handover], me=None, reflected=reflected, now=now, pushed=True)
        row["family"] = fam
        family.classify(row, fam, f, None)
        row["mr"] = info and {"iid": info["iid"], "url": info["url"], "state": info["state"]}
        if bk.m("claims").unowned(g.claim) and row["state"] == "claimed":    # filed for others: anyone may take it
            row["state"], row["state_label"] = "unclaimed", deliveries.STATE_LABEL["unclaimed"]
        into = f.get("merged_into")
        if into and fam is None and row["state"] != "closed":
            if row["state"] == "merged":
                row["state"], row["state_label"] = "local", deliveries.STATE_LABEL["local"]
            row["waits"] = deliveries.waits("nobody", "none", "合进了 %s" % into, since=epoch((mr or {}).get("merged_at")))
        closed_at = epoch((mr or {}).get("closed_at") or (mr or {}).get("updated_at")) if row["state"] == "closed" else None
        return {"project": project, "key": f["key"], "title": row["title"], "owner": row["owner"] or None,
                "stage": deliveries.flow_stage(row) or row["state_label"], "state": row["state"], "branch": f["branch"],
                "mr": info and {"iid": info["iid"], "url": info["url"], "state": str(info["state"] or ""),
                                "ci": self._ci_of(project, mr, seen)},
                "deployed": self._deployed_of(project, g.sha, deployed, seen) if f["where"] == "main" else None,
                "waits": waits_of(row, on_names(row, info, roles)),
                "updated_at": g.merged_at if f["where"] == "main" else closed_at or deliveries.updated_at(f, None)}

    # ---- GitLab, read again only where something moved ------------------------------------------------------------ #

    def _branches(self, project: str, prefix: str) -> list[dict[str, Any]]:
        rows = self.gl.pages("/projects/%s/repository/branches" % bk.m("rest").q(project), {"search": "^" + prefix},
                             100, 20, "branches")
        return [b for b in rows if isinstance(b, dict) and str(b.get("name") or "").startswith(prefix)
                and isinstance(b.get("commit"), dict) and b["commit"].get("id")]

    def _merged_keys(self, project: str, default: str, runs_dir: str, notes: list[str]) -> frozenset[str]:
        """The keys with a folder in runs_dir on the default branch (the first MAX_PAGES of 100, said when there are
        more): listed again when its head moves."""
        b = self.gl.call("GET", "/projects/%s/repository/branches/%s" % (bk.m("rest").q(project),
                                                                      bk.m("rest").q(default))) or {}
        head = str((b.get("commit") or {}).get("id") or "")
        got = self._merged.get(project)
        if got is None or got[0] != head:
            try:
                rows, cut = listed(self.gl, "/projects/%s/repository/tree" % bk.m("rest").q(project),
                                   {"path": runs_dir, "ref": head}) if head else ([], False)
            except bk.m("rest").HttpError as exc:
                if exc.status != 404:
                    raise
                rows, cut = [], False
            got = self._merged[project] = (head, frozenset(str(r.get("name")) for r in rows if r.get("type") == "tree"
                                                           and KEY_RE.match(str(r.get("name")))), cut)
        if got[2]:
            notes.append(CUT_RUNS % runs_dir)
        return got[1]

    def _claim(self, project: str, b: dict[str, Any], seen: set[tuple]) -> dict[str, Any] | None:
        """The claim.json of claim branch *b*, read once per commit; None when that is not a JSON object (a person's
        app reads it as no claim: repo.read_claim)."""
        k = (project, b["commit"]["id"])
        seen.add(k)
        if k not in self._claims:
            raw = self.gl.file_raw(project, "claim.json", b["commit"]["id"])
            try:
                doc = json.loads(raw.decode("utf-8")) if raw else None
            except (ValueError, UnicodeDecodeError):
                doc = None
            self._claims[k] = doc if isinstance(doc, dict) else None
        return self._claims[k]

    def _mr_of_branch(self, project: str, branch: str, head: str, seen: set[tuple]) -> dict[str, Any] | None:
        """*branch*'s newest MR whatever its state, asked once per head (for a hand-over none of the MRs listed is
        of: its MR ended before the last MERGED_S, or there is none)."""
        k = (project, branch, head)
        seen.add(k)
        if k not in self._branch_mr:
            rows = self.gl.call("GET", "/projects/%s/merge_requests" % bk.m("rest").q(project),
                                {"source_branch": branch, "state": "all", "per_page": 5}) or []
            rows = sorted((r for r in rows if isinstance(r, dict) and r.get("iid")), key=lambda r: int(r["iid"]))
            self._branch_mr[k] = rows[-1] if rows else None
        return self._branch_mr[k]

    def _facts_at(self, project: str, key: str, sha: str, runs_dir: str, seen: set[tuple],
                  cut: set[str]) -> dict[str, Any]:
        """read_facts at *sha*, once per commit, with the blob ids it read (kept while a commit read uses them) and
        the folders it could list only in part (added to *cut*)."""
        k = (project, key, sha)
        seen.add(k)
        if k not in self._facts:
            src = GitLabSource(self.gl, project, sha, self._blobs.setdefault(project, {}))
            self._facts[k] = Facts(read_facts(src, key, runs_dir), frozenset(src.used), tuple(src.cut))
        cut.update(self._facts[k].cut)
        return self._facts[k].facts

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


# ---- writing ----------------------------------------------------------------------------------------------------- #

def content_key(snap: Mapping[str, Any]) -> str:
    """What decides whether a snapshot is new: all of it but what moves without news, round after round: when it was
    made, the bridge's last round, each staging error's count and when the log last showed it, each patrol MR's
    updated_at (any note or label on it). Those are written along with a real change, or with the heartbeat."""
    runner = snap["runner"]
    body = dict(snap, generated_at=0, server=dict(snap["server"], last_round_at=None),
                runner=dict(runner, staging=[dict(e, count=0, last_seen=0.0) for e in runner["staging"]],
                            patrol=[dict(m, updated_at=None) for m in runner["patrol"]]))
    return hashlib.sha256(snapshot.dump(body).encode("utf-8")).hexdigest()


WRITE_SAID = {401: "GitLab 不认写状态用的令牌", 403: "GitLab 不让这个账号写状态项目",
              404: "GitLab 上找不到状态项目，或者这个账号看不到它"}
WRITE_TODO = {401: "再跑一次一键建运行机：它会查令牌、换新的", 403: "在 GitLab 上把桥的账号加成状态项目的 Maintainer，或者再跑一次一键建运行机",
              404: "再跑一次一键建运行机：它会建好状态项目、给好权限"}


class Relay:
    """snapshot.json on the status project, written by the bridge's rounds (round(), on its worker thread); view() for
    the server's own 运行机 page, from any thread."""

    def __init__(self, connect: Callable[[], Any], project: str, now: Callable[[], float] = time.time) -> None:
        self.connect, self.project, self.now = connect, project, now
        self.gl: Any = None                     # connect()'s GitLab client, made in the first round that writes
        self.there: bool | None = None          # snapshot.json is on main (None: not read yet)
        self.key: str | None = None             # content_key of the one there (None: unknown, or not one)
        self.written_at: float | None = None    # when the one there was made
        self.heartbeat = False                  # the last write wrote the same snapshot again
        self.built_at: float | None = None      # when the last round built one (or tried)
        self.error, self.todo, self.raw = "", "", ""

    def round(self, build: Callable[[float], Mapping[str, Any]]) -> str:
        """One round of the bridge: a snapshot built (build(now)) when FLOOR_S went by since the last, and written when
        it is new or the one there is HEARTBEAT_S old. -> "wait", "same", "wrote", "heartbeat" or "failed"."""
        now = self.now()
        if self.built_at is not None and now - self.built_at < FLOOR_S:
            return "wait"
        self.built_at = now
        try:
            if self.gl is None:
                self.gl = self.connect()
            if self.there is None:
                self._read()
            snap = snapshot.parse(dict(build(now)))
            key = content_key(snap)
            same = key == self.key
            if same and self.written_at is not None and now - self.written_at < HEARTBEAT_S:
                self._ok()
                return "same"
            self._write(snap, same)
            self.key, self.written_at, self.heartbeat = key, snap["generated_at"], same
            self._ok()
            return "heartbeat" if same else "wrote"
        except Exception as exc:  # noqa: BLE001 - GitLab, a bad snapshot: said on the page, the round goes on
            status = getattr(exc, "status", None)
            if isinstance(exc, snapshot.Bad):
                self.error, self.todo = "这一轮做出来的状态不对，没有写", "把细节里的原话发给管运行机的人：这是 app 的问题"
            else:
                self.error = WRITE_SAID.get(status, "这一轮没写成状态")
                self.todo = WRITE_TODO.get(status, "%d 分钟以后会再写；一直这样就看细节里 GitLab 说的" % (FLOOR_S // 60))
            self.raw = " ".join(str(exc).split())[:500]
            return "failed"

    def view(self) -> dict[str, Any]:
        """TeamRelay (ui/src/types.ts): where it writes, when the one there was made, what went wrong last."""
        return {"project": self.project, "written_at": self.written_at, "heartbeat": self.heartbeat,
                "checked_at": self.built_at, "error": self.error, "todo": self.todo, "raw": self.raw}

    def _ok(self) -> None:
        self.error = self.todo = self.raw = ""

    def _read(self) -> None:
        """The snapshot there now: whether there is one, what it says and when it was made."""
        raw = self.gl.file_raw(self.project, FILE, BRANCH)
        self.there = raw is not None
        try:
            got = snapshot.parse(raw) if raw is not None else None
        except snapshot.Bad:
            got = None
        self.key = content_key(got) if got else None
        self.written_at = got["generated_at"] if got else None

    def _write(self, snap: Mapping[str, Any], same: bool) -> None:
        path = "/projects/%s/repository/commits" % bk.m("rest").q(self.project)
        action = "update" if self.there else "create"
        body = {"branch": BRANCH, "commit_message": "团队状态：%s" % ("没有变化（心跳）" if same else "更新"),
                "actions": [{"action": action, "file_path": FILE, "content": snapshot.dump(snap)}]}
        try:
            self.gl.call("POST", path, body=body)
        except bk.m("rest").HttpError as exc:
            if exc.status != 400:
                raise
            body["actions"][0]["action"] = "create" if action == "update" else "update"    # it is there after all, or not
            self.gl.call("POST", path, body=body)
        self.there = True


# ---- what the snapshot says of the server ------------------------------------------------------------------------- #

def project_states(polled: list[Mapping[str, Any]], patrol: Mapping[str, Any], staging: Mapping[str, Any],
                   delivery: Mapping[str, str]) -> list[dict[str, Any]]:
    """Each project as the round left it (the snapshot's server.projects): whether the runner takes its hand-overs, the
    work sources its kit.json switches on, and what went wrong with it this round, each part in words (*delivery*:
    what Deliveries.build said of it)."""
    pat = {p["project"]: p.get("error") or "" for p in patrol.get("projects") or []}
    sta = {p["project"]: p.get("error") or "" for p in staging.get("projects") or []}
    out = []
    for p in polled:
        name = p["project"]
        said = [p.get("error") or "", delivery.get(name) or "",
                "MR 巡检：%s" % pat[name] if pat.get(name) else "", "测试环境报错：%s" % sta[name] if sta.get(name) else ""]
        out.append({"project": name, "on": bool(p.get("on")), "sources": list(p.get("sources") or []),
                    "error": "；".join(x for x in said if x)})
    return out


def identity_roles(path: Any) -> dict[str, list[str]]:
    """identities.json's roles (brain-authz's shape: {"users", "roles": {role: [username]}}); {} when it cannot be
    read."""
    try:
        with open(str(path), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    roles = doc.get("roles") if isinstance(doc, dict) else None
    return {str(k): [str(n) for n in v if isinstance(n, str)] for k, v in roles.items() if isinstance(v, list)} \
        if isinstance(roles, dict) else {}
