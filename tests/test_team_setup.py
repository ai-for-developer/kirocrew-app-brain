"""一键建运行机 (backend/core/team/setup.py), offline: a fake GitLab in memory (project access tokens with their own bot
users, labels, members, files per branch, commits, MRs), the real brain-kit (brain-runnerd setup), the real git
(ls-remote: the project's SSH address is a local bare repo, or a path that is not there) and ssh-keygen.

The table builds the same-user runner on a first run, makes nothing new on a second one, converges after a run that
stopped part-way, waits on the kit.json MR until it is merged, falls back to a project token when this machine's SSH does
not reach GitLab, and never writes the person's token anywhere. Through the app: the run goes on in the background, team
mode is on afterwards with its TeamRuntime and the real signer as a child process, which stops with team mode.
"""

import copy
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import pytest

from appkit import BRAINKIT, ROOT, mod, sh
from brainfix import make_service, needs_brainkit, run
from http_fakes import FakeWeb, call

pytestmark = [needs_brainkit, pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode,
                                                 reason="ssh-keygen not found")]

BASE = "https://gitlab.example.com"
P, PID = "grp/pay", 42
API = "/api/v4"
KIT = ".brain/kit.json"
# the scopes GitLab gives a project access token (its access token scopes docs): read_user, sudo, admin_mode are not
PROJECT_TOKEN_SCOPES = {"api", "read_api", "read_repository", "write_repository", "read_registry", "write_registry",
                        "self_rotate", "create_runner", "manage_runner", "ai_features", "k8s_proxy"}


def utc_today():
    return datetime.now(timezone.utc).date()


class FakeGitLab:
    """GitLab REST v4 for project grp/pay (id 42). Tokens: the person's (alice, Maintainer) and the project access
    tokens it makes, each with its own bot user, only with scopes a project token may have and ending at most 365 days
    from today (400 otherwise); rotating one gives a new value of the same bot user and revokes the old one, and
    rotating a revoked one revokes every token of its bot user (GitLab's reuse detection). A token ends at the start of
    its expires_at (UTC). members: (username, access level), and a token's bot user at its level, as GitLab adds it.
    GET /user needs api, read_api or read_user, as on GitLab. Every request is kept in calls
    (method, path, query, body); fail holds one-shot answers (method, path, status, who): who is None (any token), a
    token, or a test on the token."""

    def __init__(self, admin, ssh_url, kit, level=40,
                 members=(("alice", 40), ("bob", 30), ("carol", 30), ("rita", 20)), labels=("ai-handoff",)):
        self.users = {admin: {"id": 1, "username": "alice", "name": "Alice", "email": "alice@example.com"}}
        self.scopes = {admin: ["api"]}
        self.level, self.members, self.labels = level, list(members), list(labels)
        self.ssh_url = ssh_url
        self.files = {"main": {".brain/kit.json": kit}}
        self.tokens, self.mrs, self.calls, self.fail = [], [], [], []
        self.revoked = set()

    def project(self):
        return {"id": PID, "path_with_namespace": P, "default_branch": "main", "web_url": BASE + "/" + P,
                "ssh_url_to_repo": self.ssh_url, "http_url_to_repo": "%s/%s.git" % (BASE, P),
                "permissions": {"project_access": {"access_level": self.level}, "group_access": None}}

    def made(self, name):
        """The tokens of this name POST /access_tokens made (a rotation's new value is not one)."""
        return [t for t in self.tokens if t["name"] == name and "rotated_from" not in t]

    def live(self, name):
        """The token of this name that is active now."""
        got = [t for t in self.tokens if t["name"] == name and self.active(t)]
        assert len(got) == 1, got
        return got[0]

    def active(self, t):
        return t["token"] not in self.revoked and not (t.get("expires_at") and t["expires_at"] <= utc_today().isoformat())

    def shown(self, t):
        """A token as GET /access_tokens/:id shows it: no value, whether it is active or revoked."""
        return dict({k: v for k, v in t.items() if k != "token"}, active=self.active(t), revoked=t["token"] in self.revoked)

    def mint(self, fields, user_id=None):
        """A new token value: of a new bot user, or of *user_id*'s (a rotation)."""
        n = len(self.tokens) + 1
        value = "fake-bot-%d-%s" % (n, secrets.token_hex(8))
        if user_id is None:
            user_id, user = 100 + n, "project_%d_bot_%s" % (PID, secrets.token_hex(4))
            self.users[value] = {"id": user_id, "username": user, "name": fields["name"], "bot": True,
                                 "email": "%s@noreply.gitlab.example.com" % user}
            self.members.append((user, fields["access_level"]))
        else:
            self.users[value] = next(u for u in self.users.values() if u["id"] == user_id)
        self.scopes[value] = list(fields["scopes"])
        t = dict(fields, id=500 + n, user_id=user_id, token=value)
        self.tokens.append(t)
        return t

    @staticmethod
    def too_late(data):
        return bool(data.get("expires_at")) and data["expires_at"] > (utc_today() + timedelta(days=365)).isoformat()

    def merge(self, iid):
        mr = next(m for m in self.mrs if m["iid"] == iid)
        self.files["main"] = dict(self.files[mr["source_branch"]])
        mr["state"] = "merged"
        if mr.get("remove_source_branch"):
            self.files.pop(mr["source_branch"], None)

    def writes(self, since=0):
        return [c for c in self.calls[since:] if c[0] in ("POST", "PUT", "DELETE")]

    @staticmethod
    def page(rows, q):
        n, p = int(q.get("per_page", 20)), int(q.get("page", 1))
        return rows[(p - 1) * n: p * n]

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path, q = unquote(u.path), dict(parse_qsl(u.query))
        data = json.loads(body.decode()) if body else None
        tok = headers.get("PRIVATE-TOKEN")
        self.calls.append((method, path, q, data))
        for f in self.fail:
            who = f[3]
            if f[0] == method and f[1] == path and (who is None or who == tok or callable(who) and who(tok)):
                self.fail.remove(f)
                return f[2], b'{"message": "500 (fake)"}'
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        mine = next((t for t in self.tokens if t["token"] == tok), None)
        if tok not in self.users or mine is not None and not self.active(mine):
            return ok({"message": "401 Unauthorized"}, 401)
        if path == API + "/user":
            if not {"api", "read_api", "read_user"} & set(self.scopes[tok]):
                return ok({"error": "insufficient_scope"}, 403)
            return ok(self.users[tok])
        pre = None
        for key in (P, str(PID)):
            if path == "%s/projects/%s" % (API, key) or path.startswith("%s/projects/%s/" % (API, key)):
                pre = "%s/projects/%s" % (API, key)
        if pre is None:
            return ok({"message": "404 Project Not Found"}, 404)
        rest = path[len(pre):]
        if rest == "" and method == "GET":
            return ok(self.project())
        m = re.fullmatch(r"/repository/files/(.+)/raw", rest)
        if m and method == "GET":
            text = self.files.get(q.get("ref"), {}).get(m.group(1))
            return (200, text.encode()) if text is not None else ok({"message": "404 File Not Found"}, 404)
        if rest == "/access_tokens" and method == "POST":
            if not data.get("scopes") or set(data["scopes"]) - PROJECT_TOKEN_SCOPES:
                return ok({"error": "scopes does not have a valid value"}, 400)
            if self.too_late(data):
                return ok({"message": "400 Bad request - expires_at is more than 365 days away"}, 400)
            t = self.mint(data)
            return ok(dict(self.shown(t), token=t["token"]), 201)
        m = re.fullmatch(r"/access_tokens/(\d+)(/rotate)?", rest)
        old = m and next((t for t in self.tokens if t["id"] == int(m.group(1))), None)
        if m and old is None:
            return ok({"message": "404 Not Found"}, 404)
        if m and method == "GET" and not m.group(2):
            return ok(self.shown(old))
        if m and method == "POST" and m.group(2):
            if not self.active(old):
                self.revoked |= {t["token"] for t in self.tokens if t["user_id"] == old["user_id"]}
                return ok({"message": "401 Unauthorized"}, 401)
            if self.too_late(data or {}):
                return ok({"message": "400 Bad request - expires_at is more than 365 days away"}, 400)
            self.revoked.add(old["token"])
            end = (data or {}).get("expires_at") or (utc_today() + timedelta(days=7)).isoformat()
            t = self.mint(dict({k: old[k] for k in ("name", "scopes", "access_level")}, expires_at=end,
                               rotated_from=old["id"]), user_id=old["user_id"])
            return ok(dict(self.shown(t), token=t["token"]))
        if rest == "/labels" and method == "GET":
            return ok(self.page([{"id": i, "name": n} for i, n in enumerate(self.labels)], q))
        if rest == "/labels" and method == "POST":
            if data["name"] in self.labels:
                return ok({"message": "Label already exists"}, 409)
            self.labels.append(data["name"])
            return ok({"name": data["name"], "color": data["color"]}, 201)
        if rest == "/members/all" and method == "GET":
            return ok(self.page([{"id": i, "username": n, "access_level": lv}
                                 for i, (n, lv) in enumerate(self.members)], q))
        m = re.fullmatch(r"/repository/branches/(.+)", rest)
        if m and method == "GET":
            return ok({"name": m.group(1)}) if m.group(1) in self.files else ok({"message": "404 Branch Not Found"}, 404)
        if rest == "/repository/branches" and method == "POST":
            self.files[data["branch"]] = dict(self.files[data["ref"]])
            return ok({"name": data["branch"]}, 201)
        if rest == "/repository/commits" and method == "POST":
            for a in data["actions"]:
                self.files[data["branch"]][a["file_path"]] = a["content"]
            return ok({"id": secrets.token_hex(20)}, 201)
        if rest == "/merge_requests" and method == "GET":
            return ok([m for m in self.mrs if m["state"] == q.get("state") and m["source_branch"] == q.get("source_branch")])
        if rest == "/merge_requests" and method == "POST":
            iid = 7 + len(self.mrs)
            mr = dict(data, iid=iid, state="opened", web_url="%s/%s/-/merge_requests/%d" % (BASE, P, iid))
            self.mrs.append(mr)
            return ok(mr, 201)
        return ok({"message": "404 (fake)"}, 404)


