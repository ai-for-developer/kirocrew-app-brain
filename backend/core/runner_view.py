"""On a personal install: what the team runner does, as GitLab shows it (GET /team/status, 运行机's read-only view),
per listed repo whose kit.json names the bridge's account (bridge_user), read with the person's own token through the
GitLab client 交付总览 reads MRs with (mrinfo.client).

An open MR is at work when it carries the hand-over's running label (kit.json running_label), or when MR 巡检 replied
on it or summed it up within RECENT_S: patrol leaves the hand-over's MRs alone (team/patrol.skip_of), so a note of the
bridge's account on any other open MR is patrol's, unless it is patrol's own comment that it left the MR alone
(patrol.STOPPED_RE). A question waits on the person when the bridge asked it and counts no answer yet (mrinfo.
parse_notes: open), names them among who may answer, and nobody wrote `answer D-n` after it. The runner last acted
when the bridge's account last wrote on one of the MRs read (a system note, a label it changed, counts too).

Read: the open MRs with the running label and every MR changed within RECENT_S (at most MAX_MRS, the newest first),
each with its notes; each repo at most every TTL_S. A read that fails keeps the last one it got, with why (Problem).

状态中转 (Relays): a repo whose kit.json names team_status.project reads the team server's TeamSnapshot (snapshot.py,
team/relay.py writes it) from that GitLab project, snapshot.json on main, with the same token, at most every TTL_S for
each project however many repos name it: the server, the runner at work, 全队交付. One older than snapshot.STALE_S says
the server may have stopped (stale). A read that fails, or a file that is not a snapshot of this schema, keeps the last
good one, with why. A repo whose kit.json names none keeps the read above alone, so an older server still shows.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, NamedTuple

from . import bk, mrinfo, snapshot
from .team import patrol

RECENT_S = 24 * 3600
TTL_S = 60.0
MAX_MRS = 20
RUNNING, PATROL = "running", "patrol"       # AtWork.why: the hand-over's label, MR 巡检's notes
HAND_LABELS = ("handoff_label", "running_label", "rejected_label")


@dataclass(frozen=True)
class AtWork:
    iid: int
    title: str
    url: str | None
    why: str                    # RUNNING | PATROL
    at: float | None            # the newest note the bridge's account wrote on it


@dataclass(frozen=True)
class Waiting:
    id: str                     # D-n
    iid: int
    title: str                  # its MR's
    url: str | None             # the question's note on the MR
    kind: str
    kind_label: str
    question: str
    options: list[dict[str, str]]
    at: float | None            # when it was asked


@dataclass(frozen=True)
class Seen:
    at: float                   # when GitLab was read
    last_at: float | None       # the runner's newest act on the MRs read
    at_work: list[AtWork]
    waiting: list[Waiting]


@dataclass(frozen=True)
class Problem:
    text: str
    todo: str
    raw: str = ""


@dataclass(frozen=True)
class Runner:
    bridge_user: str
    project: str
    me: str | None              # whom the questions are read for (None: the app cannot tell who the person is there)
    seen: Seen | None           # the last read that worked
    problem: Problem | None     # why the last read did not


class Listed(NamedTuple):
    """A repo as the service lists it: its kit.json, GitLab address and project, and the person's name there."""
    repo: str
    name: str
    kit: Mapping[str, Any]
    base: str
    project: str
    me: str | None


class Mr(NamedTuple):
    iid: int
    title: str
    url: str | None
    open: bool
    labels: frozenset[str]


class Note(NamedTuple):
    id: int
    first: str                  # its first line
    author: str                 # GitLab's username, lower case
    at: float | None
    system: bool


NOT_WRITTEN = ("团队服务器还没在 %s 写过状态，或者你看不到这个项目",
               "等团队服务器跑一轮（它每 5 分钟最多写一次）；一直这样，就请管运行机的人把你加进 %s（Reporter 就够）")
