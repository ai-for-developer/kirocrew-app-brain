"""选文件… / 选文件夹… (开需求's documents): POST /pick opens this machine's own file dialog with a fixed command and
answers the paths chosen; a cancel is no change; one dialog at a time; one left open too long is closed. No real
dialog opens here: the route runs a fake picker ($BRAIN_APP_PICKER), and the macOS and Linux commands are checked on
a recorded run (and compiled, on macOS, without running)."""

import asyncio
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from appkit import mod
from http_fakes import AppRoute, FakeWeb, call

picker = mod("picker")
AppError = mod("httpkit").AppError


@pytest.fixture(autouse=True)
def no_real_dialog(monkeypatch):
    """A test that forgets its fake gets a picker that is not there, never this machine's dialog."""
    monkeypatch.setenv("BRAIN_APP_PICKER", "/nonexistent/no-real-dialog-in-tests")


def routes(tmp_path):
    svc = mod("service").Service(SimpleNamespace(name="brain", logger=None, data_dir=str(tmp_path / "data")))
    return mod("handlers").build_routes(svc, AppRoute, web=FakeWeb(), is_owner=lambda _r: True)


def fake(tmp_path, monkeypatch, body):
    """The picker the route runs: `<it> file|folder`, each call's kind logged in <tmp>/asked."""
    p = tmp_path / "picker"
    p.write_text('#!/bin/sh\necho "$1" >> "%s"\n%s\n' % (tmp_path / "asked", body), encoding="utf-8")
    p.chmod(0o755)
    monkeypatch.setenv("BRAIN_APP_PICKER", str(p))
    return p


def asked(tmp_path):
    f = tmp_path / "asked"
    return f.read_text().split() if f.exists() else []


def pick(r, body):
    return asyncio.run(call(r, "POST", "/pick", json_body=body))


def test_the_paths_chosen_come_back_absolute_each_once_without_a_trailing_slash(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "printf '%s\\n' '/Users/alice/Downloads/结算 需求.docx' '/Users/alice/Downloads/飞书导出/' '' "
                                "'/Users/alice/Downloads/结算 需求.docx' 'relative/x.md'")
    r = routes(tmp_path)
    got = pick(r, {"kind": "file"})
    assert got.status == 200, got
    assert got.body == {"paths": ["/Users/alice/Downloads/结算 需求.docx", "/Users/alice/Downloads/飞书导出"]}
    assert pick(r, {"kind": "folder"}).status == 200
    assert asked(tmp_path) == ["file", "folder"]


def test_a_cancel_answers_no_paths_and_no_error(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "exit 1")
    got = pick(routes(tmp_path), {"kind": "folder"})
    assert (got.status, got.body) == (200, {"paths": []})


def test_only_the_two_kinds_open_anything(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "echo /x/should-not-run")
    r = routes(tmp_path)
    for body in ({}, {"kind": ""}, {"kind": "files"}, {"kind": ["file"]}, {"kind": 'file" & (do shell script "touch /tmp/x") & "'}):
        got = pick(r, body)
        assert (got.status, got.code) == (400, "invalid_kind"), body
    assert asked(tmp_path) == []


def test_one_dialog_at_a_time_the_second_click_says_where_the_first_is(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'touch "%s"\nwhile [ ! -e "%s" ]; do sleep 0.05; done\necho /Users/alice/a.docx'
         % (tmp_path / "open", tmp_path / "go"))
    r = routes(tmp_path)

    async def go():
        first = asyncio.ensure_future(call(r, "POST", "/pick", json_body={"kind": "file"}))
        end = time.monotonic() + 10
        while not (tmp_path / "open").exists() and time.monotonic() < end:
            await asyncio.sleep(0.02)
        second = await call(r, "POST", "/pick", json_body={"kind": "folder"})
        (tmp_path / "go").touch()
        return await first, second, await call(r, "POST", "/pick", json_body={"kind": "file"})

    first, second, third = asyncio.run(go())
    assert (second.status, second.code) == (409, "picker_busy")
    assert "已经开着" in second.body["error"]
    assert (first.status, first.body) == (200, {"paths": ["/Users/alice/a.docx"]})
    assert third.status == 200                               # the first one done: free again
    assert asked(tmp_path) == ["file", "file"]               # the second click opened nothing


def test_a_dialog_left_open_too_long_is_closed_and_the_next_click_opens_one(tmp_path, monkeypatch):
    monkeypatch.setattr(picker, "TIMEOUT_S", 0.5)
    p = fake(tmp_path, monkeypatch, 'echo $$ > "%s"\nexec sleep 30' % (tmp_path / "pid"))
    r = routes(tmp_path)
    t0 = time.monotonic()
    got = pick(r, {"kind": "file"})
    assert (got.status, got.code) == (408, "picker_timeout")
    assert "再点一次" in got.body["error"]
    assert time.monotonic() - t0 < 10
    with pytest.raises(ProcessLookupError):                  # the dialog's process is gone
        os.kill(int((tmp_path / "pid").read_text()), 0)
    p.write_text('#!/bin/sh\necho /Users/alice/b.docx\n', encoding="utf-8")
    assert pick(r, {"kind": "file"}).body == {"paths": ["/Users/alice/b.docx"]}


