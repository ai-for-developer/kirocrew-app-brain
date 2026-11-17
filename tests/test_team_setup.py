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
import subprocess
import sys
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


class FakeGitLab:
    """GitLab REST v4 for project grp/pay (id 42). Tokens: the person's (alice, Maintainer) and the project access
    tokens it makes, each with its own bot user; GET /user needs api, read_api or read_user, as on GitLab. Every request
    is kept in calls (method, path, query, body); fail holds one-shot answers (method, path, status, who): who is
    None (any token), a token, or a test on the token."""

    def __init__(self, admin, ssh_url, kit, level=40, members=("alice", "bob", "carol"), labels=("ai-handoff",)):
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
        return [t for t in self.tokens if t["name"] == name]

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
        if tok not in self.users or tok in self.revoked:
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
            n = len(self.tokens) + 1
            value = "fake-bot-%d-%s" % (n, secrets.token_hex(8))
            user = "project_%d_bot_%s" % (PID, secrets.token_hex(4))
            self.users[value] = {"id": 100 + n, "username": user, "name": data["name"], "bot": True,
                                 "email": "%s@noreply.gitlab.example.com" % user}
            self.scopes[value] = list(data["scopes"])
            t = dict(data, id=500 + n, user_id=100 + n, token=value, active=True, revoked=False)
            self.tokens.append(t)
            return ok(t, 201)
        m = re.fullmatch(r"/access_tokens/(\d+)", rest)
        if m and method == "DELETE":
            t = next((t for t in self.tokens if t["id"] == int(m.group(1))), None)
            if t is None:
                return ok({"message": "404"}, 404)
            self.revoked.add(t["token"])
            return 204, b""
        if rest == "/labels" and method == "GET":
            return ok(self.page([{"id": i, "name": n} for i, n in enumerate(self.labels)], q))
        if rest == "/labels" and method == "POST":
            if data["name"] in self.labels:
                return ok({"message": "Label already exists"}, 409)
            self.labels.append(data["name"])
            return ok({"name": data["name"], "color": data["color"]}, 201)
        if rest == "/members/all" and method == "GET":
            return ok(self.page([{"id": i, "username": n} for i, n in enumerate(self.members)], q))
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
    def on(want, restart):
        world["team"].append((want, restart))
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
            live = [t["token"] for t in world["gl"].tokens if t["token"] not in world["gl"].revoked]
            ok = not world.get("https_down") and any("oauth2:%s@" % quote(t, safe="") in url for t in live)
            return ok, "" if ok else "fatal: unable to access '%s': The requested URL returned error: 403" % url
        p = common.git_net(str(world["tmp"]), "ls-remote", url, "HEAD", timeout=20, check=False)
        return p.returncode == 0, (p.stderr or p.stdout).strip()
    return reach