def kit_file(tmp_path, **over):
    """kit.json as brain-kit writes it (its DEFAULTS, sorted keys, indent 2)."""
    bk = mod("bk")
    path = tmp_path / "kit.json"
    bk.m("common").write_json(str(path), dict(copy.deepcopy(bk.m("config").DEFAULTS), req_prefix="PAY", **over))
    return path.read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path, home):
    bk = mod("bk")
    assert bk.load(str(BRAINKIT))["ok"]
    bare = tmp_path / "origin" / "grp" / "pay.git"
    bare.parent.mkdir(parents=True)
    sh("git", "init", "-q", "--bare", bare)
    admin = "fake-" + secrets.token_hex(12)      # made at run time: no token-looking text in the source
    w = {"tmp": tmp_path, "home": home, "admin": admin, "bare": bare, "team": [], "signer": []}
    w["gl"] = FakeGitLab(admin, str(bare), kit_file(tmp_path))
    yield w
    bk.unload()


def team_mode(world, ok=True):
    def on(want):
        world["team"].append(want)
        return {"ok": ok, "error": "" if ok else "桥没准备好", "error_raw": "" if ok else "bridge config: x"}
    return on


def fake_signer(world):
    def ensure(paths):
        world["signer"].append(json.loads(paths.signer_json.read_text()))
        return 4242, len(world["signer"]) == 1
    return ensure


def fake_reach(world):
    """git ls-remote, but for GitLab's HTTPS address (no network): that one is reached with a live token the fake
    GitLab made, unless world["https_down"]. Every other address (the local bare repo, a path not there) is real git."""
    common = mod("bk").m("common")

    def reach(url):
        if url.startswith("https://"):
            live = [t["token"] for t in world["gl"].tokens if world["gl"].active(t)]
            ok = not world.get("https_down") and any("oauth2:%s@" % quote(t, safe="") in url for t in live)
            return ok, "" if ok else "fatal: unable to access '%s': The requested URL returned error: 403" % url
        p = common.git_net(str(world["tmp"]), "ls-remote", url, "HEAD", timeout=20, check=False)
        return p.returncode == 0, (p.stderr or p.stdout).strip()
    return reach


