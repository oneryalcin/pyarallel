"""Checkpoint store: resumable ``parallel_map`` runs.

SQLite-backed — stdlib only, zero dependencies preserved. Rows are keyed by
item index plus a fingerprint of the pickled item, and the whole file is
bound to the identity of the mapped callable (name + bytecode): resuming
with a different function fails closed instead of silently serving another
computation's results.

Constraints (documented, not hidden): items and results must be picklable;
a result that cannot be checkpointed aborts the run with
``CheckpointError`` rather than mislabeling a successful item. Rows are
positional — reordering or inserting input items shifts indices, and the
fingerprint then forces recomputation of every shifted item. Items whose
pickle is not deterministic (e.g. containing sets) fingerprint differently
across runs and are safely recomputed.
"""

from __future__ import annotations

import functools
import hashlib
import pickle
import sqlite3
from pathlib import Path
from typing import Any


class CheckpointError(RuntimeError):
    """A checkpoint file cannot be used or written.

    Raised when resuming with a different function than the one that
    created the file (stale-reuse protection, fails closed), or when a
    completed result cannot be persisted (the checkpoint contract would
    silently break, so the run stops loudly instead).
    """


def _code_digest(code: Any) -> bytes:
    """Deterministic digest of a code object: bytecode, constants (recursing
    into nested code objects), and referenced names. ``co_code`` alone is
    not enough — ``x * 2`` and ``x * 3`` share identical bytecode and
    differ only in ``co_consts``.
    """
    h = hashlib.sha256(code.co_code)
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            h.update(_code_digest(const))
        else:
            h.update(repr(const).encode())
    h.update(repr(code.co_names).encode())
    return h.digest()


def _task_signature(fn: Any) -> str:
    """Stable identity for the mapped callable.

    ``module.qualname`` plus a code digest when available, so an edited
    function invalidates the checkpoint instead of silently reusing its
    predecessor's results. Objects without ``__code__`` (builtins, partials
    over C functions) fall back to name-only identity.
    """
    fn = getattr(fn, "__func__", fn)
    while isinstance(fn, functools.partial):
        fn = fn.func
    module = getattr(fn, "__module__", None) or "?"
    qualname = getattr(fn, "__qualname__", None) or type(fn).__name__
    code = getattr(fn, "__code__", None)
    digest = _code_digest(code).hex()[:16] if code is not None else ""
    return f"{module}.{qualname}:{digest}"


class _CheckpointStore:
    """One SQLite file of completed ``(index, fingerprint) -> value`` rows.

    All access happens from the thread that created the store (the sync
    submit/collect loop, or the event loop thread), so the default sqlite3
    same-thread check stands as a correctness assertion. Writes commit per
    item — a crash loses at most the in-flight results. WAL mode plus a
    busy timeout keep an accidental second reader/writer from failing
    immediately, though sharing one file between concurrent runs is not a
    supported pattern.
    """

    __slots__ = ("_conn",)

    def __init__(self, path: str | Path, signature: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            " key TEXT PRIMARY KEY,"
            " value TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS results ("
            " idx INTEGER PRIMARY KEY,"
            " fingerprint BLOB NOT NULL,"
            " value BLOB NOT NULL)"
        )
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'task_signature'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('task_signature', ?)",
                (signature,),
            )
        elif row[0] != signature:
            self._conn.close()
            raise CheckpointError(
                f"Checkpoint {str(path)!r} was created by a different function "
                f"({row[0]}, now {signature}). Refusing to reuse its results — "
                "delete the file or use a different checkpoint path."
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
        """Record a completed item.

        Raises ``CheckpointError`` when the value cannot be pickled or the
        write fails — the item's computation succeeded, but its result
        cannot be resumed from, so the run must stop rather than pretend.
        """
        try:
            blob = pickle.dumps(value)
            self._conn.execute(
                "INSERT OR REPLACE INTO results (idx, fingerprint, value)"
                " VALUES (?, ?, ?)",
                (idx, fingerprint, blob),
            )
            self._conn.commit()
        except Exception as exc:
            raise CheckpointError(
                f"Failed to checkpoint result for item {idx}: {exc}"
            ) from exc

    def close(self) -> None:
        self._conn.close()
