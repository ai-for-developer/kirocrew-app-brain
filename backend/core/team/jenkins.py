"""Jenkins, read for MR 巡检 (patrol.py): the console of a build that reported to GitLab as a commit status (Jenkins'
GitLab plugin). GitLab has no job or log of such a check, only its name, its status and its link (target_url).

bridge.json's optional "jenkins": {"base", "user", "token_file"} names it. *base* is the address this machine reaches
Jenkins at, *user* a read-only user (Overall/Read and Job/Read: Jenkins answers 403 to no one), *token_file* its API
token or password, in a file only its owner can read. A target_url names the build at Jenkins' own configured address,
which this machine may not reach (a name on Jenkins' docker network, say): its /job/<…>/<number>/ is read at *base*
instead. A build whose log is not read (no Jenkins named, Jenkins refusing, a link that is no Jenkins build) says why
in one line.

The bridge's rounds run one at a time on one worker thread, so a read of Jenkins holds up the next round's hand-over
steps. Logs keeps that short: a build's log is read once and kept by the build's address while the app runs; only its
end, TAIL_BYTES (Jenkins' progressiveText from a byte on, read as consoleText reads: Jenkins.tail); TIMEOUT_S a request
at most; no read starts once ROUND_S went by since the round's first one, and none is given longer than what is left of
it (one it cuts short is left for the next round, as the rest are); after a Jenkins that cannot be reached none is tried
that round (the other builds fail the same way); a build not read is asked for again RETRY_S later, MAX_TRIES times in
all. A read while Jenkins says more is coming (X-More-Data: the GitLab plugin posts a check failed before its build
ends) is not the log: the build is read again each round, its MR waiting, WAIT_S at most from the first such read; then
the last one read goes out, saying the build had not ended. A start of team mode starts with none read.
"""

from __future__ import annotations

import base64
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, NamedTuple

from .. import bk