def setup_ctx(world, push="auto", roles=None, patrol=True):
    setup, rest = mod("team.setup"), mod("bk").m("rest")
    inp = setup.SetupInput.parse({"gitlab_base": BASE, "project": P, "admin_token": world["admin"],
                                  "roles": roles or {"builder": "bob"}, "push": push, "patrol": patrol})
    paths = setup.Paths.default(BRAINKIT, root=world["tmp"] / "team-setup")
    return setup.Ctx(inp, paths, gitlab=lambda tok: rest.GitLab(BASE, tok, world["gl"]), team_mode=team_mode(world),
                     signer=fake_signer(world), reach=fake_reach(world))


def build(world, **kw):
    setup = mod("team.setup")
    ctx = setup_ctx(world, **kw)
    with setup.Lock(ctx.paths.root):
        return setup.run(ctx), ctx.paths


def statuses(view):
    return {s["id"]: s["status"] for s in view["steps"]}


def step(view, sid):
    return next(s for s in view["steps"] if s["id"] == sid)


def saved_tokens(state, base=BASE):
    """The project tokens setup-state.json keeps for grp/pay on GitLab *base*."""
    return state["tokens"]["%s/projects/%d" % (base, PID)]


def days_left(expires_at):
    return (date.fromisoformat(expires_at) - utc_today()).days


def everything_done(view, kit_mr="waiting"):
    want = {s["id"]: "done" for s in view["steps"]}
    want["kit_mr"] = kit_mr
    return statuses(view) == want


def files_under(*roots):
    out = {}
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                with open(p, "rb") as fh:
                    out[p] = fh.read()
    return out


def mode(path):
    return oct(os.stat(path).st_mode & 0o777)


def test_a_first_run_builds_the_runner_and_waits_for_the_kit_mr(world):
    view, ps = build(world)
    gl = world["gl"]
    assert everything_done(view), [(s["id"], s["status"], s["message"], s["detail"][:200]) for s in view["steps"]]
    by = {s["id"]: s for s in view["steps"]}
    bridge = gl.made("brain-bridge")
    assert len(bridge) == 1 and bridge[0]["scopes"] == ["api"] and bridge[0]["access_level"] == 30
    assert days_left(bridge[0]["expires_at"]) in (363, 364)                 # a year less a day, from today in UTC
    assert not gl.made("brain-bot")                                         # this machine's git reaches the project
    bot = gl.users[bridge[0]["token"]]["username"]
    assert sorted(gl.labels) == ["ai-handoff", "ai-handoff-rejected", "ai-running", "brain-reflect"]
    # the token's value is in its own file only, 0600; the person's token is nowhere
    assert (ps.root / "bridge-gitlab-token").read_text().strip() == bridge[0]["token"]
    assert mode(ps.root / "bridge-gitlab-token") == "0o600" and mode(ps.key) == "0o600" and mode(ps.root) == "0o700"
    ids = json.loads(ps.identities.read_text())            # rita, a Reporter, and the bridge's bot user are no users
    assert ids == {"users": {"alice": {}, "bob": {}, "carol": {}},
                   "roles": {"product": ["alice"], "builder": ["bob"], "security": ["alice"], "data": ["alice"]}}
    pub = subprocess.run(["ssh-keygen", "-y", "-f", str(ps.key)], capture_output=True, text=True).stdout.split()
    assert ps.allowed.read_text() == 'brain-bridge namespaces="brain-voucher" %s %s\n' % (pub[0], pub[1])
    b, r, s = (json.loads(p.read_text()) for p in (ps.bridge_json, ps.runnerd_json, ps.signer_json))
    assert b["projects"] == [P] and b["gitlab"] == {"base": BASE, "token_env": "BRAIN_TEAM_NO_TOKEN_ENV",
                                                     "token_file": str(ps.root / "bridge-gitlab-token")}
    assert b["signer"] == {"type": "spool", "spool_dir": str(ps.root / "signer-spool"), "allowed_signers": str(ps.allowed)}
    assert b["publish_vouchers"] is False and "intake_dir" not in b and "signing_key" not in b
    assert re.fullmatch(r"[0-9a-f]{32}", b["pseudonym_salt"]) and s["pseudonym_salt"] == b["pseudonym_salt"]
    assert r["spool_dir"] == b["spool_dir"] and r["runs_root"] == b["runner_runs_root"] == str(ps.runs)
    assert r["clone_url"] == str(world["bare"]).replace("grp/pay.git", "{project}.git")
    assert (r["git_name"], r["git_email"], r["kit_dir"]) == ("brain-bot", "brain-bot@noreply.invalid", str(BRAINKIT))
    assert s["bridge_users"] == [bot] and s["gitlab"]["token_file"] == b["gitlab"]["token_file"]
    assert s["signing_key"] == str(ps.key) and s["vouchers_dir"] != b["vouchers_dir"] and s["ledger"] != b["ledger"]
    for d in ("spool/requests", "spool/ready", "spool/failed", "signer-spool/requests", "signer-spool/signed",
              "signer-spool/refused"):
        assert (ps.root / d).is_dir(), d
    assert ps.runs.is_dir() and ps.cache.is_dir() and str(ps.runs).startswith(str(world["home"] / "workplace"))
    # brain-kit wrote brain-run, its hooks on this runnerd.json; brain-patrol beside it, brain-run's shape without them
    spec = json.loads(ps.agent_spec.read_text())
    assert str(ps.runnerd_json) in json.dumps(spec["hooks"])
    patrol = json.loads((ps.agent_spec.parent / "brain-patrol.json").read_text())
    assert patrol["name"] == "brain-patrol" and "hooks" not in patrol and mode(ps.agent_spec.parent / "brain-patrol.json") == "0o644"
    assert {k: patrol[k] for k in ("tools", "allowedTools", "permissions", "resources", "model")} == \
        {k: spec[k] for k in ("tools", "allowedTools", "permissions", "resources", "model")}
    assert "不要 rebase，不要强推" in patrol["prompt"] and "在 MR 上说清楚修不了什么，然后停下" in patrol["prompt"]
    # kit.json: on a branch, in an MR the person merges; only what the runner needs changed, brain-kit's formatting kept
    mr = gl.mrs[0]
    assert len(gl.mrs) == 1 and mr["title"] == "运行机：更新 .brain/kit.json" and mr["target_branch"] == "main"
    assert by["kit_mr"]["link"] == mr["web_url"] and "请在 GitLab 上合并这个 MR" in by["kit_mr"]["message"]
    old, new = gl.files["main"][".brain/kit.json"], gl.files["brain/runner-setup"][".brain/kit.json"]
    changed = set(new.splitlines()) - set(old.splitlines())
    assert changed == {'  "bridge_user": "%s",' % bot, '    "base": "%s",' % BASE, '    "project": "%s"' % P,
                       '  "version": 1,', '  "work_sources": [', '    "mr-handoff",', '    "mr-patrol"', '  ]'}, changed
    assert json.loads(new)["work_sources"] == ["mr-handoff", "mr-patrol"] and "mr-patrol" in mr["description"]
    assert json.loads(new)["bot_users"] == ["brain-bot"] and json.loads(new)["bot_emails"] == ["brain-bot@noreply.invalid"]
    # team mode: on with the two configs, the same-user trial; hand-overs refused until the MR is merged
    assert world["team"] == [{"enabled": True, "bridge_config": str(ps.bridge_json),
                              "runnerd_config": str(ps.runnerd_json), "dev_same_user": True}]
    assert "合并以前" in by["team_mode"]["message"] and world["signer"][0] == s
    state = json.loads(ps.state.read_text())
    assert saved_tokens(state)["bridge"]["username"] == bot and state["project"]["id"] == PID and not state["running"]


