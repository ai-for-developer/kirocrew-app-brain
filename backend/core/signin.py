"""A GitLab behind single sign-on, for a client kept a long time: the team runner's (team/bridgehost.py) and the deploys
reads' (deploys_view.Clients).

brain-kit's ``rest.GitLab`` asks the sign-in helper for a transport once, when the client is made (its
``rest.local_transport().transport_for(base)``), and a transport may hold the sign-in it was made with. When that
sign-in ends, every call fails with the helper's own "session expired, sign in again" or with a sign-in page in place of
the API (brain-kit's words for it say "sign in again"); signing in again on this machine does not reach a client that
keeps its transport. ``Renewing`` holds the client and says which call failed that way (``expired``); its owner calls
``renew()`` at its next turn: the helper's ``refresh()`` when it has one, then a new client made the way the first one
was, so brain-kit asks the helper for a new transport. A helper whose transport reads the sign-in again on every call
needs none of this, and renewing does it no harm. Nothing here knows where a helper keeps its sign-in.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import bk

# the sign-in helper's "session expired / sign in again", and brain-kit's own words for a sign-in page where the API's
# JSON should be (rest.GitLab.page_hint, with and without a helper in use)
EXPIRED_RE = re.compile(r"sign[- ]in page|sign in again|log ?in again|(?:session|sign[- ]in|login) (?:has )?expired",
                        re.I)


def expired(exc: BaseException) -> bool:
    """Whether *exc* is GitLab's sign-in ending: signing in again on this machine is what helps."""
    return bool(EXPIRED_RE.search(str(exc)))


class Renewing:
    """The client *make* makes (brain-kit's rest.GitLab), as its owner keeps it: every attribute is the current
    client's. A method call that fails because the sign-in ended keeps its words in ``expired`` (a call that goes
    through clears it); ``renew()`` makes the client again."""

    def __init__(self, make: Callable[[], Any]) -> None:
        self._make = make
        self._client = make()
        self.expired = ""

    def renew(self) -> None:
        """The sign-in helper's refresh(), when it offers one, then a new client: brain-kit asks the helper for its
        transport again. What goes wrong (a helper that cannot be loaded, or says to sign in) is raised and the old
        client stays."""
        helper = bk.m("rest").local_transport()
        refresh = getattr(helper, "refresh", None)
        if callable(refresh):
            refresh()
        self._client = self._make()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                out = attr(*args, **kwargs)
            except Exception as exc:
                if expired(exc):
                    self.expired = " ".join(str(exc).split())[:300]
                raise
            self.expired = ""
            return out
        return call
