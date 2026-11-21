"""状态中转, offline: GitLab REST v4 over bare git repos (one per project), as the team server's state relay reads it, and
the status project it writes its snapshot into.

A code project's branches (search ^prefix, one by name, each with its head's id, parent_ids and committed_date), files
at any ref (raw), trees (recursive or not; 404 for a folder not there), the newest commit touching a path, and merge
bases come from its bare repo with git. Its MRs, their notes and pipelines are in memory, the MR list paged and filtered
by state and updated_after as GitLab does. The status project (status()) keeps its files per branch in memory, takes the
commits API's create and update actions (400 for a create of a file there or an update of one that is not, as GitLab
answers), and can be made to fail (fail_writes). Every request is kept in calls: (method, path, query, the token's user).
"""

from __future__ import annotations

import base64
import itertools
import json
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

BASE = "https://gitlab.example.com"
API = "/api/v4"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def git(bare, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(bare), *args], capture_output=True, text=True, check=check)


class RepoGitLab:
    def __init__(self) -> None:
        self.repos: dict[str, dict] = {}            # path -> {"id", "bare"}
        self.mrs: dict[tuple[str, int], dict] = {}
        self.notes: dict[tuple[str, int], list[dict]] = {}
        self.pipes: dict[tuple[str, int], list[dict]] = {}
        self.status_path: str | None = None
        self.status_files: dict[str, dict[str, str]] = {}     # branch -> path -> text
        self.status_commits: list[dict] = []
        self.fail_writes = 0                                    # the next n commits answer 500
        self.down: set[str] = set()                            # projects GitLab answers 502 for
        self.calls: list[tuple] = []
        self.who = {"bridge-tok": "brain-bridge", "alice-tok": "alice", "bob-tok": "bob", "status-tok": "status-bot"}
        self.ids = itertools.count(9000)

    # ---- the world -------------------------------------------------------------------------------------------- #

    def project(self, path: str, pid: int, bare) -> None:
        self.repos[path] = {"id": pid, "bare": str(bare)}

    def status(self, path: str) -> None:
        self.status_path = path

    def head(self, path: str, branch: str) -> str:
        return git(self.repos[path]["bare"], "rev-parse", "refs/heads/" + branch).stdout.strip()

    def mr(self, path: str, iid: int, source: str, state: str = "opened", labels=(), updated: float | None = None,
           **over) -> dict:
        at = iso(updated if updated is not None else time.time())
        m = {"iid": iid, "title": "MR %d" % iid, "state": state, "source_branch": source, "target_branch": "main",
             "sha": self.head(path, source) if state == "opened" else over.pop("sha", None),
             "web_url": "%s/%s/-/merge_requests/%d" % (BASE, path, iid), "labels": list(labels),
             "updated_at": at, "created_at": at, "merged_at": at if state == "merged" else None,
             "merge_commit_sha": None, "squash_commit_sha": None, "author": {"username": "alice"}}
        m.update(over)
        self.mrs[(path, iid)] = m
        self.notes.setdefault((path, iid), [])
        self.pipes.setdefault((path, iid), [])
        return m

    def note(self, path: str, iid: int, author: str, body: str) -> dict:
        n = {"id": next(self.ids), "body": body, "author": {"username": author}, "system": False,
             "created_at": iso(time.time())}
        self.notes[(path, iid)].append(n)
        self.mrs[(path, iid)]["updated_at"] = iso(time.time())     # GitLab moves an MR's updated_at with each note
        return n

    def pipeline(self, path: str, iid: int, status: str, sha: str | None = None) -> dict:
        """A pipeline at the MR's head (or *sha*), its newest; GitLab does not move the MR's updated_at for it."""
        p = {"id": next(self.ids), "status": status, "sha": sha or self.mrs[(path, iid)]["sha"],
             "updated_at": iso(time.time()), "finished_at": None}
        self.pipes[(path, iid)].insert(0, p)
        return p

    ENDS = {"raw": "/raw", "tree": "/repository/tree", "commits": "/repository/commits", "notes": "/notes",
            "pipelines": "/pipelines", "merge_base": "/merge_base", "mrs": "/merge_requests"}

    def asked(self, what: str, ref: str | None = None, since: int = 0) -> list[tuple]:
        """The GETs since call *since* of *what* (a key of ENDS: files, trees, a path's newest commit, an MR's notes or
        pipelines, merge bases, MR lists), only those at *ref* when given."""
        return [c for c in self.calls[since:] if c[0] == "GET" and c[1].endswith(self.ENDS[what])
                and (ref is None or ref in (c[2].get("ref"), c[2].get("ref_name")))]

    # ---- GitLab ------------------------------------------------------------------------------------------------ #

    @staticmethod
    def page(rows: list, q: dict) -> list:
        n, p = int(q.get("per_page", 20)), int(q.get("page", 1))
        return rows[(p - 1) * n: p * n]

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path, multi = unquote(u.path), parse_qs(u.query)
        q = {k: v[-1] for k, v in multi.items()}
        data = json.loads(body.decode()) if body else None
        tok = headers.get("PRIVATE-TOKEN")
        self.calls.append((method, path, q, self.who.get(tok)))
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        if tok not in self.who:
            return ok({"message": "401 Unauthorized"}, 401)
        if path == API + "/user":
            return ok({"id": 7, "username": self.who[tok]})
        if not path.startswith(API + "/projects/"):
            return ok({"message": "404 (fake)"}, 404)
        rest = path[len(API + "/projects/"):]
        if self.status_path and (rest == self.status_path or rest.startswith(self.status_path + "/")):
            return self.status_call(method, rest[len(self.status_path):], q, data, ok)
        name = next((p for p in self.repos if rest == p or rest.startswith(p + "/")), None)
        if name is None:
            return ok({"message": "404 Project Not Found"}, 404)
        if name in self.down:
            return 502, b"Bad Gateway"
        bare, sub = self.repos[name]["bare"], rest[len(name):]
        if sub == "":
            return ok({"id": self.repos[name]["id"], "path_with_namespace": name, "default_branch": "main"})
        if sub.startswith("/repository/files/") and sub.endswith("/raw"):
            p = subprocess.run(["git", "-C", bare, "show", "%s:%s" % (q.get("ref"), sub[len("/repository/files/"):-4])],
                               capture_output=True)
            return (200, p.stdout) if p.returncode == 0 else ok({"message": "404 File Not Found"}, 404)
        if sub == "/repository/branches":
            want = q.get("search", "")
            pre = want[1:] if want.startswith("^") else want
            names = [x for x in git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout.split()
                     if (x.startswith(pre) if want.startswith("^") else pre in x)]
            return ok(self.page([self.branch(bare, x) for x in sorted(names)], q))
        if sub.startswith("/repository/branches/"):
            b = sub[len("/repository/branches/"):]
            if git(bare, "rev-parse", "-q", "--verify", "refs/heads/" + b, check=False).returncode:
                return ok({"message": "404 Branch Not Found"}, 404)
            return ok(self.branch(bare, b))
        if sub == "/repository/tree":
            ref, where = q.get("ref"), q.get("path", "")
            args = ["ls-tree", "-r", "-t"] if q.get("recursive") == "true" else ["ls-tree"]
            p = git(bare, *args, ref + ":" + where if where else ref, check=False)
            if p.returncode:
                return ok({"message": "404 Tree Not Found"}, 404)
            rows = []
            for line in p.stdout.splitlines():
                meta, _, rel = line.partition("\t")
                mode, kind, oid = meta.split()
                full = "%s/%s" % (where, rel) if where else rel
                rows.append({"id": oid, "name": rel.rsplit("/", 1)[-1], "type": kind, "path": full, "mode": mode})
            return ok(self.page(rows, q))
        if sub == "/repository/commits" and method == "GET":
            p = git(bare, "log", "-1", "--format=%H%x00%cI", q["ref_name"], "--", q.get("path", "."), check=False)
            if p.returncode or not p.stdout.strip():
                return ok([])
            sha, when = p.stdout.strip().split("\0")
            return ok([{"id": sha, "committed_date": when}])
        if sub == "/repository/merge_base":
            a, b = multi.get("refs[]", ["", ""])[:2]
            p = git(bare, "merge-base", a, b, check=False)
            return ok({"id": p.stdout.strip()}) if p.returncode == 0 else ok({"message": "404 Not Found"}, 404)
        if sub == "/merge_requests":
            since = q.get("updated_after")
            rows = [m for (pp, _), m in sorted(self.mrs.items()) if pp == name and q.get("state", "all") in ("all", m["state"])
                    and (not since or m["updated_at"] >= since)]
            return ok(self.page(rows, q))
        parts = sub.split("/")
        if len(parts) >= 3 and parts[1] == "merge_requests" and parts[2].isdigit():
            key = (name, int(parts[2]))
            if key not in self.mrs:
                return ok({"message": "404 Not Found"}, 404)
            if parts[3:] == ["notes"]:
                return ok(self.page(self.notes[key], q))
            if parts[3:] == ["pipelines"]:
                return ok(self.pipes[key][: int(q.get("per_page", 20))])
        return ok({"message": "404 (fake)"}, 404)

    @staticmethod
    def branch(bare: str, name: str) -> dict:
        sha, parents, when = git(bare, "show", "-s", "--format=%H%x00%P%x00%cI", "refs/heads/" + name).stdout.strip().split("\0")
        return {"name": name, "commit": {"id": sha, "parent_ids": parents.split(), "committed_date": when}}

    def status_call(self, method, sub, q, data, ok):
        if sub == "" and method == "GET":
            return ok({"id": 99, "path_with_namespace": self.status_path, "default_branch": "main"})
        if sub.startswith("/repository/files/") and sub.endswith("/raw") and method == "GET":
            text = self.status_files.get(q.get("ref", ""), {}).get(unquote(sub[len("/repository/files/"):-4]))
            return (200, text.encode()) if text is not None else ok({"message": "404 File Not Found"}, 404)
        if sub == "/repository/commits" and method == "POST":
            if self.fail_writes:
                self.fail_writes -= 1
                return ok({"message": "500 Internal Server Error"}, 500)
            files = self.status_files.setdefault(data["branch"], {})
            for a in data["actions"]:
                there = a["file_path"] in files
                if (a["action"] == "create" and there) or (a["action"] == "update" and not there):
                    return ok({"message": "A file with this name %s" % ("already exists" if there else "doesn't exist")}, 400)
                text = a["content"] if a.get("encoding") != "base64" else base64.b64decode(a["content"]).decode()
                files[a["file_path"]] = text
            self.status_commits.append(data)
            return ok({"id": "%040x" % next(self.ids)}, 201)
        return ok({"message": "404 (fake)"}, 404)