def test_a_second_run_makes_nothing_new(world):
    build(world)
    gl, ps = world["gl"], mod("team.setup").Paths.default(BRAINKIT, root=world["tmp"] / "team-setup")
    before, seen = files_under(ps.root), len(gl.calls)
    stamps = {p: os.stat(p).st_mtime_ns for p in (ps.bridge_json, ps.runnerd_json, ps.signer_json, ps.identities,
                                                  ps.allowed, ps.key, ps.agent_spec, ps.patrol_spec)}
    view, _ = build(world)
    assert everything_done(view)
    assert gl.writes(seen) == []                         # no token, label, branch, commit or MR made
    assert {p: os.stat(p).st_mtime_ns for p in stamps} == stamps
    after = files_under(ps.root)
    assert {k: v for k, v in after.items() if not k.endswith("setup-state.json")} == \
        {k: v for k, v in before.items() if not k.endswith("setup-state.json")}
    by = {s["id"]: s for s in view["steps"]}
    assert by["bridge_token"]["message"].startswith("用上次建的") and "还没合并" in by["kit_mr"]["message"]


def test_a_run_that_stops_part_way_converges_when_run_again(world):
    gl = world["gl"]
    # GitLab makes the bridge's token, which then cannot sign in once: the step fails with the token made
    gl.fail.append(("GET", API + "/user", 500, lambda tok: tok != world["admin"]))
    first, ps = build(world)
    assert statuses(first)["bridge_token"] == "failed" and len(gl.made("brain-bridge")) == 1
    assert {statuses(first)[k] for k in ("push", "labels", "configs", "team_mode")} == {"todo"}
    # the next run keeps that token, then stops at the labels
    gl.fail.append(("POST", "%s/projects/%d/labels" % (API, PID), 500, None))
    second, _ = build(world)
    got = statuses(second)
    assert got["bridge_token"] == "done" and got["labels"] == "failed" and got["push"] == "done"
    assert {got[k] for k in ("identities", "kit_mr", "configs", "team_mode")} == {"todo"}
    assert not ps.bridge_json.exists() and not gl.mrs and world["team"] == []
    labels = next(s for s in second["steps"] if s["id"] == "labels")
    assert "HTTP 500" in labels["message"] and "500 (fake)" in labels["detail"]
    third, _ = build(world)
    assert everything_done(third)
    assert len(gl.made("brain-bridge")) == 1 and len(gl.mrs) == 1 and gl.revoked == set()
    assert sorted(gl.labels) == ["ai-handoff", "ai-handoff-rejected", "ai-running", "brain-reflect"]
    token = gl.made("brain-bridge")[0]["token"]
    assert saved_tokens(json.loads(ps.state.read_text()))["bridge"]["username"] == gl.users[token]["username"]


def rotated(gl, since, token):
    """Exactly one write since *since*: GitLab asked to rotate *token* (a project token of grp/pay)."""
    return [c[:2] for c in gl.writes(since)] == [("POST", "%s/projects/%d/access_tokens/%d/rotate" % (API, PID, token["id"]))]


def test_a_lost_token_file_gets_a_new_value_of_the_same_token_and_account(world):
    """The token is rotated, not made again: its bot user, kit.json, its MR and signer.json stay as they were."""
    _, ps = build(world)
    gl = world["gl"]
    old = gl.live("brain-bridge")
    user, kit, signer = gl.users[old["token"]]["username"], gl.files["brain/runner-setup"][KIT], ps.signer_json.read_text()
    (ps.root / "bridge-gitlab-token").unlink()
    seen = len(gl.calls)
    view, _ = build(world)
    new = gl.live("brain-bridge")
    assert everything_done(view) and rotated(gl, seen, old) and len(gl.made("brain-bridge")) == 1
    assert old["token"] in gl.revoked and (ps.root / "bridge-gitlab-token").read_text().strip() == new["token"]
    assert gl.users[new["token"]]["username"] == user and saved_tokens(json.loads(ps.state.read_text()))["bridge"]["id"] == new["id"]
    assert len(gl.mrs) == 1 and gl.files["brain/runner-setup"][KIT] == kit and ps.signer_json.read_text() == signer
    assert step(view, "bridge_token")["message"] == "这台机器上项目令牌 brain-bridge 的值丢了或者不对，换了新的值：账号还是 %s，%s 到期" % (
        user, new["expires_at"])


def test_a_token_with_two_weeks_or_less_to_go_is_rotated_to_a_year_on_the_same_account(world):
    _, ps = build(world)
    gl = world["gl"]
    old = gl.live("brain-bridge")
    user = gl.users[old["token"]]["username"]
    old["expires_at"] = (utc_today() + timedelta(days=15)).isoformat()
    seen = len(gl.calls)
    build(world)
    assert gl.writes(seen) == []                          # more than 14 days to go: kept
    old["expires_at"] = (utc_today() + timedelta(days=14)).isoformat()
    view, _ = build(world)
    new = gl.live("brain-bridge")
    assert everything_done(view) and rotated(gl, seen, old) and days_left(new["expires_at"]) in (363, 364)
    assert gl.users[new["token"]]["username"] == user and len(gl.mrs) == 1
    assert (ps.root / "bridge-gitlab-token").read_text().strip() == new["token"]
    assert "快到期了，换了新的值：账号还是 %s" % user in step(view, "bridge_token")["message"]


