"""你是谁: the name brain-kit goes by in a repo, shown where the app shows who you are (status, 开需求, 检查), and
「你的 GitLab 用户名」 / 「你的名字」 when brain-kit would not know: git config brain.user in that repo (brain-kit's
USER_RE, no shell, no token). Both brain-kits, made from the checkout whichever it is: one without the login-name
rule (before 91e2fee, e.g. 48b6b97: no name, no start) and one with brain-kit 91e2fee's rule (a repo without GitLab
uses the login name). With a GitLab token, GitLab says who the person is (a fake GitLab that knows the token), as
brain-kit asks it; the name decides which requirements and run branches are theirs. Throwaway repos only."""

import asyncio
import json
import secrets
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

import brainfix
from appkit import BRAINKIT, mod, sh
from brainfix import brain, make_project, make_service, needs_brainkit, person, run, start_in_worktree
from http_fakes import AppRoute, FakeWeb, call
from test_progress import checkpoint, on_origin, rows_of, tick

pytestmark = needs_brainkit
identity = mod("identity")
problems = mod("problems")
bk = mod("bk")

GITLAB = {"gitlab": {"base": "https://gitlab.example.com", "project": "grp/pay"}}
OLD_ME = '''    if not who:
        raise BrainError("who are you? set GITLAB_TOKEN (preferred) or `git config brain.user <gitlab-username>`")
'''
NEW_ME = '''    if not who and not cfg["gitlab"]["base"]:
        who = _login_name()
        if not who:
            raise BrainError("who are you? your login name is not usable as a name here: "
                             "`git config brain.user <name>` (lowercase letters, digits, . _ -)")
    if not who:
        raise BrainError("who are you? this repo uses GitLab (%s): set GITLAB_TOKEN (preferred) or "
                         "`git config brain.user <your GitLab username>`; it must be your GitLab username: GitLab "
                         "checks who pushes ai/<name>/... and the bridge checks who handed over" % cfg["gitlab"]["base"])
'''
LOGIN = '''

def _login_name() -> str:
    import getpass
    try:
        name = getpass.getuser().strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    return name if USER_RE.match(name) else ""


def _store('''


