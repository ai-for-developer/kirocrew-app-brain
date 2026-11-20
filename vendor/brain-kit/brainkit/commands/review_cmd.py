"""`brain review`: the review sheet (审批单) of the approval request that is open (brainkit/review).

  brain review [KIND] [--json] [--no-prereview]
      After `lean request INCEPTION|REQUIREMENTS|DESIGN`. First time for that request: two other models review a copy
      of exactly the requested version (kit.json `model` and `review_model`, a few minutes), then the sheet is
      written to runs/<KEY>/review/<KIND>-<sha12>/ and printed. Again: prints the same sheet (write-once).
      Exit 1 when the request is stale or there is none (the message says which `lean request` to run).
  brain review log [--json] [--ref REF] [--all]
      Every round of this run's gates: version, outcome, what you typed against it, item states once approved.
      --all also lists turns that did not count (no matching Lean HUMAN_TURN) and lines that were not verdicts.

No command records a verdict: only the prompt hook does, from the person's own chat line (an agent can run commands).
"""
from __future__ import annotations

import json
import sys

from .. import config, review
from ..common import BrainError, repo_root

OUTCOME = {"approved": "批了", "rejected": "整份退回了", "superseded": "被新的一版取代", "open": "等你回",
           "stale": "过期了（请求之后页面改过）"}
STATE = {"accepted": "逐条看过", "superseded": "被取代", "passed": "随文档批", "rejected": "驳回还在", "open": "没批"}


def _when(ts) -> str:
    s = str(ts or "")
    return "%s %s UTC" % (s[:10], s[11:16]) if len(s) >= 16 else (s or "?")


def _line(v) -> str:
    about = "（%s）" % "、".join(v.resolves_to) if v.target.startswith("F-") and v.resolves_to else ""
    return "%s %s%s" % (v.target, v.words, about)


def _log(repo, a) -> int:
    rounds = review.log(repo, ref=a.ref)
    intent = rounds[0].gate.intent if rounds else None
    kinds = list(dict.fromkeys(r.gate.kind.value for r in rounds))
    approved = [k for k in kinds if any(r.gate.kind.value == k and r.outcome.value == "approved" for r in rounds)]
    states = {k: review.states(repo, intent, k, ref=a.ref) for k in approved}
    rejected = review.rejections(repo, intent, ref=a.ref) if intent else []
    extra = review.turns(repo, intent, ref=a.ref) if a.all and intent else []
    if a.json:
        print(json.dumps({
            "schema": review.SCHEMA, "intent": intent,
            "rounds": [{"kind": r.gate.kind.value, "n": r.n, "sha256": r.sha256, "outcome": r.outcome.value,
                        "requested_at": r.requested_at, "seen_at": r.seen_at, "lean_words": r.lean_words,
                        "verdicts": [{"target": v.target, "effect": v.effect.value, "words": v.words, "at": v.at,
                                      "ref": v.ref, "resolves_to": list(v.resolves_to)} for v in r.verdicts]}
                       for r in rounds],
            "states": {k: {i: s.value for i, s in v.items()} for k, v in states.items()},
            "rejections": [{"kind": x.gate.kind.value, "item": x.item, "round_sha8": x.round_sha8, "words": x.words,
                            "via_finding": x.via_finding, "ref": x.ref} for x in rejected],
            **({"turns": extra} if a.all else {})}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not rounds:
        print("还没有审批请求：先 `lean request INCEPTION`（或 REQUIREMENTS、DESIGN）。")
        return 0
    print("审批记录 · %s" % intent)
    for r in rounds:
        print("%s %s · %s · %s · 请求于 %s%s" % (
            r.gate.kind.value, r.sha256[:8], "第 %d 轮" % r.n if r.n else "没人看过（不算一轮）",
            OUTCOME[r.outcome.value], _when(r.requested_at), "，你第一次回在 %s" % _when(r.seen_at) if r.seen_at
            else ""))
        if r.lean_words:
            print("  Lean 记下的原话：%s" % r.lean_words)
        if r.verdicts:
            print("  你写的：%s" % " · ".join(_line(v) for v in r.verdicts))
    for k, st in states.items():
        counts = {}
        for i, s in st.items():
            counts.setdefault(s.value, []).append(i)
        print("%s 批了的这一版：%s" % (k, "，".join(
            "%s %d 条%s" % (STATE[s], len(ids), "（%s）" % "、".join(ids) if s in ("superseded", "rejected") else "")
            for s, ids in counts.items())))
    if rejected:
        print("退回过、要写 correction 的：%s" % "、".join("%s（%s %s）" % (x.item, x.gate.kind.value, x.round_sha8)
                                                    for x in rejected))
    for t in extra:
        if "problem" in t:
            print("  记录有问题：%s" % t["problem"])
        elif not t["counted"]:
            print("  不算数的一轮回复（Lean 没记这条是人发的）：%s %s" % (_when(t["at"]), t["ref"]))
        else:
            other = [x for x in t["lines"] if x["effect"] not in ("change", "keep")]
            for x in other:
                print("  没算成意见的一行（%s）：%s %s" % ("问题" if x["effect"] == "question" else "没看懂",
                                                   "、".join(x["ids"]), x["words"]))
            for h in t["hints"]:
                print("  没记下的一行：%s" % h)
    return 0


def cmd_review(a) -> int:
    repo = repo_root()
    if a.what == "log":
        return _log(repo, a)
    if a.ref or a.all:
        raise BrainError("--ref 和 --all 只用在 `brain review log`")
    cfg = config.load(repo)
    try:
        s = review.sheet(repo, cfg, a.what, prereview=not a.no_prereview,
                         progress=lambda line: print(line, file=sys.stderr, flush=True))
    except review.NoRequest as e:
        print("brain: %s" % e, file=sys.stderr)
        return 1
    from ..cli import record_checks     # cli imports this module
    for level, what in record_checks(repo, cfg):
        if level != "OK":
            print("brain review：人回的审批单行可能记不下：%s" % what, file=sys.stderr)
    if a.json:
        print(json.dumps(review.view(repo, s.gate.intent, s.gate.kind.value), ensure_ascii=False, indent=2,
                         sort_keys=True))
    else:
        print(review.render(s), end="")
    return 0


def register(sub):
    s = sub.add_parser("review", help="the review sheet (审批单) of the open INCEPTION / REQUIREMENTS / DESIGN request")
    s.add_argument("what", nargs="?", help="INCEPTION | REQUIREMENTS | DESIGN (default: the open one), or `log`")
    s.add_argument("--json", action="store_true", help="the data instead of the Markdown")
    s.add_argument("--no-prereview", action="store_true", help="build the sheet without the two reviewers")
    s.add_argument("--ref", help="log: read from this ref (after the run merged)")
    s.add_argument("--all", action="store_true", help="log: also the turns and lines that did not count")
    s.set_defaults(fn=cmd_review)
