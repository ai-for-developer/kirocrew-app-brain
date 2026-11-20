"""Jenkins, read for MR 巡检 (patrol.py): the console of a build that reported to GitLab as a commit status (Jenkins'
GitLab plugin). GitLab has no job or log of such a check, only its name, its status and its link (target_url).

bridge.json's optional "jenkins": {"base", "user", "token_file"} names it. *base* is the address this machine reaches
Jenkins at, *user* a read-only user (Overall/Read and Job/Read: Jenkins answers 403 to no one), *token_file* its API
token or password, in a file only its owner can read. A target_url names the build at Jenkins' own configured address,
which this machine may not reach (a name on Jenkins' docker network, say): its /job/<…>/<number>/ is read at *base*
instead. A build whose log is not read (no Jenkins named, Jenkins refusing, a link that is no Jenkins build) says why
in one line.
"""

from __future__ import annotations

import base64
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import bk

# one /job/<name> or more (folders, multibranch), then the build's number: what a Jenkins build's address holds
BUILD_RE = re.compile(r"((?:/job/[^/]+)+)/(\d+)(?:/|$)")
NOT_SET = "运行机没配 Jenkins（bridge.json 里没有 jenkins）"
NOT_A_BUILD = "这个链接不是 Jenkins 构建的地址"


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


@dataclass(frozen=True)
class Jenkins:
    """The Jenkins bridge.json names (Jenkins.of). *why*: why no log is read at all ("": one may be)."""
    base: str = ""
    user: str = ""
    auth: str = field(default="", repr=False)
    why: str = NOT_SET
    transport: Any = None

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

    def console(self, target_url: str) -> tuple[str, bytes | None, str]:
        """(the build's page, its consoleText, "") for the Jenkins build *target_url* links to, both at *base*;
        (its page, None, why) when its log is not read. The page is *target_url* itself unless *base* is known."""
        build = build_path(target_url)
        if build is None:
            return target_url, None, NOT_A_BUILD
        page = self.base + build + "/" if self.base else target_url
        if self.why:
            return page, None, self.why
        rest, common = bk.m("rest"), bk.m("common")
        client = rest.Rest(self.base, {"Authorization": self.auth} if self.auth else {}, self.transport)
        try:
            return page, client.call("GET", build + "/consoleText", raw=True) or b"", ""
        except rest.HttpError as exc:
            return page, None, refused(exc.status, self.user)
        except common.BrainError as exc:
            return page, None, "连不上 Jenkins：%s" % " ".join(str(exc).split())[:200]
