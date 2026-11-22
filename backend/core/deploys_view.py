"""What happened after a merge, from brain-kit's deploys (brainkit/deploys.py: a trace's outcome.deploy and the eval's
``deploys``), as the pages say it: 需求现状's 上线 (``requirement``) and 交付总览's 部署 numbers (``metrics``, ``notes``).

brain-kit reads them from GitLab only when the default branch's .brain/kit.json has "deploys"; without that, or with a
brain-kit that has no deploys module, there is nothing to show. A GitLab behind single sign-on signs each new client in
on its first call (17 s measured), then each call takes a second or two: ``Clients`` keeps one client per GitLab and
token for the app's life (made again once a call found that its sign-in ended: signin.py), and makes a new Source for
each trace and each eval (a Source keeps what it read, so it is never shared between two of them, nor between
threads).

Promoted and aborted are what the deploy evidence says (brain-kit's rule D2); any other value is unknown, never a
success. brain-kit's reasons are said in words (WHY), its own words go under 细节.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any

from . import bk, identity, lean_view, signin

VALUES = ("promoted", "aborted", "running", "not_deployed", "unknown")
RESULTS = ("promoted", "aborted", "running", "unknown")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_ATTEMPTS = 20
# brain-kit deploys.py's reasons (an attempt's why, a record's missing), in words; another one is UNREAD
WHY = ((re.compile(r"no GitLab token here"), "这台机器上没有 GitLab 的令牌"),
       (re.compile(r"gitlab\.base and gitlab\.project are not set"), "kit.json 里没写 GitLab 的地址和项目"),
       (re.compile(r"deploys(\.\w+)? in \S+ must|is not valid JSON"), "kit.json 里 deploys 的写法不对"),
       (re.compile(r"is not in this clone"), "那次部署的提交这里还没有：先从远端取最新"),
       (re.compile(r"\(never written, or expired\)"), "部署证据不在了：没写，或者过期了"),
       (re.compile(r"while it ran: the rollout may have gone on"), "部署作业中途停了，灰度可能还在接着走"),
       (re.compile(r"gives no result|names no image digest|is not a JSON object|is not JSON"), "部署证据不全，看不出结果"),
       (re.compile(r"the deploy evidence"), "部署证据和这次部署对不上"),
       (signin.NO_HELPER_RE, "GitLab 在单点登录后面，这台机器上没有登录助手"),
       (re.compile(r"HTTP 40[13]\b|sign-in page|sign in again"), "GitLab 不让读：令牌或登录过期了"))
UNREAD = "GitLab 上的部署记录读不了"
RULE = {"deploy_frequency": "最近 28 天，默认分支部署到 %s、灰度走完上线的次数，按周算（中止的、读不到的不算）",
        "lead_time": "从合并到第一次上线了这个需求的部署（%s），取中位",
        "change_failure": "部署过的需求里，上线前灰度中止过、或合并后 14 天内有回滚、热修、缺陷的 ÷ 14 天已满、结果读得到的部署过的需求"
                          "（%s）"}


def said(why: Any) -> str:
    """One of brain-kit's reasons, in words."""
    text = str(why or "")
    return next((words for rx, words in WHY if rx.search(text)), UNREAD)


def _digest(x: Any) -> str | None:
    return x if isinstance(x, str) and DIGEST_RE.match(x) else None


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _text(x: Any, limit: int) -> str | None:
    """Text with each run of whitespace made one space, cut to *limit*; None when it is not text, or empty."""
    if not isinstance(x, str):
        return None
    return " ".join(x.split())[:limit] or None


def _int(x: Any) -> int | None:
    return x if type(x) is int else None


def pipeline_url(base: str, project: str, pipeline: int | None, web_url: Any) -> str | None:
    """A deploy's pipeline page on the repo's GitLab, as mrinfo.parse_pipelines makes one; for a project named by its
    number, GitLab's own page of the deploy job, when it is on *base*. Never a page of another site."""
    home = (base or "").rstrip("/")
    if not home:
        return None
    if pipeline and "/" in str(project or ""):
        return "%s/%s/-/pipelines/%d" % (home, project, pipeline)
    return web_url if isinstance(web_url, str) and web_url.startswith(home + "/") else None


