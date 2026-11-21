"""`brain mr` and `brain full`: how a run without a Lean intent ends, or becomes a Lean run (flows.FAST.actions).

  brain mr [--no-push] [--json]    push the run branch and open its MR into the default branch, or bring the open one's
                                   变更说明 up to date (mrdesc, from the records). No label: a person reviews and merges
                                   it, and MR 巡检 may work it. Refused while anything is uncommitted, new files git
                                   does not ignore included (Lean's ledger alone is committed for you), when the branch
                                   changes a risk path, or when the default branch no longer allows fast runs: CI would
                                   fail the MR, and `brain full` is the way on. Without a GitLab token or
                                   gitlab.project, or with --no-push: the exact commands and GitLab's new-MR link
                                   instead.
  brain full [--no-push] [--json]  switch this run to the full flow on the same branch: its Lean intent (`lean new`, or
                                   the folder a stopped try left), selected in this clone, run.json without mode, one
                                   commit, pushed. The code written so far stays; Lean's gates hold from requirements
                                   on. On a run that is full already it says so and exits 0 (and pushes a switch a
                                   stopped try committed but did not push). A warning when MR 巡检's bot has commits
                                   on the branch: on the full flow CI counts them only after a hand-over.
--json prints one ActOut object; a refusal stays `brain: <why>` on stderr with exit 2, as for every brain command.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from .. import ci, claims, cli, config, flows, lean, mrdesc, runrec
from ..common import BrainError, git, git_out, load_json, repo_root, write_json
from ..flows import Action
from ..rest import HttpError

LEDGER = lean.DOCS + "/audit"


@dataclass(frozen=True)
class ActOut:
    """What `brain mr --json` and `brain full --json` print. The Brain app parses it at its boundary."""
    ok: bool
    action: str                  # "mr" | "full"
    text: str                    # one sentence for the person (Chinese, as the app shows it)
    pushed: bool
    mr: Optional[Dict]           # {"iid": int, "url": str, "existing": bool}; None without one
    commands: Tuple[str, ...]    # no GitLab token or project, or --no-push: what to run instead
    new_mr_url: Optional[str]    # GitLab's new-MR page for the branch, when no MR was opened and the project is known
    intent: Optional[str]        # the run's Lean intent now (brain full)
    warnings: Tuple[str, ...]


def _say(a, out: ActOut) -> int:
    if a.json:
        print(json.dumps(asdict(out), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for w in out.warnings:
        print("WARN: " + w)
    print(out.text)
    for line in out.commands + ((out.new_mr_url,) if out.new_mr_url else ()):
        print("  " + line)
    return 0


# ---------- brain mr ----------

def _commit_records(repo, key) -> bool:
    """Commit Lean's ledger alone when the session left records after its last commit (Lean asks no verify for a
    records-only commit). True when it made the commit."""
    if not os.path.isdir(os.path.join(repo, LEDGER)):
        return False
    git(repo, "add", "--", LEDGER)
    if git(repo, "diff", "--cached", "--quiet", "--", LEDGER, check=False).returncode == 0:
        return False
    git(repo, "commit", "-q", "-m", "%s: Lean records" % key, "--", LEDGER)
    return True


def _description(repo, cfg, key, warnings):
    try:
        return mrdesc.render(mrdesc.build(repo, cfg, key))
    except Exception as e:  # noqa: BLE001 - the description helps the reviewers; it does not hold up the MR
        warnings.append("变更说明没生成（%s）：MR 先不带说明" % str(e).replace(repo, "."))
        return None


def _commands(repo, cfg, branch, title, desc, pushed) -> list:
    q = shlex.quote
    base, project = cfg["gitlab"]["base"], cfg["gitlab"]["project"]
    out = [] if pushed else ["git -C %s push -u origin %s" % (q(repo), q(branch))]
    where = "-R %s " % q("%s/%s" % (base.rstrip("/"), project)) if base and project else ""
    out.append("glab mr create %s--source-branch %s --target-branch %s --title %s%s --yes" % (
        where, q(branch), q(cfg["default_branch"]), q(title), " --description %s" % q(desc) if desc else ""))
    return out


def _new_mr_url(cfg, branch):
    base, project = cfg["gitlab"]["base"], cfg["gitlab"]["project"]
    if not base or not project:
        return None
    return "%s/%s/-/merge_requests/new?%s=%s&%s=%s" % (
        base.rstrip("/"), project, quote("merge_request[source_branch]", safe=""), quote(branch, safe=""),
        quote("merge_request[target_branch]", safe=""), quote(cfg["default_branch"], safe=""))


def _no_mr(cfg, pushed) -> str:
    missing = ["gitlab." + k for k in ("base", "project") if not cfg["gitlab"][k]]
    if not pushed:
        why = "没推送（--no-push）"
    elif missing:
        why = "推上去了。%s 没写 %s" % (config.KIT_CONFIG, "、".join(missing))
    else:
        why = "推上去了。这台机器上没有 GitLab 令牌（GITLAB_TOKEN，或 %s）" % os.environ.get("BRAIN_GITLAB_TOKEN_FILE",
                                                                                   "~/.brain/gitlab-token")
    return why + "：用下面的命令开 MR，不加交接标签（人来审、合并）"


def _create(gl, project, branch, target, title, desc, warnings):
    """A new MR without labels, with the description; when GitLab refuses it with one, without (and a warning)."""
    if desc is not None:
        try:
            return gl.create_mr(project, branch, target, title, [], description=desc)
        except HttpError as e:
            warnings.append("GitLab 没收变更说明（%s）：MR 先不带说明" % e)
    return gl.create_mr(project, branch, target, title, [])


def cmd_mr(a):
    repo = repo_root()
    cfg = config.load(repo)
    br, b, rp, rec = cli._current(repo, cfg)
    flow = flows.of(rec)
    why = flows.action_problem(flow, Action.MR)
    if why:
        raise BrainError(why)
    if cli._me(repo, cfg) != rec["owner"]:
        raise BrainError("这是 %s 的需求：只有负责人能开 MR" % rec["owner"])
    target = cfg["default_branch"]
    if not cli._has_origin(repo):
        raise BrainError("这个仓库没有远端，开不了 MR：在本机合并（git switch %s && git merge --no-ff %s）" % (target, br))
    code = sorted(p for p in {r[3:].strip() for r in cli._dirty(repo)} | cli._untracked(repo)
                  if not p.startswith(LEDGER + "/"))
    if code:
        raise BrainError("还有没提交的改动（%s%s）：在会话里让 AI 跑 lean verify，绿了再提交（Lean 的记录 brain mr 会自己提交）" % (
            "、".join(code[:3]), " 等 %d 个文件" % len(code) if len(code) > 3 else ""))
    ref = cli._brain_base(repo, cfg)[0]
    refused = flows.refusals(flow, lambda: flows.policy_at(repo, ref))
    if refused:
        raise BrainError("没开 MR，CI 会拦它：%s。先 brain full 转完整流程" % "；".join(r.zh for r in refused))
    bad, risky = ci.risk_paths(repo, rec, cfg, mrdesc._MainTarget(repo, ref), git_out(repo, "merge-base", ref, "HEAD"))
    if risky:
        raise BrainError("改到了风险目录（%s）：快速需求不开这个 MR，CI 会拦。先 brain full 转完整流程（别为了过检查把改动撤掉）"
                         % "、".join(risky[:5]))
    warnings = list(bad)
    _commit_records(repo, b["key"])
    desc = _description(repo, cfg, b["key"], warnings)
    title = "%s %s" % (b["key"], b["slug"].replace("-", " "))
    pushed = not a.no_push
    if pushed:
        git(repo, "push", "-q", "-u", "origin", br)
    gl = cli._gitlab(cfg) if pushed else None
    project = cfg["gitlab"]["project"]
    if not gl or not project:
        return _say(a, ActOut(True, "mr", _no_mr(cfg, pushed), pushed, None,
                              tuple(_commands(repo, cfg, br, title, desc, pushed)), _new_mr_url(cfg, br), None,
                              tuple(warnings)))
    open_mrs = [m for m in gl.mrs_for_branch(project, br) if m.get("target_branch") == target]
    if open_mrs:
        mr, existing = open_mrs[0], True
        if desc is not None:
            try:
                gl.edit_mr(project, mr["iid"], description=desc)
            except BrainError as e:
                warnings.append("MR !%s 的变更说明没更新（%s）" % (mr["iid"], e))
    else:
        mr, existing = _create(gl, project, br, target, title, desc, warnings), False
    url = mr.get("web_url") or "%s/%s/-/merge_requests/%s" % (cfg["gitlab"]["base"].rstrip("/"), project, mr["iid"])
    text = "推上去了，%s（没有交接标签：人来审、合并）" % (
        "MR !%s 的变更说明更新了" % mr["iid"] if existing else "开了 MR !%s" % mr["iid"])
    return _say(a, ActOut(True, "mr", text, True, {"iid": int(mr["iid"]), "url": url, "existing": existing}, (), None,
                          None, tuple(warnings)))


# ---------- brain full ----------

def _committed(repo, rel, branch, cfg) -> dict:
    """run.json as HEAD has it, checked as _current checks the working tree's: what `brain full` converges from."""
    p = git(repo, "show", "HEAD:" + rel, check=False)
    try:
        rec = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        rec = None
    probs = runrec.validate(rec, branch=branch, prefix=cfg["branch_prefix"]) if rec is not None else [
        "%s is not committed" % rel]
    if probs:
        raise BrainError("run.json at HEAD: " + "; ".join(probs))
    return rec


