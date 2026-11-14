"""交接给运行机: brain-kit's hand-over of one requirement, ``brain handoff`` in its worktree on this machine (brain-kit
223ced5 cli.cmd_handoff), and what it said, in plain Chinese with what to do next (E2E-10: no terminal for it).

``brain handoff`` checks the hand-over note, the uncommitted work, the kit files the team runner needs and Lean's code
fingerprint; publishes the requirement's materials; commits the hand-over (run.json, MR.md); pushes the run branch;
opens or updates its MR and labels it once GitLab has recorded the push. How far it got is the answer's ``state``:

  handed        the MR has the hand-over label: the bridge checks it, then the team runner goes on (exit 0)
  committed     the hand-over commit is made, the branch is not pushed: no remote (exit 0), or the push failed (exit 2)
  not_labelled  pushed, but the MR has no hand-over label: nothing was handed over (exit 1, or GitLab failed after the
                push: exit 2)
  refused       brain-kit refused before anything changed (``brain: <message>`` on stderr, exit 2)
  timeout       stopped at the time limit (family.run_brain: exit 124)
  failed        brain-kit could not run, or ended with words this module does not know

A HandoffResult (ui/src/types.ts) is ``{ok, state, text, todo, mr, warnings, raw, code}``: ok only when handed; text one
short line, what happened and why; todo 做什么 ('' when nothing); mr ``{iid, url}`` when brain-kit named it; warnings its
``WARN:`` lines as CheckRows (problems.py); raw brain-kit's own output, credentials hidden; code its exit status.

The words are tables: OUTCOMES (the line a run ends with, by exit code), REFUSALS (why it was not handed over: a
refusal's message, or the reason inside a pushed-but-not-labelled line) and WARNINGS; problems.row says what none of
them knows. why_not is what the app refuses itself, before anything runs. Pure but ``run``.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Optional, Union

from . import family, mrinfo, problems

TIMEOUT_S = 300
STATES = ("handed", "committed", "not_labelled", "refused", "timeout", "failed")
# a state's words, ahead of its reason: 「没交出去：交接记录还没写好」
HEAD = {"handed": "交出去了", "committed": "交接记录提交了，但没推上去", "not_labelled": "推上去了，但 {mr}上没加交接标签",
        "refused": "没交出去", "timeout": "交接没做完", "failed": "没交接成"}
BUTTON = "交接给运行机"      # ui/src/views/Handover.tsx HANDOVER: every 做什么 points at the button by this name
AGAIN = "再点一次「%s」" % BUTTON
DONE = "等运行机接着做：它有问题会发在 MR 上，到时在 MR 上回答"
LOOK = "看细节里 brain-kit 的原话；看不懂就问维护 brain-kit 的人"
NOTE_TODO = "在会话里让 AI 写好交接记录（做到哪了、运行机接着做什么）并提交，" + AGAIN
# the kit files a run branch carries come from the default branch: fixed there, then merged into the branch
KIT_TODO = ("先在默认分支上修好 kit 文件（「设置」里有「%s」），再在会话里让 AI 把默认分支合进这个需求的分支，然后"
            % problems.FIX_BUTTON["kit"]) + AGAIN
NAME_TODO = "在「设置」的「仓库」里填上你在 GitLab 上的用户名，" + AGAIN
TOKEN_TODO = "照安装说明第 3 节放好你的 GitLab 令牌（只有你能读），" + AGAIN
RISK_TODO = "不用现在做：运行机会先在 MR 上请人批设计（DESIGN），批了才接着写代码；你在本机会话里批的不算"
INTAKE_TODO = "运行机拿不到材料，桥会拒收这次交接：%s，再交接一次"
# a hand-over the person takes again: pushed without its label, or refused by the bridge (deliveries.classify)
AGAIN_DO = ("label_handoff", "redo_handoff")

Say = Union[str, Callable[[dict], str]]


def _rules(rows: tuple) -> list:
    return [(re.compile(rx, re.S), reason, todo) for rx, reason, todo in rows]


def _note(g: dict) -> str:
    """lean.handover_text_problems' reasons, joined by '; '."""
    words = (("still say", "Status 还是开工时的样子"), ("no '### Next", "没写下一步"),
             ("is still Lean's template", "下一步还是 Lean 的模板"), ("is missing", "没有交接记录文件"))
    said = [w for k, w in words if k in g["probs"]]
    return "交接记录还没写好" + ("（%s）" % "、".join(said) if said else "")