def attempt(a: dict[str, Any], base: str, project: str) -> dict[str, Any]:
    """One deploy that shipped the requirement (brain-kit's deploy record) in the page's shape."""
    result = a.get("result") if a.get("result") in RESULTS else "unknown"
    sha = a.get("sha") if isinstance(a.get("sha"), str) and SHA_RE.match(a["sha"]) else ""
    failed = a.get("failed_checks") if isinstance(a.get("failed_checks"), list) else []
    pipeline = _int(a.get("pipeline"))
    return {"result": result, "deployment": _int(a.get("deployment")),
            "at": lean_view.epoch(a.get("finished_at")) or lean_view.epoch(a.get("created_at")),
            "pipeline": pipeline, "url": pipeline_url(base, project, pipeline, a.get("web_url")),
            "sha": sha[:12], "digest": _digest(a.get("digest")),
            "kept": _digest(a.get("stable_digest_after")) if result == "aborted" else None,
            "failed": [{"check": _text(c.get("check"), 80) or "?", "error": _text(c.get("error"), 200)}
                       for c in failed[:20] if isinstance(c, dict)],
            "why": said(a.get("why")) if result == "unknown" else "",
            "raw": [t for t in (_text(a.get("why"), 300), _text(a.get("reason"), 300)) if t]}


