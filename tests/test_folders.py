"""KiroCrew's sidebar: 「Brain 需求」, one folder per repo in it, one folder per requirement in that. The app moves its own
older requirement folders there once, and never a person's folder. The fake gateway refuses what KiroCrew refuses."""

import asyncio
from pathlib import Path

from appkit import FakeGateway, GatewayError, mod

gateway = mod("gateway")


def test_moving_a_folder_asks_kirocrew_to_change_only_its_parent(tmp_path):
    gw = gateway.Gateway("brain", tmp_path / ".app_secret")
    sent = []

    async def request(method, path, *, params=None, body=None):
        sent.append((method, path, body))
        return {}

    gw.request = request
    asyncio.run(gw.move_folder("ab7b85458c31", "f0d1"))
    assert sent == [("PATCH", "/api/chat/folders/ab7b85458c31", {"parent_id": "f0d1"})]


def test_the_fake_refuses_what_kirocrew_refuses_an_app():
    async def go():
        gw = FakeGateway()
        person = {"id": "p1", "name": "oms-brain-trial", "parent_id": "", "project_dir": ""}
        gw.folder_rows.append(person)
        root = await gw.create_folder("Brain 需求")
        a = await gw.create_folder("A", parent_id=root["id"])
        b = await gw.create_folder("B", parent_id=root["id"])
        refused = []
        for call in (lambda: gw.create_folder("x", parent_id="p1"), lambda: gw.create_folder("x", parent_id="nope"),
                     lambda: gw.move_folder("p1", root["id"]), lambda: gw.move_folder(a["id"], "p1"),
                     lambda: gw.move_folder(root["id"], a["id"]), lambda: gw.hide_folder("p1")):
            try:
                await call()
            except GatewayError as exc:
                refused.append((exc.status, exc.code))
        gw.folder_rows.append({"id": "p2", "name": "mine", "parent_id": b["id"]})
        try:
            await gw.move_folder(b["id"], a["id"])
        except GatewayError as exc:
            refused.append((exc.status, exc.code))
        await gw.move_folder(a["id"], b["id"])
        return refused, a

    refused, a = asyncio.run(go())
    assert refused == [(403, "folder_not_owned"), (400, "folder_parent_not_found"), (403, "folder_not_owned"),
                       (403, "folder_not_owned"), (400, "folder_cycle"), (403, "folder_not_owned"),
                       (403, "folder_not_owned")]
    assert a["parent_id"] == "f3"


sessions = mod("sessions")


def manager(tmp_path, gw):
    import logging
    return sessions.SessionManager(gw, app="brain", state_file=tmp_path / "sessions.json", log=logging.getLogger("t"))


def tree(gw):
    """Every folder as its path of names, a person's marked （人）, another app's with its name."""
    by_id = {f["id"]: f for f in gw.folder_rows}

    def path(f):
        names = []
        while f is not None:
            owner = f.get("owner_app")
            names.insert(0, f["name"] + ("" if owner == gw.app else "（%s）" % (owner or "人")))
            f = by_id.get(f.get("parent_id") or "")
        return " › ".join(names)

    return sorted(path(f) + ("（隐藏）" if f.get("hidden") else "") for f in gw.folder_rows)


async def open_local(mgr, key, repo, folder, cwd):
    return await mgr.open_session("开工", parent_session_key="brain:" + key, cwd=cwd, title=key + " · 本地开发",
                                  folder=folder, agent="brain-dev-crew", strict_agent=True, root_folder="Brain 需求",
                                  repo_folder=repo, project_dir=cwd, kind="local", key=key)


