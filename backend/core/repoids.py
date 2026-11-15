"""The listed repos' ids (r1, r2 …): given once per repo path and kept, so the records keyed by an id (sessions,
events, credits, big.json, the page's links) stay with their repo when another one is removed (UI-19: ids were list
positions). ``repo_ids.json`` in the app's data dir: {"ids": {path: id}, "next": n}. A removed path keeps its id and
gets it back when it is added again; an id is never given to another path, also by another app process on the same
data dir (the file is read again under a lock before a new id is given). An app from before has no file yet: its ids
were the list's positions, so those are the first ones given (the records on disk keep pointing at their repo). A file
that cannot be read is kept as it is and said (``problem``): the ids are the list's positions for this run, never saved
over it.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterator

from .constants import REPO_ID_RE

try:
    import fcntl
except ImportError:      # no flock here: one app process per data dir
    fcntl = None  # type: ignore[assignment]

DAMAGED = "仓库编号的记录 %s 坏了，读不出来：这次先按设置里的顺序编号，那个文件没动。修好它，再重启 Brain 交付（KiroCrew 的「应用」里先停再启动）"


class RepoIds:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ids, self._next, self.problem = self._load()

    def _load(self) -> tuple[dict[str, str] | None, int, str]:
        """(ids, next, problem) as on disk; no ids and no problem when there is no file yet."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ids = {str(p): str(i) for p, i in data["ids"].items() if REPO_ID_RE.match(str(i)) and i != "runner"}
        except FileNotFoundError:
            return None, 1, ""
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None, 1, DAMAGED % self.path
        n = data.get("next")
        n = n if isinstance(n, int) and not isinstance(n, bool) else 1
        return ids, max([n] + [int(i[1:]) + 1 for i in ids.values()]), ""

    def of(self, listed: list[str]) -> dict[str, str]:
        """{path: id} for the listed paths, in their order; a path seen for the first time gets the next id (saved)."""
        with self._lock:
            if not self.problem and (self._ids is None or any(p not in self._ids for p in listed)):
                with self._file_lock():
                    ids, n, self.problem = self._load()      # another app on this data dir may have given ids
                    if not self.problem:
                        ids = ids if ids is not None else {p: "r%d" % k for k, p in enumerate(listed, 1)}
                        n = max([n] + [int(i[1:]) + 1 for i in ids.values()])
                        for p in listed:
                            if p not in ids:
                                ids[p], n = "r%d" % n, n + 1
                        self._save(ids, n)
                        self._ids, self._next = ids, n
            if self.problem:
                return {p: "r%d" % k for k, p in enumerate(listed, 1)}
            return {p: self._ids[p] for p in listed}

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        if fcntl is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _save(self, ids: dict[str, str], n: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".repo_ids.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"ids": ids, "next": n}, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        dfd = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