def _left_folder(repo, slug):
    """The intent folder Lean made for this run (<yymmdd>-<slug>) that a stopped try left behind, or None."""
    d = os.path.join(repo, lean.DOCS)
    found = sorted(x for x in (os.listdir(d) if os.path.isdir(d) else ())
                   if re.match(r"^[0-9]{6}-%s$" % re.escape(slug), x) and os.path.isdir(os.path.join(d, x)))
    if len(found) > 1:
        raise BrainError("这个需求有不止一个 Lean intent 文件夹（%s）：留一个，再跑 brain full" % "、".join(found))
    return found[0] if found else None


def _select(repo, intent):
    """Make `intent` this clone's active Lean intent, replacing `use --none` (nothing is recorded when it is so)."""
    try:
        active = load_json(os.path.join(repo, cli.LEAN_ACTIVE)).get("intent")
    except (OSError, ValueError, AttributeError):
        active = None
    if active != intent:
        lean.call(repo, "use", intent)


def _origin_still_fast(repo, branch, rel) -> bool:
    """Does origin's copy of the branch, as this clone last fetched or pushed it, still hold the fast run.json?"""
    seen = git(repo, "rev-parse", "-q", "--verify", "refs/remotes/origin/%s" % branch, check=False).stdout.strip()
    p = git(repo, "show", "%s:%s" % (seen, rel), check=False) if seen else None
    try:
        there = json.loads(p.stdout) if p is not None and p.returncode == 0 else None
        return isinstance(there, dict) and not flows.of(there).intent
    except (ValueError, BrainError):
        return False