def test_a_token_revoked_on_gitlab_is_made_again(world):
    """Its bot user is gone with it: the new token's account goes into kit.json through the MR."""
    _, ps = build(world)
    gl = world["gl"]
    gl.revoked.add(gl.live("brain-bridge")["token"])
    seen = len(gl.calls)
    view, _ = build(world)
    new = gl.made("brain-bridge")[-1]
    assert everything_done(view) and len(gl.made("brain-bridge")) == 2
    assert not [c for c in gl.calls[seen:] if c[1].endswith("/rotate") or c[0] == "DELETE"]
    user = gl.users[new["token"]]["username"]
    assert json.loads(ps.signer_json.read_text())["bridge_users"] == [user]
    assert json.loads(gl.files["brain/runner-setup"][KIT])["bridge_user"] == user and len(gl.mrs) == 1


def test_tokens_are_kept_per_gitlab_and_project_and_one_this_setup_did_not_make_is_never_touched(world):
    """The same root set up on GitLab A, then on GitLab B whose grp/pay has the same id, and someone's token there the
    same id as A's brain-bridge; then on A again, which rotates its own token (the file held B's)."""
    setup, rest = mod("team.setup"), mod("bk").m("rest")
    _, ps = build(world)
    a = world["gl"]
    mine = a.live("brain-bridge")
    admin_b = "fake-" + secrets.token_hex(12)
    b = FakeGitLab(admin_b, str(world["bare"]), a.files["main"][KIT])
    _, payload = b("POST", "https://b.example.com/api/v4/projects/%d/access_tokens" % PID, {"PRIVATE-TOKEN": admin_b},
                   json.dumps({"name": "someones-ci-token", "scopes": ["api"], "access_level": 30}).encode())
    theirs = json.loads(payload)
    assert theirs["id"] == mine["id"]
    inp = setup.SetupInput.parse({"gitlab_base": "https://b.example.com", "project": P, "admin_token": admin_b,
                                  "roles": {"builder": "bob"}})
    ctx = setup.Ctx(inp, ps, gitlab=lambda tok: rest.GitLab("https://b.example.com", tok, b),
                    team_mode=team_mode(world), signer=fake_signer(world), reach=fake_reach(world))
    with setup.Lock(ps.root):
        on_b = setup.run(ctx)
    assert everything_done(on_b) and len(b.made("brain-bridge")) == 1
    assert b.active(theirs) and not [c for c in b.calls if c[1].endswith("/rotate") or c[0] == "DELETE"]
    seen = len(a.calls)
    back, _ = build(world)
    assert everything_done(back) and rotated(a, seen, mine)
    assert a.users[a.live("brain-bridge")["token"]]["username"] == a.users[mine["token"]]["username"]
    state = json.loads(ps.state.read_text())
    assert saved_tokens(state, "https://b.example.com")["bridge"]["id"] == b.live("brain-bridge")["id"]


def test_a_run_cut_off_part_way_says_the_step_it_had_not_finished(world):
    """A run whose process ended part-way (an app restart) is left running in its file: the page reads it as not
    running, cut off at the step it was in. A run that ended, or one going on now, is not cut off."""
    setup = mod("team.setup")
    view, ps = build(world)
    assert view["cut_off"] is None
    state = json.loads(ps.state.read_text())
    state.update(running=True, current="kit_mr", ended_at=None, steps=state["steps"][:5])
    ps.state.write_text(json.dumps(state))
    cut = setup.view(setup.load_state(ps), ps, running=setup.busy(ps.root))
    assert (cut["running"], cut["cut_off"], cut["ended_at"]) == (False, "kit_mr", None)
    with setup.Lock(ps.root):
        live = setup.view(setup.load_state(ps), ps, running=setup.busy(ps.root))
    assert (live["running"], live["cut_off"]) == (True, None) and step(live, "kit_mr")["status"] == "running"


def test_patrol_off_writes_no_brain_patrol_and_its_kit_mr_switches_mr_patrol_off(world):
    """巡检所有开着的 MR left off: no brain-patrol, and a kit.json that has mr-patrol among its work sources loses only it
    (another kind someone named stays); on, the two are added after the ones already there."""
    world["gl"].files["main"][KIT] = kit_file(world["tmp"], work_sources=["mr-handoff", "mr-patrol", "issues-later"])
    view, ps = build(world, patrol=False)
    assert step(view, "patrol_agent")["status"] == "skipped" and not ps.patrol_spec.exists()
    assert json.loads(branch_kit(world))["work_sources"] == ["mr-handoff", "issues-later"]
    assert view["input"]["patrol"] is False
    world["gl"].files["main"][KIT] = kit_file(world["tmp"], work_sources=["issues-later"])
    view, ps = build(world)
    assert step(view, "patrol_agent")["status"] == "done" and ps.patrol_spec.exists()
    assert json.loads(branch_kit(world))["work_sources"] == ["issues-later", "mr-handoff", "mr-patrol"]


def test_a_rerun_whose_kit_mr_only_switches_patrol_says_patrol_waits_for_it_and_hand_overs_do_not(world):
    """A runner set up before 巡检 (kit.json has all the hand-over needs, no work_sources), run again with 巡检 ticked:
    the kit.json MR only adds work_sources, and nothing says hand-overs are refused until it is merged; unticked later,
    it only takes mr-patrol out again."""
    gl = world["gl"]
    build(world, patrol=False)
    gl.merge(gl.mrs[-1]["iid"])
    for patrol, then in ((True, "开始"), (False, "停")):
        before = dict(json.loads(gl.files["main"][KIT]))
        view, _ = build(world, patrol=patrol)
        after = json.loads(branch_kit(world))
        assert [k for k in after if after[k] != before.get(k)] == ["work_sources"]
        by = {s["id"]: s for s in view["steps"]}
        assert by["kit_mr"]["status"] == "waiting" and "交接" not in gl.mrs[-1]["description"]
        assert by["team_mode"]["message"] == "团队模式开了；上面那个 MR 合并以后，巡检才" + then
        gl.merge(gl.mrs[-1]["iid"])


