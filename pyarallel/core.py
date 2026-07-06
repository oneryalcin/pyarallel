"""Sync engine: parallel_map, parallel_starmap, parallel_iter.

Simple, explicit parallel execution over ``concurrent.futures``. No magic
type detection, no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import contextvars
import functools
import os
import pickle
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from pathlib import Path
from typing import Any, Literal, TypedDict

from ._plan import (
    _mark_timeout_indices,
    _progress_total,
    _timeout_failure,
    _total_if_known,
    _validate_max_errors,
)
from ._procexec import _call_resolved, _call_resolved_args, _resolve_process_target
from .checkpoint import _open_checkpoint
from .limiter import Limiter, _as_limiter
from .policies import RateLimit, Retry
from .result import (
    _PENDING,
    Aborted,
    ItemResult,
    ParallelResult,
    _Failure,
    _item_result,
    _Outcome,
)

ExecutorType = Literal["thread", "process"]


class SyncMapOptions(TypedDict, total=False):
    """Per-call options of ``parallel_map`` — the decorator ``.map()``
    surface. Declared to match the engine signature (the source of
    truth); ``typing_assertions.py`` keeps them honest. Every key allows
    ``None`` = "inherit the decorator default"."""

    workers: int | None
    executor: ExecutorType | None
    rate_limit: Limiter | RateLimit | float | None
    timeout: float | None
    on_progress: Callable[[int, int], None] | None
    batch_size: int | None
    retry: Retry | None
    checkpoint: str | Path | None
    checkpoint_key: Callable[[Any], str | int | bytes] | None
    max_errors: int | None
    sequential: bool | None
    worker_init: Callable[[], None] | None
    max_tasks_per_worker: int | None


class SyncStarmapOptions(TypedDict, total=False):
    """Per-call options of ``parallel_starmap`` (no checkpoint)."""

    workers: int | None
    executor: ExecutorType | None
    rate_limit: Limiter | RateLimit | float | None
    timeout: float | None
    on_progress: Callable[[int, int], None] | None
    batch_size: int | None
    retry: Retry | None
    sequential: bool | None
    worker_init: Callable[[], None] | None
    max_tasks_per_worker: int | None


class SyncStreamOptions(TypedDict, total=False):
    """Per-call options of ``parallel_iter`` — the ``.stream()`` surface."""

    workers: int | None
    executor: ExecutorType | None
    rate_limit: Limiter | RateLimit | float | None
    batch_size: int | None
    retry: Retry | None
    ordered: bool | None
    on_progress: Callable[[int, int], None] | None
    max_errors: int | None
    sequential: bool | None
    worker_init: Callable[[], None] | None
    max_tasks_per_worker: int | None


def _execute_outcome(
    fn: Callable[..., Any],
    item: Any,
    retry: Retry | None = None,
    limiter: Limiter | None = None,
) -> _Outcome:
    """Run one item to its final outcome, counting attempts and timing.

    The clock starts inside the worker (queue wait excluded) and stops at
    the final success or failure — retry backoff sleeps included. That is
    the duration a latency budget cares about.

    Every retry attempt is a fresh API call, so it draws a fresh token from
    the shared *limiter* (the first attempt's token was paid at submission).
    A server-mandated wait (``Retry.wait_from``) also pauses the limiter —
    even on the final attempt, so a 429 from a task that is about to give
    up still slows the rest of the pool.
    """
    start = time.perf_counter()
    attempts = retry.attempts if retry is not None else 1
    last_exc: Exception | None = None
    made = 0
    for attempt in range(attempts):
        made = attempt + 1
        try:
            if attempt > 0 and limiter is not None:
                limiter.wait()
            value = fn(item)
        except Exception as exc:
            last_exc = exc
            if retry is None or not retry._should_retry(exc):
                break
            server_wait = retry._server_wait(exc)
            if server_wait is not None and limiter is not None:
                limiter.pause(server_wait)
            if attempt < attempts - 1:
                delay = (
                    server_wait if server_wait is not None else retry._delay(attempt)
                )
                if delay > 0:
                    time.sleep(delay)
        else:
            return _Outcome(value, None, made, time.perf_counter() - start)
    return _Outcome(None, last_exc, made, time.perf_counter() - start)


def _validate_common(
    workers: int | None, executor: str, batch_size: int | None
) -> None:
    """Shared argument validation for the sync entry points."""
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if workers is not None and workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if executor not in ("thread", "process"):
        raise ValueError(f'executor must be "thread" or "process", got {executor!r}')


def _validate_worker_options(
    executor: str,
    worker_init: Callable[[], None] | None,
    max_tasks_per_worker: int | None,
) -> None:
    """Validation for the worker-lifecycle options (fail fast, fail loud)."""
    if max_tasks_per_worker is not None:
        if executor != "process":
            raise ValueError(
                "max_tasks_per_worker requires executor='process' — thread "
                "workers have no per-worker task budget"
            )
        if max_tasks_per_worker < 1:
            raise ValueError(
                f"max_tasks_per_worker must be >= 1, got {max_tasks_per_worker}"
            )
    if worker_init is not None and executor == "process":
        try:
            pickle.dumps(worker_init)
        except Exception as exc:
            raise ValueError(
                "worker_init cannot be pickled for executor='process' — "
                "use a module-level function, not a lambda or closure."
            ) from exc


def _make_pool(
    executor: ExecutorType,
    workers: int | None,
    worker_init: Callable[[], None] | None,
    max_tasks_per_worker: int | None,
) -> ThreadPoolExecutor | ProcessPoolExecutor:
    """Build the executor pool with the worker-lifecycle options applied."""
    if executor == "thread":
        return ThreadPoolExecutor(max_workers=workers, initializer=worker_init)
    return ProcessPoolExecutor(
        max_workers=workers,
        initializer=worker_init,
        max_tasks_per_child=max_tasks_per_worker,
    )


def _submit_task(
    pool: ThreadPoolExecutor | ProcessPoolExecutor,
    executor: ExecutorType,
    task_fn: Callable[..., _Outcome],
    item: Any,
) -> Future[_Outcome]:
    """Submit one item, propagating the caller's context to thread workers.

    ``copy_context()`` runs here, in the submitting thread, once per item
    — copying inside the worker would capture the worker's own (empty)
    context and propagate nothing. Per-item capture is also what makes
    concurrent execution legal: a single ``Context`` cannot be entered
    twice at once. Writes inside tasks land in the copy and stay
    isolated. Contexts don't pickle, so process workers are skipped.
    """
    if executor == "thread":
        return pool.submit(contextvars.copy_context().run, task_fn, item)
    return pool.submit(task_fn, item)


def _effective_workers(workers: int | None, executor: ExecutorType) -> int:
    """The worker count actually in force — the caller's, else the stdlib default.

    Used to size the streaming window: ``parallel_iter(..., workers=2)``
    must window at 4, not at the thread pool's default of up to 64.
    """
    if workers is not None:
        return workers
    if executor == "thread":
        return min(32, (os.cpu_count() or 1) + 4)  # ThreadPoolExecutor default
    return os.cpu_count() or 1  # ProcessPoolExecutor default


def _build_task_fn(
    fn: Callable[..., Any],
    executor: ExecutorType,
    retry: Retry | None,
    limiter: Limiter | None,
) -> Callable[..., _Outcome]:
    """Compose the per-item callable: process-safe target, then execution.

    Every task runs through ``_execute_outcome``, which owns retries,
    attempt counting, and wall-clock timing — the future always resolves
    to an ``_Outcome``, and task exceptions travel inside it.

    The limiter is threaded into the retry loop for thread executors only:
    a ``Limiter`` holds a lock and cannot be pickled, and pausing a copy in
    a worker process would not affect the parent's limiter anyway. Process
    workers still honor server-mandated waits as their own retry delay.
    """
    task_fn = fn
    if executor == "process":
        if retry is not None:
            try:
                pickle.dumps(retry)
            except Exception as exc:
                raise ValueError(
                    "This Retry cannot be pickled for executor='process' — "
                    "its retry_if/wait_from callables must be module-level "
                    "functions, not lambdas or closures. Alternatively use "
                    "executor='thread'."
                ) from exc
        resolved = _resolve_process_target(fn)
        if resolved is not None:
            module_name, qualname = resolved
            task_fn = functools.partial(
                _call_resolved,
                module_name=module_name,
                qualname=qualname,
            )
    return functools.partial(
        _execute_outcome,
        task_fn,
        retry=retry,
        limiter=limiter if executor == "thread" else None,
    )


def parallel_map[T, R](
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    checkpoint: str | Path | None = None,
    checkpoint_key: Callable[[T], str | int | bytes] | None = None,
    max_errors: int | None = None,
    sequential: bool = False,
    worker_init: Callable[[], None] | None = None,
    max_tasks_per_worker: int | None = None,
) -> ParallelResult[R]:
    """Execute *fn* over *items* in parallel, returning ordered results.

    Note: there is deliberately no ``task_timeout`` here — Python threads
    cannot be cancelled mid-execution. Use ``timeout=`` for a total
    wall-clock limit, put timeouts inside your function, or use
    ``async_parallel_map`` for per-task timeouts.

    Args:
        fn: Function applied to each item.
        items: Any iterable (list, generator, range, …).
        workers: Number of parallel workers. Defaults to ``None`` which lets
            the executor decide — ``min(32, cpu_count+4)`` for threads,
            ``cpu_count()`` for processes.
        executor: ``"thread"`` for I/O-bound, ``"process"`` for CPU-bound.
        rate_limit: ``RateLimit`` spec, a plain number (ops per second), or
            a shared ``Limiter`` instance to draw from one budget across
            multiple calls.
        timeout: Total wall-clock timeout in seconds for the whole
            operation. On expiry the result's ``timed_out`` flag is set;
            unfinished sized slots are marked with ``TimeoutError``,
            while unsized inputs return a shorter result — the source is
            never drained after a stop.
        on_progress: ``callback(completed, total)`` fired after each task.
            When ``items`` has no known length, ``total`` is the number
            of items *admitted* so far (one window ahead of
            completions) — it grows as the run progresses, so a
            percentage over it is meaningless; pass a sized input for a
            real total.
        batch_size: The admission window — the maximum number of items
            submitted but not yet resolved (default ``2 × workers``).
            It is a memory/lookahead bound, not a chunk size: there are
            no barriers, and input is consumed lazily, one window ahead,
            so generators are never materialized.
        retry: ``Retry`` object for per-item retry with backoff.
        checkpoint: Path to a checkpoint file (created if missing, SQLite).
            Completed item results are stored there; rerunning the same
            call resumes — cached items load from disk, failed and unseen
            items execute. The file is bound to the mapped function's
            identity: resuming with a different function raises
            ``CheckpointError``. Items and results must be picklable; a
            result that cannot be checkpointed aborts the run with
            ``CheckpointError``. Rows are positional by default —
            reordering or inserting inputs forces shifted items to
            recompute; pass ``checkpoint_key`` when inputs evolve.
        checkpoint_key: Stable identity for each item (returns str, int,
            or bytes) — checkpoint rows are then keyed by identity, not
            position, so prepending or reordering inputs no longer
            invalidates completed work. Requires ``checkpoint=``.
            Duplicate keys raise ``CheckpointError``; the item fingerprint
            check still applies (a changed payload under the same key
            recomputes).
        max_errors: Abort after this many failures (counted after retries
            are exhausted). Because admission is windowed, a dead API
            costs the abort-trigger point plus one window of calls, not
            thousands. Items that never ran are marked with ``Aborted``
            — distinguishable from real failures — and the result's
            ``aborted`` flag is set. The source is never consumed after
            the stop: sized inputs get one ``Aborted`` entry per unseen
            item, unsized inputs return a result covering only the items
            actually pulled.
        sequential: Run every item inline in the calling thread — no
            pool, real stack traces, working breakpoints, deterministic
            order. Honors rate_limit, retry, checkpoint, on_progress, and
            max_errors; ``timeout`` is checked between items only (an
            in-flight item cannot be interrupted); ``workers`` and
            ``executor`` are both ignored (debug mode always runs
            in-thread), so one env flag can flip production code into
            debug mode. ``worker_init`` runs once in the calling thread.
        worker_init: Run once in each worker before it takes tasks — open
            one DB connection or load one model per worker, not per item.
            For ``executor="process"`` it must be picklable (module-level
            function).
        max_tasks_per_worker: Recycle each process worker after this many
            tasks (guards against native-library memory leaks). Requires
            ``executor="process"``.

    Contextvars set by the caller are visible inside thread-executor
    tasks — each item runs under a fresh copy of the submitting thread's
    context, so correlation IDs survive and writes inside tasks stay
    isolated. Process workers are skipped (contexts don't pickle).

    If the input iterable itself raises mid-run, the error propagates
    promptly: queued work is cancelled, but tasks already running cannot
    be interrupted and may complete — side effects included — in the
    background. If you need no-work-after-error guarantees, materialize
    the input first (``list(items)``) so source errors cannot happen
    mid-run, or use ``async_parallel_map`` (async tasks are cancelled
    and awaited).

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    _validate_common(workers, executor, batch_size)
    _validate_max_errors(max_errors)
    if not sequential:
        # Debug mode ignores the pool entirely — a process-configured call
        # with a local worker_init must still be one flag away from inline.
        _validate_worker_options(executor, worker_init, max_tasks_per_worker)
    if checkpoint_key is not None and checkpoint is None:
        raise ValueError("checkpoint_key requires checkpoint= to be set")

    if sequential:
        return _sequential_collected_map(
            fn,
            items,
            rate_limit=rate_limit,
            timeout=timeout,
            on_progress=on_progress,
            retry=retry,
            checkpoint=checkpoint,
            checkpoint_key=checkpoint_key,
            max_errors=max_errors,
            worker_init=worker_init,
        )

    return _collected_map(
        fn,
        items,
        workers=workers,
        executor=executor,
        rate_limit=rate_limit,
        timeout=timeout,
        on_progress=on_progress,
        batch_size=batch_size,
        retry=retry,
        checkpoint=checkpoint,
        checkpoint_key=checkpoint_key,
        max_errors=max_errors,
        worker_init=worker_init,
        max_tasks_per_worker=max_tasks_per_worker,
    )


def _collected_map(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    workers: int | None,
    executor: ExecutorType,
    rate_limit: Limiter | RateLimit | float | None,
    timeout: float | None,
    on_progress: Callable[[int, int], None] | None,
    batch_size: int | None,
    retry: Retry | None,
    checkpoint: str | Path | None,
    checkpoint_key: Callable[[Any], str | int | bytes] | None,
    max_errors: int | None,
    worker_init: Callable[[], None] | None = None,
    max_tasks_per_worker: int | None = None,
) -> ParallelResult[Any]:
    """The collected-map engine: bounded admission through a window.

    One engine for every non-sequential collected map. Input is
    consumed lazily, at most ``window`` items are unresolved at any
    moment, and the source is never touched after a stop. The window
    is also what makes ``max_errors`` cheap: a dead API costs the
    abort-trigger point plus one window, not thousands of upfront
    submissions. Keeping the window small is deliberate —
    ``wait()`` re-registers waiters across the in-flight set per
    wake, so admission bounded by workers is what keeps the driver
    loop O(n) (benchmarked in the v0.6 plan).
    """
    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, executor, retry, bucket)
    window = (
        batch_size
        if batch_size is not None
        else 2 * _effective_workers(workers, executor)
    )
    total = _total_if_known(items)
    source = iter(items)
    results: list[Any] = []
    in_flight: dict[Future[_Outcome], int] = {}
    completed = 0
    failures = 0
    aborted = False
    timed_out = False
    deadline = (time.monotonic() + timeout) if timeout is not None else None

    store = (
        _open_checkpoint(checkpoint, fn, checkpoint_key)
        if checkpoint is not None
        else None
    )
    pool = _make_pool(executor, workers, worker_init, max_tasks_per_worker)

    def _report() -> None:
        if on_progress:
            on_progress(completed, _progress_total(total, results))

    def _submit_next() -> bool:
        nonlocal completed, timed_out
        while True:
            # Cached checkpoint hits consume real time (lookup + key_fn)
            # without ever reaching a wait — the deadline must bind here
            # too, or a checkpoint-heavy run ignores timeout= entirely.
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                return False
            try:
                item = next(source)
            except StopIteration:
                return False
            idx = len(results)
            results.append(_PENDING)
            if store is not None:
                cached = store.lookup(idx, item)
                if cached is not None:
                    results[idx] = cached[0]
                    completed += 1
                    _report()
                    continue  # cached — keep looking for work to admit
            if bucket is not None:
                budget: float | None = None
                if deadline is not None:
                    budget = max(0.0, deadline - time.monotonic())
                if not bucket.wait(timeout=budget):
                    # timeout= wins over rate-limit pacing; this item was
                    # never submitted — mark its slot, stop admitting.
                    assert timeout is not None
                    results[idx] = _timeout_failure(timeout, idx)
                    timed_out = True
                    return False
            in_flight[_submit_task(pool, executor, task_fn, item)] = idx
            return True

    def _absorb(future: Future[_Outcome], idx: int) -> None:
        nonlocal completed, failures, aborted
        error: Exception | None
        try:
            outcome = future.result()
        except Exception as exc:
            error = exc  # infrastructure failure — task errors ride the outcome
        else:
            error = outcome.error
        if error is not None:
            results[idx] = _Failure(error)
            failures += 1
            if max_errors is not None and failures >= max_errors:
                aborted = True
        else:
            results[idx] = outcome.value
            # Outside the except: a checkpoint write failure raises
            # CheckpointError instead of mislabeling a success as a failure.
            if store is not None:
                store.put(idx, results[idx])
        completed += 1
        _report()

    # The mid-fill absorb sweep exists so a dead API stops admission
    # mid-fill (max_errors) and so progress stays timely while a rate
    # limiter stretches the fill over seconds. Without either, filling
    # is fast and the sweep is only an O(window) scan per admission.
    sweep_mid_fill = max_errors is not None or bucket is not None

    try:
        while True:
            # Deadline first — an already-expired timeout must not admit
            # any work at all (timeout=0 means zero tasks run).
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            while not aborted and len(in_flight) < window:
                if not _submit_next():
                    break
                if sweep_mid_fill:
                    done_now, _pending_now = wait(in_flight, timeout=0)
                    for future in done_now:
                        _absorb(future, in_flight.pop(future))
            # A mid-fill abort must stop here — falling through to the
            # blocking wait would delay the abort by a task completion
            # (or repackage it as a timeout if the deadline fires first).
            if timed_out or aborted:
                break
            if not in_flight:
                break
            wait_timeout: float | None = None
            if deadline is not None:
                wait_timeout = max(0.0, deadline - time.monotonic())
            done, _pending = wait(
                in_flight, timeout=wait_timeout, return_when=FIRST_COMPLETED
            )
            if not done:
                timed_out = True
                break
            for future in done:
                _absorb(future, in_flight.pop(future))
            if aborted:
                break

        # Aftermath fills never touch the source again: a poison, blocking,
        # or infinite input must not be consumed after the run has stopped.
        # Sized inputs get one placeholder per unseen item (by count);
        # unsized inputs yield a shorter result — documented.
        if timed_out:
            assert timeout is not None
            for future in in_flight:
                future.cancel()
            _mark_timeout_indices(results, in_flight.values(), timeout)
            if total is not None:
                for idx in range(len(results), total):
                    results.append(_timeout_failure(timeout, idx))
        elif aborted:
            # Salvage completions that raced the stop, drop the rest.
            done, pending = wait(in_flight, timeout=0)
            for future in done:
                _absorb(future, in_flight.pop(future))
            for future in pending:
                future.cancel()
            reason = f"aborted after {failures} failures (max_errors={max_errors})"
            for idx in in_flight.values():
                if results[idx] is _PENDING:
                    results[idx] = _Failure(Aborted(reason))
            if total is not None:
                results.extend(
                    _Failure(Aborted(reason)) for _ in range(total - len(results))
                )
    finally:
        # Every stop — timeout, abort, or an escaping driver error
        # (source iterator, checkpoint write, progress callback) —
        # cancels queued work and returns promptly. Running tasks cannot
        # be interrupted and may complete in the background; waiting for
        # them instead was tried and rejected (Round 2): a single hung
        # task would block the error from ever surfacing.
        stopped = timed_out or aborted or sys.exc_info()[0] is not None
        pool.shutdown(wait=not stopped, cancel_futures=stopped)
        if store is not None:
            store.close()

    # First stop reason wins: the flags are exclusive by construction.
    return ParallelResult(
        results, timed_out=timed_out, aborted=aborted and not timed_out
    )


def _sequential_collected_map(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    rate_limit: Limiter | RateLimit | float | None,
    timeout: float | None,
    on_progress: Callable[[int, int], None] | None,
    retry: Retry | None,
    checkpoint: str | Path | None,
    checkpoint_key: Callable[[Any], str | int | bytes] | None,
    max_errors: int | None,
    worker_init: Callable[[], None] | None,
) -> ParallelResult[Any]:
    """The debug engine: every item runs inline in the calling thread.

    No pool, no futures — real stack traces and working breakpoints.
    ``timeout`` is checked between items only (an in-flight item cannot
    be interrupted). On timeout or abort, sized inputs fill by count and
    unsized inputs return a shorter result — same policy as the windowed
    engine. ``worker_init`` runs once, here, in the calling thread.
    """
    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, "thread", retry, bucket)
    total = _total_if_known(items)
    deadline = (time.monotonic() + timeout) if timeout is not None else None
    results: list[Any] = []
    completed = 0
    failures = 0
    timed_out = False
    aborted = False

    store = (
        _open_checkpoint(checkpoint, fn, checkpoint_key)
        if checkpoint is not None
        else None
    )
    if worker_init is not None:
        worker_init()

    try:
        for idx, item in enumerate(items):
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            results.append(_PENDING)
            if store is not None:
                cached = store.lookup(idx, item)
                if cached is not None:
                    results[idx] = cached[0]
                    completed += 1
                    if on_progress:
                        on_progress(completed, _progress_total(total, results))
                    continue
            if bucket is not None:
                budget: float | None = None
                if deadline is not None:
                    budget = max(0.0, deadline - time.monotonic())
                if not bucket.wait(timeout=budget):
                    assert timeout is not None
                    results[idx] = _timeout_failure(timeout, idx)
                    timed_out = True
                    break
            outcome = task_fn(item)
            if outcome.error is not None:
                results[idx] = _Failure(outcome.error)
                failures += 1
            else:
                results[idx] = outcome.value
                if store is not None:
                    store.put(idx, results[idx])
            completed += 1
            if on_progress:
                on_progress(completed, _progress_total(total, results))
            if max_errors is not None and failures >= max_errors:
                aborted = True
                break

        # Same no-drain policy as the windowed engine: never touch the
        # source after the stop.
        if timed_out and total is not None:
            assert timeout is not None
            for idx in range(len(results), total):
                results.append(_timeout_failure(timeout, idx))
        elif aborted and total is not None:
            reason = f"aborted after {failures} failures (max_errors={max_errors})"
            results.extend(
                _Failure(Aborted(reason)) for _ in range(total - len(results))
            )
    finally:
        if store is not None:
            store.close()

    return ParallelResult(results, timed_out=timed_out, aborted=aborted)