def setup_ctx(world, push="auto", roles=None):
    setup, rest = mod("team.setup"), mod("bk").m("rest")
    inp = setup.SetupInput.parse({"gitlab_base": BASE, "project": P, "admin_token": world["admin"],
                                  "roles": roles or {"builder": "bob"}, "push": push})
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
    assert bridge[0]["expires_at"] > "2026-" and not gl.made("brain-bot")     # this machine's git reaches the project
    bot = gl.users[bridge[0]["token"]]["username"]
    assert sorted(gl.labels) == ["ai-handoff", "ai-handoff-rejected", "ai-running", "brain-reflect"]
    # the token's value is in its own file only, 0600; the person's token is nowhere
    assert (ps.root / "bridge-gitlab-token").read_text().strip() == bridge[0]["token"]
    assert mode(ps.root / "bridge-gitlab-token") == "0o600" and mode(ps.key) == "0o600" and mode(ps.root) == "0o700"
    ids = json.loads(ps.identities.read_text())
    assert ids == {"users": {"alice": {}, "bob": {}},
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
    # brain-kit wrote brain-run, its hooks on this runnerd.json
    spec = json.loads(ps.agent_spec.read_text())
    assert str(ps.runnerd_json) in json.dumps(spec["hooks"])
    # kit.json: on a branch, in an MR the person merges; only what the runner needs changed, brain-kit's formatting kept
    mr = gl.mrs[0]
    assert len(gl.mrs) == 1 and mr["title"] == "运行机：更新 .brain/kit.json" and mr["target_branch"] == "main"
    assert by["kit_mr"]["link"] == mr["web_url"] and "请在 GitLab 上合并这个 MR" in by["kit_mr"]["message"]
    old, new = gl.files["main"][".brain/kit.json"], gl.files["brain/runner-setup"][".brain/kit.json"]
    changed = set(new.splitlines()) - set(old.splitlines())
    assert changed == {'  "bridge_user": "%s",' % bot, '    "base": "%s",' % BASE, '    "project": "%s"' % P}, changed
    assert json.loads(new)["bot_users"] == ["brain-bot"] and json.loads(new)["bot_emails"] == ["brain-bot@noreply.invalid"]
    # team mode: on with the two configs, the same-user trial; hand-overs refused until the MR is merged
    assert world["team"] == [({"enabled": True, "bridge_config": str(ps.bridge_json),
                               "runnerd_config": str(ps.runnerd_json), "dev_same_user": True}, True)]
    assert "合并以前" in by["team_mode"]["message"] and world["signer"][0] == s
    state = json.loads(ps.state.read_text())
    assert state["tokens"]["bridge"]["username"] == bot and state["project"]["id"] == PID and not state["running"]


def test_a_second_run_makes_nothing_new(world):
    build(world)
    gl, ps = world["gl"], mod("team.setup").Paths.default(BRAINKIT, root=world["tmp"] / "team-setup")
    before, seen = files_under(ps.root), len(gl.calls)
    stamps = {p: os.stat(p).st_mtime_ns for p in (ps.bridge_json, ps.runnerd_json, ps.signer_json, ps.identities,
                                                  ps.allowed, ps.key, ps.agent_spec)}
    view, _ = build(world)
    assert everything_done(view)
    assert gl.writes(seen) == []                         # no token, label, branch, commit or MR made
    assert {p: os.stat(p).st_mtime_ns for p in stamps} == stamps
    after = files_under(ps.root)
    assert {k: v for k, v in after.items() if not k.endswith("setup-state.json")} == \
        {k: v for k, v in before.items() if not k.endswith("setup-state.json")}
    by = {s["id"]: s for s in view["steps"]}
    assert by["bridge_token"]["message"].startswith("用上次建的") and "还没合并" in by["kit_mr"]["message"]
    assert world["team"][-1][1] is False                 # nothing team mode reads changed: not started again


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
    assert json.loads(ps.state.read_text())["tokens"]["bridge"]["username"] == gl.users[token]["username"]


def test_a_lost_token_file_gets_a_new_token_and_the_old_one_is_revoked(world):
    _, ps = build(world)
    gl = world["gl"]
    old = gl.made("brain-bridge")[0]
    (ps.root / "bridge-gitlab-token").unlink()
    view, _ = build(world)
    assert everything_done(view)
    new = gl.made("brain-bridge")[1]
    assert gl.revoked == {old["token"]} and (ps.root / "bridge-gitlab-token").read_text().strip() == new["token"]
    assert json.loads(ps.signer_json.read_text())["bridge_users"] == [gl.users[new["token"]]["username"]]
    assert json.loads(gl.files["brain/runner-setup"][".brain/kit.json"])["bridge_user"] == gl.users[new["token"]]["username"]
    assert world["team"][-1][1] is True                  # its token and signer.json changed: started again


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
    assert len(bot) == 1 and bot[0]["scopes"] == ["read_repository", "write_repository", "read_user"]
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
    assert got.roles == {"product": ("carol", "dave"), "builder": ()} and "tok-123456" not in repr(got)
    assert "tok-123456" not in json.dumps(got.public())
    for bad in ({"gitlab_base": "gitlab.example.com"}, {"gitlab_base": "https://u:p@gitlab.example.com"},
                {"project": "pay"}, {"admin_token": ""}, {"admin_token": "a b"}, {"roles": {"boss": "x"}},
                {"roles": {"product": "a/b"}}, {"push": "ftp"}):
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
            doc = json.loads(signer.read_text())
            signer.write_text(json.dumps(dict(doc, keep_days=7)))       # a rerun that changes it: a new one
            changed = signerproc.ensure(signer, ROOT, python)
            old_gone = subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0
            return bad, started, again, end, got, team, signer, pid, same, changed, old_gone
        finally:
            await svc.stop()

    bad, started, again, end, got, team, signer, pid, same, changed, old_gone = run(go())
    assert same == (pid, False) and changed[1] is True and changed[0] != pid and old_gone
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


def test_the_command_line_says_one_line_per_step(world):
    """scripts/runner_setup.py, its own process, against the fake GitLab behind a loopback HTTP server: a line per step
    as it ends, team mode started and stopped again to check it, exit 0 with the MR waiting; again, nothing new."""
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
    assert first.returncode == 0 and len(lines) == 12, first.stdout + first.stderr
    assert lines[0].startswith("✓ 找到项目  grp/pay") and lines[5].startswith("… kit.json 的 MR  开了 MR !7")
    assert lines[11].startswith("✓ 打开团队模式  团队模式的配置能用") and lines[11].endswith("交接都会被拒")
    assert second.returncode == 0 and world["gl"].writes(seen) == [], second.stdout + second.stderr
    assert "✓ 桥的令牌  用上次建的项目令牌 brain-bridge" in second.stdout
    assert world["admin"] not in first.stdout + first.stderr + second.stdout + second.stderr
