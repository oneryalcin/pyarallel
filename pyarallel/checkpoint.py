"""Checkpoint store: resumable ``parallel_map`` runs.

SQLite-backed — stdlib only, zero dependencies preserved. Rows are keyed by
item index plus a fingerprint of the pickled item, so a changed input at
the same position is recomputed rather than served stale. Failures are
never checkpointed; a resumed run retries them.

Constraints (documented, not hidden): items and results must be picklable.
Items whose pickle is not deterministic (e.g. containing sets) fingerprint
differently across runs and are safely recomputed.
"""

from __future__ import annotations

import hashlib
import pickle
import sqlite3
from pathlib import Path
from typing import Any


class _CheckpointStore:
    """One SQLite file of completed ``(index, fingerprint) -> value`` rows.

    All access happens from the thread that created the store (the sync
    submit/collect loop, or the event loop thread), so the default sqlite3
    same-thread check stands as a correctness assertion. Writes commit per
    item — a crash loses at most the in-flight results.
    """

    __slots__ = ("_conn",)

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS results ("
            " idx INTEGER PRIMARY KEY,"
            " fingerprint BLOB NOT NULL,"
            " value BLOB NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def fingerprint(item: Any) -> bytes:
        """Content hash of *item* used to detect changed inputs."""
        return hashlib.sha256(pickle.dumps(item)).digest()

    def get(self, idx: int, fingerprint: bytes) -> tuple[Any] | None:
        """Return ``(value,)`` for a matching row, else ``None``.

        The 1-tuple wrapper distinguishes a stored ``None`` from a miss.
        """
        row = self._conn.execute(
            "SELECT fingerprint, value FROM results WHERE idx = ?", (idx,)
        ).fetchone()
        if row is None or row[0] != fingerprint:
            return None
        return (pickle.loads(row[1]),)

    def put(self, idx: int, fingerprint: bytes, value: Any) -> None:
        """Record a completed item. Raises if *value* cannot be pickled."""
        self._conn.execute(
            "INSERT OR REPLACE INTO results (idx, fingerprint, value)"
            " VALUES (?, ?, ?)",
            (idx, fingerprint, pickle.dumps(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
