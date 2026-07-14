# `item_key=` Result Identity

## Goal

Add an optional, stable application identity to every admitted item without
changing the ordering contract carried by `ItemResult.index`.

The public surface is:

```python
item_key: Callable[[T], str | int | bytes] | None = None
```

on sync and async map, starmap, and streaming APIs, plus:

```python
ItemResult.key: str | int | bytes | None
```

This completes the `on_result=` story: callbacks can identify a domain item
such as `"customer-8f2"` instead of relying only on input position `47_391`.

## Contract and Invariants

1. `index` remains the zero-based source position and remains the sole ordering
   key. `item_key` never changes result order.
2. The key function runs on the driver thread or async event-loop thread as an
   item is admitted, before the user task is submitted.
3. A key is computed exactly once per admitted item for each applicable,
   distinct key function. Positional/no-key runs evaluate no key function. The
   engine stores and transports the computed value; workers do not evaluate
   key functions.
4. `ItemResult.key` is available for successes, task failures, existing
   `on_result=` callback deliveries, and `ParallelResult.item_results()`.
   Callback cadence does not expand: synthetic timeout, abort, and cancellation
   placeholders still do not trigger `on_result=`.
5. Items admitted before timeout, cancellation, or `max_errors` truncation keep
   their computed key even when their final `ItemResult` contains a synthetic
   failure. Sized-source placeholders for items never admitted have `key=None`.
6. Duplicate `item_key` values are valid. Identity uniqueness is not a result
   reporting requirement.
7. Checkpoint uniqueness is unchanged: duplicate `checkpoint_key` values still
   fail because they would address the same persistence row.
8. A key function must be synchronous and return `str`, `int`, or `bytes`;
   `bool` and awaitables are rejected. An invalid standalone or distinct
   `item_key` return raises `TypeError`; an invalid `checkpoint_key` return
   raises `CheckpointError`; when the same callable supplies both options,
   checkpoint lookup owns its single evaluation and invalid returns raise
   `CheckpointError`. Validation fails during admission, before the item task
   runs. Exceptions raised by either key callable propagate unchanged as
   driver/input errors.
9. Starmap key functions receive the original source tuple, not unpacked
   positional arguments.
10. Hand-constructed `ItemResult` and `ParallelResult` values remain constructor
    source compatible: `key` is an optional final argument and defaults to
    `None`. As a dataclass field, `key` deliberately participates in
    `ItemResult` equality and representation.
11. Key evaluation and checkpoint lookup are admission work. After that work,
    the engine rechecks stop/deadline state before submitting live work. A
    pulled and keyed item that crosses the boundary becomes a synthetic result
    with its key; its user task does not run. A completed checkpoint hit remains
    a completed item and keeps the existing cache-hit classification.

## `checkpoint_key=` Interoperation

On collected map APIs, when `item_key` is omitted and `checkpoint_key` is
present, the raw checkpoint identity automatically populates `ItemResult.key`.
The checkpoint layer must return the raw validated key alongside a cache lookup
so the function is not evaluated twice. Starmap and streaming APIs do not have
a checkpoint surface, so fallback does not apply to them.

When both options are present:

- If they reference the same callable object, checkpoint lookup evaluates and
  validates it once; result identity reuses that raw value.
- If they are distinct callables, checkpoint lookup evaluates and validates
  `checkpoint_key` first, then `item_key` evaluates once. This preserves
  checkpoint error precedence and avoids result-key side effects when
  checkpoint addressing is already invalid.
- `item_key` remains the reported key; `checkpoint_key` remains the persistence
  key.

Positional checkpoints without `checkpoint_key` do not manufacture an item
identity; `ItemResult.key` remains `None` unless `item_key` is supplied.

## Explicit Boundaries and Non-goals

- Do not add a second generic parameter to `ItemResult`; the deliberately small
  public key type is `str | int | bytes | None`.
- Do not require keys to be unique outside checkpoint addressing.
- Do not pass key functions into worker processes or interpreters.
- Do not add `item_key` as a decorator-construction default. It is a per-run
  option on `.map()`, `.starmap()`, and `.stream()`: one decorator-level
  callable cannot honestly describe both a map item and a starmap tuple.
- Do not change checkpoint schema or row encoding.
- Do not change completion-order versus input-order behavior.

## Implementation Plan

1. In `pyarallel/result.py`, add `key` to `ItemResult`, teach internal result
   builders to accept it, and add an optional index-aligned key ledger to
   `ParallelResult`. Validate ledger alignment at construction.
2. In `pyarallel/checkpoint.py`, make `_RunCheckpoint.lookup()` expose the raw
   validated checkpoint key with its cached value. When both options reference
   the same callable, the engine reuses that returned raw value instead of
   calling the function itself. Preserve duplicate detection and encoded
   persistence keys.
3. In `pyarallel/core.py`, add `item_key` to sync option TypedDicts and public
   map, starmap, and streaming signatures. Compute keys at admission and keep
   them aligned through cache hits, live completions, ordered streaming,
   sequential execution, timeout, cancellation, and abort aftermath.
   Recheck stop/deadline state after key/lookup work and before live submission.
4. Mirror the same state transitions in `pyarallel/aio.py`, including async
   iterables and cancellation of admitted tasks.
5. Keep decorators pass-through only: extend the existing per-call TypedDicts
   without adding a decorator default.
6. Update the API snapshot, typing assertions, API reference, user guide, and
   changelog with the implemented contract and checkpoint fallback.

## Failure Modes to Lock Down

- A key is attached to the wrong index after completion-order streaming.
- Cached and live results interleave and shift key alignment.
- A checkpoint key function runs twice, observes mutable state, or returns two
  different values for the same item.
- Duplicate reporting keys are accidentally rejected by checkpoint logic.
- A key-function exception is converted into an item task failure or drains
  more of a lazy source.
- A slow key function crosses a deadline or stop request and the engine submits
  the user task without rechecking the admission gate.
- Timeout, stop, or `max_errors` loses keys for already admitted items or
  invents keys for items never pulled.
- Sync and async engines drift on key validation, fallback, or callback data.
- Process/interpreter execution attempts to pickle the key callable.

## Verification

Targeted tests must cover:

- sync and async map, starmap, and stream success and failure;
- `on_result=` and collected `item_results()` receipts;
- input-order and completion-order streaming;
- sequential, thread, process, interpreter, sync iterable, and async iterable
  paths where supported;
- cached hits, live results, and cached/live interleaving;
- fallback from `checkpoint_key`, exactly-once evaluation, same-callable reuse,
  and distinct-callable evaluation;
- duplicate result keys accepted and duplicate checkpoint keys rejected;
- invalid key types and key-function exceptions before task execution;
- async key callables returning awaitables are rejected before task execution;
- timeout, cancellation, and `max_errors`, distinguishing admitted from unseen
  items;
- post-key errors and stop/deadline crossings do not drain the source or start
  the keyed item task;
- constructor compatibility and key-ledger alignment guards.

Then run the full applicable gate: Ruff, strict mypy and Pyright, the complete
pytest suite, API snapshot verification, executable documentation examples,
strict MkDocs build, and package/runtime matrix checks used by CI.

## Review and Stop Condition

Before implementation, an independent reviewer must challenge the public
contract, evaluation timing, checkpoint boundary, and truncation behavior.
After implementation, one normal reviewer and one adversarial reviewer must
verify the load-bearing paths independently.

The slice is complete only when the public surface, implementation, tests,
typing, docs, and API snapshot agree; the full local verification gate passes;
both post-implementation reviews have no unresolved blocker; and the branch is
committed and ready for a pull request. This work does not release `0.11.0`.
