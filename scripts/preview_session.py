"""PAY-9's session on alice's machine (brain-PAY-9-7f2c), as KiroCrew's transcript holds it, for the preview's 产出
drawer (scripts/preview_world.py): the app's kickoff, then two messages alice typed (her answer to the agent's question,
then the INCEPTION line), each followed by the agent's calls and its reply. The rows are KiroCrew's: the app's message
carries its sendId, a typed one meta.human; a call its kind and its input. Lean recorded each message's sha256
(HUMAN_TURN), which is how the drawer finds them; what alice typed is in here as a transcript would have it and is
never shown.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

LEAN = "python3 .kiro/aidlc-lean/aidlc_lean.py"
ANSWER = "A。正式文案运营下周给，先用默认模板。设计里把失败重试写清楚。"     # what alice typed at 10:21


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "+00:00"


def pay9(at: Callable[[str], float], wt: str, intent: str, kickoff: str, inception: str) -> dict[str, Any]:
    """*at*: '10:05:31' -> that moment of PAY-9's day; *wt*: its worktree; *inception*: the line alice types. The
    session's rows, oldest first, and what the app showed of the message it sent (SessionInfo.shown, by sendId)."""
    doc = "aidlc-docs/%s/inception" % intent
    rows: list[dict[str, Any]] = []

    def user(hm: str, text: str, meta: dict[str, Any]) -> None:
        rows.append({"role": "user", "content": text, "meta": meta, "ts": _iso(at(hm))})

    def call(hm: str, kind: str, **args: Any) -> None:
        name = args.pop("tool_name", None)
        if "path" in args:
            args["path"] = "%s/%s" % (wt, args["path"])
        meta = {"kind": kind, "input": json.dumps(args, ensure_ascii=False)}
        if name:
            meta["tool_name"] = name
        rows.append({"role": "tool", "content": "🔧 Tool", "meta": meta, "ts": _iso(at(hm))})

    def reply(hm: str, text: str, credits: float) -> None:
        rows.append({"role": "assistant", "content": text, "meta": {"turn_stats": {"credits": credits}}, "ts": _iso(at(hm))})

    user("10:05:20", kickoff, {"sendId": "s-9a1f0c2b7e-0", "turnActor": "app"})
    call("10:05:31", "read", path="AGENTS.md")
    for hm, p in (("10:05:33", "brain/PRODUCT.md"), ("10:05:35", "brain/TECH.md"), ("10:05:37", "brain/patterns.md"),
                  ("10:05:39", "brain/terms.md"), ("10:05:41", "runs/PAY-9/run.json")):
        call(hm, "read", path=p)
    call("10:05:45", "execute", command="%s stage start requirements" % LEAN)
    call("10:05:52", "execute", command='%s stage skip reverse-engineering --reason "新功能，代码已经读过"' % LEAN)
    call("10:06:02", "execute", command="%s stage start application-design" % LEAN)
    call("10:06:40", "read", path="src/refund/events.py")
    call("10:07:15", "other", tool_name="local_knowledge_search", query="短信 通知 重试")
    call("10:08:30", "read", path="src/refund/service.py")
    call("10:12:10", "edit", path="%s/requirements/requirements.md" % doc, command="create")
    call("10:19:00", "execute", command='%s stage done requirements --note "需求写好了：退款成功以后给用户发短信，5 条验收标准"' % LEAN)
    call("10:19:30", "execute", command='%s stage skip user-stories --reason "只有一类用户"' % LEAN)
    reply("10:19:50", "需求初稿写好了：退款成功以后给下单手机号发一条短信，5 条验收标准。\n\n"
          "有一个问题先问你（D-1）：短信文案用默认模板，还是等运营给正式文案？\n\n"
          "- A：先用默认模板，正式文案来了再换（推荐：不挡上线）\n- B：等运营的正式文案", 1.8)

    user("10:21:00", ANSWER, {"sendId": "web-3c1d", "human": True})
    call("10:21:30", "read", path="%s/requirements/requirements.md" % doc)
    call("10:22:10", "other", tool_name="local_knowledge_search", query="统一通知服务 限流")
    call("10:24:05", "edit", path="%s/application-design/application-design.md" % doc, command="create")
    call("10:29:40", "read", path="src/refund/notify/__init__.py")
    call("10:30:15", "edit", path="aidlc-docs/%s/decisions.md" % intent, command="str_replace")
    call("10:31:12", "execute", command='%s stage done application-design --note "…"' % LEAN)
    call("10:36:02", "execute", command="%s request INCEPTION" % LEAN)
    call("10:41:05", "execute", command='git add -A && git commit -m "PAY-9：需求和设计"')
    reply("10:41:20", "需求和设计都写好了，等你批：\n\n"
          "- `requirements.md`：5 条验收标准。AC-1 退款成功后 1 分钟内发短信；AC-4 退款失败不发；AC-5 同一笔退款只发一次。\n"
          "- `application-design.md`：新加 `SmsNotifier`，接在退款成功事件上；短信走统一通知服务（照 Brain 的 P-7），"
          "不新开通道；失败重试 3 次（T-12）。\n\n"
          "短信文案按你的回答先用默认模板（D-1），记在 decisions.md。\n\n"
          "请在会话里打这一行，一起批需求和设计：\n\n`%s`" % inception, 3.1)

    user("10:52:00", inception, {"sendId": "web-77e2", "human": True})
    call("10:52:30", "execute", command="%s stage start units" % LEAN)
    call("10:52:40", "read", path="%s/application-design/application-design.md" % doc)
    call("10:52:55", "edit", path="%s/application-design/unit-of-work.md" % doc, command="create")
    call("10:53:00", "execute", command='git add -A && git commit -m "PAY-9：拆成 api 和 web"')
    call("10:54:00", "execute", command='%s stage done units --note "拆成 api 和 web 两个单元"' % LEAN)
    call("11:01:00", "execute", command='git add -A && git commit -m "PAY-9：交接说明"')
    call("11:04:30", "execute", command="brain handoff")
    reply("11:05:00", "拆好了：api 和 web 两个单元，api 先做。交接说明写好了，交给运行机了（提交 e7d2b11）。", 1.6)
    return {"rows": rows, "sent": {"s-9a1f0c2b7e-0": {"by": "app", "cause": "开工消息，只有编号", "text": kickoff}}}