def test_a_requirement_session_opens_in_its_repo_folder_and_its_later_sessions_open_in_the_same_folder(tmp_path):
    async def go():
        gw = FakeGateway()
        mgr = manager(tmp_path, gw)
        first = await open_local(mgr, "r1-PAY-1", "pay", "PAY-1 · refund", str(tmp_path))
        root = next(f for f in gw.folder_rows if f["name"] == "Brain 需求")
        gw.folder_rows.append({"id": "p1", "name": "shop", "parent_id": root["id"]})   # a person's, of the same name
        await open_local(mgr, "r1-PAY-2", "pay", "PAY-2 · invoice", str(tmp_path))
        await open_local(mgr, "r2-SHOP-1", "shop", "SHOP-1 · cart", str(tmp_path))
        review = await open_local(mgr, "r1-PAY-1", "pay", "PAY-1 · refund", str(tmp_path))   # 开审查会话, 重开
        again = manager(tmp_path, gw)
        await mgr.save()
        again.load()
        later = await open_local(again, "r1-PAY-1", "pay", "PAY-1 · refund", str(tmp_path))
        await open_local(again, "r1-PAY-3", "pay", "PAY-3 · tax", str(tmp_path))
        return gw, first, review, later

    gw, first, review, later = asyncio.run(go())
    assert tree(gw) == ["Brain 需求", "Brain 需求 › pay", "Brain 需求 › pay › PAY-1 · refund",
                        "Brain 需求 › pay › PAY-2 · invoice", "Brain 需求 › pay › PAY-3 · tax", "Brain 需求 › shop",
                        "Brain 需求 › shop › SHOP-1 · cart", "Brain 需求 › shop（人）"]
    assert {gw.slot_rows[i.slot]["folder_id"] for i in (first, review, later)} == {
        next(f["id"] for f in gw.folder_rows if f["name"] == "PAY-1 · refund")}


def snapshot(gw):
    """KiroCrew's folders on the 2026-11-16 machine (the app's ids, a person's folders, another app's), plus a requirement
    folder a person nested a folder in, one a person moved into their own folder, and a hidden one."""
    person = lambda fid, name, parent="": {"id": fid, "name": name, "parent_id": parent, "project_dir": ""}  # noqa: E731
    app = lambda fid, name, parent="", owner="brain": dict(person(fid, name, parent), owner_app=owner)  # noqa: E731
    gw.folder_rows += [
        person("26c3bd5d5906", "remote-orca-multica"), person("d9d8cf732187", "261102-one-click-deploy", "26c3bd5d5906"),
        app("a7c82c7a6ab9", "AI-DLC 团队", owner="aidlc-team"), app("33dce022afe8", "双十一 · r110839d3", "a7c82c7a6ab9", "aidlc-team"),
        person("0a6df3310a44", "oms-brain-trial"),
        app("82ead8e50edc", "Brain 需求"), app("ab7b85458c31", "DEMO-1 · subtract", "82ead8e50edc"),
        app("f90c488f57c6", "DEMO-3 · multiply", "82ead8e50edc"),
        app("2c77727dc64f", "OMS-4 · order-status-summary", "82ead8e50edc"),
        app("aa0000000001", "DEMO-5 · nested", "82ead8e50edc"), person("pp0000000001", "我的笔记", "aa0000000001"),
        app("aa0000000002", "DEMO-6 · moved", "0a6df3310a44"),
        dict(app("aa0000000003", "DEMO-7 · hidden", "82ead8e50edc"), hidden=True),
    ]
    rec = lambda fid, name: {"id": fid, "name": name, "root": "82ead8e50edc", "root_name": "Brain 需求"}  # noqa: E731
    return {"brain:r1-DEMO-1": rec("ab7b85458c31", "DEMO-1 · subtract"),
            "brain:r1-DEMO-3": rec("f90c488f57c6", "DEMO-3 · multiply"),
            "brain:r3-OMS-4": rec("2c77727dc64f", "OMS-4 · order-status-summary"),
            "brain:r1-DEMO-5": rec("aa0000000001", "DEMO-5 · nested"),
            "brain:r1-DEMO-6": rec("aa0000000002", "DEMO-6 · moved"),
            "brain:r1-DEMO-7": rec("aa0000000003", "DEMO-7 · hidden")}


REPO_OF = {"r1": "demo", "r3": "oms-brain-trial"}


def repo_of(key):
    return REPO_OF.get(key.partition(":")[2].split("-")[0], "")


