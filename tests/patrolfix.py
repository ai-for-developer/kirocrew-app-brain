"""MR 巡检 (team/patrol.py), offline: GitLab in memory, the session surface in memory, and a clock both read.

PatrolGitLab answers what patrol and the bridge's hand-over poll ask: projects and their kit.json on the default branch,
open MRs (paged, the label filter the bridge uses), an MR with its head pipeline, a pipeline's failed jobs and their
logs, discussions (paged), and the notes posted. Its times are the clock's, so "before patrol started" is the same
moment for both. FakeSessions is crewadapter's patrol surface (open_patrol … patrol_stop): a turn runs until a test
ends it.
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

from appkit import mod

BASE = "https://gitlab.example.com"
API = "/api/v4"
SHA = {n: ("%x" % n) * 40 for n in range(1, 10)}      # a head sha per number: SHA[1] = "111…"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def iso(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(self.t if t is None else t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class PatrolGitLab:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.projects: dict[str, dict] = {}
        self.mrs: dict[tuple[str, int], dict] = {}
        self.discussions: dict[tuple[str, int], list[dict]] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.traces: dict[int, bytes] = {}
        self.calls: list[tuple] = []
        self.who = {"bridge-tok": "brain-bridge"}
        self.ids = itertools.count(5000)

    # ---- the world ------------------------------------------------------------------------------------------- #

    def project(self, path: str, pid: int, kit: dict | None = None) -> None:
        self.projects[path] = {"id": pid, "default_branch": "main", "kit": kit if kit is not None else {}}

    def mr(self, path: str, iid: int, sha: str = SHA[1], source: str = "feature", created: float | None = None,
           **over) -> dict:
        p = self.projects[path]
        at = self.clock.iso(created)
        m = {"iid": iid, "project_id": p["id"], "title": "MR %d" % iid, "state": "opened",
             "web_url": "%s/%s/-/merge_requests/%d" % (BASE, path, iid), "author": {"username": "alice"},
             "source_branch": source, "target_branch": "main", "sha": sha, "created_at": at, "updated_at": at,
             "labels": [], "source_project_id": p["id"], "target_project_id": p["id"], "has_conflicts": False,
             "detailed_merge_status": "mergeable", "_pipeline": None}
        m.update(over)
        self.mrs[(path, iid)] = m
        self.discussions.setdefault((path, iid), [])
        return m

    def touch(self, path: str, iid: int) -> None:
        self.mrs[(path, iid)]["updated_at"] = self.clock.iso()

    def fail(self, path: str, iid: int, jobs: dict[str, bytes] | None = None, allowed: tuple[str, ...] = ()) -> int:
        """A new pipeline at the MR head that failed: its failed jobs with their logs (*allowed*: allowed to fail)."""
        m = self.mrs[(path, iid)]
        pid = next(self.ids)
        self.jobs[pid] = []
        for name, log in (jobs or {"test": b"FAILED test_discount\n"}).items():
            jid = next(self.ids)
            self.jobs[pid].append({"id": jid, "name": name, "status": "failed", "allow_failure": name in allowed})
            self.traces[jid] = log
        m["_pipeline"] = {"id": pid, "status": "failed", "sha": m["sha"]}
        return pid

    def pipeline(self, path: str, iid: int, status: str) -> int:
        m = self.mrs[(path, iid)]
        pid = next(self.ids)
        self.jobs[pid] = []
        m["_pipeline"] = {"id": pid, "status": status, "sha": m["sha"]}
        return pid

    def thread(self, path: str, iid: int, author: str, body: str, file: str | None = None, line: int | None = None,
               resolvable: bool = True, resolved: bool = False) -> dict:
        """A review thread (a diff note on *file*:*line*, or a thread on the MR), or a plain comment (resolvable=False:
        GitLab's individual note). GitLab moves the MR's updated_at with every note."""
        n = self._note(author, body, resolvable, resolved)
        if file:
            n["type"], n["position"] = "DiffNote", {"new_path": file, "new_line": line, "old_path": file, "old_line": None}
        d = {"id": "%040x" % next(self.ids), "individual_note": not resolvable, "notes": [n]}
        self.discussions[(path, iid)].append(d)
        self.touch(path, iid)
        return d

    def reply(self, path: str, iid: int, d: dict, author: str, body: str) -> dict:
        n = self._note(author, body, d["notes"][0]["resolvable"], False)
        d["notes"].append(n)
        self.touch(path, iid)
        return n

    def system(self, path: str, iid: int, body: str) -> None:
        n = dict(self._note("alice", body, False, False), system=True)
        self.discussions[(path, iid)].append({"id": "%040x" % next(self.ids), "individual_note": True, "notes": [n]})

    def push(self, path: str, iid: int, sha: str, **over) -> None:
        """A new head: GitLab moves updated_at; the head has no pipeline until one is made."""
        m = self.mrs[(path, iid)]
        m.update(sha=sha, _pipeline=None, **over)
        self.touch(path, iid)

    def close(self, path: str, iid: int) -> None:
        self.mrs[(path, iid)]["state"] = "merged"

    def _note(self, author: str, body: str, resolvable: bool, resolved: bool) -> dict:
        return {"id": next(self.ids), "body": body, "author": {"username": author}, "system": False,
                "resolvable": resolvable, "resolved": resolved, "created_at": self.clock.iso()}

    def posted(self, kind: str = "notes") -> list[dict]:
        """The bodies of the notes POSTed: on the MR itself (notes) or as replies in a thread (replies)."""
        want = re.compile(r".*/merge_requests/\d+/notes$" if kind == "notes" else r".*/discussions/[0-9a-f]+/notes$")
        return [c[3] for c in self.calls if c[0] == "POST" and want.match(c[1])]

    def asked(self, pattern: str) -> int:
        return sum(1 for c in self.calls if c[0] == "GET" and re.search(pattern, c[1]))

    # ---- GitLab ---------------------------------------------------------------------------------------------- #

    @staticmethod
    def page(rows: list, q: dict) -> list:
        n, p = int(q.get("per_page", 20)), int(q.get("page", 1))
        return rows[(p - 1) * n: p * n]

    def _project(self, key: str) -> tuple[str, dict] | None:
        for path, p in self.projects.items():
            if key in (path, str(p["id"])):
                return path, p
        return None

    @staticmethod
    def shown(m: dict) -> dict:
        return {k: v for k, v in m.items() if not k.startswith("_")}

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path, q = unquote(u.path), dict(parse_qsl(u.query))
        data = json.loads(body.decode()) if body else None
        self.calls.append((method, path, q, data))
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        tok = headers.get("PRIVATE-TOKEN")
        if tok not in self.who:
            return ok({"message": "401 Unauthorized"}, 401)
        if path == API + "/user":
            return ok({"id": 9, "username": self.who[tok]})
        m = re.fullmatch(API + r"/projects/(.+?)(/(?:merge_requests|repository|pipelines|jobs)(?:/.*)?)?", path)
        hit = self._project(m.group(1)) if m else None
        if hit is None:
            return ok({"message": "404 Project Not Found"}, 404)
        name, p = hit
        rest = m.group(2) or ""
        if rest == "" and method == "GET":
            return ok({"id": p["id"], "path_with_namespace": name, "default_branch": p["default_branch"]})
        if rest == "/repository/files/.brain/kit.json/raw" and q.get("ref") == p["default_branch"]:
            return 200, json.dumps(p["kit"]).encode()
        if rest == "/merge_requests" and method == "GET":
            rows = [self.shown(x) for (pp, _), x in sorted(self.mrs.items()) if pp == name
                    and x["state"] == q.get("state", x["state"])
                    and (not q.get("labels") or q["labels"] in x["labels"])]
            return ok(self.page(rows, q))
        m = re.fullmatch(r"/merge_requests/(\d+)(/.*)?", rest)
        mr = self.mrs.get((name, int(m.group(1)))) if m else None
        if m and mr is None:
            return ok({"message": "404 Not Found"}, 404)
        sub = (m.group(2) or "") if m else None
        if sub == "" and method == "GET":
            return ok(dict(self.shown(mr), head_pipeline=mr["_pipeline"]))
        if sub == "/discussions" and method == "GET":
            return ok(self.page(self.discussions[(name, mr["iid"])], q))
        if sub == "/notes" and method == "POST":
            n = self._note(self.who[tok], data["body"], False, False)
            self.discussions[(name, mr["iid"])].append({"id": "%040x" % next(self.ids), "individual_note": True,
                                                        "notes": [n]})
            self.touch(name, mr["iid"])
            return ok(n, 201)
        d = re.fullmatch(r"/discussions/([0-9a-f]+)/notes", sub or "")
        if d and method == "POST":
            thread = next((x for x in self.discussions[(name, mr["iid"])] if x["id"] == d.group(1)), None)
            if thread is None:
                return ok({"message": "404 Discussion Not Found"}, 404)
            return ok(self.reply(name, mr["iid"], thread, self.who[tok], data["body"]), 201)
        j = re.fullmatch(r"/pipelines/(\d+)/jobs", rest)
        if j and method == "GET":
            want = q.get("scope[]")
            return ok([x for x in self.jobs.get(int(j.group(1)), []) if not want or x["status"] == want])
        t = re.fullmatch(r"/jobs/(\d+)/trace", rest)
        if t and method == "GET":
            raw = self.traces.get(int(t.group(1)))
            return (200, raw) if raw is not None else ok({"message": "404"}, 404)
        return ok({"message": "404 (fake)"}, 404)


class FakeSessions:
    """crewadapter's patrol surface in memory. A turn is running from its send until end() or stop."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str, str]] = []
        self.projects: dict[str, str] = {}
        self.sent: list[tuple[str, str, str]] = []      # (slot, message, send_id)
        self.turns: dict[str, str] = {}
        self.stopped: list[str] = []
        self.fail_send = False

    def open_patrol(self, name, agent, model, label):
        if any(o[0] == name for o in self.opened):
            raise mod("bk").brain_error()("slot %s exists" % name)
        self.opened.append((name, agent, model, label))

    def patrol_project(self, name, path):
        self.projects[name] = path

    def patrol_send(self, name, message, send_id):
        if self.fail_send:
            raise mod("bk").brain_error()("KiroCrew：boom")
        self.sent.append((name, message, send_id))
        self.turns[send_id] = "running"

    def patrol_turn(self, name, send_id):
        return self.turns.get(send_id, "unknown")

    def patrol_stop(self, name):
        self.stopped.append(name)
        for slot, _, sid in self.sent:
            if slot == name and self.turns.get(sid) == "running":
                self.turns[sid] = "failed"

    def end(self, how: str = "ended") -> None:
        """Every running turn ends (*how*: ended, or failed: KiroCrew's error)."""
        for sid, st in list(self.turns.items()):
            if st == "running":
                self.turns[sid] = how

    def messages(self, slot: str | None = None) -> list[str]:
        return [m for s, m, _ in self.sent if slot is None or s == slot]


class FakeWorkspaces:
    """Workspaces.prepare without git: a path per MR; *moved*: the MRs whose origin head is not the one read."""

    def __init__(self, root: str = "/Users/alice/workplace/brain-team/patrol") -> None:
        self.root, self.prepared, self.moved = root, [], set()

    def prepare(self, item):
        if (item.project, item.iid) in self.moved:
            raise mod("team.patrol").Moved("origin moved")
        self.prepared.append((item.project, item.iid, item.head_sha))
        return "%s/%d-%d" % (self.root, item.project_id, item.iid)
