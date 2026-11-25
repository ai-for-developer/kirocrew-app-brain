"""A team's .brain/kit.json changed the one way the app changes it: on a branch of its own, through an MR a person merges.

kit.json on the default branch is the team's rule (一键建运行机's bridge_user, bot_users and work_sources; 设置's
「允许快速模式」, fast_mode), and brain-kit and CI read it from there. So the app never writes it on the default branch:
kit_mr puts the change on a branch through GitLab's API with the person's own token, written the way the file is
written, and opens the MR when none is open. Run again, or after a crash, it ends the same: the branch, at most one
commit that changes the file, one open MR.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shlex
import urllib.parse
from typing import Any, Mapping

from . import bk, flowview
from .installer import new_mr_link

KIT = ".brain/kit.json"
WS_RE = re.compile(r"[ \t\n\r]*")         # JSON's whitespace
FAST_BRANCH = "brain/fast-mode"
FAST_TITLE = {True: "设置：允许快速模式（.brain/kit.json fast_mode）", False: "设置：关掉快速模式（.brain/kit.json fast_mode）"}
FAST_TEXT = {
    True: "Brain app 设置页「允许快速模式」开的 MR。合并以后，这个仓库可以开快速需求：不开 Lean intent，不走阶段和审批；"
          "提交前的自动检查、CI 和风险目录照旧，做完开 MR，由人合并。",
    False: "Brain app 设置页「允许快速模式」开的 MR。合并以后，这个仓库不再允许快速需求：brain start --fast 和 Brain app 的"
           "「快速」都会拒绝。已经开着的快速需求 MR，下一次流水线会失败：转完整流程，或者合并以前把开关再打开。",
}


def _parsed(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def kit_text(doc: Any, like: str) -> str:
    """*doc* as JSON written the way *like*, the text it replaces, is, so the MR shows only the keys that changed: the
    text of each key whose value stays is kept as it is; a changed value, or a new key's, is written the way that
    value was (an object or list on one line stays on one line), a new key last (where it sorts, when the keys are
    sorted); whether non-ASCII was escaped and the final newline stay. A *like* that is not one JSON object is written
    whole in its indent and key order."""
    old = _parsed(like)
    if isinstance(doc, dict) and isinstance(old, dict) and old:
        try:
            return _edited(doc, old, like)
        except ValueError:          # a key twice, nothing left: written whole
            pass
    return _whole(doc, like)


def _escaped(like: str) -> bool:
    return bool(re.search(r"\\u[0-9a-fA-F]{4}", like)) and not re.search(r"[^\x00-\x7f]", like)


def _whole(doc: Any, like: str) -> str:
    """*doc* in *like*'s indent, key order (sorted when its keys were) and final newline."""
    m = re.search(r"\n([ \t]+)\S", like)
    indent: Any = (m.group(1) if "\t" in m.group(1) else len(m.group(1))) if m else None
    old = _parsed(like)
    keys = list(old) if isinstance(old, dict) else []
    text = json.dumps(doc, indent=indent, sort_keys=len(keys) > 1 and keys == sorted(keys), ensure_ascii=_escaped(like))
    return text + ("\n" if like.endswith("\n") or not like else "")


def _members(text: str) -> tuple[int, list[tuple[str, int, int, int, int]], int]:
    """The JSON object *text* is: (where its { is, its members as (key, key start, key end, value start, value end),
    where its } is). ValueError when it names a key twice."""
    dec = json.JSONDecoder()
    start = WS_RE.match(text, 0).end()
    i, out = WS_RE.match(text, start + 1).end(), []
    while text[i:i + 1] != "}":
        key, ke = json.decoder.scanstring(text, i + 1)
        vs = WS_RE.match(text, WS_RE.match(text, ke).end() + 1).end()
        _, ve = dec.raw_decode(text, vs)
        out.append((key, i, ke, vs, ve))
        i = WS_RE.match(text, ve).end()
        if text[i:i + 1] == ",":
            i = WS_RE.match(text, i + 1).end()
    if len({m[0] for m in out}) != len(out):
        raise ValueError("a key twice")
    return start, out, i


