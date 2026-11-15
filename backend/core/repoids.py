"""The listed repos' ids (r1, r2 …): given once per repo path and kept, so the records keyed by an id (sessions,
events, credits, big.json, the page's links) stay with their repo when another one is removed (UI-19: ids were list
positions). ``repo_ids.json`` in the app's data dir: {"ids": {path: id}, "next": n}. A removed path keeps its id and
gets it back when it is added again; an id is never given to another path, also by another app process on the same
data dir (the file is read again under a lock before a new id is given). An app from before has no file yet: its ids
were the list's positions, so those are the first ones given (the records on disk keep pointing at their repo).

Every write also writes ``repo_ids.json.bak``, the last good copy: a file that is damaged or gone is read from it and
written back. Without a good copy the file is kept as it is and said (``problem``), and no id is given (list positions
would attach records to the wrong repo) until 按记录重建编号 rebuilds it from the ids the records carry (from_records).
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

DAMAGED = ("仓库编号的记录 %s 坏了，也没有好的备份：这次先不认列表里的仓库，免得记录记到别的仓库上。"
           "在设置里点「按记录重建编号」")


def _read(path: Path) -> tuple[dict[str, str], int] | None:
    """(ids, next) of one file, None when it is not there; ValueError when it cannot be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = {str(p): str(i) for p, i in data["ids"].items() if REPO_ID_RE.match(str(i)) and i != "runner"}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(str(exc)) from None
    n = data.get("next")
    n = n if isinstance(n, int) and not isinstance(n, bool) else 1
    return ids, max([n] + [int(i[1:]) + 1 for i in ids.values()])


def from_records(listed: list[str], keys_of: dict[str, set[str] | None], found: dict[str, set[str]]
                 ) -> tuple[dict[str, str], int, list[str]]:
    """Ids for the listed paths from the records on disk. *found*: {id: the requirement keys with records under it};
    *keys_of*: {path: the requirement keys that repo has, None when it cannot be read}. A path keeps an id when it is
    the one listed repo with any of that id's keys and that id the one whose keys it has; every other path gets a new
    id above all the ids found, so no record is read as another repo's (its own old ones just do not show here).
    -> (ids, next, the paths given a new id)."""
    owners = {rid: {p for p in listed if keys & (keys_of.get(p) or set())} for rid, keys in found.items()}
    ids: dict[str, str] = {}
    for p in listed:
        mine = [rid for rid, hit in owners.items() if p in hit]
        if len(mine) == 1 and owners[mine[0]] == {p}:
            ids[p] = mine[0]
    n = max([1] + [int(i[1:]) + 1 for i in found])
    fresh = [p for p in listed if p not in ids]
    for p in fresh:
        ids[p], n = "r%d" % n, n + 1
    return ids, n, fresh


class RepoIds:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.copy = self.path.with_name(self.path.name + ".bak")
        self._lock = threading.Lock()
        self._ids, self._next, self.problem, self._restore = self._load()

    def _load(self) -> tuple[dict[str, str] | None, int, str, bool]:
        """(ids, next, problem, restore): the file's, else its last good copy's (*restore*: write it back); no ids and
        no problem when neither is there yet; no ids and the problem when one is damaged and there is no good one."""
        damaged = False
        for i, f in enumerate((self.path, self.copy)):
            try:
                got = _read(f)
            except ValueError:
                damaged = True
                continue
            if got is not None:
                return got[0], got[1], "", i == 1
        return None, 1, DAMAGED % self.path if damaged else "", False

    def of(self, listed: list[str]) -> dict[str, str]:
        """{path: id} for the listed paths, in their order; a path seen for the first time gets the next id (saved).
        {} while the file is damaged without a good copy."""
        with self._lock:
            if not self.problem and (self._ids is None or self._restore or any(p not in self._ids for p in listed)):
                with self._file_lock():
                    ids, n, self.problem, _ = self._load()      # another app on this data dir may have given ids
                    if not self.problem:
                        ids = ids if ids is not None else {p: "r%d" % k for k, p in enumerate(listed, 1)}
                        n = max([n] + [int(i[1:]) + 1 for i in ids.values()])
                        for p in listed:
                            if p not in ids:
                                ids[p], n = "r%d" % n, n + 1
                        self._save(ids, n)
                        self._ids, self._next, self._restore = ids, n, False
            if self.problem:
                return {}
            return {p: self._ids[p] for p in listed}

    def rebuild(self, ids: dict[str, str], n: int) -> None:
        """按记录重建编号: *ids* and *next* (from_records) over a damaged file, used from now on."""
        with self._lock, self._file_lock():
            self._save(ids, n)
            self._ids, self._next, self.problem, self._restore = dict(ids), n, "", False

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
        text = json.dumps({"ids": ids, "next": n}, ensure_ascii=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for f in (self.path, self.copy):
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".repo_ids.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, f)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        dfd = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