def test_a_picker_that_fails_says_why_and_that_paths_can_be_pasted(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "echo 'cannot open display' >&2\nexit 3")
    got = pick(routes(tmp_path), {"kind": "file"})
    assert (got.status, got.code) == (502, "picker_failed")
    assert "cannot open display" in got.body["error"] and "粘贴" in got.body["error"]


class Recorded:
    """subprocess.run as the dialog would answer: what it was asked, and the answer given."""

    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err, self.argv = code, out, err, None

    def __call__(self, argv, **kw):
        self.argv = argv
        assert kw["timeout"] == picker.TIMEOUT_S and kw["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(argv, self.code, self.out, self.err)


def mac(kind, rec):
    return picker.pick(kind, run=rec, platform="darwin", env={})


def test_on_macos_osascript_asks_for_several_files_or_folders_and_answers_their_posix_paths():
    rec = Recorded(out="/Users/alice/Downloads/需求.docx\n/Users/alice/Downloads/飞书导出/\n")
    assert mac("file", rec) == ["/Users/alice/Downloads/需求.docx", "/Users/alice/Downloads/飞书导出"]
    assert rec.argv[:2] == ["/usr/bin/osascript", "-e"] and len(rec.argv) == 3
    script = rec.argv[2]
    assert "choose file" in script and "multiple selections allowed" in script and "POSIX path" in script
    assert "choose folder" not in script
    mac("folder", rec)
    assert "choose folder" in rec.argv[2] and "multiple selections allowed" in rec.argv[2]


def test_on_macos_cancel_is_no_change_and_any_other_error_is_said():
    assert mac("file", Recorded(1, err="0:120: execution error: User canceled. (-128)\n")) == []
    with pytest.raises(AppError) as e:
        mac("file", Recorded(1, err="0:120: execution error: No user interaction allowed. (-1713)\n"))
    assert e.value.code == "picker_failed" and e.value.status == 502 and "-1713" in str(e.value)


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/osacompile"), reason="macOS only")
def test_the_macos_scripts_compile(tmp_path):
    for kind in picker.KINDS:
        rec = Recorded(1, err="User canceled. (-128)")
        mac(kind, rec)
        out = subprocess.run(["/usr/bin/osacompile", "-e", rec.argv[2], "-o", str(tmp_path / ("%s.scpt" % kind))],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def linux(kind, rec, env, tools):
    return picker.pick(kind, run=rec, platform="linux", env=env, which=lambda name: tools.get(name))


def test_on_linux_zenity_then_kdialog_with_a_display():
    rec = Recorded(out="/home/alice/a.docx\n/home/alice/b.pdf\n")
    zenity = {"zenity": "/usr/bin/zenity", "kdialog": "/usr/bin/kdialog"}
    assert linux("file", rec, {"DISPLAY": ":0"}, zenity) == ["/home/alice/a.docx", "/home/alice/b.pdf"]
    assert rec.argv[0] == "/usr/bin/zenity" and "--file-selection" in rec.argv and "--multiple" in rec.argv
    assert "--directory" not in rec.argv
    linux("folder", rec, {"WAYLAND_DISPLAY": "wayland-0"}, zenity)
    assert rec.argv[0] == "/usr/bin/zenity" and "--directory" in rec.argv
    assert linux("file", Recorded(1), {"DISPLAY": ":0"}, zenity) == []          # zenity's cancel
    linux("file", rec, {"DISPLAY": ":0"}, {"kdialog": "/usr/bin/kdialog"})
    assert rec.argv[0] == "/usr/bin/kdialog" and "--getopenfilename" in rec.argv and "--multiple" in rec.argv
    linux("folder", rec, {"DISPLAY": ":0"}, {"kdialog": "/usr/bin/kdialog"})
    assert rec.argv[0] == "/usr/bin/kdialog" and "--getexistingdirectory" in rec.argv


def test_without_a_dialog_the_answer_says_to_paste_the_paths():
    for platform, env, tools in (("linux", {}, {"zenity": "/usr/bin/zenity"}),       # no display (a service, ssh)
                                 ("linux", {"DISPLAY": ":0"}, {}),                    # neither zenity nor kdialog
                                 ("win32", {}, {})):
        rec = Recorded()
        with pytest.raises(AppError) as e:
            picker.pick("file", run=rec, platform=platform, env=env, which=lambda name: tools.get(name))
        assert (e.value.code, e.value.status) == ("no_picker", 501) and "粘贴" in str(e.value)
        assert rec.argv is None
