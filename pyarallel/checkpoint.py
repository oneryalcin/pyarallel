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
import inspect
import pickle
import sqlite3
import types
from pathlib import Path
from typing import Any


class CheckpointError(RuntimeError):
    """A checkpoint file cannot be used or written.

    Raised when resuming with a different function than the one that
    created the file (stale-reuse protection, fails closed), when the
    mapped callable carries state the checkpoint cannot see (bound
    methods, callable objects), or when a completed result cannot be
    persisted (the checkpoint contract would silently break, so the run
    stops loudly instead).
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


_PLAIN_IMMUTABLE = (str, bytes, int, float, complex, bool, type(None))


def _state_token(value: Any) -> str:
    """Stable identity token for one piece of captured state.

    Plain immutable values — the config-drift case: a model name, a
    ``factor=3`` — contribute their full repr, so changing them invalidates
    the checkpoint. Everything else (live clients, sessions, mutable
    counters) contributes only its type: object reprs embed memory
    addresses and mutable containers legitimately change during a run —
    either would make the signature differ on every rerun and turn the
    fail-closed guard into a false-positive machine.
    """
    if isinstance(value, _PLAIN_IMMUTABLE):
        return repr(value)
    if isinstance(value, (tuple, frozenset)):
        inner = [_state_token(v) for v in value]
        if isinstance(value, frozenset):
            inner.sort()
        return f"{type(value).__name__}({','.join(inner)})"
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _reject_stateful(kind: str) -> CheckpointError:
    return CheckpointError(
        f"checkpoint= cannot bind to a {kind} — its instance state shapes "
        "the results but is invisible to the checkpoint, so a state change "
        "between runs would silently serve stale rows. Wrap the call in a "
        "module-level function instead."
    )


def _task_signature(fn: Any) -> str:
    """Stable identity for the mapped callable.

    ``module.qualname`` plus a digest of the code (including constants),
    default argument values, closure cell contents, and any
    ``functools.partial`` arguments — visible state joins the identity, so
    an edited function or changed captured config invalidates the
    checkpoint instead of silently reusing its predecessor's results.

    Live objects in that state contribute only their type (see
    ``_state_token``); config hidden *inside* them is invisible — delete
    the checkpoint file when it changes. Callables whose entire calling
    state is an opaque instance (bound methods, callable objects) are
    rejected: there is no function code to anchor an honest identity.
    """
    state = hashlib.sha256()
    while isinstance(fn, functools.partial):
        for arg in fn.args:
            state.update(_state_token(arg).encode())
        for key, value in sorted(fn.keywords.items()):
            state.update(f"{key}={_state_token(value)}".encode())
        fn = fn.func

    if inspect.ismethod(fn):
        raise _reject_stateful("bound method")
    self_obj = getattr(fn, "__self__", None)
    if self_obj is not None and not isinstance(self_obj, types.ModuleType):
        raise _reject_stateful("bound method")

    code = getattr(fn, "__code__", None)
    if code is not None:
        state.update(_code_digest(code))
        for default in getattr(fn, "__defaults__", None) or ():
            state.update(_state_token(default).encode())
        for key, value in sorted((getattr(fn, "__kwdefaults__", None) or {}).items()):
            state.update(f"{key}={_state_token(value)}".encode())
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                state.update(_state_token(cell.cell_contents).encode())
            except ValueError:  # empty cell (e.g. not-yet-bound recursive name)
                state.update(b"<empty-cell>")
    elif not isinstance(fn, (types.BuiltinFunctionType, type)):
        # No code object, not a builtin or a class: a callable instance.
        raise _reject_stateful("callable object")

    module = getattr(fn, "__module__", None) or "?"
    qualname = getattr(fn, "__qualname__", None) or type(fn).__name__
    return f"{module}.{qualname}:{state.hexdigest()[:16]}"


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