def _runner(g: dict) -> str:
    """cli.runner_kit_problems' first reason, said by problems.row."""
    said = problems.row("FAIL", g["probs"])["text"]
    return "运行机跑不了这个分支" if said == problems.GENERIC_TEXT["FAIL"] else "%s，运行机跑不了这个分支" % said


def _files(g: dict) -> str:
    return "还有没提交的改动（%d 个文件）" % len([x for x in g["files"].splitlines() if x.strip()])


PUSH = r"^command failed \(\d+\): git .*? push -q -u origin "      # the run branch's push (cli.cmd_handoff)
REFUSALS = _rules((
    # cli.cmd_handoff
    (r"^this run belongs to (?P<o>\S+); only the owner hands it off", "这是 {o} 的需求：只有 {o} 能交接", "请 {o} 在自己的机器上交接"),
    (r"^hand-over note not ready \([^)]*\): (?P<probs>.+)", _note, NOTE_TODO),
    (r"^commit your work first:(?P<files>.*)", _files, "在会话里让 AI 提交（或者你自己提交），" + AGAIN),
    (r"^the team runner could not run this branch: (?P<probs>.+)", _runner, KIT_TODO),
    (r"^not handed over: Lean would refuse this run's RELEASE approval later", "Lean 把%s也算成了代码" % problems.RECORDS,
     KIT_TODO),
    (r"^current branch (?P<b>\S+) is not a run branch", "工作区不在这个需求的分支上（现在是 {b}）",
     "在工作区里切回这个需求的分支，" + AGAIN),
    (r"^\S+ is missing on this branch", "这个分支上没有开工记录", "开工记录被删了的话，先在工作区里恢复它，" + AGAIN),
    (r"^run\.json: ", "开工记录不对", LOOK),
    (r"^\S+ is split into \d+ children and (?P<m>\d+) of them", "还有 {m} 个小需求没合进来", "等小需求都合进大需求的分支，" + AGAIN),
    # cli.publish_materials
    (r"^package (?P<p>\S+) in \S+ does not match run\.json", "需求材料包 {p} 和开工时记下的不一样",
     "到开工的那台机器上交接：材料包在那里的 brain-intake 里"),
    (r"^package (?P<p>\S+) in \S+ is damaged", "需求材料包 {p} 坏了", LOOK),
    (r"^intake store \S+: the materials do not rebase", "需求材料发不上去：本机的 brain-intake 和远端对不上",
     "在本机的 brain-intake 里理顺它（细节里有原话），" + AGAIN),
    (r"^command failed \(\d+\): git .*? push -q origin HEAD", "需求材料推不上去（brain-intake）", "检查网络和 SSH 登录，" + AGAIN),
    (r"^git .*? (?:pull|push) .*?did not finish within", "需求材料发不上去：连 brain-intake 的远端超时了", "检查网络，" + AGAIN),
    # cli._me: who the person is (GitLab's answer for the token, or the repo's git settings)
    (r"^who are you\?", "brain-kit 不知道你是谁", NAME_TODO),
    (r"^brain\.user '?(?P<u>.*?)'? is not a GitLab username", "这个仓库记下的名字（{u}）不能用",
     "在「设置」的「仓库」里改成你在 GitLab 上的用户名，" + AGAIN),
    (r"^\S+ is readable by others; run: chmod 600", "GitLab 令牌文件别人也能读：brain-kit 不用它",
     "把它改成只有你能读（安装说明第 3 节），" + AGAIN),
    (r"^GET /user -> HTTP (?P<st>\d+)", "GitLab 不认你的令牌（HTTP {st}）", "在 GitLab 上建一个新的个人令牌（api 权限）换上，" + AGAIN),
    (r"^GET /user returned non-JSON", "GitLab 的回答读不懂（多半是登录页）", "检查网络和 GitLab 的登录，" + AGAIN),
    # GitLab after the push
    (r"^[A-Z]+ /projects/\S+ -> HTTP (?P<st>401|403)", "GitLab 不让你改这个 MR（HTTP {st}）",
     "看看你的令牌在这个项目里是不是 Developer 或以上，" + AGAIN),
    (r"^[A-Z]+ /projects/\S+ -> HTTP (?P<st>\d+)", "GitLab 出错了（HTTP {st}）", "等一会儿，" + AGAIN),
    (r"^cannot reach (?P<host>[^\s:]+)", "连不上 {host}", "检查网络，" + AGAIN),
    # the run branch's push
    (PUSH + r".*?(?:\[rejected\]|non-fast-forward|fetch first)", "远端的分支上有别人的新提交", "先把远端的分支拉下来看过，" + AGAIN),
    (PUSH + r".*?(?i:permission denied|authentication failed|could not read username|host key verification failed)",
     "远端不认你的登录", "刷新你的 SSH 凭据（ssh-add 加上密钥，或者重新登录你们的 SSH 证书），" + AGAIN),
    (PUSH + r".*?(?i:could not resolve host|unable to access|connection refused|timed out|network is unreachable)",
     "连不上远端", "检查网络，" + AGAIN),
    (PUSH, "推送失败", "看细节里 git 的原话，弄好以后" + AGAIN),
    # why a pushed hand-over is not labelled (inside an exit-1 line: cli._label_check, the setup this machine lacks)
    (r"^the MR head is now \S+, not \S+ which this hand-over pushed", "你推送以后又有人推了这个分支",
     "把分支拉下来，看过别人的提交，" + AGAIN),
    (r"^GitLab has not recorded the push of \S+ on the MR within", "GitLab 还没记下这次推送", "等一会儿，" + AGAIN + "（点两次没关系）"),
    (r"^.*?gitlab\.(?:base|project) in ", "这个仓库的 brain-kit 设置里没写 GitLab",
     "请人在 .brain/kit.json 里写上 GitLab 的地址和项目、合进默认分支，再交接；或者自己去 MR 上加交接标签"),
    (r"^.*?a GitLab token on this machine", "这台机器上没有 GitLab 令牌", TOKEN_TODO),
))