def _patrol_warning(repo, cfg, rec) -> tuple:
    """The bot's commits on this branch that the default branch does not have: on the full flow CI counts them only
    after a hand-over (ci.check_run), so the MR stays red until then."""
    bots = {e.lower() for e in cfg.get("bot_emails") or []}
    known = [r for r in ("refs/remotes/origin/" + cfg["default_branch"], "refs/heads/" + cfg["default_branch"])
             if git(repo, "rev-parse", "-q", "--verify", r, check=False).returncode == 0]
    rows = git_out(repo, "log", "--format=%ae%x00%ce", "HEAD", "--not", rec["brain_commit"], *known).splitlines()
    n = sum(1 for row in rows if bots & {x.strip().lower() for x in row.split("\0")})
    return ("这个分支上已经有机器人（MR 巡检）的 %d 个提交：转成完整流程以后，CI 要等交接（brain handoff）以后才认它们，在那之前"
            "流水线是红的" % n,) if n else ()


def cmd_full(a):
    repo = repo_root()
    cfg = config.load(repo)
    br, b, rp, work = cli._current(repo, cfg)
    rel = runrec.path(cfg["runs_dir"], b["key"]).replace(os.sep, "/")
    rec = _committed(repo, rel, br, cfg)
    flow = flows.of(rec)
    if cli._me(repo, cfg) != rec["owner"]:
        raise BrainError("这是 %s 的需求：只有负责人能转完整流程" % rec["owner"])
    push = cli._has_origin(repo) and not a.no_push
    if flow.intent:
        _select(repo, rec["intent"])
        pushed = push and _origin_still_fast(repo, br, rel)
        if pushed:
            git(repo, "push", "-q", "-u", "origin", br)
        return _say(a, ActOut(True, "full", "%s（Lean intent %s）" % (flows.action_problem(flow, Action.FULL),
                                                                   rec["intent"]), pushed, None, (), None,
                              rec["intent"], _patrol_warning(repo, cfg, rec) if pushed else ()))
    why = flows.action_problem(flow, Action.FULL)
    if why:
        raise BrainError(why)
    full = {k: v for k, v in rec.items() if k != "mode"}
    if work != rec and dict(work, intent=None) != full:
        raise BrainError("%s 有没提交的改动：先提交或者撤掉，再跑 brain full" % rel)
    slug = "%s-%s" % (b["key"].lower(), b["slug"]) if b["slug"] else b["key"].lower()
    doc = claims.read(repo, b["key"])
    title = doc["title"] if isinstance(doc, dict) and isinstance(doc.get("title"), str) and doc["title"].strip() else \
        b["slug"].replace("-", " ")
    intent = _left_folder(repo, slug) or lean.new_intent(repo, slug, "%s %s" % (b["key"], title))
    _select(repo, intent)
    full["intent"] = intent
    probs = runrec.validate(full, branch=br, prefix=cfg["branch_prefix"])
    if probs:
        raise BrainError("run.json would not be valid: %s" % "; ".join(probs))
    write_json(rp, full)
    paths = [rel, "%s/%s" % (lean.DOCS, intent)] + ([LEDGER] if os.path.isdir(os.path.join(repo, LEDGER)) else [])
    git(repo, "add", "--", *paths)
    git(repo, "commit", "-q", "-m", "%s: switch to the full flow (Lean intent %s)" % (b["key"], intent), "--", *paths)
    if push:
        git(repo, "push", "-q", "-u", "origin", br)
    return _say(a, ActOut(True, "full", "转成完整流程：Lean intent %s（同一个分支，写好的代码留着；接下来从需求开始走 Lean 的阶段"
                                        "和审批）" % intent, push, None, (), None, intent,
                          _patrol_warning(repo, cfg, rec)))


def register(sub):
    s = sub.add_parser("mr", help="a fast run is done: push it and open its MR (no hand-over label; a person merges)")
    s.add_argument("--no-push", action="store_true", help="push and open nothing: print the commands instead")
    s.add_argument("--json", action="store_true", help="print one JSON object")
    s.set_defaults(fn=cmd_mr)
    s = sub.add_parser("full", help="switch a fast run to the full flow on the same branch (a Lean intent from now on)")
    s.add_argument("--no-push", action="store_true")
    s.add_argument("--json", action="store_true", help="print one JSON object")
    s.set_defaults(fn=cmd_full)
