"""MR 巡检 (team/patrol.py), offline: GitLab in memory, the session surface in memory, and a clock both read.

PatrolGitLab answers what patrol and the bridge's hand-over poll ask: projects and their kit.json on the default branch,
MRs (paged; by state, all of them included, the label filter the bridge uses, updated_after), an MR with its head
pipeline, a pipeline's failed jobs and their logs, discussions (paged), an MR's notes (paged, oldest first, as GitLab's
notes API lists the discussions' notes), the notes posted, and the commits a push brought (ref_name old..new, with who
committed them). Its times are the clock's, so "before patrol started" is the same moment for both. FakeSessions is
crewadapter's patrol surface (open_patrol … patrol_stop): a turn runs until a test ends it.
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
SHA = {n: ("%x" % n) * 40 for n in range(1, 16)}      # a head sha per number: SHA[1] = "111…", SHA[10] = "aaa…"
RUNNER = "brain-bot@noreply.invalid"                   # the runner's git email (brain-kit's default kit.json bot_emails)


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
        self.commits: dict[tuple[str, str], tuple[str, str]] = {}    # (project, sha) -> (its parent, its committer)
        self.ranges = True          # False: GitLab will not list a range of commits (ref_name old..new is a 400)
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

    def push(self, path: str, iid: int, sha: str, by: str = RUNNER, **over) -> None:
        """A new head, a commit *by* committed on the old one: GitLab moves updated_at; the head has no pipeline until
        one is made."""
        m = self.mrs[(path, iid)]
        self.commits[(path, sha)] = (m["sha"], by)
        m.update(sha=sha, _pipeline=None, **over)
        self.touch(path, iid)

    def _line(self, path: str, sha: str) -> list[str]:
        out = []
        while (path, sha) in self.commits and sha not in out:
            out.append(sha)
            sha = self.commits[(path, sha)][0]
        return out

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
        c = re.fullmatch(r"/repository/commits/([0-9a-f]{40})", rest)
        if c and method == "GET" and (name, c.group(1)) in self.commits:
            return ok({"id": c.group(1), "committer_email": self.commits[(name, c.group(1))][1]})
        if rest == "/repository/commits" and method == "GET" and ".." in q.get("ref_name", ""):
            if not self.ranges:
                return ok({"message": "400 Bad request - ref_name is invalid"}, 400)
            old, new = q["ref_name"].split("..", 1)
            seen = set(self._line(name, old))
            return ok([{"id": c, "committer_email": self.commits[(name, c)][1]} for c in self._line(name, new)
                       if c not in seen])
        if rest == "/merge_requests" and method == "GET":
            since = q.get("updated_after")
            rows = [self.shown(x) for (pp, _), x in sorted(self.mrs.items()) if pp == name
                    and q.get("state", "all") in ("all", x["state"])
                    and (not q.get("labels") or q["labels"] in x["labels"])
                    and (not since or _when(x["updated_at"]) >= _when(since))]
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
        if sub == "/notes" and method == "GET":
            notes = sorted((n for x in self.discussions[(name, mr["iid"])] for n in x["notes"]),
                           key=lambda n: (_when(n["created_at"]), n["id"]))
            return ok(self.page(notes, q))
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


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class FakeSessions:
    """crewadapter's patrol surface in memory, answering as the session runner does. A turn is running from its send
    until end() or a stop. *refuse*: KiroCrew answers no to every message (patrol.Refused: nothing goes in). *lose*: it
    answers none, and the message never lands; the session runner follows such a turn as running until it is stopped."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str, str]] = []
        self.projects: dict[str, str] = {}
        self.asked: list[str] = []                      # the slot of every message patrol handed over
        self.sent: list[tuple[str, str, str]] = []      # (slot, message, send_id): the ones that landed
        self.turns: dict[str, str] = {}                 # send_id -> running | ended | failed
        self.slot_of: dict[str, str] = {}
        self.stopped: list[str] = []
        self.refuse = self.lose = False

    def open_patrol(self, name, agent, model, label):
        if any(o[0] == name for o in self.opened):
            raise mod("bk").brain_error()("slot %s exists" % name)
        self.opened.append((name, agent, model, label))

    def patrol_project(self, name, path):
        self.projects[name] = path

    def patrol_send(self, name, message, send_id):
        self.asked.append(name)
        if self.refuse:
            raise mod("team.patrol").Refused("KiroCrew 没收下：HTTP 409 slot_busy")
        self.turns[send_id], self.slot_of[send_id] = "running", name
        if self.lose:
            raise mod("bk").brain_error()("KiroCrew 90 秒内没有回应")
        self.sent.append((name, message, send_id))

    def patrol_turn(self, name, send_id):
        return self.turns.get(send_id, "unknown")

    def patrol_stop(self, name):
        self.stopped.append(name)
        for sid, slot in self.slot_of.items():
            if slot == name and self.turns[sid] == "running":
                self.turns[sid] = "failed"

    def running(self) -> list[str]:
        """The slot of every turn at work now."""
        return [self.slot_of[sid] for sid, st in self.turns.items() if st == "running"]

    def end(self, how: str = "ended") -> None:
        """Every running turn ends (*how*: ended, or failed: KiroCrew's error)."""
        for sid, st in list(self.turns.items()):
            if st == "running":
                self.turns[sid] = how

    def messages(self, slot: str | None = None) -> list[str]:
        return [m for s, m, _ in self.sent if slot is None or s == slot]


class FakeWorkspaces:
    """Workspaces without git: a path per MR; *moved*: the MRs whose origin head is not the one read; *dropped*: the
    clones deleted, (project id, iid)."""

    ident = (("user.name", "brain-bot"), ("user.email", RUNNER))

    def __init__(self, root: str = "/Users/alice/workplace/brain-team/patrol") -> None:
        self.root, self.prepared, self.moved, self.dropped = root, [], set(), []

    def prepare(self, item):
        if (item.project, item.iid) in self.moved:
            raise mod("team.patrol").Moved("origin moved")
        self.prepared.append((item.project, item.iid, item.head_sha))
        return "%s/%d-%d" % (self.root, item.project_id, item.iid)

    def drop(self, project_id, iid):
        self.dropped.append((project_id, iid))