# the line a run ends with on stdout, by exit code: (pattern, state, reason, todo); a reason of None is the group
# "why" said through REFUSALS
OUTCOMES = {code: [(re.compile(rx, re.M | re.S), state, reason, todo) for rx, state, reason, todo in rows]
            for code, rows in {
    0: ((r"^Handed off: MR !(?P<iid>\d+) labelled '[^']*' at ", "handed", "MR !{iid} 加上了交接标签", DONE),
        (r"^Committed the hand-over; not pushed, and material package", "committed", "需求材料也没发布",
         AGAIN + "：它会发布材料、推送，再加交接标签"),
        (r"^Committed the hand-over; not pushed\.", "committed", "这个仓库没有远端", "给仓库加上远端，" + AGAIN)),
    1: ((r"^Pushed \S+, but NOT labelled: missing (?P<why>.+?)\. Nothing was handed over", "not_labelled", None, None),
        (r"^Pushed \S+, but NOT labelled \(--no-label\)", "not_labelled", "", "准备好了就" + AGAIN),
        (r"^Pushed \S+, but NOT labelled MR !(?P<iid>\d+): (?P<why>.+?)\.\nNothing was handed over", "not_labelled",
         None, None)),
}.items()}

WARNINGS = _rules((
    (r"^this run changes risk paths \((?P<paths>[^;)]+)", "这个需求改了高风险的文件：{paths}", RISK_TODO),
    (r"^this run plans to change \(run\.json expected_paths\) risk paths \((?P<paths>[^;)]+)", "这个需求要改高风险的文件：{paths}",
     RISK_TODO),
    (r"^the hand-over note \(.*?\) does not say so", "交接记录的下一步没先写「请人批设计」",
     "只是提醒：运行机要先在 MR 上请人批设计，才能接着写代码"),
    (r"^risk paths not checked", "没查成高风险的文件", "只是提醒：细节里有原因"),
    (r"^package (?P<p>\S+): old-version package", "需求材料包 {p} 是旧版的，检查得没那么严", "只是提醒：要紧的材料用新版重新打包"),
    (r"^intake store \S+ is not a git clone of brain-intake", "需求材料没发布：本机放材料的目录不是 brain-intake 的副本",
     INTAKE_TODO % "把团队的 brain-intake 仓库克隆到这台机器上"),
    (r"^intake store has no origin", "需求材料只存在本机：brain-intake 没有远端", INTAKE_TODO % "给本机的 brain-intake 加上远端"),
    (r"^\S+ not written \(.*\): the hand-over goes on without the MR description", "变更说明没写成，MR 上没有它", "只是提醒：交接照常"),
    (r"^GitLab did not take the MR description", "GitLab 没收下变更说明：MR 开了，没有说明", "只是提醒：可以在 MR 上自己补上"),
    (r"^the description of MR !(?P<iid>\d+) was not set", "MR !{iid} 的说明没更新", "只是提醒：说明在分支上的 MR.md 里"),
))
# printed once the hand-over commit is made (cli.handoff_description), before the push
COMMITTED_RE = re.compile(r"into the hand-over commit|the hand-over goes on without the MR description")
LIMIT_RE = re.compile(r"（超过 (\d+) 秒，停掉了）")


