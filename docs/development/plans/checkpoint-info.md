# Read-only Checkpoint Inspection

## Goal

Add a small public API that answers operational questions about an existing
checkpoint before a rerun:

```python
info = checkpoint_info("run.ckpt")
```

It reports the schema version, semantic `checkpoint_version`, task signature,
completed row count, and primary database file size without loading any stored
result value. This is the next candidate selected from issue #32. It remains
Unreleased work and does not start a `0.11.0` release.

## Proposed Public Surface

In `pyarallel.checkpoint`:

```python
@dataclass(frozen=True, slots=True)
class CheckpointInfo:
    path: Path
    schema_version: str
    checkpoint_version: str | int | bytes | tuple[str | int | bytes, ...] | None
    task_signature: str
    completed: int
    size_bytes: int


def checkpoint_info(path: str | Path) -> CheckpointInfo: ...
```

Both names are re-exported from `pyarallel.__init__` and therefore join the
public API snapshot (`pyarallel/__init__.py:8-40`,
`tests/test_api_snapshot.py:28-94`).

`completed` means persisted rows in the current checkpoint, not successful
items in the original input and not a percentage. The checkpoint format does
not store the input total, failed items, unseen items, or a run status, so the
API must not manufacture those claims.

`path` is the supplied location normalized to a `Path`; it is not resolved to a
different target. `size_bytes` comes from the same initial `os.lstat()` receipt
used to validate the leaf as a regular file. It is the pre-open primary SQLite
file size, excludes transient `-wal`/`-shm` sidecars, and is not transactionally
coherent with a live writer.

## Read-only and Security Invariants

1. The function never creates a missing path. Missing files raise
   `FileNotFoundError` with the supplied path.
2. The function rejects symlinks, directories, and special files with
   `CheckpointError`, matching the checkpoint path safety boundary in
   `pyarallel/checkpoint.py:72-83`.
3. SQLite opens through a `file:` URI with `mode=ro&cache=private`, and the
   connection sets `PRAGMA query_only=ON`. Inspection never runs schema
   creation, migration, journal-mode changes, or logical database writes.
4. Inspection first validates `sqlite_master` and `PRAGMA table_info` for both
   objects. `meta` and `results` must be ordinary tables with the exact
   writer-compatible columns, declared types, primary-key positions, and
   nullability from `pyarallel/checkpoint.py:247-249` and
   `pyarallel/checkpoint.py:278-283`. A view or a v2-looking foreign table such
   as `results(x)` is rejected before metadata is reported.
5. After shape validation, inspection reads only the `meta.value` rows for
   `schema_version`, `task_signature`, and `checkpoint_version`, plus
   `COUNT(*)` from `results`. It never selects `results.value` or calls
   `pickle.loads`; malicious or corrupt result blobs cannot execute during
   inspection.
6. All schema, metadata, and count queries run inside one explicit read
   transaction begun with `BEGIN`, so they observe one coherent SQLite
   snapshot even if a writer commits between queries. `size_bytes` is an
   earlier pre-open filesystem observation from the initial `lstat()` and is not
   transactionally coherent with that logical snapshot during a live writer.
7. The current checkpoint format remains schema version `2`
   (`pyarallel/checkpoint.py:199-199`, `pyarallel/checkpoint.py:247-323`).
   Missing, unsupported, or internally inconsistent metadata raises
   `CheckpointError` with delete/recreate guidance. Inspection does not bless
   foreign or legacy SQLite files.
8. SQLite parse, lock, and corruption failures are translated to
   `CheckpointError`, consistent with normal checkpoint opening
   (`pyarallel/checkpoint.py:254-262`). The connection closes on every exit.
9. Inspection never compares the stored task signature or semantic version to
   a live callable or caller token; it reports what the file contains. Normal
   resume remains responsible for compatibility enforcement.
10. Do not use SQLite's `immutable=1` shortcut. Pyarallel's core reliability
   case is inspecting a checkpoint after abrupt process death, where committed
   rows may live only in an existing WAL. Immutable mode can ignore that WAL
   and undercount or even miss the schema.

`mode=ro` is a logical read-only guarantee, not a byte-for-byte filesystem
immutability guarantee. SQLite may create or update `-wal`/`-shm` sidecars to
read a WAL-mode database. The API must document that possibility and must not
promise unchanged directory entries or sidecar mtimes. The primary database is
never created when missing, and SQL writes remain prohibited.

## External Contract Probe

SQLite's official URI documentation defines `mode=ro` as a read-only open and
shows `mode=ro&cache=private` as the private-cache form:
<https://sqlite.org/uri.html>. Its WAL documentation states that a read-only
WAL database requires readable existing sidecars, permission to create them,
or immutable mode: <https://sqlite.org/wal.html#readonly>.

