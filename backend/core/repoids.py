"""The listed repos' ids (r1, r2 …): given once per repo path and kept, so the records keyed by an id (sessions,
events, credits, big.json, the page's links) stay with their repo when another one is removed (UI-19: ids were list
positions). ``repo_ids.json`` in the app's data dir: {"ids": {path: id}, "next": n}. A removed path keeps its id and
gets it back when it is added again; an id is never given to another path. An app from before has no file yet: its
ids were the list's positions, so those are the first ones given (the records on disk keep pointing at their repo).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .constants import REPO_ID_RE


class RepoIds:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ids, self._next = self._load()

    def _load(self) -> tuple[dict[str, str] | None, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ids = {str(p): str(i) for p, i in data["ids"].items() if REPO_ID_RE.match(str(i)) and i != "runner"}
            return ids, max([int(data["next"])] + [int(i[1:]) + 1 for i in ids.values()])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None, 1

    def of(self, listed: list[str]) -> dict[str, str]:
        """{path: id} for the listed paths, in their order; a path seen for the first time gets the next id (saved)."""
        with self._lock:
            ids = dict(self._ids) if self._ids is not None else {p: "r%d" % n for n, p in enumerate(listed, 1)}
            n = max([self._next] + [int(i[1:]) + 1 for i in ids.values()])
            for p in listed:
                if p not in ids:
                    ids[p], n = "r%d" % n, n + 1
            if ids != self._ids or n != self._next:
                self._save(ids, n)
                self._ids, self._next = ids, n
            return {p: ids[p] for p in listed}

    def _save(self, ids: dict[str, str], n: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".repo_ids.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"ids": ids, "next": n}, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
