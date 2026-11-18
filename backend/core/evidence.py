"""证据 of a requirement, for the 验收 card and 需求现状: brain-kit's ``brainkit.proof.bind()`` at the commit under
review, as its JSON (brain-kit docs/proof.md 「给 app 的 JSON」), and the row the card shows of it.

The commit under review is the run branch's tip as this machine has it, a merged run's too while its branch is here
(the default branch moves on with other requirements' code, and every merged run's evidence would read as old), else
the default branch's; on the team runner's page, its workspace's HEAD. bind() reads that commit's blobs and runs git;
nothing of the project runs. The records it seals against are the requirement's Lean records as the board reads them:
in a worktree they hold the newest verify before it is committed, so evidence it left uncommitted is said to be.
A split child (brain split) reads its own clone's records and its own unit's plan, as brain-kit's mrdesc does.

bind() is not given the requirement's ACs: they are parsed by brain-kit's mrdesc alone, so this row never says which
AC has no evidence (ac_without, ui_without_shot); the 变更说明 on the same card does.

Words: the gap sentences are brain-kit's (proof.TEXT, filled in by bind()), shown as they come; the state line is made
of the words brain-kit's MR description says what verify left with (截图 3 张 · 测试输出 1 份 · 是这份代码的); a chip's
mark is the one the MR's 证据 column gives its row (没看到, 旧代码, 核对不了, 还没拍, 没拍成, 不算). The page keeps tones.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import bk, mrdesc_view, stages
from .repo import Git

NO_MODULE = "这个 brain-kit 还不会读证据（它太旧）：在设置里换新一点的 brain-kit"
NOT_READ = "证据这次读不出来：brain-kit 读不出它的记录。在设置里看 brain-kit 能不能用"
NO_RUN = "还没开工：开工以后，verify 留下的截图和测试输出在这里"
# a row's state (bind's: current, or a ROW_KINDS value) -> the MR's mark for it (brainkit.mrdesc._evidence_cell)
MARK = {"current": "", "missed": "没看到", "old": "旧代码", "unknown": "核对不了", "planned": "还没拍", "not_taken": "没拍成",
        "void": "不算"}
FRESH = {"current": "current", "stale": "old", "unknown": "unknown"}     # a log's freshness, as a row's state
CLONE_RE = re.compile(r"^[0-9a-f]{6}$")


def empty(error: str) -> Dict[str, Any]:
    return {"available": bk.has("proof"), "state": None, "line": "", "late": "", "at": None, "chips": [], "gaps": [],
            "error": error}


def scope(f: Mapping[str, Any]) -> Tuple[List[Mapping[str, Any]], Optional[str], Optional[List[str]]]:
    """(events, clone, units) bind() reads for a requirement: a split child's own clone's records and its unit (run.json
    parent, unit and lean_clone, brainkit.mrdesc's rule), everyone else's every record."""
    run = f.get("run") or {}
    events = list(f.get("events") or [])
    unit = run.get("unit") if isinstance(run.get("parent"), str) and isinstance(run.get("unit"), str) else None
    if not unit:
        return events, None, None
    clone = run.get("lean_clone") if CLONE_RE.match(str(run.get("lean_clone") or "")) else None
    if clone:
        events = [e for e in events if str(e.get("_file") or "").endswith("-%s.jsonl" % clone)]
    return events, clone, [unit]


def read(path: str, sha: str, intent: str, events: Sequence[Mapping[str, Any]], clone: Optional[str] = None,
         units: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """bind()'s JSON for *intent* at commit *sha* of the repo at *path* (blocking: call it in a thread). A file is read
    as the drawer reads it: a regular blob of at most 4 MB (mrdesc_view.committed_file), a symlink never."""
    git = Git(path)

    def raw(rel: str) -> Optional[bytes]:
        return mrdesc_view.committed_file(git, sha, rel)

    try:
        lcfg = json.loads((raw("aidlc-docs/lean.json") or b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        lcfg = {}
    ev = bk.m("proof").bind(path, lambda prefix: git.ls(sha, prefix), raw, "aidlc-docs/%s" % intent, events,
                            lcfg if isinstance(lcfg, dict) else {}, sha, clone=clone, units=list(units) if units else None)
    return ev.to_json()


def line(ev: Mapping[str, Any]) -> str:
    """What verify left and whether it is of the code under review: 「截图 3 张（1 张没看到） · 测试输出 1 份 · 是这份代码的」;
    不留证据 when the project's verify keeps none, 还没有 before it left any (the gaps say why)."""
    if ev["state"] == "off":
        return "不留证据"
    shown = [r for r in ev["rows"] if r["shown"]]
    judged = [r["shown"] for r in shown] + list(ev["logs"])
    if not judged:
        return "还没有"
    parts = []
    if shown:
        missed = sum(1 for r in shown if r["shown"]["result"] == "failed")
        parts.append("截图 %d 张%s" % (len(shown), "（%d 张没看到）" % missed if missed else ""))
    if ev["logs"]:
        parts.append("测试输出 %d 份" % len(ev["logs"]))
    old = sum(1 for x in judged if x["freshness"] == "stale")
    unknown = sum(1 for x in judged if x["freshness"] == "unknown")
    if old:
        parts.append("是旧代码的" if old == len(judged) else "其中 %d 份是旧代码的" % old)
    elif unknown:
        parts.append("核对不了是不是这份代码的" if unknown == len(judged) else "其中 %d 份核对不了" % unknown)
    else:
        parts.append("是这份代码的")
    return " · ".join(parts)


def row(ev: Mapping[str, Any], nodes: Sequence[stages.Node], sha: str) -> Dict[str, Any]:
    """The card's 证据 row of bind()'s JSON *ev*: the state and its line, a chip per plan row and per test output (the
    build-and-test round whose VERIFY sealed what it shows, and its file: where the 产出 drawer opens; none when
    nothing of it counts), brain-kit's gap sentences, and *late*: the newest verify took no screenshot, so the ones
    shown are an earlier verify's."""
    bt = next((n for n in nodes if n.stage.id == "build-and-test"), None)
    sealed_in = {c.output_sha256: r.n for r in (bt.rounds if bt else ()) for c in r.end.checks if c.output_sha256}
    set_of = {s["dir"]: s["output_sha256"] for s in ev["sets"]}

    def sealed_round(path: Optional[str]) -> Optional[int]:
        return sealed_in.get(set_of.get(path.rsplit("/", 1)[0])) if path else None

    chips, late = [], ""
    for r in ev["rows"]:
        path = r["shown"]["path"] if r["shown"] else None
        chips.append({"kind": "shot", "label": r["label"], "ac": r["ac"], "what": r["what"], "state": r["state"],
                      "mark": MARK.get(r["state"], ""), "round": sealed_round(path), "path": path})
        if not late and r["shown"] and r["last"] and r["last"]["result"] == "skipped":
            n = sealed_round(path)
            late = "最近一次 verify 没拍截图（%s）：显示的是%s拍的" % (r["last"]["note"] or "没拍",
                                                         "第 %d 轮" % n if n else "之前那次 verify ")
    for lg in ev["logs"]:
        state = FRESH.get(lg["freshness"], "unknown")
        chips.append({"kind": "log", "label": "测试输出 %s" % lg["set"], "ac": [], "what": lg["label"], "state": state,
                      "mark": MARK[state], "round": sealed_round(lg["path"]), "path": lg["path"]})
    return {"available": True, "state": ev["state"], "line": line(ev), "late": late, "at": sha[:12], "chips": chips,
            "gaps": [{"kind": g["kind"], "text": g["text"]} for g in ev["gaps"]], "error": ""}