def _say(v: Say, g: dict) -> str:
    return v(g) if callable(v) else v.format(**g)


def _first(rules: list, s: str) -> Optional[tuple[str, str]]:
    for rx, reason, todo in rules:
        m = rx.match(s)
        if m:
            g = {k: (x or "") for k, x in m.groupdict().items()}
            return _say(reason, g), _say(todo, g)
    return None


def reason(message: str) -> tuple[str, str]:
    """Why brain-kit did not hand it over (a refusal's message, or the reason in a not-labelled line): (reason, 做什么)."""
    s = message.strip()
    hit = _first(REFUSALS, s)
    if hit:
        return hit
    r = problems.row("FAIL", s)
    return r["text"], r["todo"]


def warning(line: str) -> dict[str, Any]:
    """One ``WARN:`` line as a CheckRow: WARNINGS, else problems.row."""
    s = " ".join(line.split())
    hit = _first(WARNINGS, s)
    return problems.mk("WARN", hit[0], hit[1], None, None, s) if hit else problems.row("WARN", s)


def said(code: int, stdout: str, stderr: str, *, base: str = "", project: str = "") -> dict[str, Any]:
    """What one run of ``brain handoff`` did, from its exit status and output -> HandoffResult. *base*, *project*:
    kit.json's GitLab, for the MR's link."""
    out, err = stdout or "", stderr or ""
    iid: Optional[int] = None
    end = next(((m, state, why, todo) for rx, state, why, todo in OUTCOMES.get(code, ())
                for m in [rx.search(out)] if m), None)
    refusal = re.search(r"^brain: (.*)\Z", err, re.M | re.S) if code == 2 else None
    if end:
        m, state, why, todo = end
        g = {k: (x or "") for k, x in m.groupdict().items()}
        iid = int(g["iid"]) if g.get("iid") else None
        if why is None:
            why, todo = reason(g["why"])
            if why == problems.GENERIC_TEXT["FAIL"]:
                why, todo = "", "看细节里 brain-kit 的原话：照它说的补上交接标签，或者" + AGAIN
        else:
            why, todo = _say(why, g), _say(todo, g)
    elif refusal:
        msg = refusal.group(1)
        state = "refused" if not COMMITTED_RE.search(out) else (
            "committed" if re.match(PUSH, msg, re.S) else "not_labelled")
        why, todo = reason(msg)
    elif code == 124:
        limit = LIMIT_RE.search(err)
        state, why = "timeout", "超过 %s 秒，停掉了" % limit.group(1) if limit else "等太久，停掉了"
        todo = "看一眼 MR 上有没有交接标签：没有就" + AGAIN + "（点两次没关系）"
    elif code == 127:
        state, why, todo = "failed", "跑不了 brain-kit", "到「设置」里看 brain-kit 那张卡，写对它的位置，" + AGAIN
    elif code == 0:
        state, why, todo = "failed", "brain-kit 没说交出去没有", "看一眼 MR 上有没有交接标签；细节里有 brain-kit 的原话"
    else:
        state, why, todo = "failed", "brain-kit 出错了（退出码 %d）" % code, LOOK
    head = HEAD[state].format(mr="MR !%d " % iid if iid else "MR ")
    return {"ok": state == "handed", "state": state, "text": "%s：%s" % (head, why) if why else head, "todo": todo,
            "mr": {"iid": iid, "url": mrinfo.mr_url(base, project, iid)} if iid else None,
            "warnings": [warning(x) for x in re.findall(r"^WARN: (.+)$", out, re.M)],
            "raw": family.output(subprocess.CompletedProcess(["brain", "handoff"], code, out, err)), "code": code}