A focused Python `sqlite3` probe confirmed both load-bearing consequences on
the supported runtime:

- opening a clean WAL-mode database with `mode=ro` read the data but created
  empty `-wal` and `-shm` sidecars;
- after a subprocess committed and exited without closing SQLite, the main file
  contained no visible schema under `immutable=1`, while `mode=ro` read the
  committed row through the surviving WAL.

Therefore implementation must preserve WAL visibility and document possible
sidecar activity rather than claiming physical immutability.

## Error and Cleanup Matrix

- An initial leaf `lstat()` reporting `ENOENT` propagates as
  `FileNotFoundError` with the supplied path.
- An initial leaf that exists but is a symlink, directory, or special file
  raises `CheckpointError`.
- Other initial `lstat()` failures, and all OS/SQLite/schema/decode errors
  after leaf validation (including disappearance or replacement races), raise
  `CheckpointError` chained from the cause.
- The connection closes in `finally` on success and failure. A cleanup error is
  reported when it is the only error, but must not replace an active validation
  error.

The safety check covers the final path component only, matching
`_create_secure()` today. Parent directories are trusted. A hostile parent can
replace the leaf between `lstat()` and SQLite open; schema validation limits
the consequence but cannot provide race-free directory authority. This API
does not claim protection from a hostile checkpoint directory.

## URI Construction

Return `Path(path)` exactly. For SQLite only, derive a lexical absolute path
with `Path(path).absolute().as_uri()` and append
`?mode=ro&cache=private`. Never call `resolve()`: it follows symlinks and would
change both the trusted-parent boundary and the reported path.

Tests cover relative paths and names containing spaces, `#`, `?`, `%`, and
Unicode. The Windows lane additionally covers drive-letter paths and, where
the runner permits it, UNC handling. It also proves the connection is closed
by renaming and deleting the file after inspection.

## Semantic Version Decoding

The current store persists `checkpoint_version` as the stable `repr` produced
by `_encode_version()` (`pyarallel/checkpoint.py:444-464`). `checkpoint_info()`
decodes accepted text with `ast.literal_eval`, then validates it by re-encoding
with `_encode_version()` and requiring an exact text match. No new size limit is
introduced: current Pyarallel can write and resume arbitrarily large valid
tokens, so inspection must not silently reject its own checkpoints.

Accepted decoded values are exactly a scalar `str`, `int`, or `bytes`, or a
flat tuple of those types (including an empty tuple). `bool`, nested tuples,
and decoded `None` are rejected. Only a missing metadata row maps to `None`.
`SyntaxError`, `ValueError`, `TypeError`, and `RecursionError` are translated to
`CheckpointError`. `literal_eval` avoids code execution but can consume CPU or
memory on adversarially large/deep text. Inspection is safe from pickle code
execution, not a resource-isolation sandbox for arbitrary hostile files; use OS
resource limits when inspecting an untrusted checkpoint.

A missing metadata row maps to `None`. A malformed row raises
`CheckpointError`; it is evidence of corruption or a foreign writer, not a
version string to expose optimistically.

## Explicit Boundaries and Non-goals

- No result values, keys, fingerprints, or per-row listing.
- No deletion, repair, migration, vacuum, checkpoint truncation, or resume.
- No progress percentage or original-input total; the schema does not know it.
- No claim that a completed computation was committed to an external sink.
- No live-run monitoring guarantee. SQLite provides a read snapshot while
  another process may write, but concurrent execution runs remain unsupported
  and the returned count is simply the snapshot observed by this call.
- No CLI or JSON-specific wrapper; callers can use the frozen dataclass.
- No checkpoint schema change. The reader consumes existing version-2 files.

## Implementation Steps

1. In `pyarallel/checkpoint.py`, add the frozen `CheckpointInfo` dataclass,
   safe path validation, exact schema-shape validation, validated
   semantic-version decoding, one-transaction snapshot reads, and
   `checkpoint_info()`.
   Keep the reader separate from `_CheckpointStore.__init__`, because that
   constructor intentionally creates and validates a checkpoint for a specific
   live function (`pyarallel/checkpoint.py:237-323`).
2. In `pyarallel/__init__.py`, export `CheckpointInfo` and `checkpoint_info`.
   Regenerate `tests/api_snapshot.txt` so the new function and constructor are
   visible in review.
3. Add focused tests in `tests/test_checkpoint.py` or a dedicated
   `tests/test_checkpoint_info.py` covering the acceptance matrix below.
4. Add user-facing documentation to `docs/api-reference/core.md`, the
   checkpoint section of `docs/user-guide/advanced-features.md`, and
   `CHANGELOG.md`. Include one example that checks `completed` and
   `checkpoint_version` before deciding whether to resume or delete a file,
   plus an explicit warning that inspection does not validate against a live
   function and does not report total progress.