def branch_kit(world):
    """kit.json on the setup's branch: what the kit.json MR would merge."""
    return world["gl"].files["brain/runner-setup"][KIT]


def test_the_kit_mr_waits_until_it_is_merged_then_is_done(world):
    view, ps = build(world)
    gl = world["gl"]
    assert statuses(view)["kit_mr"] == "waiting"
    gl.merge(gl.mrs[0]["iid"])
    seen = len(gl.calls)
    view, _ = build(world)
    by = {s["id"]: s for s in view["steps"]}
    assert everything_done(view, kit_mr="done") and by["kit_mr"]["link"] is None
    assert gl.writes(seen) == [] and by["team_mode"]["message"] == "团队模式开了"
    assert "kit_mr" not in json.loads(ps.state.read_text())


def test_push_auto_falls_back_to_a_project_token_when_ssh_does_not_reach_gitlab(world):
    gl = world["gl"]
    gl.ssh_url = str(world["tmp"] / "nowhere" / "grp" / "pay.git")
    view, ps = build(world)
    assert everything_done(view)
    push = next(s for s in view["steps"] if s["id"] == "push")
    assert push["message"].startswith("这台机器的 SSH 连不上 GitLab") and push["detail"]
    bot = gl.made("brain-bot")
    assert len(bot) == 1 and bot[0]["scopes"] == ["read_repository", "write_repository", "read_api"]
    user = gl.users[bot[0]["token"]]
    r = json.loads(ps.runnerd_json.read_text())
    assert r["clone_url"] == "https://oauth2:%s@gitlab.example.com/{project}.git" % bot[0]["token"]
    assert (r["git_name"], r["git_email"]) == ("brain-bot", user["email"])
    assert mode(ps.runnerd_json) == "0o600" and mode(ps.root / "bot-gitlab-token") == "0o600"
    kit = json.loads(gl.files["brain/runner-setup"][".brain/kit.json"])
    assert kit["bot_users"] == [user["username"]] and kit["bot_emails"] == [user["email"]]
    seen = len(gl.calls)
    view, _ = build(world)                               # the bot's token is used again, as the bridge's is
    assert everything_done(view) and gl.writes(seen) == []


def test_a_bot_token_git_cannot_clone_with_fails_the_push_step(world):
    world["gl"].ssh_url = str(world["tmp"] / "nowhere" / "grp" / "pay.git")
    world["https_down"] = True                           # e.g. HTTPS git behind the single sign-on the API is not
    view, ps = build(world)
    push = next(s for s in view["steps"] if s["id"] == "push")
    assert push["status"] == "failed" and "SSH 和项目令牌 brain-bot 都连不上" in push["message"]
    token = world["gl"].made("brain-bot")[0]["token"]
    assert "error: 403" in push["detail"] and token not in push["detail"] and not ps.runnerd_json.exists()
    world["https_down"] = False
    view, _ = build(world)
    assert everything_done(view) and len(world["gl"].made("brain-bot")) == 1


def test_a_scope_gitlab_does_not_give_project_tokens_is_said_as_its_refusal(world, monkeypatch):
    """GitLab answers 400 to a project token asked with a scope project tokens cannot have: the step says GitLab
    refused the request, which running again does not change, and GitLab's words are under 细节."""
    setup = mod("team.setup")
    world["gl"].ssh_url = str(world["tmp"] / "nowhere" / "grp" / "pay.git")
    monkeypatch.setattr(setup, "BOT_SCOPES", setup.BOT_SCOPES + ("read_user",))
    view, _ = build(world)
    push = next(s for s in view["steps"] if s["id"] == "push")
    assert push["status"] == "failed" and "HTTP 400" in push["message"] and "等一会儿" not in push["message"]
    assert "scopes does not have a valid value" in push["detail"] and not world["gl"].made("brain-bot")


def test_push_ssh_alone_fails_when_ssh_does_not_reach_gitlab(world):
    world["gl"].ssh_url = str(world["tmp"] / "nowhere" / "grp" / "pay.git")
    view, ps = build(world, push="ssh")
    assert statuses(view)["push"] == "failed" and not world["gl"].made("brain-bot")


def test_the_persons_token_is_never_written(world):
    world["gl"].ssh_url = str(world["tmp"] / "nowhere" / "grp" / "pay.git")   # token mode: every file there is
    _, ps = build(world)
    build(world)
    gl = world["gl"]
    bridge_token, bot_token = gl.made("brain-bridge")[0]["token"], gl.made("brain-bot")[0]["token"]
    salt = json.loads(ps.bridge_json.read_text())["pseudonym_salt"]
    files = files_under(ps.root, ps.work, ps.agent_spec.parent)
    assert {os.path.basename(p) for p in files} >= {"setup-state.json", "bridge-gitlab-token", "bot-gitlab-token",
                                                   "identities.json", "bridge_ed25519", "allowed_signers", "bridge.json",
                                                   "runnerd.json", "signer.json", "brain-run.json"}
    where = lambda secret: sorted(os.path.relpath(p, ps.root) for p, b in files.items() if secret.encode() in b)  # noqa: E731
    assert where(world["admin"]) == []
    assert where(bridge_token) == ["bridge-gitlab-token"]
    assert where(bot_token) == ["bot-gitlab-token", "runnerd.json"]      # its clone address: runnerd clones with it
    assert where(salt) == ["bridge.json", "signer.json"]
    state = ps.state.read_text()
    assert world["admin"] not in state and bridge_token not in state and bot_token not in state and salt not in state
    assert "admin_token" not in state


def test_everyone_on_the_project_who_can_push_may_hand_over_and_the_roles_stay_as_typed(world):
    """users: each member with Developer or more, not only the people in a role; no bot (the account the runner
    pushes as, the bridge's token user); a role may name a Reporter."""
    world["gl"].members.append(("brain-bot", 30))
    view, ps = build(world, roles={"product": "rita", "builder": "carol"})
    assert json.loads(ps.identities.read_text()) == {
        "users": {"alice": {}, "bob": {}, "carol": {}},
        "roles": {"product": ["rita"], "builder": ["carol"], "security": ["alice"], "data": ["alice"]}}
    assert step(view, "identities")["message"].startswith("写好了身份表：3 个 Developer 以上的人能交接；产品 rita")


