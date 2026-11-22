"""A GitLab behind single sign-on, for a client kept a long time: the team runner's (team/bridgehost.py) and the deploys
reads' (deploys_view.Clients).

brain-kit's ``rest.GitLab`` asks the sign-in helper for a transport once, when the client is made (its
``rest.local_transport().transport_for(base)``), and a transport may hold the sign-in it was made with. When that
sign-in ends, every call fails with the helper's own "session expired, sign in again" or with a sign-in page in place of
the API (brain-kit's words for it say "sign in again"); signing in again on this machine does not reach a client that
keeps its transport. On a machine with no sign-in helper, brain-kit's words for that page say to put the helper's file
in place (``helper_missing``): signing in again does not help there. ``Renewing`` holds the client and says which call
failed either way (``expired``, ``no_helper``); its owner calls ``renew()`` at its next turn: the helper's ``refresh()``
when it has one, then a new client made the way the first one was, so brain-kit asks the helper (one put in place since,
too) for a new transport. A helper whose transport reads the sign-in again on every call needs none of this, and
renewing does it no harm. Nothing here knows where a helper keeps its sign-in.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import bk

# the sign-in helper's "session expired / sign in again", and brain-kit's own words for a sign-in page where the API's
# JSON should be (rest.GitLab.page_hint, with and without a helper in use)
EXPIRED_RE = re.compile(r"sign[- ]in page|sign in again|log ?in again|(?:session|sign[- ]in|login) (?:has )?expired",
                        re.I)
# brain-kit's words for that page when no sign-in helper is in use (rest.GitLab.page_hint): the helper's file is missing
NO_HELPER_RE = re.compile(r"needs its sign-in helper|put the helper file at", re.I)


def expired(exc: BaseException) -> bool:
    """Whether *exc* is GitLab's sign-in failing: it ended, and signing in again on this machine is what helps, or this
    machine has no sign-in helper (helper_missing). Either way a client made again uses what the machine has then."""
    return bool(EXPIRED_RE.search(str(exc)))


def helper_missing(exc: BaseException) -> bool:
    """Whether *exc* says this machine has no sign-in helper: putting its file in place is what helps."""
    return bool(NO_HELPER_RE.search(str(exc)))


class Renewing:
    """The client *make* makes (brain-kit's rest.GitLab), as its owner keeps it: every attribute is the current
    client's. A method call that fails because of the sign-in keeps its words in ``expired``, and ``no_helper`` says
    whether that is for want of a sign-in helper here (a call that goes through clears both); ``renew()`` makes the
    client again."""

    def __init__(self, make: Callable[[], Any]) -> None:
        self._make = make
        self._client = make()
        self.expired = ""
        self.no_helper = False

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
                    self.expired, self.no_helper = " ".join(str(exc).split())[:300], helper_missing(exc)
                raise
            self.expired, self.no_helper = "", False
            return out
        return call