OTHER_SCHEMA = ("团队服务器写的状态是第 %s 版，这个 app 读不了", "把这个 app 升级到团队服务器的版本")
NOT_SNAPSHOT = ("团队服务器写的状态读不懂", "请管运行机的人看一下团队服务器：%s 里的 snapshot.json 坏了")
NO_PROJECT = Problem("这个仓库的 kit.json 没写 GitLab 项目，读不了团队运行机", "在 .brain/kit.json 里写上 gitlab.base 和 gitlab.project")
NO_TOKEN = Problem("这台机器上没有你的 GitLab 令牌，读不了团队运行机",
                   "把你的 GitLab 令牌放在 ~/.brain/gitlab-token（chmod 600，只有你能读）或环境变量 GITLAB_TOKEN 里，要有 read_api 权限")


def problem_of(exc: BaseException) -> Problem:
    """A read GitLab refused or could not answer, in words; its own words for 细节."""
    raw, status = str(exc).split("\n", 1)[0][:300], getattr(exc, "status", None)
    if status in (401, 403):
        return Problem("GitLab 不认你的令牌", "看看你的 GitLab 令牌有没有过期，有没有 read_api 权限", raw)
    if status == 404:
        return Problem("GitLab 上看不到这个项目", "看看 .brain/kit.json 里的 gitlab.project 写得对不对，你在这个项目里有没有权限", raw)
    return Problem("这次没读到 GitLab", "过一会儿会再读；一直这样就看看网络，GitLab 要单点登录的先登录", raw)


def epoch(stamp: Any) -> float | None:
    t = bk.m("rest").gl_time(stamp)
    return t.timestamp() if t else None


def mr_of(row: Any) -> Mr | None:
    iid = row.get("iid") if isinstance(row, dict) else None
    if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
        return None
    return Mr(iid, str(row.get("title") or "")[:300], str(row.get("web_url") or "") or None, row.get("state") == "opened",
              frozenset(x for x in row.get("labels") or () if isinstance(x, str)))


def note_of(n: Any) -> Note | None:
    if not isinstance(n, dict) or isinstance(n.get("id"), bool) or not isinstance(n.get("id"), int):
        return None
    return Note(n["id"], str(n.get("body") or "").split("\n", 1)[0].rstrip("\r"),
                str((n.get("author") or {}).get("username") or "").lower(), epoch(n.get("created_at")), bool(n.get("system")))


def read(gl: Any, project: str, kit: Mapping[str, Any], now: float) -> tuple[list[Mr], dict[int, list[Any]]]:
    """The MRs to look at, and each one's notes as GitLab gave them (blocking)."""
    running = str(kit.get("running_label") or "")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - RECENT_S))
    rows = list(gl.mrs_with_label(project, running) if running else [])
    rows += gl.call("GET", "/projects/%s/merge_requests" % bk.m("rest").q(project),
                    {"state": "all", "updated_after": since, "order_by": "updated_at", "sort": "desc",
                     "per_page": MAX_MRS}) or []
    mrs: dict[int, Mr] = {}
    for m in filter(None, map(mr_of, rows)):
        mrs.setdefault(m.iid, m)
    return list(mrs.values()), {iid: gl.mr_notes(project, iid) for iid in mrs}


def seen_of(mrs: list[Mr], notes: Mapping[int, list[Any]], *, kit: Mapping[str, Any], bridge_user: str,
            me: str | None, now: float) -> Seen:
    bridge, running = bridge_user.lower(), str(kit.get("running_label") or "")
    hand = {str(kit.get(k) or "") for k in HAND_LABELS} - {""}
    at_work: list[AtWork] = []
    waiting: list[Waiting] = []
    acts: list[float] = []
    for mr in mrs:
        raw = notes.get(mr.iid) or []
        ns = [n for n in map(note_of, raw) if n]
        own = [n for n in ns if n.author == bridge]
        acts += [n.at for n in own if n.at is not None]
        said = [n for n in own if not n.system]
        last = said[-1] if said else None
        if not mr.open:
            continue
        if running in mr.labels:
            at_work.append(AtWork(mr.iid, mr.title, mr.url, RUNNING, last.at if last else None))
        elif not (mr.labels & hand) and last and last.at is not None and now - last.at <= RECENT_S \
                and not patrol.STOPPED_RE.match(last.first):
            at_work.append(AtWork(mr.iid, mr.title, mr.url, PATROL, last.at))
        if me:
            waiting += asked_of(mr, raw, ns, bridge_user, me.lower())
    at_work.sort(key=lambda w: (w.why != RUNNING, -(w.at or 0), w.iid))
    waiting.sort(key=lambda q: (q.at or 0, q.iid))
    return Seen(now, max(acts, default=None), at_work, waiting)


