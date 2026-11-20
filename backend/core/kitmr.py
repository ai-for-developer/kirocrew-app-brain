"""A team's .brain/kit.json changed the one way the app changes it: on a branch of its own, through an MR a person merges.

kit.json on the default branch is the team's rule (一键建运行机's bridge_user, bot_users and work_sources; 设置's
「允许快速模式」, fast_mode), and brain-kit and CI read it from there. So the app never writes it on the default branch:
kit_mr puts the change on a branch through GitLab's API with the person's own token, written the way the file is
written, and opens the MR when none is open. Run again, or after a crash, it ends the same: the branch, at most one
commit that changes the file, one open MR.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Mapping

KIT = ".brain/kit.json"


def _parsed(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def kit_text(doc: Any, like: str) -> str:
    """*doc* as JSON written the way *like*, the text it replaces, is: its indent, its key order (sorted when its keys
    were), whether it escaped non-ASCII, its final newline. So the MR shows only the keys that changed."""
    m = re.search(r"\n([ \t]+)\S", like)
    indent: Any = (m.group(1) if "\t" in m.group(1) else len(m.group(1))) if m else None
    old = _parsed(like)
    keys = list(old) if isinstance(old, dict) else []
    escaped = bool(re.search(r"\\u[0-9a-fA-F]{4}", like)) and not re.search(r"[^\x00-\x7f]", like)
    text = json.dumps(doc, indent=indent, sort_keys=len(keys) > 1 and keys == sorted(keys), ensure_ascii=escaped)
    return text + ("\n" if like.endswith("\n") or not like else "")


def _project(project: Any) -> str:
    return urllib.parse.quote(str(project), safe="")


def branch_exists(gl: Any, project: Any, name: str) -> bool:
    try:
        gl.call("GET", "/projects/%s/repository/branches/%s" % (_project(project), urllib.parse.quote(name, safe="")))
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "status", None) == 404:
            return False
        raise
    return True


def kit_mr(gl: Any, project: Any, default: str, branch: str, want: Mapping[str, Any], like: str, *, title: str,
           text: str, message: str) -> tuple[dict[str, Any], bool]:
    """kit.json on *branch* made to equal *want*, and an MR of it into *default*: the branch from *default* when it is
    missing, one commit when the branch's kit.json differs (written the way the branch's file is, else *like*, the
    default branch's text), the MR (*title*, *text*; its branch removed once merged) when none is open. *gl* is
    brain-kit's rest.GitLab with the person's token; *project* its id or path. -> (the MR, whether it was opened now)."""
    p = _project(project)
    if not branch_exists(gl, project, branch):
        gl.call("POST", "/projects/%s/repository/branches" % p, body={"branch": branch, "ref": default})
    raw = gl.file_raw(project, KIT, branch)
    there = raw.decode("utf-8-sig") if raw is not None else ""
    if _parsed(there) != want:
        gl.call("POST", "/projects/%s/repository/commits" % p, body={
            "branch": branch, "commit_message": message,
            "actions": [{"action": "update" if raw is not None else "create", "file_path": KIT,
                         "content": kit_text(want, there or like)}]})
    mr = gl.mr_for_branch(project, branch)
    if mr is not None:
        return mr, False
    return gl.call("POST", "/projects/%s/merge_requests" % p, body={
        "source_branch": branch, "target_branch": default, "title": title, "description": text,
        "remove_source_branch": True}) or {}, True