def requirement(t: Any, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """需求现状's 上线: the outcome.deploy of brainkit.trace's record *t* in the page's shape, the pipelines' pages on the
    GitLab of *cfg* (the repo's kit.json). None when there is nothing to show: deploys are off, the requirement is not
    merged, or the trace has no deploys (an older brain-kit, or no trace)."""
    outcome = t.get("outcome") if isinstance(t, dict) else None
    dep = outcome.get("deploy") if isinstance(outcome, dict) else None
    if not isinstance(dep, dict) or dep.get("value") is None:
        return None
    g = cfg.get("gitlab") if isinstance(cfg.get("gitlab"), dict) else {}
    base, project = str(g.get("base") or ""), str(g.get("project") or "")
    value = dep["value"] if dep["value"] in VALUES else "unknown"
    attempts = [attempt(a, base, project) for a in (dep.get("attempts") or [])[:MAX_ATTEMPTS] if isinstance(a, dict)]
    raw = [x for x in (_text(m, 300) for m in dep.get("missing") or []) if x][:10]
    done = value == "promoted"
    hours = _num(dep.get("lead_time_hours"))
    why = ""
    if value == "unknown":
        why = next((a["why"] for a in attempts if a["result"] == "unknown"), "") or (said(raw[0]) if raw else UNREAD)
    return {"value": value, "environment": _text(dep.get("environment"), 80) or "",
            "digest": _digest(dep.get("digest")) if done else None,
            "at": lean_view.epoch(dep.get("at")) if done else None,
            "lead_s": round(hours * 3600) if done and hours is not None else None,
            "why": why, "raw": raw, "attempts": attempts}


def _pair(x: Any) -> list[int] | None:
    if isinstance(x, (list, tuple)) and len(x) == 2 and all(type(v) is int for v in x):
        return [x[0], x[1]]
    return None


def metrics(dp: Any) -> list[dict[str, Any]]:
    """交付总览's three 部署 numbers (DORA) from brain-kit eval's ``deploys``, as its metric tiles (EvalMetric): none
    when deploys are off."""
    if not isinstance(dp, dict) or not dp.get("environment"):
        return []
    env = _text(dp["environment"], 80) or "?"
    f, lt, cf = (dp.get(k) if isinstance(dp.get(k), dict) else {}
                 for k in ("frequency", "lead_time_hours", "change_failure"))
    return [{"id": "deploy_frequency", "label": "部署频率", "value": _num(f.get("per_week")),
             "rule": RULE["deploy_frequency"] % env},
            {"id": "lead_time", "label": "合并到上线", "value": _num(lt.get("median")), "rule": RULE["lead_time"] % env},
            {"id": "change_failure", "label": "变更失败率", "value": _pair(cf.get("value")),
             "rule": RULE["change_failure"] % env}]


def lead_hours(dp: Any) -> list[float]:
    """The hours from merge to the first promoted deploy, one per requirement: what several repos' 合并到上线 is made
    of (a median does not add up)."""
    lt = dp.get("lead_time_hours") if isinstance(dp, dict) else None
    by_run = lt.get("by_run") if isinstance(lt, dict) else None
    if not isinstance(by_run, dict):
        return []
    return sorted(h for h in (_num(v) for v in by_run.values()) if h is not None)


def notes(dp: Any, unknown_after_merge: Any) -> list[dict[str, str]]:
    """The eval's notes on the deploys (EvalNote): deploys it could not read, changes known to have failed whose 14
    days are not over (counted once they are, as 交付正确率 counts its own), and the deliveries left out of 交付正确率
    because what happened after their merge is not known."""
    out = []
    if isinstance(dp, dict) and dp.get("environment") and dp.get("missing"):
        raw = [t for t in (_text(m, 300) for m in dp["missing"]) if t]
        out.append({"text": "%s 有部署读不到结果（%s），不算进部署频率，也不算上线" % (
                        _text(dp["environment"], 80), said(raw[0] if raw else "")),
                    "todo": "看细节里的原话：令牌或登录过期就换一个，证据过期了就只能不算", "raw": "\n".join(raw[:5])})
    cf = dp.get("change_failure") if isinstance(dp, dict) else None
    open_ = [k for k in cf.get("failed_open") or [] if isinstance(k, str)] if isinstance(cf, dict) else []
    if open_:
        out.append({"text": "已知变更失败、14 天还没满（满了再算进变更失败率）：%s" % "、".join(open_[:20]),
                    "todo": "不用管：满 14 天会自动算进去；要看是哪次部署没过，点开需求看「上线」", "raw": ""})
    keys = [k for k in unknown_after_merge if isinstance(k, str)] if isinstance(unknown_after_merge, list) else []
    if keys:
        out.append({"text": "合并满 14 天了，可部署结果读不到，不算进交付正确率：%s" % "、".join(keys[:20]),
                    "todo": "点开需求看「上线」：读不到的原因写在那里", "raw": ""})
    return out


class Clients:
    """brain-kit's GitLab client, one per GitLab and token, kept for the app's life (the module's note on single
    sign-on) and made again when it is next asked for after its sign-in ended; ``source`` makes the deploys Source of a
    repo with it."""

    def __init__(self, transport: Any = None) -> None:
        self.transport = transport      # tests: a fake GitLab; None: brain-kit's own (its sign-in helper, if any)
        self._by_base: dict[str, tuple[str, Any]] = {}
        self._lock = threading.Lock()

    def gitlab(self, base: str) -> Any:
        """The client for *base* with this machine's token; None without either."""
        tok = identity.token()
        home = (base or "").rstrip("/")
        if not home or not tok:
            return None
        mark = hashlib.sha256(tok.encode("utf-8")).hexdigest()
        with self._lock:
            got = self._by_base.get(home)
            if got is None or got[0] != mark:
                made = signin.Renewing(lambda: bk.m("rest").GitLab(home, tok, self.transport))
                got = self._by_base[home] = (mark, made)
        if got[1].expired:          # a new sign-in on this machine is used from now on
            got[1].renew()
        return got[1]

    def source(self, path: str, cfg: dict[str, Any], ref: str | None = None) -> Any:
        """A new deploys Source of the repo at *path*: what .brain/kit.json on its default branch sets up
        (brainkit.deploys.source_for), read with this app's client of kit.json's GitLab. None when it sets up none,
        when this brain-kit has no deploys, or when it cannot be made here (brain-kit then reads them itself)."""
        if not (bk.has("deploys") and bk.has("trace")):
            return None
        try:
            dref = bk.m("trace").default_ref(path, cfg, ref)
            base = str((cfg.get("gitlab") or {}).get("base") or "")
            return bk.m("deploys").source_for(path, dref, gitlab=self.gitlab(base))
        except Exception:  # noqa: BLE001 - a git or sign-in helper failure must not take the page down
            return None
