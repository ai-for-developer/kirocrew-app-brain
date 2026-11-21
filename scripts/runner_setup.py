#!/usr/bin/env python3
"""一键建运行机 from a terminal, for a live check outside KiroCrew: the steps of the app's 设置 → 团队模式 card
(backend/core/team/setup.py), one line per step as it ends, and under a step about the projects one line per project
when there are two or more.

  python3 scripts/runner_setup.py --root DIR --gitlab https://gitlab.example.com --project grp/pay --token-file FILE
      [--role product=alice,bob] [--role builder=carol] [--push auto|ssh|token] [--no-patrol] [--no-team-mode]
      [--jenkins https://jenkins.example.com --jenkins-user NAME [--jenkins-token-file FILE]]
      [--brainkit DIR] [--work DIR] [--agent-spec FILE]
  python3 scripts/runner_setup.py --root DIR --gitlab https://gitlab.example.com --mode bot-account
      --project grp/pay --project grp/order --bridge-token-file FILE [--bot-token-file FILE] [--token-file FILE] ...

--mode project-token (the default) serves the one --project with the project access tokens that the one-time token in
--token-file makes. --mode bot-account serves every --project as two GitLab users of the runner's own:
--bridge-token-file holds the bridge user's personal token (api; the user is a Developer in every project), and
--bot-token-file the bot user's (write_repository and read_user) when the runner pushes as that user rather than over
this machine's SSH. A token file left out takes the token an earlier run kept. There --token-file is optional: without
it the labels and the kit.json MRs are made as the bridge user, and --role must name 产品 (product) and 开发 (builder).

--token-file holds a one-time personal token with api: it is read here and copied nowhere. --push is auto unless
bot-account mode is given --bot-token-file (then token). --jenkins is Jenkins as this machine reaches it, where MR 巡检
reads a failed build's log as --jenkins-user, a read-only user; that user's token (--jenkins-token-file, left out on a
run after one that had it) is kept in --root, 0600. --work is where the runs and the clone cache go (default
~/workplace/brain-team); --agent-spec where brain-run goes (default ~/.kiro/agents/brain-run.json, brain-patrol
beside it); --brainkit the brain-kit (default: the one the app would load). --no-patrol leaves 巡检所有开着的 MR off: no
brain-patrol, and the kit.json MRs leave work source mr-patrol out. The last step starts team mode the way the app
does, here and only to check it (a TeamRuntime starts and stops again): from outside KiroCrew it cannot switch the
app's own team mode on. --no-team-mode skips that check. The signer it starts keeps running after it exits: its last
line says the signer's pid and how to stop it.

Exit 0: no step failed (one waiting for a person, a kit.json MR, is no failure); 1: a step failed; 2: the arguments,
brain-kit, or another run holding the root.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARK = {"done": "✓", "waiting": "…", "failed": "✗", "skipped": "-"}
CHECKED = "团队模式的配置能用（在这里开了一下又关了）：到 app 的设置 → 团队模式里打开它"
SIGNER_LEFT = "签名服务还在跑（进程 %d），这个命令退出以后也不停：要停就 kill %d"
TOKEN_FILES = (("admin_token", "token_file"), ("bridge_token", "bridge_token_file"), ("bot_token", "bot_token_file"),
               ("jenkins_token", "jenkins_token_file"))


def app_modules() -> tuple[Any, Any, Any, Any]:
    sys.path.insert(0, str(ROOT))
    import backend.routes  # noqa: F401  (bootstraps the private namespace the app's modules live in)
    core = "_brainapp_backend.core."
    return tuple(importlib.import_module(core + m) for m in ("bk", "team.setup", "team.bridgehost", "team.signerproc"))


def team_check(bridgehost: Any) -> Any:
    """Team mode as the app starts it, started and stopped again here: its TeamRuntime's own words."""
    def team_mode(want: dict[str, Any]) -> dict[str, Any]:
        rt = bridgehost.TeamRuntime(want, None, None, None)
        try:
            rt.start()
            st = rt.status()
        finally:
            rt.stop()
        return dict(st, said=CHECKED)
    return team_mode


def say(step: Any, r: Any) -> None:
    line(r, step.title, "")
    for x in r.projects:
        line(x, x.project, "    ")


