"""repo_ids.json (backend/core/repoids.py, UI-19): a repo's id is given once per path and kept. Every good write keeps
a last good copy, read when the file is damaged; without one the file is kept as it is and said in words, and no id is
guessed (list positions would attach records to the wrong repo) until 按记录重建编号 rebuilds it from the records'
ids; two app processes on one data dir never give one id to two paths."""

import json

import pytest

from appkit import mod

repoids = mod("repoids")
RepoIds = repoids.RepoIds
A, B, C, D = "/w/a", "/w/b", "/w/c", "/w/d"
SAVED = {"ids": {A: "r1", B: "r2", C: "r3"}, "next": 4}


@pytest.mark.parametrize("damage", ["", json.dumps(SAVED)[:25], "[]", json.dumps({"ids": 3, "next": 4})])
def test_a_damaged_file_is_kept_and_said_in_words(tmp_path, damage):
    f = tmp_path / "repo_ids.json"
    f.write_text(damage)
    ids = RepoIds(f)
    assert ids.of([B, C, D]) == {}
    assert f.read_text() == damage
    assert "坏了" in ids.problem and str(f) in ids.problem and "按记录重建编号" in ids.problem


@pytest.mark.parametrize("damage", [None, "", json.dumps(SAVED)[:25]])
def test_a_damaged_or_lost_file_is_read_from_its_last_good_copy_and_written_back(tmp_path, damage):
    f = tmp_path / "repo_ids.json"
    RepoIds(f).of([A, B, C])
    assert json.loads(f.with_name("repo_ids.json.bak").read_text()) == SAVED
    if damage is None:
        f.unlink()
    else:
        f.write_text(damage)
    ids = RepoIds(f)
    assert ids.of([B, C, D]) == {B: "r2", C: "r3", D: "r4"} and ids.problem == ""
    assert json.loads(f.read_text())["ids"] == dict(SAVED["ids"], **{D: "r4"})


def test_from_records_keeps_an_id_only_where_the_records_name_one_repo_and_gives_the_rest_new_ones():
    pay, shop, web = "/w/pay", "/w/shop", "/w/web"
    keys = {pay: {"PAY-1", "PAY-2"}, shop: {"SHOP-1"}, web: {"WEB-1"}}
    # r1: pay's; r2: shop's and web's (a run numbered by position once): neither; r5: a removed repo's, kept free
    found = {"r1": {"PAY-1"}, "r2": {"SHOP-1", "WEB-1"}, "r5": {"OLD-1"}}
    ids, n, fresh = repoids.from_records([pay, shop, web], keys, found)
    assert ids == {pay: "r1", shop: "r6", web: "r7"} and n == 8 and fresh == [shop, web]
    ids, n, fresh = repoids.from_records([pay, shop], {pay: keys[pay], shop: None}, {"r1": {"PAY-2"}})
    assert ids == {pay: "r1", shop: "r2"} and fresh == [shop]


def test_a_file_without_next_keeps_its_ids_and_gives_the_next_one_after_them(tmp_path):
    f = tmp_path / "repo_ids.json"
    f.write_text(json.dumps({"ids": SAVED["ids"]}))
    ids = RepoIds(f)
    assert ids.of([B, C, D]) == {B: "r2", C: "r3", D: "r4"} and ids.problem == ""
    assert json.loads(f.read_text())["ids"] == dict(SAVED["ids"], **{D: "r4"})


def test_without_a_file_the_list_positions_are_the_first_ids(tmp_path):
    ids = RepoIds(tmp_path / "repo_ids.json")
    assert ids.of([B, C]) == {B: "r1", C: "r2"} and ids.problem == ""


def test_two_apps_on_one_data_dir_never_give_one_id_to_two_paths(tmp_path):
    f = tmp_path / "repo_ids.json"
    f.write_text(json.dumps(SAVED))
    one, two = RepoIds(f), RepoIds(f)
    x, y = one.of([B, C, "/w/x"])["/w/x"], two.of([B, C, "/w/y"])["/w/y"]
    assert x != y
    assert json.loads(f.read_text())["ids"] == dict(SAVED["ids"], **{"/w/x": x, "/w/y": y})


def test_the_settings_page_hears_of_a_damaged_file_and_the_file_stays(tmp_path, home):
    from brainfix import make_project, make_service
    from test_import import started
    pay = make_project(tmp_path)
    f = tmp_path / "data" / "repo_ids.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"ids": {"%s": "r7"' % pay)
    svc = make_service(tmp_path, [pay])
    st = started(svc, svc.status)
    assert "坏了" in st["repo_ids_problem"] and str(f) in st["repo_ids_problem"]
    assert f.read_text() == '{"ids": {"%s": "r7"' % pay
    fine = make_service(tmp_path / "other", [pay])
    assert started(fine, fine.status)["repo_ids_problem"] == ""


def test_a_damaged_file_after_a_removal_keeps_the_ids_from_its_copy(tmp_path, home):
    """pay r1, shop r2; the file is cut short and pay is removed: shop stays r2 (it was r1 for that run before)."""
    from brainfix import make_project, make_service
    from test_import import started
    pay, shop = make_project(tmp_path, "pay", "PAY"), make_project(tmp_path, "shop", "SHOP")
    first = make_service(tmp_path, [pay, shop])
    assert {r["name"]: r["id"] for r in started(first, first.status)["repos"]} == {"pay": "r1", "shop": "r2"}
    f = tmp_path / "data" / "repo_ids.json"
    f.write_text(f.read_text()[:30])
    again = make_service(tmp_path, [shop])
    st = started(again, again.status)
    assert [(r["name"], r["id"]) for r in st["repos"]] == [("shop", "r2")] and st["repo_ids_problem"] == ""


@pytest.mark.skipif(not __import__("brainfix").BRAINKIT.is_dir(), reason="needs brain-kit")
def test_without_a_good_copy_no_repo_is_numbered_until_it_is_rebuilt_from_the_records(tmp_path, home):
    from brainfix import make_project, make_service, run, start_in_worktree
    pay, shop = make_project(tmp_path, "pay", "PAY"), make_project(tmp_path, "shop", "SHOP")
    start_in_worktree(shop, tmp_path / "wt" / "shop-1", "refund", "退款")
    data = tmp_path / "data"
    (data / "events").mkdir(parents=True)
    (data / "events" / "r2-SHOP-1.jsonl").write_text('{"at": "2026-11-15T01:00:00Z", "kind": "app", "label": "开工"}\n')
    (data / "repo_ids.json").write_text('{"ids": {"%s": "r' % pay)
    svc = make_service(tmp_path, [pay, shop])

    async def go():
        await svc.start()
        try:
            before = await svc.status()
            got = await svc.repo_ids_rebuild()
            return before, got, await svc.overview("all")
        finally:
            await svc.stop()

    before, got, ov = run(go())
    assert before["repos"] == [] and "按记录重建编号" in before["repo_ids_problem"], before["repo_ids_problem"]
    st = got["status"]
    assert {r["name"]: r["id"] for r in st["repos"]} == {"shop": "r2", "pay": "r3"} and st["repo_ids_problem"] == ""
    assert "shop" in got["text"] and "pay" in got["text"], got["text"]
    assert [(r["repo"], r["key"]) for r in ov["rows"]] == [("r2", "SHOP-1")]
    assert json.loads((data / "repo_ids.json").read_text())["ids"] == {str(shop): "r2", str(pay): "r3"}