# one /job/<name> or more (folders, multibranch), then the build's number: what a Jenkins build's address holds
BUILD_RE = re.compile(r"((?:/job/[^/]+)+)/(\d+)(?:/|$)")
# a console note as the log keeps it (ConsoleNote's PREAMBLE, its base64, its POSTAMBLE): Pipeline puts one before
# each [Pipeline] line, a link one before the text it links; consoleText leaves them out
NOTE_RE = re.compile(rb"\x1b\[8mha:[A-Za-z0-9+/=]*\x1b\[0m")
NOT_SET = "运行机没配 Jenkins（bridge.json 里没有 jenkins）"
NOT_A_BUILD = "这个链接不是 Jenkins 构建的地址"
TAIL_BYTES = 64 * 1024      # of a build's log, its end: far more than patrol's LOG_LINES lines
TIMEOUT_S = 10.0            # one request to Jenkins, its connect and each read
ROUND_S = 20.0              # the reads of one round, from its first one on
RETRY_S = 10 * 60           # a build whose log was not read is asked for again this long after
MAX_TRIES = 3               # ... this many times in all
WAIT_S = 10 * 60            # a build still running when read is read again each round, this long at most
KEEP = 200                  # builds whose read is kept (the oldest are let go)
OUT_OF_TIME = "这一轮读 Jenkins 的 %d 秒用完了，下一轮接着读" % ROUND_S
RUNNING = "构建还没跑完，跑完再读"
UNFINISHED = "构建等了 %d 分钟还没跑完，下面是那时读到的" % (WAIT_S // 60)


def build_path(target_url: str) -> str | None:
    """/job/<…>/<number> of the Jenkins build *target_url* links to (its /display/redirect, /console … left out), or
    None when it links to no Jenkins build."""
    u = urllib.parse.urlsplit(str(target_url or ""))
    m = BUILD_RE.search(u.path) if u.scheme in ("http", "https") and u.hostname else None
    return "%s/%s" % m.groups() if m else None


def refused(status: int, user: str) -> str:
    if status in (401, 403) and not user:
        return "Jenkins 不让没登录的人读这个构建（HTTP %d）：bridge.json 的 jenkins 写上只读用户和它的 token" % status
    if status == 401:
        return "Jenkins 不认 %s 和它的 token（HTTP 401）：看 bridge.json 的 jenkins" % user
    if status == 403:
        return "Jenkins 不让 %s 读这个构建（HTTP 403）：只读用户要有 Overall/Read 和 Job/Read" % user
    if status == 404:
        return "Jenkins 上找不到这个构建（HTTP 404）：只读用户没有它的 Job/Read，或者构建删掉了，或者 bridge.json 的 jenkins.base 不对"
    return "Jenkins 出错了（HTTP %d）" % status


def urllib_fetch(method: str, url: str, headers: Mapping[str, str], cap: int,
                 timeout: float) -> tuple[int, bytes, dict[str, str]]:
    """One request: (its status, the first *cap* bytes of its body, the rest left unread, its headers in lower case).
    BrainError when it cannot be made or answered within *timeout*."""
    req = urllib.request.Request(url, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(cap), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code, b"", {}
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        why = getattr(exc, "reason", None) or exc or type(exc).__name__
        raise bk.m("common").BrainError("%s: %s" % (urllib.parse.urlsplit(url).netloc, why)) from None


@dataclass(frozen=True)
class Jenkins:
    """The Jenkins bridge.json names (Jenkins.of). *why*: why no log is read at all ("": one may be). *transport*
    (tests): called as urllib_fetch is."""
    base: str = ""
    user: str = ""
    auth: str = field(default="", repr=False)
    why: str = NOT_SET
    transport: Callable[..., tuple[int, bytes, Mapping[str, str]]] | None = None

    @classmethod
    def of(cls, raw: Any, transport: Any = None) -> "Jenkins":
        """bridge.json's "jenkins", read where team mode starts: none when it is left out; one that says what is wrong
        with the block in *why*. The token is read here once, as the bridge's GitLab token is."""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            return cls(why="bridge.json 的 jenkins 要写成 {\"base\": …, \"user\": …, \"token_file\": …}")
        base = str(raw.get("base") or "").strip().rstrip("/")
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ("http", "https") or not u.hostname or "@" in u.netloc or u.query or u.fragment:
            return cls(why="bridge.json 的 jenkins.base 要写成 https://jenkins.example.com 这样")
        user, path = str(raw.get("user") or "").strip(), str(raw.get("token_file") or "").strip()
        if bool(user) != bool(path):
            return cls(base, user, why="bridge.json 的 jenkins 要 user 和 token_file 一起写：只读用户和它的 token")
        if not user:
            return cls(base, why="", transport=transport)
        path = os.path.expanduser(path)
        try:
            if os.stat(path).st_mode & 0o077:
                return cls(base, user, why="Jenkins 的 token 别人也能读：先 chmod 600 %s" % path)
            with open(path, encoding="utf-8") as fh:
                token = fh.read().strip()
        except (OSError, ValueError):
            token = ""
        if not token:
            return cls(base, user, why="读不到 Jenkins 的 token：%s 不在，或者是空的" % path)
        auth = "Basic " + base64.b64encode(("%s:%s" % (user, token)).encode()).decode()
        return cls(base, user, auth, "", transport)

    def page(self, target_url: str, build: str) -> str:
        """The build's page: at *base* when it is known, else *target_url* itself."""
        return self.base + build + "/" if self.base else target_url

    def tail(self, build: str, timeout: float) -> tuple[bytes, bool]:
        """(the end of the build's log at *base* as its consoleText reads, TAIL_BYTES of the log at most, from the start
        of a line to the end of one; whether that is the end of its log: not while Jenkins says more is coming,
        X-More-Data, the build still running). Jenkins' progressiveText gives a log from a byte on and says how long it
        is (X-Text-Size), so a longer one is asked for again from TAIL_BYTES before its end. It sends what consoleText
        does not: the console notes (NOTE_RE), and each line end as CRLF, so those bytes come as up to twice as many. A
        line the window cuts, at its start or at a log's end Jenkins is still writing, is left out with any note in it.
        rest.HttpError when Jenkins refuses, BrainError when it cannot be reached."""
        body, size, cut, more = self._from(build, 0, TAIL_BYTES, timeout)
        start = 0
        if cut:
            start = max(0, size - TAIL_BYTES)
            body, _, _, more = self._from(build, start, 2 * TAIL_BYTES, timeout)
        body = body.replace(b"\r\n", b"\n")
        if start:
            body = body[body.find(b"\n") + 1:]
        return NOTE_RE.sub(b"", body[:body.rfind(b"\n") + 1]), not more

    def _from(self, build: str, start: int, cap: int, timeout: float) -> tuple[bytes, int, bool, bool]:
        """(the log from byte *start* as Jenkins sends it, *cap* bytes at most, its whole length (-1: Jenkins did not
        say), whether Jenkins sent more than *cap*, whether it says more is coming)."""
        url = "%s%s/logText/progressiveText?start=%d" % (self.base, build, start)
        status, body, headers = (self.transport or urllib_fetch)(
            "GET", url, {"Authorization": self.auth} if self.auth else {}, cap + 1, timeout)
        if status != 200:
            raise bk.m("rest").HttpError(status, "GET %s -> HTTP %d" % (url, status))
        said = {k.lower(): str(v) for k, v in headers.items()}
        size = said.get("x-text-size") or ""
        return body[:cap], int(size) if size.isdigit() else -1, len(body) > cap, said.get("x-more-data") == "true"


class Log(NamedTuple):
    page: str                   # the build's page (target_url itself for a link that is no build)
    raw: bytes | None           # the end of its log; None: not read, *why* says why
    why: str = ""
    later: bool = False         # not read yet: the round's time for Jenkins is over, or cut the read short (no try),
                                # or the build is still running (RUNNING)


@dataclass
class Got:
    raw: bytes | None
    why: str
    tries: int
    at: float
    running: float | None = None    # still running at the last read, which *raw* is: since this first read found it


class Logs:
    """The builds' logs as this process read them, for MR 巡检's rounds (round(), then console() for each failed check),
    from its worker thread only."""

    def __init__(self, jenkins: Jenkins, now: Callable[[], float] = time.time) -> None:
        self.jenkins, self.now = jenkins, now
        self.got: dict[str, Got] = {}
        self.round()

    def round(self) -> None:
        """A new round: its reads may take ROUND_S from the first one, and Jenkins is tried again."""
        self.deadline: float | None = None
        self.down = ""

    def console(self, target_url: str) -> Log:
        """The log of the Jenkins build *target_url* links to: the one read before; else read now, unless it was tried
        less than RETRY_S ago or MAX_TRIES times, or Jenkins could not be reached this round, or the round's time for
        Jenkins is over (later). A build still running is read again each round (later) until it ends, WAIT_S at most
        from the first read that found it running; then the last one read goes out, saying so (UNFINISHED)."""
        build = build_path(target_url)
        if build is None:
            return Log(target_url, None, NOT_A_BUILD)
        j = self.jenkins
        page = j.page(target_url, build)
        if j.why:
            return Log(page, None, j.why)
        now, had = self.now(), self.got.get(build)
        running = had is not None and had.running is not None
        if had is not None and not running and (
                had.raw is not None or had.tries >= MAX_TRIES or now - had.at < RETRY_S):
            return Log(page, had.raw, had.why)
        if self.deadline is None:
            self.deadline = now + ROUND_S
        left = self.deadline - now
        if not self.down and left < 1:
            return Log(page, None, OUT_OF_TIME, later=True)
        raw, done, why = None, False, self.down
        if not why:
            timeout = min(TIMEOUT_S, left)
            try:
                raw, done = j.tail(build, timeout)
            except bk.m("rest").HttpError as exc:
                why = refused(exc.status, j.user)
            except bk.m("common").BrainError as exc:
                if timeout < TIMEOUT_S:             # the round's time cut it short: no try, the next round reads it
                    return Log(page, None, OUT_OF_TIME, later=True)
                why = self.down = "连不上 Jenkins：%s" % " ".join(str(exc).split())[:200]
        tries = (had.tries if had is not None else 0) + 1
        if raw is not None and done:
            got = Got(raw, "", tries, now)
        elif raw is not None or running:        # the build runs on: this read of it, or the last one when this failed
            since = had.running if running else now
            got = Got(raw if raw is not None else had.raw, "", tries, now, since)
            if now - since < WAIT_S:
                self._keep(build, got)
                return Log(page, None, RUNNING, later=True)
            got = replace(got, why=UNFINISHED, running=None)
        else:
            if tries >= MAX_TRIES:
                why += "（试了 %d 次都没读到，不再读）" % tries
            got = Got(None, why, tries, now)
        self._keep(build, got)
        return Log(page, got.raw, got.why)

    def _keep(self, build: str, got: Got) -> None:
        self.got.pop(build, None)
        self.got[build] = got
        while len(self.got) > KEEP:
            self.got.pop(next(iter(self.got)))
