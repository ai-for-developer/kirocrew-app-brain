"""What happened after a merge: the deploys GitLab recorded for a landed change (`brain trace`, `brain eval`,
`brain deploys`). Read-only: the evidence stays in GitLab (its Deployments API and job artifacts), nothing is
written to git.

A deploy ships a change when its commit is a commit of the default branch that contains the change's merge commit:
the merge itself or any later commit of that branch (git ancestry, in this clone). A deploy of another branch (a
pipeline someone ran by hand) ships nothing judged here. The change's attempts are those deploys, oldest first,
through the first promoted one. That one gives the lead time. An aborted one before it is a change failure (trace:
outcome.reverted_or_hotfixed_within_14d). A deploy that aborts after it is a later change's failure, because by then
this change was already stable. A deploy whose commit is not in this clone, or whose evidence cannot be read, is an
attempt whose result is unknown: never a promotion, and never "no failure".

Config: "deploys" in .brain/kit.json as the default branch has it (a run branch cannot change how its deploys are
judged). No "deploys", or an empty environment: off, and no GitLab call is made.
  environment       the GitLab environment whose deployments count (staging)
  deploy_job        the job that deploys there; "": any job of that environment
  deploy_evidence   that job's artifact: what the rollout did (deploy-evidence.json)
  build_job         the job of the same pipeline that built the image; "": build evidence not read
  build_evidence    that job's artifact: the digest built and its scan (build-evidence.json)
GitLab: gitlab.base and gitlab.project of the same kit.json. The token comes from GITLAB_TOKEN or
~/.brain/gitlab-token (rest.read_token); a GitLab behind single sign-on is reached with its sign-in helper. Such a
GitLab signs each new client in (17 s measured on one, then 1.5-3 s a call): a long-running caller passes its own
client, source_for(repo, ref, gitlab=client), and the Source to trace.build(deploys=...).

Evidence files, written by the project's CI (other fields are ignored):
  deploy  environment, deploy_commit, digest (sha256:<64 hex>), image, previous_stable_digest, result (promoted or
          aborted), reason, stable_digest_after, steps_reached [{index, step, seen_at}], analysis_runs [{step_index,
          smoke [{check, ok, error}]}]
  build   commit, image_digest, scan {status, critical, high}, gate (PASS or BLOCKED), problems [text]

Records. The field names are stable (the KiroCrew app reads them); times are ISO UTC.
  deploy: one GitLab deployment to the environment, with what its evidence says
    deployment, pipeline, job   GitLab ids (job: the deploy job, whose artifact is the evidence); web_url: its page
    ref, sha                    the branch and the commit deployed
    created_at, finished_at     GitLab's times; finished_at is null while it runs
    status                      GitLab's deployment status (success, failed, running, canceled, ...)
    result                      promoted | aborted | running | unknown. Promoted and aborted come from the evidence
                                only: GitLab's status alone never makes a deploy promoted
    why                         why the result is unknown, else null
    digest, image               what it rolled out
    previous_stable_digest      what the environment ran before it
    stable_digest_after         what it ran after (aborted: the previous digest, kept)
    reason                      the rollout's own words
    steps                       [{index, step, at}]: the canary steps it reached ("setWeight 20", "analysis smoke")
    failed_checks               [{step, check, error}]: the smoke checks that failed, by canary step
    build                       null when not read (no build_job, or not one of a change's attempts), else {value:
                                pass | blocked | unknown, commit, digest, scan {status, critical, high}, problems, why,
                                src}
    src                         where it was read
  change: trace outcome.deploy, and one row of `brain deploys`
    value             promoted: a deploy shipping it was promoted. aborted: one was tried, none promoted yet.
                      running. not_deployed: no deploy ships it yet. unknown: see missing. null: off, or not merged
    environment       the environment judged; null when off
    commit            the commit matched (the merge commit)
    at, digest        when the first promotion finished, and the digest it rolled out
    lead_time_hours   at minus the merge time
    attempts          [deploy]: the deploys shipping it, oldest first, through the first promoted
    missing           what could not be read
    src, rules        where it was read, and how it was judged
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from . import config
from .common import BrainError, git, iso, parse_iso
from .rest import DEFAULT_TOKEN_FILE, GitLab, gl_time, read_token

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
KEYS = ("environment", "deploy_job", "deploy_evidence", "build_job", "build_evidence")
RUNNING = ("created", "running", "blocked", "waiting", "pending")
STOPPED = ("canceled", "skipped")
PER_PAGE, MAX_PAGES = 100, 50
SLACK = timedelta(days=1)     # deploys created up to a day before the merge time are read too: clocks disagree
OFF = "off: no deploys.environment in %s on the default branch" % config.KIT_CONFIG
RULES = ("a deploy to %s ships the change when its commit is a commit of %s that contains the merge commit (git "
         "ancestry here); attempts are those deploys, oldest first, through the first promoted one; promoted and "
         "aborted only as the deploy evidence says; a deploy whose commit is not here, or whose evidence cannot be "
         "read, is unknown")


def settings(kit):
    """(conf, problem) from a kit.json document: (None, None) when deploys are off, (None, why) when its "deploys"
    cannot be used, else (conf, None)."""
    d = kit.get("deploys") if isinstance(kit, dict) else None
    if d is None:
        return None, None
    if not isinstance(d, dict):
        return None, "deploys in %s must be an object" % config.KIT_CONFIG
    conf = dict(config.DEFAULTS["deploys"], **d)
    if conf["environment"] in ("", None):
        return None, None
    for k in KEYS:
        if not isinstance(conf[k], str):
            return None, "deploys.%s in %s must be a string" % (k, config.KIT_CONFIG)
    for k in ("deploy_evidence", "build_evidence"):
        parts = conf[k].split("/")
        if "" in parts or ".." in parts:
            return None, "deploys.%s in %s must name a file of the job's artifacts, like %s" % (
                k, config.KIT_CONFIG, config.DEFAULTS["deploys"][k])
    return {k: conf[k] for k in KEYS}, None


def source_for(repo, ref, token=None, gitlab=None):
    """The Source of the deploys that .brain/kit.json at `ref` (the default branch) configures, or None when they
    are off there. `gitlab`: a rest.GitLab to read them with (one for kit.json's gitlab.base; else a new one is made).
    A kit.json, GitLab or token that cannot be used gives a Source that answers every read with why."""
    p = git(repo, "show", "%s:%s" % (ref, config.KIT_CONFIG), check=False) if ref else None
    if p is None or p.returncode != 0:
        return None
    try:
        kit = json.loads(p.stdout)
    except ValueError:
        return Source(None, problem="%s on %s is not valid JSON" % (config.KIT_CONFIG, ref))
    conf, problem = settings(kit)
    if conf is None:
        return Source(None, problem=problem) if problem else None
    gl = kit.get("gitlab") if isinstance(kit.get("gitlab"), dict) else {}
    base, project = gl.get("base"), gl.get("project")
    if not (isinstance(base, str) and base and isinstance(project, (str, int)) and str(project)):
        return Source(conf, problem="gitlab.base and gitlab.project are not set in %s on %s" % (config.KIT_CONFIG,
                                                                                             ref))
    if gitlab is not None and gitlab.base == base.rstrip("/") + "/api/v4":
        return Source(conf, gitlab, project)
    try:
        tok = read_token() if token is None else token
        if not tok:
            return Source(conf, problem="no GitLab token here (GITLAB_TOKEN, or %s)" % DEFAULT_TOKEN_FILE)
        return Source(conf, GitLab(base, tok), project)
    except BrainError as e:              # a token file others may read, a sign-in helper that cannot start
        return Source(conf, problem=str(e))


class Source:
    """The deployments to one environment of one GitLab project. They are read newest first, a page at a time and
    only as far back as asked, then kept; each job's evidence file is read once."""

    def __init__(self, conf, gitlab=None, project=None, problem=None):
        self.conf, self.gl, self.project, self.problem = conf, gitlab, project, problem
        self.environment = conf["environment"] if conf else None
        self._rows, self._oldest, self._pages, self._all = {}, None, 0, False
        self._files, self._jobs = {}, {}

    @property
    def where(self) -> str:
        return "project %s, environment %s" % (self.project, self.environment) if self.gl else self.problem

    def deployments(self, since):
        """The deployments created at or after `since` (ISO), oldest first. BrainError when they cannot be read."""
        if self.problem:
            raise BrainError(self.problem)
        while not self._all and (self._oldest is None or self._oldest >= since):
            if self._pages == MAX_PAGES:
                raise BrainError("more than %d deployments to %s since %s: not read" % (
                    PER_PAGE * MAX_PAGES, self.environment, since))
            self._pages += 1
            page = self.gl.deployments(self.project, self.environment, page=self._pages, per_page=PER_PAGE)
            if not isinstance(page, list):
                raise BrainError("GitLab lists no deployments for %s" % self.environment)
            for raw in page:
                at = gl_time(raw.get("created_at")) if isinstance(raw, dict) else None
                if at is not None and (self._oldest is None or iso(at) < self._oldest):
                    self._oldest = iso(at)
                d = _deployment(raw, self.conf["deploy_job"])
                if d is not None:
                    self._rows[d["deployment"]] = d
            self._all = len(page) < PER_PAGE
        return sorted((d for d in self._rows.values() if d["created_at"] >= since), key=lambda d: d["deployment"])

    def evidence(self, d, as_of):
        """The deploy record of deployment `d` as of `as_of` (ISO): GitLab's fields and what its evidence says."""
        rec = _record(d)
        if d["status"] in RUNNING or (d["finished_at"] or "") > as_of:
            rec["result"] = "running"
            return rec
        if d["status"] in STOPPED:
            rec["why"] = "its job was %s while it ran: the rollout may have gone on without it" % d["status"]
            return rec
        path = self.conf["deploy_evidence"]
        rec["src"] += ", job %s %s" % (d["job"], path)
        doc, why = self._file(d["job"], path)
        rec.update(read_deploy(doc, d["sha"], self.environment) if why is None else {"why": why})
        return rec

    def build(self, d):
        """The build record of the pipeline deploy `d` ran in (its build_job's evidence); None without build_job."""
        name = self.conf["build_job"]
        if not name:
            return None
        pid = d["pipeline"]
        if pid is None:
            return _unknown_build("GitLab names no pipeline for deployment %d" % d["deployment"],
                                  "GitLab deployment %d" % d["deployment"])
        if pid not in self._jobs:
            try:
                jobs = self.gl.pipeline_jobs(self.project, pid, scope=["success", "failed"])
                self._jobs[pid] = ([j for j in jobs if isinstance(j, dict) and j.get("name") == name
                                    and type(j.get("id")) is int], None)
            except BrainError as e:
                self._jobs[pid] = ([], "the jobs of pipeline %d could not be read: %s" % (pid, e))
        jobs, why = self._jobs[pid]
        if why or not jobs:
            return _unknown_build(why or "pipeline %d has no finished %s job" % (pid, name), "GitLab pipeline %d" % pid)
        job = max(jobs, key=lambda j: j["id"])["id"]
        doc, why = self._file(job, self.conf["build_evidence"])
        rec = read_build(doc, d["sha"], d["digest"]) if why is None else _unknown_build(why)
        rec["src"] = "GitLab pipeline %d, job %d %s" % (pid, job, self.conf["build_evidence"])
        return rec

    def _file(self, job, path):
        """(the JSON document, None) or (None, why) for one artifact file of a job, read once."""
        if job is None:
            return None, "GitLab names no job for it"
        if (job, path) not in self._files:
            try:
                raw = self.gl.job_artifact(self.project, job, path)
                got = (None, "job %d has no %s (never written, or expired)" % (job, path)) if raw is None else (
                    json.loads(raw.decode("utf-8")), None)
            except BrainError as e:
                got = None, "%s of job %d could not be read: %s" % (path, job, e)
            except ValueError:
                got = None, "%s of job %d is not JSON" % (path, job)
            self._files[(job, path)] = got
        return self._files[(job, path)]


def _unknown_build(why, src=None):
    return {"value": "unknown", "commit": None, "digest": None, "scan": {"status": None, "critical": None, "high": None},
            "problems": [], "why": why, "src": src}


def _int(x):
    return x if type(x) is int else None


def _text(x, limit):
    """Text with each run of whitespace made one space, cut to `limit`; None when it is not text, or empty."""
    if not isinstance(x, str):
        return None
    return " ".join(x.split())[:limit] or None


def _digest(x):
    return x if isinstance(x, str) and DIGEST_RE.match(x) else None


def _deployment(raw, job_name):
    """A deployment as GitLab lists it -> GitLab's fields of the deploy record. None for one of another job, one
    cancelled before it started (it deployed nothing), and one without an id, a commit or a time."""
    if not isinstance(raw, dict):
        return None
    job = raw.get("deployable") if isinstance(raw.get("deployable"), dict) else {}
    pipe = job.get("pipeline") if isinstance(job.get("pipeline"), dict) else {}
    created, sha, status = gl_time(raw.get("created_at")), raw.get("sha"), str(raw.get("status") or "")
    if _int(raw.get("id")) is None or created is None or not (isinstance(sha, str) and SHA_RE.match(sha)):
        return None
    if (job_name and job.get("name") != job_name) or (status in STOPPED and not job.get("started_at")):
        return None
    done = gl_time(raw.get("finished_at")) or gl_time(job.get("finished_at"))
    return {"deployment": raw["id"], "pipeline": _int(pipe.get("id")), "job": _int(job.get("id")),
            "web_url": job.get("web_url") if isinstance(job.get("web_url"), str) else None,
            "ref": str(raw.get("ref") or ""), "sha": sha, "created_at": iso(created),
            "finished_at": iso(done) if done and status not in RUNNING else None, "status": status}


def _record(d):
    return dict(d, result="unknown", why=None, digest=None, image=None, previous_stable_digest=None,
                stable_digest_after=None, reason=None, steps=[], failed_checks=[], build=None,
                src="GitLab deployment %d" % d["deployment"])


def read_deploy(doc, sha, environment) -> dict:
    """What a deploy evidence document says about the deploy of commit `sha` to `environment`: result (only when
    it is promoted or aborted), why (when it is not), and the record's other evidence fields."""
    from .trace import scrub
    if not isinstance(doc, dict):
        return {"why": "the deploy evidence is not a JSON object"}
    if doc.get("environment") != environment:
        return {"why": "the deploy evidence is for environment %r, not %s" % (doc.get("environment"), environment)}
    if doc.get("deploy_commit") not in (None, sha):
        return {"why": "the deploy evidence is for commit %s, not %s" % (str(doc["deploy_commit"])[:12], sha[:12])}
    out = {"digest": _digest(doc.get("digest")), "image": _text(doc.get("image"), 300),
           "previous_stable_digest": _digest(doc.get("previous_stable_digest")),
           "stable_digest_after": _digest(doc.get("stable_digest_after")),
           "reason": scrub(_text(doc.get("reason"), 300)), "steps": [], "failed_checks": []}
    for s in doc.get("steps_reached") if isinstance(doc.get("steps_reached"), list) else []:
        if isinstance(s, dict) and isinstance(s.get("step"), str) and len(out["steps"]) < 50:
            at = gl_time(s.get("seen_at") or s.get("at"))
            out["steps"].append({"index": _int(s.get("index")), "step": s["step"][:80], "at": iso(at) if at else None})
    for run in doc.get("analysis_runs") if isinstance(doc.get("analysis_runs"), list) else []:
        smoke = run.get("smoke") if isinstance(run, dict) and isinstance(run.get("smoke"), list) else []
        step = str(run.get("step_index")) if isinstance(run, dict) else ""
        for c in smoke:
            if isinstance(c, dict) and c.get("ok") is False and len(out["failed_checks"]) < 20:
                out["failed_checks"].append({"step": int(step) if step.isdigit() else None,
                                             "check": _text(c.get("check"), 80) or "?",
                                             "error": scrub(_text(c.get("error"), 200))})
    result = doc.get("result")
    if result not in ("promoted", "aborted"):
        out["why"] = "the deploy evidence gives no result" + (": %s" % out["reason"] if out["reason"] else "")
    elif out["digest"] is None:
        out["why"] = "the deploy evidence names no image digest"
    elif result == "promoted" and doc.get("stable_digest_after") is not None and \
            out["stable_digest_after"] != out["digest"]:
        out["why"] = "the deploy evidence says promoted, but the stable digest after it is %s" % (
            out["stable_digest_after"] or doc.get("stable_digest_after"))
    else:
        out["result"] = result
    return out


def read_build(doc, sha, deployed) -> dict:
    """What a build evidence document says about the image built for commit `sha`; `deployed`: the digest the
    deploy rolled out (a build of another digest is unknown)."""
    from .trace import scrub
    if not isinstance(doc, dict):
        return _unknown_build("the build evidence is not a JSON object")
    if doc.get("commit") not in (None, sha):
        return _unknown_build("the build evidence is for commit %s, not %s" % (str(doc["commit"])[:12], sha[:12]))
    scan = doc.get("scan") if isinstance(doc.get("scan"), dict) else {}
    out = {"value": {"PASS": "pass", "BLOCKED": "blocked"}.get(doc.get("gate"), "unknown"),
           "commit": doc.get("commit"), "digest": _digest(doc.get("image_digest")),
           "scan": {"status": _text(scan.get("status"), 40), "critical": _int(scan.get("critical")),
                    "high": _int(scan.get("high"))},
           "problems": [scrub(_text(p, 200)) for p in doc.get("problems") or [] if isinstance(p, str)][:5]
           if isinstance(doc.get("problems"), list) else [], "why": None, "src": None}
    if out["value"] == "unknown":
        out["why"] = "the build evidence gives no gate (PASS or BLOCKED)"
    elif deployed and out["digest"] != deployed:
        out.update(value="unknown", why="the build made %s, but the deploy rolled out %s" % (out["digest"], deployed))
    return out


def contains_in(repo, commit):
    """contains(sha) for change(): is `commit` in the history of commit `sha` here (None: one is not in this clone)."""
    def contains(sha):
        rc = git(repo, "merge-base", "--is-ancestor", commit, sha, check=False).returncode
        return True if rc == 0 else False if rc == 1 else None
    return contains


def change(src, commit, landed_at, contains, as_of, branch) -> dict:
    """The change record of `commit`, which landed on `branch` at `landed_at`, judged as of `as_of` (ISO times).
    contains(sha): does commit `sha` contain it (True, False, or None when this clone cannot tell)."""
    out = {"value": None, "environment": src.environment if src else None, "commit": commit, "at": None,
           "digest": None, "lead_time_hours": None, "attempts": [], "missing": [], "src": OFF, "rules": None}
    if src is None:
        return out
    out["rules"] = RULES % (src.environment or "the environment", branch)
    if not commit or not landed_at:
        out["src"] = "not merged"
        return out
    out["src"] = "GitLab deployments (%s)" % src.where
    try:
        rows = src.deployments(iso(parse_iso(landed_at) - SLACK))
    except BrainError as e:
        out["value"] = "unknown"
        out["missing"].append("the deploys to %s: %s" % (src.environment or "?", e))
        return out
    for d in rows:
        if d["ref"] != branch or d["created_at"] > as_of:
            continue
        has = contains(d["sha"])
        if has is False:
            continue
        if has:
            a = src.evidence(d, as_of)
            a["build"] = src.build(a)
        else:
            a = dict(_record(d), why="its commit %s is not in this clone: whether it ships %s is not known (fetch "
                                     "it)" % (d["sha"][:12], commit[:12]))
        out["attempts"].append(a)
        if a["result"] == "promoted":
            break
    results = [a["result"] for a in out["attempts"]]
    out["value"] = next((v for v in ("promoted", "unknown", "running", "aborted") if v in results), "not_deployed")
    first = out["attempts"][-1] if out["value"] == "promoted" else None
    if first:
        out["at"], out["digest"] = first["finished_at"], first["digest"]
        if first["finished_at"]:
            out["lead_time_hours"] = round((parse_iso(first["finished_at"]) - parse_iso(landed_at)).total_seconds()
                                           / 3600, 2)
    out["missing"] += ["deployment %d (%s): %s" % (a["deployment"], a["sha"][:12], a["why"])
                       for a in out["attempts"] if a["result"] == "unknown"]
    return out


def failures(dep, start, until):
    """(rows, open) for the 14-day watch of a change record: rows, its aborted attempts that finished between
    `start` and `until` (ISO), as trace evidence rows; open, why whether one aborted then is not known (an attempt
    whose result is unknown or still running, or deploys not read), else None. ([], None) when deploys are off."""
    if not dep or dep["value"] is None:
        return [], None
    if not dep["attempts"] and dep["value"] == "unknown":
        return [], "; ".join(dep["missing"]) or "the deploys could not be read"
    rows, open_ = [], None
    for a in dep["attempts"]:
        at = a["finished_at"] or a["created_at"]
        if not start <= at <= until:
            continue
        if a["result"] == "aborted":
            rows.append({"deployment": a["deployment"], "sha": a["sha"], "at": at, "src": a["src"],
                         "rule": "canary aborted on %s: %s kept" % (dep["environment"],
                                                                     a["stable_digest_after"] or "no stable digest")})
        elif a["result"] in ("unknown", "running") and open_ is None:
            open_ = "deployment %d to %s: %s" % (a["deployment"], dep["environment"], a["why"] or "still running")
    return rows, open_


LABEL = {"promoted": "上线了", "aborted": "灰度中止", "running": "还在部署", "not_deployed": "还没部署",
         "unknown": "读不到结果"}


def line(a) -> str:
    """One deploy record as text: when, which deployment, pipeline and commit, and its result."""
    why = ""
    if a["result"] == "aborted":
        why = "：%s" % (a["reason"] or "没写原因")
        if a["failed_checks"]:
            why += "；冒烟没过 " + "、".join(x["check"] for x in a["failed_checks"])
    elif a["result"] == "unknown":
        why = "：%s" % a["why"]
    return "%s 部署 %d（流水线 %s，%s %s）%s%s" % (a["created_at"], a["deployment"], a["pipeline"] or "?", a["ref"],
                                             a["sha"][:12], LABEL[a["result"]], why)


def lines(c) -> list:
    """A change record as text (`brain trace`, `brain deploys`): the outcome, then one line per attempt."""
    if not c["value"]:
        return []
    head = "部署到 %s：%s" % (c["environment"] or "?", LABEL[c["value"]])
    if c["value"] == "promoted":
        head += " %s（%s，合并后 %s 小时）" % (c["digest"] or "?", c["at"] or "?",
                                         "?" if c["lead_time_hours"] is None else c["lead_time_hours"])
    return [head] + ["  " + line(a) for a in c["attempts"]]