def test_the_apps_older_requirement_folders_move_into_their_repo_folder_once_and_a_persons_never(tmp_path):
    import json

    async def go():
        gw = FakeGateway()
        (tmp_path / "sessions.json").write_text(json.dumps({"infos": [], "folders": snapshot(gw)}), encoding="utf-8")
        mgr = manager(tmp_path, gw)
        mgr.load()
        stuck = await mgr.regroup(repo_of)
        first = (tree(gw), [c for c in gw.calls if c[0] in ("folder", "move_folder")])
        gw.calls.clear()
        await mgr.save()
        again = manager(tmp_path, gw)
        again.load()
        stuck2 = await again.regroup(repo_of)
        writes2 = [c for c in gw.calls if c[0] in ("folder", "move_folder", "hide_folder")]
        later = await open_local(again, "r1-DEMO-8", "demo", "DEMO-8 · divide", str(tmp_path))
        return gw, stuck, first, (stuck2, writes2), later

    gw, stuck, (tree1, writes), (stuck2, writes2), later = asyncio.run(go())
    assert tree1 == [
        "AI-DLC 团队（aidlc-team）", "AI-DLC 团队（aidlc-team） › 双十一 · r110839d3（aidlc-team）", "Brain 需求", "Brain 需求 › DEMO-5 · nested",
        "Brain 需求 › DEMO-5 · nested › 我的笔记（人）", "Brain 需求 › DEMO-7 · hidden（隐藏）", "Brain 需求 › demo",
        "Brain 需求 › demo › DEMO-1 · subtract", "Brain 需求 › demo › DEMO-3 · multiply", "Brain 需求 › oms-brain-trial",
        "Brain 需求 › oms-brain-trial › OMS-4 · order-status-summary", "oms-brain-trial（人）",
        "oms-brain-trial（人） › DEMO-6 · moved", "remote-orca-multica（人）",
        "remote-orca-multica（人） › 261102-one-click-deploy（人）"]
    assert [w[0] for w in writes] == ["folder", "move_folder", "move_folder", "folder", "move_folder"]
    assert stuck == [{"name": "DEMO-5 · nested", "repo": "demo", "why": "里面有不是这个 app 建的文件夹，KiroCrew 不让 app 移它"}]
    assert stuck2 == stuck and writes2 == []
    assert tree(gw) == sorted(tree1 + ["Brain 需求 › demo › DEMO-8 · divide"])
    assert gw.slot_rows[later.slot]["folder_id"] == next(f["id"] for f in gw.folder_rows if f["name"] == "DEMO-8 · divide")


def test_a_folder_kirocrew_refuses_to_move_stays_where_it_is_said_in_chinese_and_is_tried_again_next_start(tmp_path):
    async def go():
        gw = FakeGateway()
        mgr = manager(tmp_path, gw)
        mgr._folders = {k: v for k, v in snapshot(gw).items() if k == "brain:r1-DEMO-1"}
        move = gw.move_folder

        async def refused(fid, parent_id):
            raise GatewayError(0, "gateway unreachable (ClientConnectorError)", "unreachable", "/api/chat/folders")

        gw.move_folder = refused
        stuck = await mgr.regroup(repo_of)
        where = next(f["parent_id"] for f in gw.folder_rows if f["id"] == "ab7b85458c31")
        gw.move_folder = move
        return stuck, where, await mgr.regroup(repo_of), tree(gw)

    stuck, where, stuck2, after = asyncio.run(go())
    assert stuck == [{"name": "DEMO-1 · subtract", "repo": "demo", "why": "连不上 KiroCrew：看它是不是还开着，再试一次"}]
    assert where == "82ead8e50edc" and stuck2 == [] and "Brain 需求 › demo › DEMO-1 · subtract" in after


# ---- the app: 打开会话 and 开审查会话 in the repo's folder; the older folders moved on start; what stays said in 设置 -- #

def test_open_and_review_sessions_of_a_requirement_open_in_its_folder_inside_its_repo_folder(tmp_path, home):
    from brainfix import make_service, run
    from test_todo_answer import setup

    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)

    async def go():
        await svc.start()
        try:
            return await svc.open_session("r1", "PAY-1"), await svc.open_session("r1", "PAY-1", review=True)
        finally:
            await svc.stop()

    dev, review = run(go())
    assert tree(gw) == ["Brain 需求", "Brain 需求 › pay", "Brain 需求 › pay › PAY-1 · refund"]
    assert gw.slot_rows[dev["slot"]]["folder_id"] == gw.slot_rows[review["slot"]]["folder_id"] == next(
        f["id"] for f in gw.folder_rows if f["name"] == "PAY-1 · refund")


