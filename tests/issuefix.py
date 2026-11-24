"""Brain takes work from a GitLab issue (team/issues.py), offline: a project with brain-kit and Lean installed and its
kit.json switching issue-label on, a bare repo as its origin, a fake GitLab served from that bare repo, the separate
signer (signer/brain_signer.py with its own identities and ledger) and the bridge.json and runnerd.json the app hosts.

IssueGitLab keeps issues, their label events and notes, members' roles, MRs and their notes in memory; branches,
files, commits, a commit's files and merge bases come from the bare repo, as GitLab's API shows them. Times move one
second per thing that happens, so "after the label" is GitLab's own order. A test can make any call answer 502.
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit

from appkit import mod, sh
from brainfix import make_project
from teamfix import keypair, load_signer

P, PID = "grp/repo", 7
BASE = "https://gl.example"
IID = 3
TITLE = "订单列表：按状态筛选"
BODY = ("要一个接口 GET /orders?status=created|cancelled，按状态返回订单。\n\n"
        "验收标准：\n- A-1 status=created 只返回已创建的订单\n- A-2 按创建时间倒序\n- A-3 最多 50 条\n"
        "- A-4 不认识的状态返回 422\n")
USERS = {"alice": 11, "bob": 12, "rita": 13, "carol": 14}
LEVELS = {"alice": 30, "bob": 30, "rita": 20, "carol": 40}      # Developer, Developer, Reporter, Maintainer
RUNNER = {"GIT_AUTHOR_NAME": "brain-bot", "GIT_AUTHOR_EMAIL": "brain-bot@noreply.invalid",
          "GIT_COMMITTER_NAME": "brain-bot", "GIT_COMMITTER_EMAIL": "brain-bot@noreply.invalid"}
CHECKS = ["python3 -c pass"]        # lean.json's checks: Lean's template has none, and then lean verify cannot pass


def set_checks(repo, checks, msg):
    """aidlc-docs/lean.json's checks in *repo*, committed on its branch (a person's commit: lean.json is theirs)."""
    path = repo / "aidlc-docs" / "lean.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("checks") != checks:
        path.write_text(json.dumps(dict(cfg, checks=checks), indent=2), encoding="utf-8")
        sh("git", "-C", repo, "commit", "-q", "-am", msg)


class IssueGitLab:
    """GitLab REST v4 for project P (path or id 7)."""

    def __init__(self, bare):
        self.bare = str(bare)
        self.t = 0
        self.who = {"bridge-tok": "brain-bridge", "signer-tok": "brain-signer"}
        self.levels = dict(LEVELS)
        self.issues, self.issue_events, self.issue_notes = {}, {}, {}
        self.mrs, self.mr_notes, self.versions = {}, {}, {}
        self.note_ids, self.event_ids, self.mr_ids = itertools.count(1000), itertools.count(201), itertools.count(1)
        self.fail = []              # [method, path regex, times]: GitLab answers 502 that many times
        self.calls = []

    # ---- the world --------------------------------------------------------------------------------------- #

    def tick(self):
        self.t += 1
        return "2026-11-21T09:%02d:%02d.000Z" % (self.t // 60, self.t % 60)

    def issue(self, iid=IID, title=TITLE, body=BODY, author="carol"):
        self.issues[iid] = {"iid": iid, "project_id": PID, "title": title, "description": body, "state": "opened",
                            "labels": [], "author": {"username": author}, "created_at": self.tick(),
                            "web_url": "%s/%s/-/issues/%d" % (BASE, P, iid)}
        self.issue_events[iid], self.issue_notes[iid] = [], []
        return self.issues[iid]

    def label(self, user, iid=IID, action="add", name="brain"):
        ev = {"id": next(self.event_ids), "action": action, "label": {"name": name},
              "user": {"id": USERS.get(user, 99), "username": user}, "created_at": self.tick()}
        self.issue_events[iid].append(ev)
        labels = self.issues[iid]["labels"]
        if action == "add" and name not in labels:
            labels.append(name)
        if action == "remove" and name in labels:
            labels.remove(name)
        return ev

    def edit(self, iid=IID, body=None, title=None, by="carol"):
        """Someone edits the issue: GitLab writes a system note for it."""
        i = self.issues[iid]
        if body is not None:
            i["description"] = body
            self._issue_note(iid, by, "changed the description", system=True)
        if title is not None:
            self._issue_note(iid, by, "changed title from **%s** to **%s**" % (i["title"], title), system=True)
            i["title"] = title

    def _issue_note(self, iid, author, body, system=False):
        n = {"id": next(self.note_ids), "body": body, "author": {"username": author}, "system": system,
             "created_at": self.tick()}
        self.issue_notes[iid].append(n)
        return n

    def head(self, branch):
        p = subprocess.run(["git", "-C", self.bare, "rev-parse", "-q", "--verify", "refs/heads/" + branch],
                           capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""

    def branches(self, prefix=""):
        out = subprocess.run(["git", "-C", self.bare, "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
                             capture_output=True, text=True).stdout.split()
        return sorted(b for b in out if b.startswith(prefix))

    def notes_on(self, iid, author="brain-bridge"):
        return [n["body"] for n in self.issue_notes[iid] if n["author"]["username"] == author]

    def mr_bodies(self, iid):
        return [n["body"] for n in self.mr_notes.get(iid, [])]

    def sync(self):
        """GitLab records an MR version once it sees a new head of its source branch."""
        for iid, m in self.mrs.items():
            head = self.head(m["source_branch"])
            vs = self.versions[iid]
            if head and (not vs or vs[-1]["head_commit_sha"] != head):
                vs.append({"id": len(vs) + 1, "head_commit_sha": head, "created_at": self.tick()})

    def mr(self, iid):
        m = self.mrs[iid]
        return dict({k: v for k, v in m.items()}, sha=self.head(m["source_branch"]), iid=iid, project_id=PID,
                    source_project_id=PID, target_project_id=PID,
                    web_url="%s/%s/-/merge_requests/%d" % (BASE, P, iid))

    # ---- GitLab -------------------------------------------------------------------------------------------- #

    @staticmethod
    def page(rows, q):
        n, p = int(q.get("per_page", 20)), int(q.get("page", 1))
        return rows[(p - 1) * n: p * n]

    def __call__(self, method, url, headers, body):
        self.sync()
        u = urlsplit(url)
        path, multi = unquote(u.path), parse_qs(u.query)
        q = {k: v[-1] for k, v in multi.items()}
        data = json.loads(body.decode()) if body else None
        self.calls.append((method, path, q, data))
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        for f in self.fail:
            if f[2] > 0 and f[0] == method and re.search(f[1], path):
                f[2] -= 1
                return 502, b"Bad Gateway"
        who = self.who.get(headers.get("PRIVATE-TOKEN"))
        if who is None:
            return ok({"message": "401 Unauthorized"}, 401)
        if path == "/api/v4/user":
            return ok({"id": 1, "username": who, "bot": True})
        if path == "/api/graphql" and method == "POST":
            return ok({"data": {"project": {"mergeRequest": {"notes": {"pageInfo": {"hasNextPage": False,
                                                                                     "endCursor": None}, "nodes": []}}}}})
        m = re.match(r"^/api/v4/projects/(?:%s|%d)(/.*)?$" % (re.escape(P), PID), path)
        if not m:
            return ok({"message": "404 Project Not Found"}, 404)
        rest = m.group(1) or ""
        if rest == "" and method == "GET":
            return ok({"id": PID, "default_branch": "main", "path_with_namespace": P, "web_url": BASE + "/" + P})
        r = re.fullmatch(r"/members/all/(\d+)", rest)
        if r:
            name = next((n for n, i in USERS.items() if i == int(r.group(1))), None)
            if name is None or name not in self.levels:
                return ok({"message": "404 Not found"}, 404)
            return ok({"id": USERS[name], "username": name, "access_level": self.levels[name]})
        if rest.startswith("/issues"):
            return self.issue_api(method, rest, q, data, who, ok)
        if rest.startswith("/merge_requests"):
            return self.mr_api(method, rest, q, data, who, ok)
        if rest.startswith("/repository/"):
            return self.repo_api(method, rest, q, multi, ok)
        return ok({"message": "404 (fake)"}, 404)

    def issue_api(self, method, rest, q, data, who, ok):
        if rest == "/issues" and method == "GET":
            rows = [i for _, i in sorted(self.issues.items()) if i["state"] == q.get("state", i["state"])
                    and (not q.get("labels") or q["labels"] in i["labels"])]
            return ok(self.page(rows, q))
        r = re.fullmatch(r"/issues/(\d+)(/.*)?", rest)
        iid = int(r.group(1)) if r else 0
        if iid not in self.issues:
            return ok({"message": "404 Issue Not Found"}, 404)
        sub = r.group(2) or ""
        if sub == "" and method == "GET":
            return ok(self.issues[iid])
        if sub == "/resource_label_events" and method == "GET":
            return ok(self.page(self.issue_events[iid], q))
        if sub == "/notes" and method == "GET":
            return ok(self.page(self.issue_notes[iid], q))
        if sub == "/notes" and method == "POST":
            return ok(self._issue_note(iid, who, data["body"]), 201)
        return ok({"message": "404 (fake)"}, 404)

    def mr_api(self, method, rest, q, data, who, ok):
        if rest == "/merge_requests" and method == "GET":
            rows = [self.mr(i) for i in sorted(self.mrs)]
            rows = [m for m in rows if m["state"] == q.get("state", m["state"])
                    and m["source_branch"] == q.get("source_branch", m["source_branch"])
                    and (not q.get("labels") or q["labels"] in m["labels"])]
            return ok(self.page(rows, q))
        if rest == "/merge_requests" and method == "POST":
            if any(m["source_branch"] == data["source_branch"] and m["state"] == "opened" for m in self.mrs.values()):
                return ok({"message": ["Another open merge request already exists for this source branch"]}, 409)
            iid = next(self.mr_ids)
            self.mrs[iid] = {"title": data["title"], "description": data.get("description") or "",
                             "source_branch": data["source_branch"], "target_branch": data["target_branch"],
                             "labels": [x for x in (data.get("labels") or "").split(",") if x], "state": "opened",
                             "author": {"username": who}}
            self.mr_notes[iid], self.versions[iid] = [], []
            self.sync()
            return ok(self.mr(iid), 201)
        r = re.fullmatch(r"/merge_requests/(\d+)(/.*)?", rest)
        iid = int(r.group(1)) if r else 0
        if iid not in self.mrs:
            return ok({"message": "404 Not Found"}, 404)
        sub, m = r.group(2) or "", self.mrs[iid]
        if sub == "" and method == "GET":
            return ok(self.mr(iid))
        if sub == "" and method == "PUT":
            for name in (data.get("remove_labels") or "").split(","):
                if name in m["labels"]:
                    m["labels"].remove(name)
            for name in (data.get("add_labels") or "").split(","):
                if name and name not in m["labels"]:
                    m["labels"].append(name)
            if data.get("state_event") == "close":
                m["state"] = "closed"
            return ok(self.mr(iid))
        if sub == "/notes" and method == "GET":
            return ok(self.page(self.mr_notes[iid], q))
        if sub == "/notes" and method == "POST":
            n = {"id": next(self.note_ids), "body": data["body"], "author": {"username": who}, "system": False,
                 "created_at": self.tick()}
            n["updated_at"] = n["created_at"]
            self.mr_notes[iid].append(n)
            return ok(n, 201)
        if sub == "/versions":
            return ok(self.page(list(reversed(self.versions[iid])), q))
        if sub == "/resource_label_events":
            return ok([])
        return ok({"message": "404 (fake)"}, 404)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.bare] + list(args), capture_output=True)

    def repo_api(self, method, rest, q, multi, ok):
        r = re.fullmatch(r"/repository/files/(.+)/raw", rest)
        if r and method == "GET":
            p = self.git("show", "%s:%s" % (q.get("ref"), r.group(1)))
            return (200, p.stdout) if p.returncode == 0 else ok({"message": "404 File Not Found"}, 404)
        r = re.fullmatch(r"/repository/commits/([^/]+)/diff", rest)
        if r and method == "GET":
            p = self.git("diff-tree", "-r", "--no-commit-id", "-M", "--name-status", r.group(1))
            if p.returncode != 0:
                return ok({"message": "404 Commit Not Found"}, 404)
            rows = []
            for line in p.stdout.decode().splitlines():
                f = line.split("\t")
                rows.append({"old_path": f[1], "new_path": f[-1], "new_file": f[0] == "A",
                             "deleted_file": f[0] == "D", "renamed_file": f[0].startswith("R")})
            return ok(self.page(rows, q))
        r = re.fullmatch(r"/repository/commits/([^/]+)", rest)
        if r and method == "GET":
            c = self.commit(r.group(1))
            return ok(c) if c else ok({"message": "404 Commit Not Found"}, 404)
        if rest == "/repository/merge_base" and method == "GET":
            refs = multi.get("refs[]") or []
            p = self.git("merge-base", *refs)
            return ok({"id": p.stdout.decode().strip()}) if p.returncode == 0 and len(refs) == 2 else \
                ok({"message": "400"}, 400)
        return ok({"message": "404 (fake)"}, 404)

    def commit(self, rev):
        p = subprocess.run(["git", "-C", self.bare, "show", "-s", "--format=%H%x00%P%x00%an%x00%ae%x00%cn%x00%ce",
                            rev + "^{commit}"], capture_output=True, text=True)
        f = p.stdout.rstrip("\n").split("\0")
        if p.returncode != 0 or len(f) != 6:
            return None
        return {"id": f[0], "parent_ids": f[1].split(), "author_name": f[2], "author_email": f[3],
                "committer_name": f[4], "committer_email": f[5]}


def make_issue_world(tmp_path, home, monkeypatch, *, kit_extra=None, checks=CHECKS):
    """The project (prefix PAY) on origin with issue-label on and *checks* in its lean.json, issue #3 filed by carol,
    the bridge.json, runnerd.json and signer of a same-user runner, and brain-authz naming alice, bob and carol (rita
    is not in it)."""
    kit = {"gitlab": {"base": BASE, "project": P}, "bot_users": ["brain-bot"], "bot_emails": ["brain-bot@noreply.invalid"],
           "bridge_user": "brain-bridge", "work_sources": ["mr-handoff", "issue-label"]}
    kit.update(kit_extra or {})
    repo = make_project(tmp_path, name="repo", origin=True, kit=kit)
    set_checks(repo, checks, "lean verify runs the tests")
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    bare = tmp_path / "repo-origin.git"
    gl = IssueGitLab(bare)
    gl.issue()
    (tmp_path / "keys").mkdir()
    key, allowed = keypair(tmp_path / "keys")
    ids = {"users": {"alice": {}, "bob": {}, "carol": {}}, "roles": {"product": ["carol"], "builder": ["bob"]}}
    app_ids, signer_ids = tmp_path / "app-identities.json", tmp_path / "signer-identities.json"
    app_ids.write_text(json.dumps(ids))
    signer_ids.write_text(json.dumps(ids))
    spool, sspool, runs_root = tmp_path / "spool", tmp_path / "signer-spool", tmp_path / "work" / "runs"
    runs_root.mkdir(parents=True)
    bridge_cfg = tmp_path / "bridge.json"
    bridge_cfg.write_text(json.dumps({
        "projects": [P], "gitlab": {"base": BASE, "token_env": "TEST_BRIDGE_TOKEN"}, "identities": str(app_ids),
        "signer": {"type": "spool", "spool_dir": str(sspool), "allowed_signers": str(allowed), "timeout": 5,
                   "poll": 0.02},
        "vouchers_dir": str(tmp_path / "app-vouchers"), "spool_dir": str(spool), "ledger": str(tmp_path / "ledger.sqlite"),
        "runner_runs_root": str(runs_root), "kirocrew": {"agent": "brain-run", "memory_mode": "incognito"},
        "pseudonym_salt": "s", "interval": 60}))
    runnerd_cfg = tmp_path / "runnerd.json"
    runnerd_cfg.write_text(json.dumps({
        "spool_dir": str(spool), "cache_dir": str(tmp_path / "work" / "cache"), "runs_root": str(runs_root),
        "allowed_signers": str(allowed), "clone_url": str(bare), "git_name": "brain-bot",
        "git_email": "brain-bot@noreply.invalid"}))
    monkeypatch.setenv("TEST_BRIDGE_TOKEN", "bridge-tok")
    bs = load_signer()
    rest = __import__("brainkit.rest", fromlist=["GitLab"])
    scfg = {"spool": str(sspool), "signing_key": str(key), "projects": [P], "identities": str(signer_ids),
            "vouchers_dir": str(tmp_path / "signer-vouchers"), "publish_vouchers": False,
            "ledger": str(tmp_path / "signer.sqlite"), "pseudonym_salt": "s", "contract_ttl_hours": 72}
    signer = bs.Signer(scfg, rest.GitLab(BASE, "signer-tok", gl), None, out=lambda *_: None)
    team = {"enabled": True, "bridge_config": str(bridge_cfg), "runnerd_config": str(runnerd_cfg), "dev_same_user": True}
    return {"repo": repo, "bare": bare, "gl": gl, "signer": signer, "bs": bs, "team": team, "runs_root": runs_root,
            "spool": spool, "sspool": sspool, "tmp": tmp_path, "runnerd_cfg": runnerd_cfg, "signer_ids": signer_ids,
            "allowed": allowed, "scfg": scfg}


def other_signer(world, **cfg):
    """The world's signer with other settings (its own identities file, say), on the same spool, GitLab and ledger."""
    rest = __import__("brainkit.rest", fromlist=["GitLab"])
    return world["bs"].Signer(dict(world["scfg"], **cfg), rest.GitLab(BASE, "signer-tok", world["gl"]), None,
                              out=lambda *_: None)