def _edited(doc: dict, old: dict, like: str) -> str:
    """kit_text of an object *like* that has members (*old*, parsed), member by member."""
    start, members, end = _members(like)
    keys, by_key = [m[0] for m in members], {m[0]: m for m in members}
    escaped, sort = _escaped(like), len(keys) > 1 and keys == sorted(keys)
    lead = like[start + 1:members[0][1]]
    pad = lead.rsplit("\n", 1)[1] if "\n" in lead else ""           # a member's indent: one level
    colon = like[members[0][2]:members[0][3]]
    seps = (",", ":") if colon.strip() == ":" and not colon.endswith(" ") else (", ", ": ")
    nested = [like[m[3]:m[4]] for m in members if isinstance(old[m[0]], (dict, list)) and old[m[0]]]
    flat = sum("\n" not in t for t in nested) * 2 > len(nested) if nested else "\n" not in like[start:end]

    def value(v: Any, was: str | None) -> str:
        if (("\n" not in was) if was is not None else flat) or not pad or not isinstance(v, (dict, list)) or not v:
            return json.dumps(v, ensure_ascii=escaped, separators=seps, sort_keys=sort)
        return json.dumps(v, ensure_ascii=escaped, indent=pad if "\t" in pad else len(pad),
                          sort_keys=sort).replace("\n", "\n" + pad)

    order = [k for k in keys if k in doc]
    for k in [k for k in doc if k not in by_key]:
        order.insert(next((n for n, o in enumerate(order) if o > k), len(order)) if sort else len(order), k)
    if not order:
        raise ValueError("no key left")
    sep = like[members[0][4]:members[1][1]] if len(members) > 1 else "," + (lead or " ")
    parts: list[str] = []
    for n, k in enumerate(order):
        m = by_key.get(k)
        if m is None:
            text = json.dumps(k, ensure_ascii=escaped) + colon + value(doc[k], None)
        elif json.dumps(doc[k], sort_keys=True) == json.dumps(old[k], sort_keys=True):      # 1 is not true
            text = like[m[1]:m[4]]
        else:
            text = like[m[1]:m[3]] + value(doc[k], like[m[3]:m[4]])
        if n:
            i = keys.index(order[n - 1]) if order[n - 1] in by_key else -1
            parts.append(like[members[i][4]:m[1]] if m is not None and 0 <= i and i + 1 < len(members)
                         and members[i + 1][0] == k else sep)
        parts.append(text)
    return like[:start + 1] + lead + "".join(parts) + like[members[-1][4]:end] + like[end:]


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


def own_mrs(gl: Any, project: Any, branch: str) -> list[dict[str, Any]]:
    """The open MRs of *branch* in *project* and into it: never one from a fork's branch of that name."""
    pid = (gl.project(project) or {}).get("id")
    return [m for m in gl.mrs_for_branch(project, branch) if m.get("source_branch") == branch
            and m.get("source_project_id") == pid and m.get("target_project_id") == pid]


def kit_mr(gl: Any, project: Any, default: str, branch: str, want: Mapping[str, Any], like: str, *, title: str,
           text: str, message: str) -> tuple[dict[str, Any], bool]:
    """kit.json on *branch* made to equal *want*, and an MR of it into *default*: with an MR of it open (own_mrs), its
    branch, and its description made *text* when it says something else; with none, the branch made again from
    *default* (what an earlier change left on it never rides along); one commit when the branch's kit.json differs
    (written the way the branch's file is, else *like*, the default branch's text), the MR (*title*, *text*; its branch
    removed once merged) when none is open. *gl* is brain-kit's rest.GitLab with the person's token; *project* its id
    or path. -> (the MR, whether it was opened now)."""
    p = _project(project)
    mr = next(iter(own_mrs(gl, project, branch)), None)
    if mr is None:
        if branch_exists(gl, project, branch):
            gl.call("DELETE", "/projects/%s/repository/branches/%s" % (p, urllib.parse.quote(branch, safe="")))
        gl.call("POST", "/projects/%s/repository/branches" % p, body={"branch": branch, "ref": default})
    raw = gl.file_raw(project, KIT, branch)
    there = raw.decode("utf-8-sig") if raw is not None else ""
    if _parsed(there) != want:
        gl.call("POST", "/projects/%s/repository/commits" % p, body={
            "branch": branch, "commit_message": message,
            "actions": [{"action": "update" if raw is not None else "create", "file_path": KIT,
                         "content": kit_text(want, there or like)}]})
    if mr is not None:
        if mr.get("description") != text:
            mr = gl.edit_mr(project, int(mr["iid"]), description=text) or dict(mr, description=text)
        return mr, False
    return gl.call("POST", "/projects/%s/merge_requests" % p, body={
        "source_branch": branch, "target_branch": default, "title": title, "description": text,
        "remove_source_branch": True}) or {}, True


# ---- 设置's 「允许快速模式」 ---------------------------------------------------------------------------------------- #

def fast_policy(path: str, ref: str | None) -> tuple[bool, str]:
    """(fast_mode on the branch at *ref*, why fast runs are not offered there whatever the switch says: brain-kit's
    first other refusal, or why its kit.json cannot be read; '' when none)."""
    p, unreadable = flowview.policy(path, ref)
    if p is None:
        return True, unreadable
    fl = bk.m("flows")
    why = fl.refusals(fl.Flow.FAST, lambda: dataclasses.replace(p, fast_mode=True))
    return p.fast_mode, why[0].zh if why else ""