def test_a_role_names_only_members_of_the_project(world):
    view, ps = build(world, roles={"product": "carol, dave", "builder": "erin"})
    by = {s["id"]: s for s in view["steps"]}
    assert by["identities"]["status"] == "failed" and "dave、erin" in by["identities"]["message"]
    assert not ps.identities.exists()


def test_a_developer_cannot_set_up_the_runner(world):
    world["gl"].level = 30
    view, _ = build(world)
    by = {s["id"]: s for s in view["steps"]}
    assert by["project"]["status"] == "failed" and "Developer" in by["project"]["message"] and "Maintainer" in by["project"]["message"]
    assert world["gl"].writes() == []


def test_a_repo_without_brain_kit_is_imported_first(world):
    world["gl"].files["main"] = {}
    view, _ = build(world)
    msg = next(s for s in view["steps"] if s["id"] == "project")["message"]
    assert "先在个人电脑上导入这个仓库" in msg and world["gl"].writes() == []


def test_the_input_is_read_at_the_boundary():
    setup = mod("team.setup")
    got = setup.SetupInput.parse({"gitlab_base": BASE + "/", "project": BASE + "/grp/sub/pay/-/tree/main",
                                  "admin_token": " tok-123456 ", "roles": {"product": "@Carol，dave", "builder": []}})
    assert (got.gitlab_base, got.project, got.admin_token, got.push) == (BASE, "grp/sub/pay", "tok-123456", "auto")
    assert got.patrol is True and got.public()["patrol"] is True     # 巡检所有开着的 MR: on unless the page says no
    assert setup.SetupInput.parse({"gitlab_base": BASE, "project": P, "admin_token": "tok-123456",
                                   "patrol": False}).public()["patrol"] is False
    assert got.roles == {"product": ("carol", "dave"), "builder": ()} and "tok-123456" not in repr(got)
    assert "tok-123456" not in json.dumps(got.public())
    for bad in ({"gitlab_base": "gitlab.example.com"}, {"gitlab_base": "https://u:p@gitlab.example.com"},
                {"project": "pay"}, {"admin_token": ""}, {"admin_token": "a b"}, {"roles": {"boss": "x"}},
                {"roles": {"product": "a/b"}}, {"push": "ftp"}, {"patrol": "yes"}):
        body = dict({"gitlab_base": BASE, "project": P, "admin_token": "tok-123456"}, **bad)
        with pytest.raises(setup.SetupRefused):
            setup.SetupInput.parse(body)


def test_kit_json_is_written_back_the_way_brain_kit_writes_it(tmp_path, home):
    bk, setup = mod("bk"), mod("team.setup")
    assert bk.load(str(BRAINKIT))["ok"]
    try:
        text = kit_file(tmp_path)
        assert setup.kit_text(json.loads(text), text) == text
        two = '{\n    "b": 1,\n    "a": "é"\n}'
        assert setup.kit_text(json.loads(two), two) == two                   # its order, indent, no final newline
    finally:
        bk.unload()


def test_a_pid_file_naming_another_process_is_never_signalled(tmp_path):
    signerproc = mod("team.signerproc")
    cfg = tmp_path / "signer.json"
    cfg.write_text("{}")
    (tmp_path / "signer.pid").write_text(json.dumps({"pid": os.getpid(), "config": str(cfg)}))
    assert signerproc.running(cfg) is None
    assert signerproc.stop(cfg) is False and not (tmp_path / "signer.pid").exists()   # we are still here