def line(r: Any, what: str, indent: str) -> None:
    print("%s%s %s  %s%s" % (indent, MARK.get(r.status, "?"), what, r.message, "  " + r.link if r.link else ""), flush=True)
    if r.status == "failed" and r.detail:
        for d in r.detail.splitlines()[-20:]:
            print(indent + "    " + d, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="runner_setup.py", description="一键建运行机（同一个系统用户）")
    ap.add_argument("--root", required=True, help="the runner's files (the app uses ~/.brain/team-setup)")
    ap.add_argument("--gitlab", required=True, help="GitLab's address, https://gitlab.example.com")
    ap.add_argument("--mode", choices=("project-token", "bot-account"), default="project-token")
    ap.add_argument("--project", action="append", required=True, help="group/repo (bot-account: once for each)")
    ap.add_argument("--token-file", help="a file holding a one-time personal token with api (bot-account: optional)")
    ap.add_argument("--bridge-token-file", help="bot-account: the bridge user's personal token (api)")
    ap.add_argument("--bot-token-file", help="bot-account: the bot user's personal token (write_repository, read_user)")
    ap.add_argument("--role", action="append", default=[], metavar="ROLE=NAME,NAME",
                    help="product, builder, security or data (left out: the one-time token's owner)")
    ap.add_argument("--push", choices=("auto", "ssh", "token"), help="default: token with bot-account and --bot-token-file, else auto")
    ap.add_argument("--no-patrol", action="store_true", help="do not patrol every open MR (no brain-patrol)")
    ap.add_argument("--no-team-mode", action="store_true", help="skip the last step (starting team mode to check it)")
    ap.add_argument("--jenkins", help="Jenkins' address as this machine reaches it, for MR 巡检's logs")
    ap.add_argument("--jenkins-user", help="a read-only Jenkins user (Overall/Read and Job/Read)")
    ap.add_argument("--jenkins-token-file", help="a file holding that user's API token or password")
    ap.add_argument("--status-project", help="状态中转's GitLab project, group/name (default: brain-status in the first "
                                             "project's group)")
    ap.add_argument("--brainkit")
    ap.add_argument("--work")
    ap.add_argument("--agent-spec")
    a = ap.parse_args(argv)
    bk, setup, bridgehost, signerproc = app_modules()
    kit = a.brainkit or bk.default_path()
    info = bk.load(kit)
    if not info["ok"]:
        print("brain-kit (%s): %s" % (kit, info["error"]), file=sys.stderr)
        return 2
    tokens = {}
    for key, opt in TOKEN_FILES:
        if getattr(a, opt):
            try:
                tokens[key] = Path(getattr(a, opt)).expanduser().read_text(encoding="utf-8").strip()
            except (OSError, ValueError) as exc:
                print("--%s cannot be read: %s" % (opt.replace("_", "-"), getattr(exc, "strerror", None) or exc),
                      file=sys.stderr)
                return 2
    jenkins_token = tokens.pop("jenkins_token", "")
    roles = {}
    for r in a.role:
        role, eq, names = r.partition("=")
        if not eq:
            print("--role is ROLE=NAME,NAME, not %r" % r, file=sys.stderr)
            return 2
        roles[role.strip()] = names
    jenkins = {"base": a.jenkins, "user": a.jenkins_user or "", "token": jenkins_token} if a.jenkins else None
    try:
        inp = setup.SetupInput.parse(dict(tokens, gitlab_base=a.gitlab, mode=a.mode, projects=a.project, roles=roles,
                                          push=a.push or ("token" if a.mode == "bot-account" and a.bot_token_file else "auto"),
                                          patrol=not a.no_patrol, jenkins=jenkins, status_project=a.status_project or ""))
    except setup.SetupRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    paths = setup.Paths.default(kit, root=a.root, work=a.work, agent_spec=a.agent_spec)
    rest = bk.m("rest")
    ctx = setup.Ctx(inp, paths, gitlab=lambda tok: rest.GitLab(inp.gitlab_base, tok),
                    team_mode=None if a.no_team_mode else team_check(bridgehost))
    try:
        with setup.Lock(paths.root):
            got = setup.run(ctx, on_step=say)
    except setup.Busy as exc:
        print(exc, file=sys.stderr)
        return 2
    for s in got["steps"]:
        if s["status"] == "todo":
            print("  %s  （没做到）" % s["title"])
    pid = signerproc.running(paths.signer_json)
    if pid:
        print(SIGNER_LEFT % (pid, pid))
    return 1 if any(s["status"] == "failed" for s in got["steps"]) else 0


if __name__ == "__main__":
    sys.exit(main())
