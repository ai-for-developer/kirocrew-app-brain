"""repo_ids.json (backend/core/repoids.py, UI-19): a repo's id is given once per path and kept. A damaged file is kept
as it is and said in words, never read as a missing one (that would number the repos by list position again and
overwrite the file); two app processes on one data dir never give one id to two paths."""

import json

import pytest

from appkit import mod

RepoIds = mod("repoids").RepoIds
A, B, C, D = "/w/a", "/w/b", "/w/c", "/w/d"
SAVED = {"ids": {A: "r1", B: "r2", C: "r3"}, "next": 4}


@pytest.mark.parametrize("damage", ["", json.dumps(SAVED)[:25], "[]", json.dumps({"ids": 3, "next": 4})])
def test_a_damaged_file_is_kept_and_said_in_words(tmp_path, damage):
    f = tmp_path / "repo_ids.json"
    f.write_text(damage)
    ids = RepoIds(f)
    ids.of([B, C, D])
    assert f.read_text() == damage
    assert "坏了" in ids.problem and str(f) in ids.problem and "重启" in ids.problem


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
