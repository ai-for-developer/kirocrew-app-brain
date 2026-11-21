"""What the app shows that an agent or a tool wrote goes through a scrubber first: KiroCrew's own when it has one, else
basic_scrub (a password, token or key written out, and the shapes of AWS, OpenAI-style, GitHub and GitLab tokens).
scrub_tree runs it on the free-text fields of a page's answer (SCRUB_KEYS) and leaves ids, paths and numbers alone."""

from __future__ import annotations

import re
from typing import Any, Callable

_SECRETS = re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*|bearer\s+)\S+"
                      r"|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}")


def basic_scrub(text: str) -> str:
    return _SECRETS.sub(lambda m: (m.group(1) or "") + "[已隐去]", text)


SCRUB_KEYS = ("text", "why", "question", "label", "detail", "status", "next", "open_questions", "output", "reasons",
              "findings", "review", "blocking", "error", "session_error", "notes", "warnings", "problems", "summary",
              "first", "todo", "raw", "doing", "facts", "title", "name", "checks", "note", "subject", "sub", "push_why",
              "push_cmd", "reply", "task", "ticker", "why_not", "also", "cause", "missing", "here", "do")


def scrub_tree(obj: Any, scrub: Callable[[str], str], key: str = "") -> Any:
    """Agent- and tool-written text on its way to the page goes through the scrubber (KiroCrew's own when it has one):
    decisions, Session notes, questions, timeline lines, command output. Ids, paths and numbers are left alone."""
    if isinstance(obj, dict):
        return {k: scrub_tree(v, scrub, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_tree(v, scrub, key) for v in obj]
    if isinstance(obj, str) and key in SCRUB_KEYS and obj:
        return scrub(obj)
    return obj
