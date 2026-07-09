"""Checkpoint store: resumable ``parallel_map`` runs.

SQLite-backed — stdlib only, zero dependencies preserved. Rows are keyed
by a type-tagged key (positional index by default, a user key with
``checkpoint_key=``) plus a fingerprint of the pickled item, and the whole
file is bound to the identity of the mapped callable (name + bytecode):
resuming with a different function fails closed instead of silently
serving another computation's results.

Schema v2 (the only schema): ``results(key TEXT PRIMARY KEY, fingerprint,
value)`` with keys encoded ``i:<decimal>`` / ``s:<text>`` / ``b:<base64>``
— type-tagged and reversible, so ``1``, ``"1"``, and ``b"1"`` are three
distinct rows. Files without ``schema_version = '2'`` (including v0.4-era
positional files) fail closed with instructions to delete: one rule, no
silent migration.

SECURITY: checkpoint rows are pickle. Loading a checkpoint file executes
whatever its pickle streams contain — treat checkpoint files like code,
not data. Never resume from a file you didn't create; new files are
created ``0o600`` (POSIX), and a directory writable by others is not a
safe place for one.

Constraints (documented, not hidden): items and results must be picklable;
a result that cannot be checkpointed aborts the run with
``CheckpointError`` rather than mislabeling a successful item. Positional
rows mean reordering or inserting input items shifts indices, and the
fingerprint then forces recomputation of every shifted item — use
``checkpoint_key=`` when inputs evolve. Items whose pickle is not
deterministic (e.g. containing sets) fingerprint differently across runs
and are safely recomputed.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import hashlib
import inspect
import os
import pickle
import sqlite3
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _create_secure(path: str | Path) -> None:
    """Create a new checkpoint file with 0o600 before SQLite touches it.

    A checkpoint contains pickle — anyone who can write it can execute
    code in the resuming process, and anyone who can read it sees the
    results. Creating restrictively (not chmod-after-open) closes the
    creation-time exposure window; ``O_EXCL`` also refuses to follow a
    symlink planted at the path. An *existing* file is left exactly as
    found — its permissions may be intentional. On Windows the mode is
    advisory; rely on directory ACLs there.

    This does not defend against a checkpoint directory writable by
    others (documented: keep checkpoints out of /tmp-like locations) —
    SQLite's -wal/-shm sidecars inherit the database file's permissions.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    # An existing file is reused as-is; its permissions are never touched.
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, flags, 0o600))


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


_SCHEMA_VERSION = "2"


def _encode_key(key: Any) -> str:
    """Type-tagged, reversible row-key encoding.

    ``1``, ``"1"``, and ``b"1"`` must be three distinct rows — plain text
    normalization would collide them and silently serve the wrong result.
    """
    if isinstance(key, bool) or not isinstance(key, (int, str, bytes)):
        raise CheckpointError(
            f"checkpoint_key must return str, int, or bytes, got {type(key).__name__}"
        )
    if isinstance(key, int):
        return f"i:{key}"
    if isinstance(key, str):
        return f"s:{key}"
    return f"b:{base64.b64encode(key).decode('ascii')}"