def test_the_app_runs_it_in_the_background_and_team_mode_starts_with_the_signer(world, tmp_path):
    """Through the routes: the run goes on in a worker thread, the page reads its steps; team mode is on afterwards
    through the settings (its TeamRuntime started) with the real signer as a child process, which stops with the app."""
    signerproc = mod("team.signerproc")
    svc = make_service(tmp_path / "app", [], transport=world["gl"])
    routes = mod("handlers").build_routes(svc, __import__("http_fakes").AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    body = {"gitlab_base": BASE, "project": P, "admin_token": world["admin"], "roles": {"builder": "bob"}}

    async def go():
        await svc.start()
        try:
            bad = await call(routes, "POST", "/team/setup", json_body=dict(body, project="pay"))
            started = await call(routes, "POST", "/team/setup", json_body=body)
            again = await call(routes, "POST", "/team/setup", json_body=body)    # one run at a time
            end = await svc.wait_setup()
            got = await call(routes, "GET", "/team/setup")
            team = (svc.settings["team"], svc.team is not None and svc.team.host is not None, svc._team_status())
            signer = signerproc.config_for(svc.settings["team"])
            pid = signerproc.running(signer)
            python = mod("constants").PYTHON_BIN
            same = signerproc.ensure(signer, ROOT, python)              # signer.json as it was: that very signer
            token = signer.parent / "bridge-gitlab-token"
            token.write_text("rotated-" + secrets.token_hex(8) + "\n")  # its token rotated, signer.json the same
            new_token = signerproc.ensure(signer, ROOT, python)
            doc = json.loads(signer.read_text())
            signer.write_text(json.dumps(dict(doc, keep_days=7)))       # a rerun that changes it: a new one
            changed = signerproc.ensure(signer, ROOT, python)
            old_gone = subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0
            return bad, started, again, end, got, team, signer, pid, same, new_token, changed, old_gone
        finally:
            await svc.stop()

    bad, started, again, end, got, team, signer, pid, same, new_token, changed, old_gone = run(go())
    assert same == (pid, False) and new_token[1] is True and new_token[0] != pid and old_gone
    assert changed[1] is True and changed[0] != new_token[0]
    assert bad.status == 400 and bad.body["code"] == "invalid_setup" and "组/仓库" in bad.body["error"]
    assert started.status == 200 and started.body["running"] is True
    assert {s["status"] for s in started.body["steps"]} <= {"todo", "running", "done"}
    assert again.status == 409 and again.body["code"] == "setup_busy"
    assert everything_done(end), [(s["id"], s["status"], s["message"], s["detail"][:300]) for s in end["steps"]]
    assert got.body == end and got.body["running"] is False and world["admin"] not in json.dumps(got.body)
    settings, on, status = team
    assert on and status["ok"] and status["same_user"], status
    assert settings == {"enabled": True, "bridge_config": end["root"] + "/bridge.json",
                        "runnerd_config": end["root"] + "/runnerd.json", "dev_same_user": True}
    assert pid is not None                               # the signer, as team mode left it
    assert signerproc.running(signer) is None            # ... stopped with the app
    with open(signer.parent / "signer.log", "rb") as fh:
        assert world["admin"].encode() not in fh.read()
    # the page's fixture has exactly the keys the backend answers with
    from test_contract import diff_keys, fixture
    assert diff_keys(fixture("team-setup.json"), json.loads(json.dumps(end))) == []


def the_app(world, tmp_path):
    """The app on the fake GitLab and its routes; setup(): one run through POST /team/setup, to its end."""
    svc = make_service(tmp_path / "app", [], transport=world["gl"])
    routes = mod("handlers").build_routes(svc, __import__("http_fakes").AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    body = {"gitlab_base": BASE, "project": P, "admin_token": world["admin"], "roles": {"builder": "bob"}}

    async def setup():
        started = await call(routes, "POST", "/team/setup", json_body=body)
        assert started.status == 200, started.body
        return await svc.wait_setup()
    return svc, routes, setup


def test_a_rerun_starts_team_mode_again_once_a_file_it_read_has_changed(world, tmp_path):
    """The review's runs through the app. A builds the runner: team mode and the signer start on the bridge's token T1.
    B finds the token file gone, gets a new value of that token (T1 no longer signs in) and stops at brain-run's setup,
    before the signer and team mode. C writes nothing, and still both start again, as T1 is what they read when they started:
    a round of the bridge then signs in to GitLab. D changes nothing either: team mode goes on as it was."""
    signerproc = mod("team.signerproc")
    svc, routes, setup = the_app(world, tmp_path)

    async def go():
        await svc.start()
        try:
            a = await setup()
            paths = svc._setup_paths()
            t1, signer_a = paths.token("bridge").read_text().strip(), signerproc.running(paths.signer_json)
            paths.token("bridge").unlink()
            svc._setup_paths = lambda: replace(paths, python="/usr/bin/false")     # brain-run's setup fails
            b = await setup()
            del svc._setup_paths
            c = await setup()
            signer_c = signerproc.running(paths.signer_json)
            loop = await call(routes, "POST", "/team/loop")
            last = svc._team_status()["last_loop_at"]
            d = await setup()
            return (a, b, c, d, t1, signer_a, signer_c, loop, last, svc._team_status()["last_loop_at"],
                    signerproc.running(paths.signer_json))
        finally:
            await svc.stop()

    a, b, c, d, t1, signer_a, signer_c, loop, last, last_d, signer_d = run(go())
    assert everything_done(a) and everything_done(c) and everything_done(d)
    assert statuses(b)["bridge_token"] == "done" and statuses(b)["agent"] == "failed"
    assert {statuses(b)[k] for k in ("signer", "team_mode")} == {"todo"}
    assert t1 in world["gl"].revoked
    assert signer_a and signer_c and signer_c != signer_a       # the signer read T1 when it started: a new one
    assert loop.status == 200 and loop.body["ok"] is True and loop.body["error"] == "", loop.body
    assert last is not None and last_d == last and signer_d == signer_c


def test_a_round_of_team_mode_starts_the_signer_again_once_its_process_is_gone(world, tmp_path):
    """The signer 一键建运行机 set up is killed while team mode runs: the next round starts it again, and runs."""
    signerproc = mod("team.signerproc")
    svc, routes, setup = the_app(world, tmp_path)

    def gone(signer, wait_s=10.0):
        end = time.monotonic() + wait_s
        while signerproc.running(signer) is not None and time.monotonic() < end:
            time.sleep(0.05)
        return signerproc.running(signer) is None

    async def go():
        await svc.start()
        try:
            assert everything_done(await setup())
            signer = svc._setup_paths().signer_json
            first = signerproc.running(signer)
            os.kill(first, signal.SIGKILL)
            killed = gone(signer)
            loop = await call(routes, "POST", "/team/loop")
            return first, killed, loop, signerproc.running(signer), svc._team_status()
        finally:
            await svc.stop()

    first, killed, loop, again, status = run(go())
    assert first and killed and again and again != first
    assert loop.body["ok"] is True and status["ok"] is True, (loop.body, status)


def test_the_command_line_says_one_line_per_step(world):
    """scripts/runner_setup.py, its own process, against the fake GitLab behind a loopback HTTP server: a line per step
    as it ends, team mode started and stopped again to check it, and a last line for the signer it leaves running (its
    pid, how to stop it); exit 0 with the MR waiting; again, nothing new and the same signer."""
    from teamfix import Served
    served = Served(world["gl"])
    token_file = world["tmp"] / "token"
    token_file.write_text(world["admin"] + "\n")
    token_file.chmod(0o600)
    root = world["tmp"] / "cli-setup"
    cmd = [sys.executable, "-I", str(ROOT / "scripts" / "runner_setup.py"), "--root", str(root), "--gitlab", served.base,
           "--project", P, "--token-file", str(token_file), "--role", "builder=bob", "--brainkit", str(BRAINKIT)]
    signerproc = mod("team.signerproc")
    try:
        first = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        seen = len(world["gl"].calls)
        second = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        left = signerproc.running(root / "signer.json")      # the signer stays: the app's team mode takes it on
    finally:
        signerproc.stop(root / "signer.json")
        served.stop()
    assert left is not None
    lines = first.stdout.splitlines()
    assert first.returncode == 0 and len(lines) == 14, first.stdout + first.stderr
    assert lines[0].startswith("✓ 找到项目  grp/pay") and lines[5].startswith("… kit.json 的 MR  开了 MR !7")
    assert lines[10].startswith("✓ brain-patrol agent  写好了 brain-patrol 的设置")
    assert lines[12].startswith("✓ 打开团队模式  团队模式的配置能用") and lines[12].endswith("交接都会被拒")
    for last in (lines[13], second.stdout.splitlines()[-1]):
        assert last.startswith("签名服务还在跑（进程 %d）" % left) and last.endswith("kill %d" % left), last
    assert second.returncode == 0 and world["gl"].writes(seen) == [], second.stdout + second.stderr
    assert "✓ 桥的令牌  用上次建的项目令牌 brain-bridge" in second.stdout
    assert world["admin"] not in first.stdout + first.stderr + second.stdout + second.stderr