def test_on_start_the_older_folders_move_into_their_repo_folder_and_the_one_that_cannot_is_said_in_settings(tmp_path, home):
    import json

    from brainfix import make_service, run
    from test_todo_answer import setup

    repo, wt, line, gw = setup(tmp_path, home)
    svc = make_service(tmp_path, [repo], gw=gw)
    root = {"id": "82ead8e50edc", "name": "Brain 需求", "parent_id": "", "owner_app": "brain"}
    gone_wt = home / "workplace" / "brain-app" / "gone" / "old"   # a repo no longer listed: its worktree's folder
    gw.folder_rows += [root,
                       {"id": "ab01", "name": "PAY-1 · refund", "parent_id": root["id"], "owner_app": "brain"},
                       {"id": "ab02", "name": "OLD-2 · old", "parent_id": root["id"], "owner_app": "brain"},
                       {"id": "ab03", "name": "PAY-3 · nested", "parent_id": root["id"], "owner_app": "brain"},
                       {"id": "pp01", "name": "我的", "parent_id": "ab03"}]
    rec = lambda fid, name: {"id": fid, "name": name, "root": root["id"], "root_name": "Brain 需求"}  # noqa: E731
    old = {"id": "s-0001", "task": "", "parent_session_key": "brain:r9-OLD-2", "conv_root": "s-0001", "done": True,
           "queued": False, "kind": "local", "key": "r9-OLD-2", "cwd": str(gone_wt), "title": "OLD-2 · 本地开发"}
    (tmp_path / "data" / "sessions.json").write_text(json.dumps({"infos": [old], "folders": {
        "brain:r1-PAY-1": rec("ab01", "PAY-1 · refund"), "brain:r9-OLD-2": rec("ab02", "OLD-2 · old"),
        "brain:r1-PAY-3": rec("ab03", "PAY-3 · nested")}}, ensure_ascii=False), encoding="utf-8")

    async def go():
        await svc.start()
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            moved = tree(gw)
            dev = await svc.open_session("r1", "PAY-1")
            return moved, dev, await svc.status()
        finally:
            await svc.stop()

    moved, dev, status = run(go())
    assert moved == ["Brain 需求", "Brain 需求 › PAY-3 · nested", "Brain 需求 › PAY-3 · nested › 我的（人）",
                     "Brain 需求 › gone", "Brain 需求 › gone › OLD-2 · old", "Brain 需求 › pay",
                     "Brain 需求 › pay › PAY-1 · refund"]
    assert gw.slot_rows[dev["slot"]]["folder_id"] == "ab01"
    rows = [r for r in status["crew"]["rows"] if "仓库文件夹" in r["text"]]
    assert rows == [{"level": "WARN", "text": "需求文件夹「PAY-3 · nested」没能放进仓库文件夹「pay」：里面有不是这个 app 建的文件夹，"
                                              "KiroCrew 不让 app 移它",
                     "todo": "想放进去，就在 KiroCrew 侧栏里自己把它拖进「Brain 需求」里的「pay」；不拖也不影响用",
                     "fix": None, "repo": None, "raw": ""}]


def test_cleanup_hides_the_folders_it_leaves_empty_at_every_level_and_never_the_repo_folder_of_one_it_keeps(tmp_path):
    async def go():
        gw = FakeGateway()
        mgr = manager(tmp_path, gw)
        for key, repo, folder in (("r1-PAY-1", "pay", "PAY-1 · refund"), ("r1-PAY-2", "pay", "PAY-2 · invoice"),
                                  ("r2-SHOP-1", "shop", "SHOP-1 · cart")):
            await open_local(mgr, key, repo, folder, str(tmp_path))
        return gw, await mgr.cleanup({"brain:r1-PAY-1"})

    gw, done = asyncio.run(go())
    assert done == {"sessions": 2, "folders": 3}
    assert tree(gw) == sorted(["Brain 需求", "Brain 需求 › pay", "Brain 需求 › pay › PAY-1 · refund",
                               "Brain 需求 › pay › PAY-2 · invoice（隐藏）", "Brain 需求 › shop（隐藏）",
                               "Brain 需求 › shop › SHOP-1 · cart（隐藏）"])