5. Update typing assertions for `Path`, field types, and the function return.
   Run the public API, docs, packaging, and cross-platform gates before review.

## Acceptance Criteria

- Inspecting a valid positional or identity-keyed version-2 checkpoint returns
  the supplied path as a `Path`, schema `"2"`, decoded semantic token or
  `None`, stored task signature, exact `COUNT(*)`, and the pre-open primary-file
  size from the validating `lstat()` receipt.
- Zero-row checkpoints report `completed == 0`.
- `checkpoint_info()` does not create a missing file and raises
  `FileNotFoundError`.
- A symlink, directory, or special file is refused before SQLite opens it.
- Legacy schema; missing or malformed required metadata; malformed `meta`
  shape; missing/wrong-shaped `results`; a `results` view; missing columns;
  wrong primary-key/nullability declarations; random bytes; and SQLite
  corruption fail with actionable `CheckpointError` rather than raw
  `sqlite3`/`ast`/OS exceptions.
- Semantic-version tests cover each scalar type, heterogeneous and empty flat
  tuples, bool, nested tuple, stored `'None'`, malformed syntax, recursion, and
  a large valid token produced by the current writer.
- A result row containing a pickle payload that would execute or raise if
  loaded can still be counted safely; a test makes `pickle.loads` fail if
  called and proves inspection succeeds.
- SQL trace/authorizer evidence shows inspection issues only the explicit
  `BEGIN`, schema/metadata/count reads, `PRAGMA table_info`, and
  `PRAGMA query_only=ON`: no DDL, DML, attach, vacuum, journal-mode change, or
  result-value projection. Persisted logical rows remain unchanged and a
  missing primary database is never created. Tests do not assert sidecar
  immutability because SQLite may legitimately manage WAL/SHM files for a
  read-only connection.
- A writer committing between inspection queries cannot produce mixed metadata
  and counts; a controlled second connection proves the explicit transaction
  returns one coherent logical snapshot. `size_bytes` is asserted separately,
  not as part of that transaction.
- A crash-left checkpoint whose committed rows exist in WAL is counted
  correctly; the test proves an immutable open would miss those rows.
- Repeated inspection of a closed, quiescent checkpoint returns equal frozen
  values and leaves no open handle preventing rename/delete on Windows.
- Public imports, strict mypy, Pyright, the API snapshot, executable docs,
  strict MkDocs, full pytest matrix, clean wheel/sdist installation, macOS,
  Windows, Python 3.12-3.14, and free-threaded lanes pass.

## Risks and Mitigations

- **Accidental unpickling:** keep the SQL projection explicit and regression
  test with a hostile blob plus a failing `pickle.loads` monkeypatch.
- **Accidental logical mutation:** validate with `lstat`, use
  `mode=ro&cache=private`, set query-only, and compare checkpoint metadata and
  row counts before and after. Document SQLite-managed sidecar activity.
- **Misleading progress:** call the field `completed`, not `progress`; document
  that total input and external sink state are unknowable.
- **Leaking internal encoding:** decode only the existing version token through
  `literal_eval` plus exact re-encoding; keep row keys/fingerprints private.
- **Reader/writer drift:** validate the same required schema metadata as the
  writer, hold one explicit read transaction, and keep version-specific parsing
  local to `checkpoint.py`.
- **Windows URI/path handling:** use lexical `absolute().as_uri()` and exercise
  reserved characters, Unicode, drive-letter paths, handle closure, and
  available UNC behavior in the Windows lane.
- **False clean snapshot after a crash:** never set `immutable=1`; retain a
  regression with a subprocess-left WAL whose committed row must be counted.

## Verification and Review

Run focused checkpoint-info tests first, then Ruff, pinned strict mypy, latest
installed-wheel mypy, Pyright, API snapshot, executable documentation, strict
MkDocs, full pytest, and package clean-install smoke tests. Re-read the entire
checkpoint open/create/read/close path after the change.

Because this is a public persistence API, require one normal and one
adversarial post-implementation review. The adversarial lane must specifically
attempt primary-file creation, logical database mutation, result unpickling,
schema/view confusion, mixed-snapshot reads, symlink/TOCTOU boundaries, URI
edge cases, leaked handles, parser exhaustion, and false progress claims.

## Stop Condition

The slice is ready for implementation only after this plan is independently
reviewed with no unresolved blocker. Implementation is complete only when the
reader is demonstrably read-only and non-unpickling, the public API and docs
match, every applicable verification gate passes, both post-change reviewers
approve, and the feature branch is committed for a PR. Do not release or bump
`0.11.0` as part of this work.