def scratch(world, name, rev):
    """A clone of origin at *rev* to change history in, committing as the runner's bot."""
    path = world["tmp"] / name
    if not path.exists():
        sh("git", "clone", "-q", str(world["bare"]), path)
    sh("git", "-C", path, "fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
    sh("git", "-C", path, "checkout", "-q", "-f", "--detach", rev)
    return path


def commit_as_runner(path, msg, amend=False):
    sh("git", "-C", path, "add", "-A")
    sh("git", "-C", path, "commit", "-q", "--allow-empty", "-m", msg, *(["--amend"] if amend else []), env=RUNNER)
    return sh("git", "-C", path, "rev-parse", "HEAD").stdout.strip()


def started_from_issue(origin, work, project, labeler, *, base=BASE, iid=IID, title=TITLE, body=BODY):
    """What 从 issue 接活 leaves on *origin* (a bare repo) once issue #*iid* that *labeler* labelled has started, made by
    its own code in a clone of its own that commits as runnerd's bot (issues.Clones): claim/<KEY> filed for them
    (intents.add, as IssueSource._claim files it) and the start commit pushed as ai/<labeler>/<KEY>-issue-<iid>
    (issues.make_start_commit: a new Lean intent, run.json with origin remote and no hand-over). -> (key, branch, the
    start commit)."""
    bk, issues = mod("bk"), mod("team.issues")
    ident = (("user.name", RUNNER["GIT_AUTHOR_NAME"]), ("user.email", RUNNER["GIT_AUTHOR_EMAIL"]))
    path = issues.Clones(str(work), str(origin), ident).prepare(project, PID, "main")
    kit = bk.m("config").load(path)
    url = "%s/%s/-/issues/%d" % (base, project, iid)
    key = bk.m("intents").add(path, kit, "product", title, body, refs=[url], owner=labeler)["key"]
    branch = bk.m("common").branch_name(kit["branch_prefix"], labeler, key, "issue-%d" % iid)
    row = issues.IssueRow(project=project, iid=iid, labeler=labeler, title=title, key=key)
    sha = issues.make_start_commit(path, kit, row, branch, kit["default_branch"])
    sh("git", "-C", path, "push", "-q", "origin", "%s:refs/heads/%s" % (sha, branch))
    return key, branch, sha