def branch_fast(git: Any, ref: str) -> bool | None:
    """fast_mode in kit.json on *ref* (missing: on, as brain-kit reads it); None when *ref* has no kit.json."""
    raw = git.show(ref, KIT)
    doc = _parsed(raw.decode("utf-8-sig", "replace")) if raw else None
    return bool(doc.get("fast_mode", True)) if isinstance(doc, dict) else None


def fast_pending(on: bool, branch_on: bool | None, mr: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The switch's change not merged yet: its MR when this machine reads it (GitLab's word wins: merged or closed is
    over), else the branch brain/fast-mode on origin saying something other than the default branch. *branch_on*: the
    branch's fast_mode at the MR's head (None when that commit is not fetched yet: an MR of the switch turns the
    default branch's value over)."""
    state = (mr or {}).get("state")
    if state == "opened":
        return {"on": branch_on if branch_on is not None else not on, "iid": mr.get("iid"), "url": mr.get("url")}
    if state is None and branch_on is not None and branch_on != on:
        return {"on": branch_on, "iid": None, "url": None}
    return None


def fast_commands(top: str, default: str, want: bool, base: str, project: str) -> list[str]:
    """The change by hand, in a worktree of its own (the person's checkout stays as it is)."""
    q, wt = shlex.quote, top.rstrip("/") + "-fast-mode"
    cmds = ["git -C %s fetch origin %s" % (q(top), q(default)),
            "git -C %s worktree add -b %s %s origin/%s" % (q(top), FAST_BRANCH, q(wt), q(default)),
            "# %s/%s 里写上 \"fast_mode\": %s" % (wt, KIT, "true" if want else "false"),
            "git -C %s commit -m %s %s" % (q(wt), q(FAST_TITLE[want]), KIT),
            "git -C %s push -u origin %s" % (q(wt), FAST_BRANCH),
            "git -C %s worktree remove %s" % (q(top), q(wt))]
    if base and project:
        cmds.append("glab mr create -R %s --source-branch %s --target-branch %s --title %s --description %s "
                    "--remove-source-branch --yes" % (q("%s/%s" % (base.rstrip("/"), project)), FAST_BRANCH, q(default),
                                                      q(FAST_TITLE[want]), q(FAST_TEXT[want])))
    return cmds


def fast_mr(top: str, default: str, want: bool, base: str, project: str, *, transport: Any = None) -> dict[str, Any]:
    """Make the default branch say fast_mode *want*, through an MR (blocking: GitLab with the person's token; call it in
    a thread). FastModeResult without its view: nothing to do when the default branch says it already (an open MR of
    the switch for the other value is named, to close); the MR through kit_mr; without a token or a GitLab project,
    the commands and GitLab's new-MR link."""
    out: dict[str, Any] = {"ok": False, "text": "", "mr": None, "commands": [], "new_mr_url": None}
    rest = bk.m("rest")
    why = "" if base and project else "brain-kit 设置里没写 GitLab 项目"
    tok = ""
    if not why:
        try:
            tok = rest.read_token()
        except Exception as exc:  # noqa: BLE001 - a token file others can read is refused, as brain-kit does
            why = "GitLab 令牌读不了（%s）" % str(exc).split("\n", 1)[0][:200]
        why = why or ("" if tok else "这台机器上没有 GitLab 令牌（放在 ~/.brain/gitlab-token 或环境变量 GITLAB_TOKEN 里，"
                                     "app 就能自己开 MR）")
    if why:
        out.update(commands=fast_commands(top, default, want, base, project),
                   new_mr_url=new_mr_link(base, project, FAST_BRANCH, default),
                   text=why + "：照下面的命令自己改、自己开 MR，合并以后生效。")
        return out
    gl = rest.GitLab(base, tok, transport)
    raw = gl.file_raw(project, KIT, default)
    like = raw.decode("utf-8-sig") if raw is not None else ""
    doc = _parsed(like)
    if not isinstance(doc, dict):
        out["text"] = "默认分支 %s 上的 .brain/kit.json 读不了：先在导入仓库里把 kit 文件装好" % default
        return out
    open_mr = gl.mr_for_branch(project, FAST_BRANCH)
    if bool(doc.get("fast_mode", True)) == want:
        out.update(ok=True, text="默认分支上已经是「%s」了" % ("允许" if want else "不允许") + (
            "：MR !%s 不用合并了，在 GitLab 上关掉它" % open_mr.get("iid") if open_mr else ""))
        return out
    mr, opened = kit_mr(gl, project, default, FAST_BRANCH, dict(doc, fast_mode=want), like, title=FAST_TITLE[want],
                        text=FAST_TEXT[want], message=FAST_TITLE[want])
    iid = int(mr.get("iid") or 0)
    url = mr.get("web_url") or "%s/%s/-/merge_requests/%d" % (base.rstrip("/"), project, iid)
    out.update(ok=True, mr={"iid": iid, "url": url, "existing": not opened},
               text=("开了 MR !%d：合并以后生效" if opened else "MR !%d 已经开着：合并以后生效") % iid)
    return out
