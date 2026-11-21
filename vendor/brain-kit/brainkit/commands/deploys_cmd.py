"""`brain deploys`: what each change on the default branch did after it landed (brainkit/deploys.py).

  brain deploys [--ref REF] [-n N] [--json] [--as-of TIME]
The last N commits of the default branch's first-parent line, newest first (each one landed a change: a merge, a
squash, a push), each with its deploys to the environment that "deploys" in that branch's .brain/kit.json names.
Then the deploys of other branches since the oldest of them (a pipeline someone ran by hand): no change is judged by
those. Read-only. --ref: the default branch to read (default: origin's copy, else the local one).
"""
from __future__ import annotations

import json

from .. import config
from .. import deploys as D
from .. import trace as T
from ..common import BrainError, git, iso, parse_iso, repo_root, utcnow
from ..rest import gl_time


def landed(repo, ref, n) -> list:
    """[(sha, ISO committer time, subject)] of the last n commits of ref's first-parent line, newest first."""
    out = []
    for row in git(repo, "log", "--first-parent", "-n", str(n), "--format=%H%x00%cI%x00%s", ref).stdout.splitlines():
        sha, at, subject = (row.split("\0") + ["", ""])[:3]
        t = gl_time(at)
        out.append((sha, iso(t) if t else None, subject[:72]))
    return out


def collect(repo, cfg, ref=None, n=10, as_of=None, source=None) -> dict:
    """{ref, as_of, environment, src, changes: [change + landed_at, subject], other_branches: [deploy], missing}."""
    ref = ref or T.default_ref(repo, cfg, None)
    if not ref or not T._commit(repo, ref):
        raise BrainError("no %s here: fetch it, or name it with --ref" % (ref or cfg["default_branch"]))
    src = source if source is not None else D.source_for(repo, ref)
    as_of = as_of or iso(utcnow())
    out = {"ref": ref, "as_of": as_of, "environment": src.environment if src else None, "src": D.OFF, "changes": [],
           "other_branches": [], "missing": []}
    if src is None:
        return out
    out["src"] = "GitLab deployments (%s)" % src.where
    rows = landed(repo, ref, n)
    for sha, at, subject in rows:
        c = D.change(src, sha, at, D.contains_in(repo, sha), as_of, cfg["default_branch"])
        out["changes"].append(dict(c, landed_at=at, subject=subject))
    oldest = min((at for _, at, _ in rows if at), default=as_of)
    try:
        out["other_branches"] = [src.evidence(d, as_of) for d in src.deployments(iso(parse_iso(oldest) - D.SLACK))
                                 if d["ref"] != cfg["default_branch"] and d["created_at"] <= as_of]
    except BrainError as e:
        out["missing"].append("the deploys to %s: %s" % (src.environment or "?", e))
    return out


def render(data) -> str:
    if data["src"] == D.OFF:
        return "部署没打开（%s）" % D.OFF
    out = ["%s 最近 %d 个改动，部署到 %s（按 %s 判断）：" % (T._short_ref(data["ref"]), len(data["changes"]),
                                                  data["environment"] or "?", data["as_of"])]
    unread = []
    for c in data["changes"]:
        out.append("%s %s %s" % (c["commit"][:12], c["landed_at"] or "?", c["subject"]))
        out += ["  " + x for x in D.lines(c)]
        if not c["attempts"]:            # an attempt's own line says why it is unknown
            unread += [x for x in c["missing"] if x not in unread]
    if data["other_branches"]:
        out.append("别的分支的部署（不算在哪个改动上）：")
        out += ["  " + D.line(a) for a in data["other_branches"]]
    out += ["读不到：" + x for x in unread + [m for m in data["missing"] if m not in unread]]
    return "\n".join(out)


def cmd_deploys(a):
    repo = repo_root()
    cfg = config.load(repo)
    data = collect(repo, cfg, ref=a.ref, n=a.n, as_of=a.as_of)
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) if a.json else render(data))
    return 0


def register(sub):
    s = sub.add_parser("deploys", help="what each change on the default branch did after it landed: its deploys")
    s.add_argument("--ref", help="the default branch to read (default: origin's copy, else the local one)")
    s.add_argument("-n", type=int, default=10, help="how many of its latest commits (default 10)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--as-of", help="judge at this time, ISO (default now)")
    s.set_defaults(fn=cmd_deploys)