def has_login_rule(kit) -> bool:
    """The checkout already has brain-kit 91e2fee's rule (a release bundles 91e2fee or newer)."""
    return "\ndef _login_name(" in (kit / "brainkit" / "cli.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def old_kit(tmp_path_factory):
    """brain-kit without the login-name rule: the checkout when it is that old, else a copy of it with 91e2fee's
    rule taken out again (OLD_ME back in cli._me, no _login_name)."""
    if not has_login_rule(BRAINKIT):
        return BRAINKIT
    dst = tmp_path_factory.mktemp("bk-old") / "brain-kit"
    shutil.copytree(BRAINKIT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    cli = dst / "brainkit" / "cli.py"
    src = cli.read_text(encoding="utf-8")
    start = src.index('    if not who and not cfg["gitlab"]["base"]:\n')
    end = src.index("    if not USER_RE.match(who.lower()):", start)
    src = src[:start] + OLD_ME + src[end:]
    fn = src.index("\n\ndef _login_name(")
    src = src[:fn] + src[src.index("\n\ndef ", fn + 2):]
    assert "_login_name" not in src
    cli.write_text(src, encoding="utf-8")
    return dst


@pytest.fixture(scope="module")
def fallback_kit(tmp_path_factory):
    """brain-kit 91e2fee's identity rule (cli._me falls back to the login name without GitLab): the checkout when it
    has it, else a copy of it with the rule applied."""
    if has_login_rule(BRAINKIT):
        return BRAINKIT
    dst = tmp_path_factory.mktemp("bk") / "brain-kit"
    shutil.copytree(BRAINKIT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    cli = dst / "brainkit" / "cli.py"
    src = cli.read_text(encoding="utf-8")
    assert OLD_ME in src and "\n\ndef _store(" in src
    cli.write_text(src.replace(OLD_ME, NEW_ME).replace("\n\ndef _store(", LOGIN, 1), encoding="utf-8")
    return dst


@pytest.fixture
def login(monkeypatch):
    for k in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.setenv(k, "Frank")       # getpass.getuser(), here and in brain's own process
    return "frank"


def project(tmp_path, kit=None):
    repo = make_project(tmp_path, kit=kit)
    sh("git", "-C", repo, "config", "--unset", "brain.user")      # make_project names alice
    return repo


def started(svc, coro_fn):
    async def go():
        await svc.start()
        try:
            return await coro_fn()
        finally:
            await svc.stop()
    return run(go())


def start_body(**kw):
    return dict({"repo": "r1", "slug": "refund", "title": "退款", "docs": [], "open_session": False}, **kw)


def test_without_gitlab_the_old_brainkit_asks_for_a_name_and_the_start_says_why(tmp_path, home, login, old_kit,
                                                                                 monkeypatch):
    monkeypatch.setattr(brainfix, "BRAINKIT", old_kit)
    repo = project(tmp_path)
    svc = make_service(tmp_path, [repo])
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)

    async def go():
        assert not identity.falls_back()                              # before 91e2fee: no login-name rule
        st = await svc.status()
        first = await svc.intake_start(start_body())
        saved = await call(routes, "POST", "/me/name", json_body={"path": str(repo), "name": first["me"]["suggest"]})
        again = await svc.intake_start(start_body())
        return st, first, saved, again

    try:
        st, first, saved, again = started(svc, go)
    finally:
        bk.unload()
    me = st["repos"][0]["me"]
    assert me["ask"] and me["name"] is None and me["label"] == "你的名字" and me["suggest"] == login
    assert me["why"] == identity.WHY_OLD and me["gitlab"] == ""
    assert st["me"] == {"user": None, "token": False, "how": "GitLab 令牌（没有）"}
    assert first["ok"] is False and "who are you?" in first["output"]
    p = first["problem"]
    assert p["fix"] == "me" and p["text"] == "brain-kit 不知道你是谁" and p["repo"] == str(repo)
    assert "点「保存」" in p["todo"] and first["me"]["ask"]
    assert saved.status == 200 and saved.body["me"]["name"] == login and saved.body["me"]["source"] == "git"
    assert sh("git", "-C", repo, "config", "--local", "brain.user").stdout.strip() == login
    assert next(r for r in saved.body["status"]["repos"] if r["id"] == "r1")["me"]["ask"] is False
    assert again["ok"] and again["branch"] == "ai/%s/PAY-1-refund" % login and again["problem"] is None


def test_without_gitlab_the_new_brainkit_uses_the_login_name_and_no_input(tmp_path, home, login, fallback_kit,
                                                                          monkeypatch):
    monkeypatch.setattr(brainfix, "BRAINKIT", fallback_kit)
    repo = project(tmp_path)
    svc = make_service(tmp_path, [repo])

    async def go():
        assert identity.falls_back()                                  # 91e2fee's rule
        st = await svc.status()
        got = await svc.import_inspect({"path": str(repo)})
        out = await svc.intake_start(start_body())
        return st, got, out, svc.me_in(svc.repos["r1"])

    try:
        st, got, out, mine = started(svc, go)
        me = st["repos"][0]["me"]
        assert me == dict(me, name=login, source="login", ask=False, error="")
        assert me["text"] == "%s（这台机器的登录名：这个仓库不用 GitLab）" % login
        assert st["me"] == {"user": login, "token": False, "how": "GitLab 令牌（没有）；" + me["text"]}
        row = next(c for c in got["checks"] if c["id"] == "me")
        assert row["level"] == "OK" and row["why"] == me["text"] and got["me"]["name"] == login
        assert out["ok"] and out["branch"] == "ai/%s/PAY-1-refund" % login        # brain-kit used it too
        assert mine == login                                                    # and the app owns what it claimed
    finally:
        bk.unload()


@pytest.mark.parametrize("kit_path", ["old", "fallback"])
def test_with_gitlab_and_no_token_both_brainkits_ask_for_the_gitlab_username(tmp_path, home, login, old_kit,
                                                                             fallback_kit, monkeypatch, kit_path):
    monkeypatch.setattr(brainfix, "BRAINKIT", fallback_kit if kit_path == "fallback" else old_kit)
    repo = project(tmp_path, kit=GITLAB)
    svc = make_service(tmp_path, [repo])

    async def go():
        got = await svc.import_inspect({"path": str(repo)})
        out = await svc.intake_start(start_body())
        return got, out

    try:
        got, out = started(svc, go)
        me = got["me"]
        assert me["ask"] and me["label"] == "你的 GitLab 用户名" and me["suggest"] == login
        assert me["why"] == identity.WHY_GITLAB and me["gitlab"] == "https://gitlab.example.com"
        row = next(c for c in got["checks"] if c["id"] == "me")
        assert row["level"] == "WARN" and row["todo"] == "在下面填上你的 GitLab 用户名，点「保存」"
        assert out["ok"] is False and out["problem"]["fix"] == "me" and out["me"]["label"] == "你的 GitLab 用户名"
        if kit_path == "fallback":
            assert out["problem"]["text"] == "brain-kit 不知道你是谁：这个仓库用 GitLab（https://gitlab.example.com）"
    finally:
        bk.unload()


def test_with_a_token_gitlab_says_who_and_a_set_name_wins_otherwise(tmp_path, home, login, monkeypatch):
    repo = project(tmp_path, kit=GITLAB)
    monkeypatch.setenv("GITLAB_TOKEN", "fake-" + "0" * 20)
    assert identity.of(str(repo), "https://gitlab.example.com", token=True) == dict(
        identity.of(str(repo), "https://gitlab.example.com", token=True), source="token", ask=False, name=None)
    monkeypatch.delenv("GITLAB_TOKEN")
    sh("git", "-C", repo, "config", "brain.user", "Dave")
    got = identity.of(str(repo), "https://gitlab.example.com", token=False)
    assert (got["name"], got["source"], got["ask"]) == ("dave", "git", False)
    sh("git", "-C", repo, "config", "brain.user", "Bad Name")
    bad = identity.of(str(repo), "", token=False)
    assert bad["ask"] and bad["name"] is None and "Bad Name" in bad["error"] and bad["why"] == identity.WHY_LOCAL
    monkeypatch.setenv("BRAIN_USER", "erin")
    env = identity.of(str(repo), "https://gitlab.example.com", token=False)
    assert (env["name"], env["source"]) == ("erin", "env")


def test_the_name_route_takes_brainkits_names_only_runs_no_shell_and_stores_no_token(tmp_path, home, login):
    repo = project(tmp_path, kit=GITLAB)
    svc = make_service(tmp_path, [repo])
    routes = mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)
    planted = tmp_path / "planted"
    token = "fake-" + "1" * 24

    async def go():
        out = {}
        for i, name in enumerate(["a b", "-x", "x" * 65, "$(touch %s)" % planted, "alice;touch %s" % planted, "", None,
                                  42, "名字", "../x"]):
            out["bad%d" % i] = await call(routes, "POST", "/me/name", json_body={"path": str(repo), "name": name})
        out["rel"] = await call(routes, "POST", "/me/name", json_body={"path": "pay", "name": "alice"})
        out["ok"] = await call(routes, "POST", "/me/name", json_body={"path": str(repo), "name": " Alice.W-1 ",
                                                                     "token": token})
        out["same_rule"] = identity.user_re() is bk.m("common").USER_RE
        return out

    got = started(svc, go)
    for k in [k for k in got if k.startswith("bad")]:
        assert (got[k].status, got[k].code) == (400, "invalid_name"), k
        assert "小写字母" in got[k].body["error"]
    assert (got["rel"].status, got["rel"].code) == (400, "invalid_path")
    assert not planted.exists()
    assert got["ok"].status == 200 and got["ok"].body["me"]["name"] == "alice.w-1"
    assert got["ok"].body["text"] == "你在这个仓库的名字设成了 alice.w-1"     # the command only under 细节
    assert got["ok"].body["raw"] == "git config brain.user alice.w-1"
    assert sh("git", "-C", repo, "config", "--local", "brain.user").stdout.strip() == "alice.w-1"
    assert token not in json.dumps(got["ok"].body) and token not in (repo / ".git" / "config").read_text()
    assert got["same_rule"] and identity.valid("alice") and not identity.valid("Alice")


def test_who_are_you_in_plain_chinese_with_the_input_as_the_fix():
    old = "brain: who are you? set GITLAB_TOKEN (preferred) or `git config brain.user <gitlab-username>`"
    new = ("brain: who are you? this repo uses GitLab (https://gitlab.example.com): set GITLAB_TOKEN (preferred) or "
           "`git config brain.user <your GitLab username>`; it must be your GitLab username: GitLab checks who pushes "
           "ai/<name>/... and the bridge checks who handed over")
    login = ("brain: who are you? your login name is not usable as a name here: `git config brain.user <name>` "
             "(lowercase letters, digits, . _ -)")
    bad = "brain: brain.user 'Bad Name' is not a GitLab username"
    rows = {k: problems.row("FAIL", t, repo="/r") for k, t in (("old", old), ("new", new), ("login", login), ("bad", bad))}
    assert {k: r["fix"] for k, r in rows.items()} == {"old": "me", "new": "me", "login": "me", "bad": "me"}
    assert rows["old"]["text"] == "brain-kit 不知道你是谁"
    assert rows["new"]["text"] == "brain-kit 不知道你是谁：这个仓库用 GitLab（https://gitlab.example.com）"
    assert rows["new"]["todo"].startswith("要你在 GitLab 上的用户名：认领和交接都按它认人")
    assert rows["login"]["text"] == "brain-kit 不知道你是谁：这台机器的登录名不能当名字"
    assert rows["bad"]["text"] == "这个仓库记下的名字（Bad Name）不能用"
    assert all(r["raw"] and "点「保存」" in r["todo"] for r in rows.values())        # the command only under 细节 (raw)
    assert problems.row("FAIL", old)["fix"] is None                    # no repo: nothing to set it in
    assert problems.who_row("Started nothing\n" + new + "\n", repo="/r")["text"] == rows["new"]["text"]
    assert problems.who_row("brain: no such requirement", repo="/r") is None


# ---- GitLab says who the token is ----------------------------------------------------------------------------------- #

GL = "https://gitlab.example.com"


class WhoAmI:
    """A GitLab that knows one token: GET /api/v4/user with it answers *username*, anything else 404. Calls are kept;
    *go*, when given, holds each answer until it is set (a slow GitLab); *down* raises as an unreachable one does."""

    def __init__(self, token, username="alice", go=None, down=False):
        self.token, self.username, self.go, self.down = token, username, go, down
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if self.go is not None:
            self.go.wait(10)
        if self.down:
            raise bk.m("common").BrainError("cannot reach gitlab.example.com: timed out")
        if method == "GET" and url == GL + "/api/v4/user" and headers.get("PRIVATE-TOKEN") == self.token:
            return 200, json.dumps({"id": 7, "username": self.username, "name": "Alice A"}).encode()
        return 404, b'{"message": "404 Not Found"}'


def a_token(monkeypatch):
    """A GitLab token in this process only (brain-kit's runs here started before it), in no shape a scrubber knows."""
    tok = "tok" + secrets.token_hex(16)
    monkeypatch.setenv("GITLAB_TOKEN", tok)
    return tok


async def status_until(svc, ok, wait=5.0):
    """/status as soon as *ok* holds for it, else the last one after *wait* seconds."""
    end = time.monotonic() + wait
    st = await svc.status()
    while not ok(st) and time.monotonic() < end:
        await asyncio.sleep(0.05)
        st = await svc.status()
    return st


def named(name):
    return lambda st: st["repos"][0]["me"]["name"] == name


@pytest.fixture
def teammates(tmp_path, home):
    """alice's checkout of a GitLab project, her run PAY-1 started while brain.user named her, and bob's work on origin
    (PAY-2 in progress on his machine, PAY-3 claimed, not started), fetched; then her brain.user is gone."""
    repo = make_project(tmp_path, origin=True, kit={"gitlab": {"base": GL, "project": "shop/pay"}})
    wt = start_in_worktree(repo, home / "workplace" / "brain-app" / "pay" / "refund", "refund", "退款")
    bob = person(tmp_path, tmp_path / "pay-origin.git", "bob")
    brain(bob, "start", "export", "--title", "导出")
    brain(bob, "claim", "--title", "对账")
    sh("git", "-C", repo, "fetch", "-q", "origin")
    sh("git", "-C", repo, "config", "--unset", "brain.user")
    return SimpleNamespace(repo=repo, wt=wt, origin=tmp_path / "pay-origin.git", app=tmp_path / "alice-app")


def test_a_token_only_person_is_who_gitlab_says_and_the_token_is_in_no_answer(teammates, monkeypatch, caplog):
    w = teammates
    tok = a_token(monkeypatch)
    gl = WhoAmI(tok)
    svc = make_service(w.app, [w.repo], transport=gl)

    async def go():
        st = await status_until(svc, named("alice"))
        return st, [st, await svc.overview("all"), await svc.todo(), await svc.requirement("r1", "PAY-1"),
                    await svc.board("r1", "PAY-2"), svc.settings_view()]

    st, answers = started(svc, go)
    me = st["repos"][0]["me"]
    assert (me["name"], me["source"], me["ask"]) == ("alice", "token", False), me
    assert me["text"].startswith("alice（") and "GitLab" in me["text"]
    assert st["me"]["user"] == "alice" and me["text"] in st["me"]["how"]
    assert gl.calls == [("GET", GL + "/api/v4/user")], "asked once, then kept"
    said = json.dumps(answers, ensure_ascii=False)
    assert tok not in said and tok not in caplog.text


def test_a_token_only_persons_rows_and_todo_are_their_own_only(teammates, monkeypatch):
    w = teammates
    svc = make_service(w.app, [w.repo], transport=WhoAmI(a_token(monkeypatch)))

    async def go():
        await status_until(svc, named("alice"))
        return rows_of(await svc.overview("all")), await svc.todo()

    rows, todo = started(svc, go)
    assert rows["PAY-1"]["owner"] == "alice" and rows["PAY-1"]["waits"]["who"] != "people", rows["PAY-1"]["waits"]
    assert (rows["PAY-2"]["waits"]["who"], rows["PAY-2"]["waits"]["text"]) == ("people", "bob 在自己的机器上做")
    assert (rows["PAY-3"]["waits"]["who"], rows["PAY-3"]["waits"]["text"]) == ("people", "等 bob 开工")
    assert {i["key"] for i in todo["items"]} <= {"PAY-1"}, todo["items"]


def test_a_token_only_persons_checkpoint_goes_to_origin(teammates, monkeypatch):
    w = teammates
    svc = make_service(w.app, [w.repo], transport=WhoAmI(a_token(monkeypatch)))

    async def go():
        await status_until(svc, named("alice"))
        tip = checkpoint(w.wt, "stage", "skip", "reverse-engineering", "--reason", "greenfield")
        tick(svc)
        return tip, (await svc.status())["repos"][0]["sync"]

    tip, sync = started(svc, go)
    assert on_origin(w.origin) == tip, "her own run branch went up at the checkpoint"
    assert sync["push_why"] == "" and sync["push_cmd"] == ""


def test_with_no_token_and_no_name_a_teammates_requirement_is_not_yours_and_settings_asks(teammates):
    w = teammates
    gl = WhoAmI("no-token-here")
    svc = make_service(w.app, [w.repo], transport=gl)

    async def go():
        return await svc.status(), rows_of(await svc.overview("all")), await svc.todo()

    st, rows, todo = started(svc, go)
    assert (rows["PAY-2"]["waits"]["who"], rows["PAY-2"]["waits"]["text"]) == ("people", "bob 在自己的机器上做")
    assert (rows["PAY-3"]["waits"]["who"], rows["PAY-3"]["waits"]["text"]) == ("people", "等 bob 开工")
    assert not [i for i in todo["items"] if i["key"] in ("PAY-2", "PAY-3")], todo["items"]
    me = st["repos"][0]["me"]
    assert (me["name"], me["ask"], me["label"], me["why"]) == (None, True, identity.LABEL_GITLAB, identity.WHY_GITLAB)
    assert gl.calls == [], "no token: GitLab is not asked"


@pytest.mark.parametrize("gitlab", ["slow", "down"])
def test_with_gitlab_slow_or_down_the_name_set_here_answers_and_nothing_waits(tmp_path, home, login, monkeypatch,
                                                                               caplog, gitlab):
    repo = project(tmp_path, kit={"gitlab": {"base": GL, "project": "shop/pay"}})
    sh("git", "-C", repo, "config", "brain.user", "dave")
    tok = a_token(monkeypatch)
    hold = threading.Event()
    gl = WhoAmI(tok, go=hold) if gitlab == "slow" else WhoAmI(tok, down=True)
    svc = make_service(tmp_path, [repo], transport=gl)

    async def go():
        t0 = time.monotonic()
        first = await svc.status()
        took = time.monotonic() - t0
        if gitlab == "down":
            first = await status_until(svc, lambda st: "没认出" in st["repos"][0]["me"]["text"])
        mine = svc.me_in(svc.repos["r1"])
        hold.set()
        return took, first, mine, await status_until(svc, named("alice"), wait=2.0 if gitlab == "slow" else 0.3)

    took, first, mine, later = started(svc, go)
    assert took < 2.0, "the page did not wait for GitLab (%.1f s)" % took
    me = first["repos"][0]["me"]
    assert (me["name"], me["source"], me["ask"]) == ("dave", "git", False), me
    assert me["text"].startswith("dave（") and "GitLab" in me["text"] and mine == "dave"
    if gitlab == "slow":
        assert later["repos"][0]["me"]["name"] == "alice", "once GitLab answers, its name wins, as brain-kit's does"
    else:
        assert later["repos"][0]["me"]["name"] == "dave" and len(gl.calls) == 1, "a failure is not asked again at once"
    assert tok not in json.dumps([first, later], ensure_ascii=False) and tok not in caplog.text