def asked_of(mr: Mr, raw: list[Any], ns: list[Note], bridge_user: str, me: str) -> list[Waiting]:
    """The bridge's open questions on *mr* that name *me* and that nobody answered after."""
    answer = bk.m("bridge.decisions").ANSWER_RE
    out = []
    for q in mrinfo.parse_notes(raw, bridge_user)["questions"]:
        if q["state"] != "open" or me not in q.get("who", ()):
            continue
        at = next((i for i, n in enumerate(ns) if n.id == q["note_id"]), -1)
        answered = {m.group(1) for m in (answer.match(n.first) for n in ns[at + 1:]) if m}
        if q["id"] in answered:
            continue
        url = "%s#note_%d" % (mr.url, q["note_id"]) if mr.url and isinstance(q["note_id"], int) else mr.url
        out.append(Waiting(q["id"], mr.iid, mr.title, url, q["kind"], q["kind_label"], q["question"], q["options"],
                           epoch(q["at"])))
    return out


def relay_of(kit: Mapping[str, Any]) -> str | None:
    """The status project kit.json's team_status names (group/name), None when it names none."""
    ts = kit.get("team_status")
    path = str(ts.get("project") or "").strip().strip("/") if isinstance(ts, dict) else ""
    return path if "/" in path else None


@dataclass(frozen=True)
class Relayed:
    """One status project's snapshot as the person's token last read it."""
    read_at: float | None               # when the last read that worked was (the person's clock)
    snapshot: dict[str, Any] | None     # TeamSnapshot, parsed (snapshot.parse)
    problem: Problem | None             # why the last read did not work


