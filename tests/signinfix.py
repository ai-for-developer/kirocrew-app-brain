"""A sign-in helper for a GitLab behind single sign-on, offline: ~/.brain/local_transport.py (where brain-kit's
rest.local_transport finds a person's helper) handing out transports to a fake GitLab, and a sign-in that ends
(expire) and is made again (sign_in_again). The helper comes in the three ways such a helper is written:

  new       each transport keeps the sign-in there was when it was made: after signing in again, a new one is needed
  refresh   transport_for hands out the one transport it keeps; only the helper's refresh() reads the sign-in again
  per-call  its transport reads the sign-in on every call

A call on an ended sign-in gets the sign-in page in place of the API (HTTP 401, an HTML page), or with *says* the
helper raises brain-kit's BrainError saying the session expired, as a helper that checks where it ended up does.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from appkit import mod

SOURCE = "import signinfix as _f\ntransport_for = _f.HELPER.transport_for\n"
REFRESH = "refresh = _f.HELPER.refresh\n"
PAGE = b"<!doctype html><html><head><title>Sign in</title></head><body>Sign in to continue</body></html>"


class Transport:
    """One transport of the helper's: *session* is the sign-in it holds (None: the one there is at each call)."""

    def __init__(self, helper: "SignIn", session: int | None) -> None:
        self.helper, self.session = helper, session

    def __call__(self, method: str, url: str, headers: Any, body: Any) -> tuple[int, bytes]:
        h = self.helper
        held = h.now if self.session is None else self.session
        h.calls.append((held, method, url))
        if held not in h.valid:
            if h.says:
                raise mod("bk").m("common").BrainError("single sign-on session expired for %s: sign in again"
                                                       % url.split("/api/")[0])
            return 401, PAGE
        return h.gitlab(method, url, headers, body)


class SignIn:
    def __init__(self, gitlab: Any, kind: str, says: bool = False) -> None:
        self.gitlab, self.kind, self.says = gitlab, kind, says
        self.now, self.valid = 1, {1}
        self.made: list[Transport] = []
        self.calls: list[tuple[int, str, str]] = []

    def transport_for(self, base: str) -> Transport:
        if self.kind == "refresh" and self.made:
            return self.made[0]
        t = Transport(self, None if self.kind == "per-call" else self.now)
        self.made.append(t)
        return t

    def refresh(self) -> None:
        for t in self.made:
            t.session = self.now

    def expire(self) -> None:
        """The single sign-on session ends (it timed out)."""
        self.valid.discard(self.now)

    def sign_in_again(self) -> None:
        """The person signs in again on this machine: a new session."""
        self.now += 1
        self.valid.add(self.now)


def install(gitlab: Any, kind: str, says: bool = False) -> SignIn:
    """The helper of *kind* at ~/.brain/local_transport.py (HOME is the test's own), its transports in front of
    *gitlab* (a fake GitLab: method, url, headers, body -> status, body)."""
    global HELPER
    HELPER = SignIn(gitlab, kind, says)
    path = Path(os.environ["HOME"]) / ".brain" / "local_transport.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE + (REFRESH if kind == "refresh" else ""), encoding="utf-8")
    return HELPER


HELPER: SignIn | None = None        # the one install() made: the helper file hands out its transports