class _CheckpointStore:
    """One SQLite file of completed ``(key, fingerprint) -> value`` rows.

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
        _create_secure(path)
        self._conn = sqlite3.connect(path)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta ("
                " key TEXT PRIMARY KEY,"
                " value TEXT NOT NULL)"
            )
            version = self._meta("schema_version")
            signature_row = self._meta("task_signature")
        except sqlite3.Error as exc:
            # A truncated write, disk corruption, or a non-database file
            # must fail closed with the same exception type as every
            # other unusable checkpoint — not leak sqlite3 internals.
            self._conn.close()
            raise CheckpointError(
                f"Checkpoint {str(path)!r} is not a usable checkpoint "
                f"database ({exc}). Delete the file to start fresh."
            ) from exc
        if version is None and signature_row is None:
            # Fresh file — but only if nothing else already lives here. A
            # results table of any other shape (interrupted write,
            # foreign file) must fail closed, not be silently adopted.
            stale = self._conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name = 'results'"
            ).fetchone()
            if stale is not None:
                self._conn.close()
                raise CheckpointError(
                    f"Checkpoint {str(path)!r} contains an unrecognized "
                    "results table with no schema version. Delete the "
                    "file to start fresh."
                )
            self._conn.execute(
                "CREATE TABLE results ("
                " key TEXT PRIMARY KEY,"
                " fingerprint BLOB NOT NULL,"
                " value BLOB NOT NULL)"
            )
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES"
                " ('schema_version', ?), ('task_signature', ?)",
                (_SCHEMA_VERSION, signature),
            )
        elif version != _SCHEMA_VERSION:
            # One rule, no silent migration: anything not stamped v2 —
            # including v0.4-era positional files — fails closed.
            self._conn.close()
            raise CheckpointError(
                f"Checkpoint {str(path)!r} uses an unsupported schema "
                f"(found {version!r}, need {_SCHEMA_VERSION!r}). It was "
                "written by an older pyarallel — delete the file to start "
                "fresh."
            )
        elif signature_row != signature:
            self._conn.close()
            raise CheckpointError(
                f"Checkpoint {str(path)!r} was created by a different function "
                f"({signature_row}, now {signature}). Refusing to reuse its "
                "results — delete the file or use a different checkpoint path."
            )
        self._conn.commit()

    def _meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row[0])

    @staticmethod
    def fingerprint(item: Any) -> bytes:
        """Content hash of *item* used to detect changed inputs."""
        return hashlib.sha256(pickle.dumps(item)).digest()

    def get(self, key: str, fingerprint: bytes) -> tuple[Any] | None:
        """Return ``(value,)`` for a matching row, else ``None``.

        The 1-tuple wrapper distinguishes a stored ``None`` from a miss.
        """
        row = self._conn.execute(
            "SELECT fingerprint, value FROM results WHERE key = ?", (key,)
        ).fetchone()
        if row is None or row[0] != fingerprint:
            return None
        try:
            return (pickle.loads(row[1]),)
        except Exception as exc:
            # A corrupted value blob must fail like every other unusable
            # checkpoint — actionably — not leak a raw unpickling error.
            raise CheckpointError(
                f"Checkpoint row {key!r} is corrupted and cannot be read "
                f"({exc}). Delete the file to start fresh."
            ) from exc

    def put(self, key: str, fingerprint: bytes, value: Any) -> None:
        """Record a completed item.

        Raises ``CheckpointError`` when the value cannot be pickled or the
        write fails — the item's computation succeeded, but its result
        cannot be resumed from, so the run must stop rather than pretend.
        """
        try:
            blob = pickle.dumps(value)
            self._conn.execute(
                "INSERT OR REPLACE INTO results (key, fingerprint, value)"
                " VALUES (?, ?, ?)",
                (key, fingerprint, blob),
            )
            self._conn.commit()
        except Exception as exc:
            raise CheckpointError(
                f"Failed to checkpoint result for row {key!r}: {exc}"
            ) from exc

    def close(self) -> None:
        self._conn.close()


class _RunCheckpoint:
    """Per-run view of a checkpoint store, as the engines consume it.

    Owns row-key computation (positional ``i:<idx>`` or the user's
    ``checkpoint_key``), duplicate-key detection, and the idx → (key,
    fingerprint) bookkeeping between lookup and put. Key-function errors
    propagate at lookup time — fail loud at submit, not at resume.
    """

    __slots__ = ("_store", "_key_fn", "_seen", "_rows")

    def __init__(
        self,
        store: _CheckpointStore,
        key_fn: Callable[[Any], str | int | bytes] | None,
    ) -> None:
        self._store = store
        self._key_fn = key_fn
        self._seen: set[str] = set()
        self._rows: dict[int, tuple[str, bytes]] = {}

    def lookup(self, idx: int, item: Any) -> tuple[Any] | None:
        """Cached ``(value,)`` for *item*, else ``None`` (and remember the
        row so a later ``put(idx, ...)`` writes to the right place)."""
        if self._key_fn is None:
            key = f"i:{idx}"
        else:
            key = _encode_key(self._key_fn(item))
            if key in self._seen:
                raise CheckpointError(
                    f"duplicate checkpoint_key {key!r} — two input items "
                    "map to the same row, which would silently corrupt "
                    "resume. Keys must be unique per run."
                )
            self._seen.add(key)
        fingerprint = _CheckpointStore.fingerprint(item)
        cached = self._store.get(key, fingerprint)
        if cached is None:
            self._rows[idx] = (key, fingerprint)
        return cached

    def put(self, idx: int, value: Any) -> None:
        key, fingerprint = self._rows.pop(idx)
        self._store.put(key, fingerprint, value)

    def close(self) -> None:
        self._store.close()


def _open_checkpoint(
    path: str | Path,
    fn: Any,
    key_fn: Callable[[Any], str | int | bytes] | None,
) -> _RunCheckpoint:
    """Open (or create) a checkpoint file for one run of *fn*."""
    return _RunCheckpoint(_CheckpointStore(path, _task_signature(fn)), key_fn)