class Runners:
    """Each listed repo's runner as GitLab last showed it, read again at most every TTL_S (blocking: call it in a
    thread), and each status project's snapshot (状态中转) the same way. The person's token never leaves brain-kit's
    GitLab client."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self.clock = clock
        self._got: dict[tuple, tuple[float, Runner]] = {}
        self._relays: dict[tuple, tuple[float, Relayed]] = {}
        self._lock = threading.Lock()

    def view(self, listed: list[Listed], *, now: float, transport: Any = None) -> dict[str, Any]:
        """GET /team/status (RunnerStatus in ui/src/types.ts): the repos in the order the app lists them, and the
        status projects they name, in the order of the first repo that names each."""
        with self._lock:
            groups: dict[tuple[str, str], list[Listed]] = {}
            for x in listed:
                path = relay_of(x.kit)
                if path:
                    groups.setdefault((x.base, path), []).append(x)
            return {"recent_h": RECENT_S // 3600, "at": now, "stale_after_s": snapshot.STALE_S,
                    "repos": [self._repo(x, now, transport) for x in listed],
                    "relays": [self._relay(base, path, xs, listed, now, transport) for (base, path), xs in groups.items()]}

    def _repo(self, x: Listed, now: float, transport: Any) -> dict[str, Any]:
        bridge = str(x.kit.get("bridge_user") or "").strip()
        if not bridge:
            return {"repo": x.repo, "name": x.name, "runner": None, "relay": relay_of(x.kit)}
        key = (x.repo, x.base, x.project, bridge.lower(), x.me)
        got = self._got.get(key)
        if got is None or self.clock() - got[0] > TTL_S:
            got = self._got[key] = (self.clock(), self._read(x, bridge, now, transport, got[1].seen if got else None))
        return {"repo": x.repo, "name": x.name, "runner": asdict(got[1]), "relay": relay_of(x.kit)}

    def _relay(self, base: str, path: str, xs: list[Listed], listed: list[Listed], now: float,
               transport: Any) -> dict[str, Any]:
        """RelayRead (ui/src/types.ts): the status project, the listed repos that name it, the person's name there, the
        listed repos of its GitLab by their project (to open a row's 运行过程 here), and its snapshot as last read."""
        key = (base, path)
        got = self._relays.get(key)
        if got is None or self.clock() - got[0] > TTL_S:
            last = got[1] if got else Relayed(None, None, None)
            got = self._relays[key] = (self.clock(), read_relay(base, path, now, transport, last))
        r = got[1]
        snap = r.snapshot
        return {"project": path, "repos": [x.repo for x in xs], "me": next((x.me for x in xs if x.me), None),
                "repo_of": {x.project: x.repo for x in listed if x.base == base and x.project},
                "read_at": r.read_at, "snapshot": snap,
                "stale": snap is not None and now - snap["generated_at"] > snapshot.STALE_S,
                "problem": asdict(r.problem) if r.problem else None}

    @staticmethod
    def _read(x: Listed, bridge: str, now: float, transport: Any, last: Seen | None) -> Runner:
        def runner(seen: Seen | None, problem: Problem | None) -> Runner:
            return Runner(bridge, x.project, x.me, seen, problem)
        if not x.base or not x.project:
            return runner(last, NO_PROJECT)
        try:
            gl = mrinfo.client(x.base, transport)
        except Exception as exc:  # noqa: BLE001 - brain-kit refuses a token file others can read
            return runner(last, Problem("GitLab 令牌读不了", "chmod 600 ~/.brain/gitlab-token：令牌文件只能你自己读",
                                        str(exc).split("\n", 1)[0][:300]))
        if gl is None:
            return runner(last, NO_TOKEN)
        try:
            mrs, notes = read(gl, x.project, x.kit, now)
        except Exception as exc:  # noqa: BLE001 - GitLab down, the token refused, SSO expired: said, the last read kept
            return runner(last, problem_of(exc))
        return runner(seen_of(mrs, notes, kit=x.kit, bridge_user=bridge, me=x.me, now=now), None)


def read_relay(base: str, path: str, now: float, transport: Any, last: Relayed) -> Relayed:
    """snapshot.json of status project *path*, read with the person's token and parsed; *last* kept when it cannot
    be, with why (blocking)."""
    def kept(problem: Problem) -> Relayed:
        return Relayed(last.read_at, last.snapshot, problem)
    try:
        gl = mrinfo.client(base, transport) if base else None
    except Exception as exc:  # noqa: BLE001 - brain-kit refuses a token file others can read
        return kept(Problem("GitLab 令牌读不了", "chmod 600 ~/.brain/gitlab-token：令牌文件只能你自己读",
                            str(exc).split("\n", 1)[0][:300]))
    if gl is None:
        return kept(NO_TOKEN if base else NO_PROJECT)
    try:
        raw = gl.file_raw(path, snapshot.FILE, snapshot.BRANCH)
    except Exception as exc:  # noqa: BLE001 - GitLab down, the token refused, SSO expired: said, the last read kept
        return kept(problem_of(exc))
    if raw is None:
        return kept(Problem(NOT_WRITTEN[0] % path, NOT_WRITTEN[1] % path))
    try:
        return Relayed(now, snapshot.parse(raw), None)
    except snapshot.OtherSchema as exc:
        return kept(Problem(OTHER_SCHEMA[0] % exc.schema, OTHER_SCHEMA[1], str(exc)))
    except snapshot.Bad as exc:
        return kept(Problem(NOT_SNAPSHOT[0], NOT_SNAPSHOT[1] % path, str(exc)[:300]))