def why_not(f: dict[str, Any], row: dict[str, Any], *, me: Optional[str], local_only: bool, busy: bool,
            pushed: bool) -> Optional[tuple[str, str, int]]:
    """Why the app does not run the hand-over (its words, code, HTTP status), or None when it may. *f*: the
    requirement's facts (repo.py); *row*: its row (deliveries.classify: state, waits); *me*: who the person is here;
    *busy*: its session on this machine is in a turn (which could commit meanwhile); *pushed*: the branch tip is on
    the remote (a hand-over commit whose push failed is not: then the row reads handed over, and nothing is)."""
    run = f.get("run") or {}
    if not run:
        return "还没开工：开工以后才能交接", "not_started", 409
    if f.get("merged") or row.get("state") in ("merged", "merged_into_parent", "closed"):
        return "它已经合并或关掉了：不用再交接", "merged", 409
    if run.get("parent"):
        return ("这是拆出来的小需求：做完是开 MR 合进大需求的分支，不交给运行机（这一步还要在它的工作区里跑 brain handoff）",
                "is_child", 409)
    if local_only:
        return "这个仓库只在本机做（没有 GitLab 或没有远端）：不交给运行机，做完在本机合并", "local_only", 409
    if run.get("handoff") and pushed and (row.get("waits") or {}).get("do") not in AGAIN_DO:
        return "已经交接过了（%s）：不用再交接" % ((row.get("waits") or {}).get("text") or row.get("state_label")), \
            "handed_over", 409
    if not me or me != f.get("owner"):
        return "只有需求负责人本人能交接（按你在这个仓库的名字认人，在「设置」的「仓库」里填）", "not_owner", 403
    if not f.get("worktree"):
        return "这台机器上没有它的工作区：到开工的那台机器上交接", "no_worktree", 409
    if busy:
        return "AI 正在这个需求的会话里做事：等它停下来再交接（它做着的时候可能会提交）", "busy", 409
    return None


def run(worktree: str, *, base: str = "", project: str = "", timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """``brain handoff`` in the requirement's worktree (blocking: call it in a thread) -> HandoffResult."""
    p = family.run_brain(["handoff"], worktree, timeout)
    return said(p.returncode, p.stdout, p.stderr, base=base, project=project)


__all__ = ["said", "run", "why_not", "reason", "warning", "STATES", "TIMEOUT_S", "BUTTON"]