def _sequential_iter(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    rate_limit: Limiter | RateLimit | float | None,
    retry: Retry | None,
    on_progress: Callable[[int, int], None] | None,
    max_errors: int | None,
    worker_init: Callable[[], None] | None,
) -> Iterator[ItemResult[Any]]:
    """Streaming twin of the debug engine — inline, inherently ordered."""
    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, "thread", retry, bucket)
    total = _total_if_known(items)
    if worker_init is not None:
        worker_init()
    failures = 0
    for idx, item in enumerate(items):
        if bucket:
            bucket.wait()
        outcome = task_fn(item)
        result = _item_result(idx, outcome)
        if on_progress:
            done = idx + 1
            on_progress(done, total if total is not None else done)
        yield result
        if not result.ok:
            failures += 1
            if max_errors is not None and failures >= max_errors:
                return


def _apply_star(fn: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    """Call ``fn(*args)`` — module-level so the partial pickles iff *fn* does."""
    return fn(*args)


def parallel_starmap[R](
    fn: Callable[..., R],
    items: Iterable[tuple[Any, ...]],
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    sequential: bool = False,
    worker_init: Callable[[], None] | None = None,
    max_tasks_per_worker: int | None = None,
) -> ParallelResult[R]:
    """Like ``parallel_map`` but unpacks each item as ``fn(*args)``.

    Example::

        def add(a, b): return a + b
        parallel_starmap(add, [(1, 2), (3, 4)])  # [3, 7]
    """
    if executor == "process":
        resolved = _resolve_process_target(fn)
        if resolved is not None:
            module_name, qualname = resolved
            return parallel_map(
                functools.partial(
                    _call_resolved_args,
                    module_name=module_name,
                    qualname=qualname,
                ),
                items,
                workers=workers,
                executor=executor,
                rate_limit=rate_limit,
                timeout=timeout,
                on_progress=on_progress,
                batch_size=batch_size,
                retry=retry,
                sequential=sequential,
                worker_init=worker_init,
                max_tasks_per_worker=max_tasks_per_worker,
            )

    # Wrap fn instead of packing items: the input passes through
    # untouched, so starmap keeps the engine contract — lazy
    # consumption, sized-ness, no-drain. (The old packing list-comp
    # materialized generators before the engine ever saw them.)
    return parallel_map(
        functools.partial(_apply_star, fn),
        items,
        workers=workers,
        executor=executor,
        rate_limit=rate_limit,
        timeout=timeout,
        on_progress=on_progress,
        batch_size=batch_size,
        retry=retry,
        sequential=sequential,
        worker_init=worker_init,
        max_tasks_per_worker=max_tasks_per_worker,
    )


def parallel_iter[T, R](
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    ordered: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    max_errors: int | None = None,
    sequential: bool = False,
    worker_init: Callable[[], None] | None = None,
    max_tasks_per_worker: int | None = None,
) -> Iterator[ItemResult[R]]:
    """Execute *fn* over *items* in parallel, yielding ``ItemResult`` in
    completion order (or input order with ``ordered=True``).

    Results stream as tasks finish — a bounded window of items is in
    flight at any moment, so memory stays constant and a straggler delays
    only itself, never the items behind it. Input is consumed lazily,
    exactly one window ahead of the yields; generators are never
    materialized.

    ``batch_size`` sets the window: the maximum number of
    submitted-but-unyielded items (default ``2 × workers``). It is a
    memory/lookahead bound, not a chunk size — there are no barriers.

    With ``ordered=True`` completed items that arrive early wait in a
    reorder buffer. The window bounds in-flight *plus* buffered
    (``in_flight + buffered <= window``), so memory stays constant even
    when one slow item holds back the stream — admission simply stalls
    until it completes. That stall is backpressure, not a bug.

    ``on_progress`` fires per *completed* item, in completion order —
    with ``ordered=True`` that is decoupled from yield order (item 5 can
    be counted done while item 0 is still unyielded). For unsized inputs
    ``total`` is the number of items consumed from the source so far,
    not the final count.

    ``max_errors`` stops the stream early: once that many failures have
    been yielded (counted after retries are exhausted), admission stops
    and the stream ends — no placeholder items for unseen input. A
    consumer detects the abort by counting error items. With
    ``ordered=True`` the stream still ends only after the Nth failure is
    yielded *in input order* — failures that completed out of order wait
    for the items ahead of them to finish first. Admission has already
    stopped by then, so the extra wait is bounded to the in-flight window
    in *items* — but not in time: a task that never completes blocks the
    ordered stream, exactly as it blocks every other call in this
    library. Put timeouts inside your function (sync) or use
    ``task_timeout`` (async); for the promptest abort on a dead API, use
    the default unordered mode. Completed-but-unyielded successes behind
    the ending failure are discarded — order cannot be preserved and
    delivered past a stop.

    Breaking out of the loop closes the generator: submission stops and
    not-yet-started tasks are cancelled. Tasks already running in a
    worker thread or process cannot be interrupted and finish in the
    background.

    ``sequential=True`` runs every item inline in the calling thread
    (debug mode — no pool, real stack traces; inherently in input
    order). ``worker_init`` runs once per worker before it takes tasks;
    ``max_tasks_per_worker`` recycles process workers. Caller
    contextvars are visible inside thread-executor tasks.

    Example::

        for item in parallel_iter(process, huge_list):
            if item.ok:
                db.save(item.value)
            else:
                log_error(item.index, item.error)
    """
    _validate_common(workers, executor, batch_size)
    _validate_max_errors(max_errors)
    if not sequential:
        _validate_worker_options(executor, worker_init, max_tasks_per_worker)

    if sequential:
        yield from _sequential_iter(
            fn,
            items,
            rate_limit=rate_limit,
            retry=retry,
            on_progress=on_progress,
            max_errors=max_errors,
            worker_init=worker_init,
        )
        return

    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, executor, retry, bucket)
    window = (
        batch_size
        if batch_size is not None
        else 2 * _effective_workers(workers, executor)
    )
    total = _total_if_known(items)
    source = enumerate(items)
    seen = 0
    completed = 0
    failures = 0
    yielded_failures = 0
    in_flight: dict[Future[_Outcome], int] = {}
    buffered: dict[int, ItemResult[R]] = {}
    next_yield = 0

    pool = _make_pool(executor, workers, worker_init, max_tasks_per_worker)

    def _submit_next() -> bool:
        nonlocal seen
        try:
            idx, item = next(source)
        except StopIteration:
            return False
        seen += 1
        if bucket:
            bucket.wait()
        in_flight[_submit_task(pool, executor, task_fn, item)] = idx
        return True

    def _failures_pending() -> int:
        # Completed-but-unprocessed failures, peeked without consuming
        # (Future.result() on a done future is idempotent). With a rate
        # limiter, filling the window can take whole seconds — a dead API
        # must stop admission mid-fill, not after the window is paid for.
        n = 0
        for f in list(in_flight):
            if f.done():
                try:
                    if f.result().error is not None:
                        n += 1
                except Exception:
                    n += 1
        return n

    def _admit() -> None:
        # The engine invariant: in_flight + buffered never exceeds the
        # window. Gating admission on the sum (not on completions) is what
        # keeps ordered mode bounded when a straggler blocks the buffer.
        while len(in_flight) + len(buffered) < window:
            if max_errors is not None and failures + _failures_pending() >= max_errors:
                return  # aborting — no new work
            if not _submit_next():
                return

    def _yield_ends_stream(item_result: ItemResult[R]) -> bool:
        nonlocal yielded_failures
        if item_result.ok or max_errors is None:
            return False
        yielded_failures += 1
        return yielded_failures >= max_errors

    try:
        _admit()
        while in_flight:
            done, _pending = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                idx = in_flight.pop(future)
                result: ItemResult[R]
                try:
                    result = _item_result(idx, future.result())
                except Exception as exc:
                    result = ItemResult(idx, error=exc)
                if not result.ok:
                    failures += 1
                completed += 1
                if on_progress:
                    on_progress(completed, total if total is not None else seen)
                if ordered:
                    buffered[idx] = result
                else:
                    yield result
                    if _yield_ends_stream(result):
                        return
                    _admit()
            if ordered:
                while next_yield in buffered:
                    ordered_result = buffered.pop(next_yield)
                    next_yield += 1
                    yield ordered_result
                    if _yield_ends_stream(ordered_result):
                        return
                _admit()
    finally:
        # Runs on exhaustion, on caller break (generator close), and when
        # the items iterator itself raises — in-flight work is abandoned,
        # unstarted futures are cancelled, the error (if any) propagates.
        pool.shutdown(wait=False, cancel_futures=True)
