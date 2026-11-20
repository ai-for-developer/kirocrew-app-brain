"""设置's 「允许快速模式」 (kitmr.py): a team rule in the default branch's .brain/kit.json, changed only through an MR
with the person's GitLab token (the one way the app opens a kit.json MR, kitmr.kit_mr), or by hand with the commands
when there is no token; the switch shows the default branch's value and 「MR !N 等合并」 until someone merges it."""

import asyncio
import json
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

from appkit import mod, sh
from brainfix import make_project, make_service, needs_brainkit, run

pytestmark = needs_brainkit
kitmr = mod("kitmr")
AppError = mod("httpkit").AppError
BASE, P, TOKEN = "https://gitlab.example.com", "grp/pay", "fake-token-for-kit-switch"
GITLAB = {"gitlab": {"base": BASE, "project": P}}


class GitLab:
    """GitLab REST v4 for grp/pay: files per branch, branches, commits, MRs (their notes: none), as kit_mr and the
    app's MR cache call them."""

    def __init__(self, kit_text):
        self.files = {"main": {kitmr.KIT: kit_text}}
        self.mrs, self.calls = [], []

    def __call__(self, method, url, headers, body):
        u = urlsplit(url)
        path, q = unquote(u.path), dict(parse_qsl(u.query))
        data = json.loads(body.decode()) if body else None
        self.calls.append((method, path, q, data))
        ok = lambda o, s=200: (s, json.dumps(o).encode())  # noqa: E731
        if headers.get("PRIVATE-TOKEN") != TOKEN:
            return ok({"message": "401 Unauthorized"}, 401)
        pre = "/api/v4/projects/%s" % P
        if not path.startswith(pre):
            return ok({"message": "404 Project Not Found"}, 404)
        rest = path[len(pre):]
        if rest.startswith("/repository/files/") and rest.endswith("/raw") and method == "GET":
            text = self.files.get(q.get("ref"), {}).get(rest[len("/repository/files/"):-len("/raw")])
            return (200, text.encode()) if text is not None else ok({"message": "404 File Not Found"}, 404)
        if rest.startswith("/repository/branches/") and method == "GET":
            name = rest[len("/repository/branches/"):]
            return ok({"name": name}) if name in self.files else ok({"message": "404 Branch Not Found"}, 404)
        if rest == "/repository/branches" and method == "POST":
            self.files[data["branch"]] = dict(self.files[data["ref"]])
            return ok({"name": data["branch"]}, 201)
        if rest == "/repository/commits" and method == "POST":
            for a in data["actions"]:
                self.files[data["branch"]][a["file_path"]] = a["content"]
            return ok({"id": "c" * 40}, 201)
        if rest == "/merge_requests" and method == "GET":
            return ok([m for m in self.mrs if m["source_branch"] == q.get("source_branch")
                       and q.get("state") in ("all", m["state"])])
        if rest == "/merge_requests" and method == "POST":
            iid = 31 + len(self.mrs)
            mr = dict(data, iid=iid, state="opened", sha="d" * 40, labels=[],
                      web_url="%s/%s/-/merge_requests/%d" % (BASE, P, iid))
            self.mrs.append(mr)
            return ok(mr, 201)
        if rest.endswith("/notes") and method == "GET":
            return ok([])
        return ok({"message": "404 (fake)"}, 404)

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]

    def merge(self, iid):
        mr = next(m for m in self.mrs if m["iid"] == iid)
        self.files["main"] = dict(self.files[mr["source_branch"]])
        mr["state"] = "merged"


@pytest.fixture
def world(tmp_path, home, monkeypatch):
    repo = make_project(tmp_path, origin=True, kit=GITLAB)
    gl = GitLab((repo / kitmr.KIT).read_text(encoding="utf-8"))
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)
    return repo, gl, make_service(tmp_path / "alice", [repo], transport=gl)


def started(svc, fn):
    async def go():
        await svc.start()
        try:
            return await fn()
        finally:
            await svc.stop()
    return run(go())


def fast_of(status):
    return status["repos"][0]["fast"]


