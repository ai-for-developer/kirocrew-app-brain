"""测试环境报错 (team/staging.py), offline: the patrol tests' in-memory GitLab, answering as well what staging asks.

StagingGitLab adds a project's Issues (opened, listed by label and state, read, closed and reopened with state_event,
commented on), the MRs GitLab lists for a commit (commits/:sha/merge_requests), a branch's head, the MRs from a source
branch (state all or opened) and an MR opened through the API. *lose_issue_answer*: GitLab makes the next Issue but its
answer never comes back (a 502).
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from patrolfix import API, BASE, SHA, PatrolGitLab


class StagingGitLab(PatrolGitLab):
    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.issues: dict[tuple[str, int], dict] = {}
        self.merged_by: dict[tuple[str, str], list[dict]] = {}
        self.heads: dict[tuple[str, str], str] = {}
        self.lose_issue_answer = False

    # ---- the world ------------------------------------------------------------------------------------------- #

    def merged(self, path: str, sha: str, iid: int, title: str = "", state: str = "merged") -> None:
        """GitLab lists the MR !iid for the commit *sha* (merged into it, or *state*)."""
        self.merged_by.setdefault((path, sha), []).append(
            {"iid": iid, "title": title or "MR %d" % iid, "state": state,
             "web_url": "%s/%s/-/merge_requests/%d" % (BASE, path, iid)})

    def opened(self, path: str) -> list[dict]:
        return [i for (p, _), i in sorted(self.issues.items()) if p == path]

    def notes(self, path: str, iid: int) -> list[str]:
        return [n["body"] for n in self.issues[(path, iid)]["_notes"]]

    def close_issue(self, path: str, iid: int) -> None:
        self.issues[(path, iid)]["state"] = "closed"

    def asked_staging(self, method: str, pattern: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == method and re.search(pattern, c[1])]

    # ---- GitLab ---------------------------------------------------------------------------------------------- #

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path, q = unquote(u.path), dict(parse_qsl(u.query))
        m = re.fullmatch(API + r"/projects/(.+?)/(issues(?:/\d+(?:/notes)?)?|repository/commits/[0-9a-f]+/merge_requests"
                               r"|repository/branches/.+|merge_requests)", path)
        hit = self._project(m.group(1)) if m and headers.get("PRIVATE-TOKEN") in self.who else None
        if hit is None or (m.group(2) == "merge_requests" and method == "GET" and "source_branch" not in q):
            return super().__call__(method, url, headers, body)
        data = json.loads(body.decode()) if body else None
        self.calls.append((method, path, q, data))
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        name, p = hit
        rest = m.group(2)
        if rest == "issues" and method == "POST":
            iid = 1 + max((i for (pp, i) in self.issues if pp == name), default=0)
            issue = {"iid": iid, "project_id": p["id"], "title": data["title"], "description": data.get("description") or "",
                     "labels": [x for x in str(data.get("labels") or "").split(",") if x], "state": "opened",
                     "web_url": "%s/%s/-/issues/%d" % (BASE, name, iid), "_notes": []}
            self.issues[(name, iid)] = issue
            if self.lose_issue_answer:
                self.lose_issue_answer = False
                return ok({"message": "502 Bad Gateway"}, 502)
            return ok(self.shown(issue), 201)
        if rest == "issues" and method == "GET":
            rows = [self.shown(i) for i in self.opened(name) if q.get("state", "all") in ("all", i["state"])
                    and (not q.get("labels") or q["labels"] in i["labels"])]
            return ok(self.page(rows, q))
        i = re.fullmatch(r"issues/(\d+)(/notes)?", rest)
        if i:
            issue = self.issues.get((name, int(i.group(1))))
            if issue is None:
                return ok({"message": "404 Not found"}, 404)
            if i.group(2) and method == "POST":
                note = {"id": next(self.ids), "body": data["body"], "author": {"username": self.who[headers["PRIVATE-TOKEN"]]}}
                issue["_notes"].append(note)
                return ok(note, 201)
            if method == "PUT":
                issue["state"] = {"close": "closed", "reopen": "opened"}.get(data.get("state_event"), issue["state"])
            return ok(self.shown(issue))
        c = re.fullmatch(r"repository/commits/([0-9a-f]+)/merge_requests", rest)
        if c:
            return ok(self.merged_by.get((name, c.group(1)), []))
        b = re.fullmatch(r"repository/branches/(.+)", rest)
        if b:
            return ok({"name": b.group(1), "commit": {"id": self.heads.get((name, b.group(1)), SHA[9])}})
        if method == "GET":
            rows = [self.shown(x) for (pp, _), x in sorted(self.mrs.items(), reverse=True) if pp == name
                    and x["source_branch"] == q["source_branch"] and q.get("state", "all") in ("all", x["state"])]
            return ok(rows)
        iid = 1 + max((n for (pp, n) in self.mrs if pp == name), default=0)
        mr = self.mr(name, iid, source=data["source_branch"], title=data["title"], description=data.get("description"),
                     target_branch=data["target_branch"], labels=[x for x in data.get("labels", "").split(",") if x])
        return ok(self.shown(mr), 201)