def test_the_switch_opens_one_kit_json_mr_and_says_it_waits_until_merged(world):
    repo, gl, svc = world

    async def go():
        before = fast_of(await svc.status())
        off = await svc.fast_mode({"repo": "r1", "on": False})
        writes = len(gl.writes())
        again = await svc.fast_mode({"repo": "r1", "on": False})
        back = await svc.fast_mode({"repo": "r1", "on": True})
        return before, off, writes, again, back

    before, off, writes, again, back = started(svc, go)
    assert before == {"on": True, "why": "", "pending": None}
    url = "%s/%s/-/merge_requests/31" % (BASE, P)
    assert (off["ok"], off["text"], off["mr"]) == (True, "开了 MR !31：合并以后生效", {"iid": 31, "url": url, "existing": False})
    assert off["fast"] == {"on": True, "why": "", "pending": {"on": False, "iid": 31, "url": url}}
    main, side = gl.files["main"][kitmr.KIT], gl.files[kitmr.FAST_BRANCH][kitmr.KIT]
    assert json.loads(side) == dict(json.loads(main), fast_mode=False)
    assert kitmr.kit_text(json.loads(side), main) == side, "written the way the file is: only fast_mode changes"
    mr = gl.mrs[0]
    assert (mr["title"], mr["description"], mr["target_branch"], mr["remove_source_branch"]) == (
        kitmr.FAST_TITLE[False], kitmr.FAST_TEXT[False], "main", True)
    assert "下一次流水线会失败" in mr["description"], "turning it off says what happens to open fast MRs"
    assert again["text"] == "MR !31 已经开着：合并以后生效" and len(gl.writes()) == writes, "a second flip changes nothing"
    assert back["text"] == "默认分支上已经是「允许」了：MR !31 不用合并了，在 GitLab 上关掉它"


def test_once_merged_and_fetched_the_switch_shows_the_default_branch_again(world):
    repo, gl, svc = world

    async def go():
        await svc.fast_mode({"repo": "r1", "on": False})
        gl.merge(31)
        (repo / kitmr.KIT).write_text(gl.files["main"][kitmr.KIT], encoding="utf-8")
        sh("git", "-C", repo, "commit", "-qam", "merge !31")
        sh("git", "-C", repo, "push", "-q", "origin", "main")
        await asyncio.to_thread(svc._repo("r1").fetch)
        await asyncio.to_thread(svc.mr.refresh, BASE, P, kitmr.FAST_BRANCH, transport=gl)
        return fast_of(await svc.status()), await svc.intake_next("r1"), mod("bk").m("flows").Refusal.OFF.zh

    view, nxt, off = started(svc, go)
    assert view == {"on": False, "why": "", "pending": None}
    assert nxt["fast"] == {"ok": False, "why": off}, "开需求 says the team switched it off"


def test_without_a_token_the_switch_gives_the_commands_and_changes_nothing(world, monkeypatch):
    repo, gl, svc = world
    monkeypatch.delenv("GITLAB_TOKEN")
    out = started(svc, lambda: svc.fast_mode({"repo": "r1", "on": False}))
    assert out["ok"] is False and out["mr"] is None and gl.writes() == []
    assert out["text"].startswith("这台机器上没有 GitLab 令牌")
    assert any(c.startswith("git -C") and "worktree add -b brain/fast-mode" in c for c in out["commands"])
    assert any('"fast_mode": false' in c for c in out["commands"])
    assert out["new_mr_url"].startswith("%s/%s/-/merge_requests/new?" % (BASE, P))


def test_a_regulated_repo_has_the_switch_off_with_why(world):
    repo, gl, svc = world
    lean_json = repo / "aidlc-docs" / "lean.json"
    lean_json.write_text(json.dumps(dict(json.loads(lean_json.read_text()), regulated=True)), encoding="utf-8")
    sh("git", "-C", repo, "commit", "-qam", "regulated")
    sh("git", "-C", repo, "push", "-q", "origin", "main")
    sh("git", "-C", repo, "fetch", "-q", "origin")

    async def go():
        view = fast_of(await svc.status())
        with pytest.raises(AppError) as refused:
            await svc.fast_mode({"repo": "r1", "on": False})
        return view, str(refused.value), mod("bk").m("flows").Refusal.REGULATED.zh

    view, refused, said = started(svc, go)
    assert view == {"on": True, "why": said, "pending": None} and refused == said and gl.writes() == []


def test_a_flip_must_say_on_or_off(world):
    _, gl, svc = world

    async def go():
        with pytest.raises(AppError) as bad:
            await svc.fast_mode({"repo": "r1", "on": "off"})
        return bad.value

    assert started(svc, go).code == "invalid_fast_mode" and gl.writes() == []
