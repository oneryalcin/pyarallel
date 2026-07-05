"""Sync engine: parallel_map, parallel_starmap, parallel_iter.

Simple, explicit parallel execution over ``concurrent.futures``. No magic
type detection, no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import functools
import os
import pickle
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
from pathlib import Path
from typing import Any, Literal

from ._plan import (
    _PENDING,
    _append_timeout_failures,
    _mark_timeout_indices,
    _plan_collected_map,
    _progress_total,
    _total_if_known,
)
from ._procexec import _call_resolved, _call_resolved_args, _resolve_process_target
from .checkpoint import _CheckpointStore, _task_signature
from .limiter import Limiter, _as_limiter
from .policies import RateLimit, Retry
from .result import ItemResult, ParallelResult, _Failure

ExecutorType = Literal["thread", "process"]


def _run_with_retry(
    fn: Callable[..., Any],
    item: Any,
    retry: Retry,
    limiter: Limiter | None = None,
) -> Any:
    """Call *fn(item)*, retrying on failure with backoff or server-driven waits.

    Every retry attempt is a fresh API call, so it draws a fresh token from
    the shared *limiter* (the first attempt's token was paid at submission).
    A server-mandated wait (``Retry.wait_from``) also pauses the limiter —
    even on the final attempt, so a 429 from a task that is about to give
    up still slows the rest of the pool.
    """
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            if attempt > 0 and limiter is not None:
                limiter.wait()
            return fn(item)
        except Exception as exc:
            last_exc = exc
            if not retry._should_retry(exc):
                raise
            server_wait = retry._server_wait(exc)
            if server_wait is not None and limiter is not None:
                limiter.pause(server_wait)
            if attempt < retry.attempts - 1:
                delay = (
                    server_wait if server_wait is not None else retry._delay(attempt)
                )
                if delay > 0:
                    time.sleep(delay)
    raise last_exc  # type: ignore[misc]


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
) -> Callable[..., Any]:
    """Compose the per-item callable: process-safe target first, then retry.

    Order matters — the retry wrapper must live *outside* the process
    resolution so retries happen inside the worker, around the real call.

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
    if retry is not None:
        task_fn = functools.partial(
            _run_with_retry,
            task_fn,
            retry=retry,
            limiter=limiter if executor == "thread" else None,
        )
    return task_fn


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
        timeout: Total wall-clock timeout in seconds for the whole operation.
        on_progress: ``callback(completed, total)`` fired after each task.
            When ``items`` has no known length and ``batch_size`` is set,
            ``total`` is the number of items seen so far rather than the
            final input size.
        batch_size: Process items in chunks of this size. With ``batch_size``
            set, unsized iterables (for example generators) are consumed
            lazily one batch at a time. Without batching, all items are
            submitted at once.
        retry: ``Retry`` object for per-item retry with backoff.
        checkpoint: Path to a checkpoint file (created if missing, SQLite).
            Completed item results are stored there; rerunning the same
            call resumes — cached items load from disk, failed and unseen
            items execute. The file is bound to the mapped function's
            identity: resuming with a different function raises
            ``CheckpointError``. Items and results must be picklable; a
            result that cannot be checkpointed aborts the run with
            ``CheckpointError``. Rows are positional — reordering or
            inserting inputs forces shifted items to recompute.

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    _validate_common(workers, executor, batch_size)

    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, executor, retry, bucket)
    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    completed = 0
    deadline = (time.monotonic() + timeout) if timeout is not None else None
    timed_out = False

    plan = _plan_collected_map(items, batch_size)
    if plan.total == 0:
        return ParallelResult([])
    results = plan.results

    store = (
        _CheckpointStore(checkpoint, _task_signature(fn))
        if checkpoint is not None
        else None
    )
    fingerprints: dict[int, bytes] = {}
    pool = pool_cls(max_workers=workers)
    try:
        for batch in plan.batches:
            if batch_size is not None:
                results.extend([_PENDING] * len(batch))

            chunk_timeout: float | None = None
            if deadline is not None:
                assert timeout is not None
                chunk_timeout = max(0.0, deadline - time.monotonic())
                if chunk_timeout <= 0:
                    _mark_timeout_indices(
                        results, (idx for idx, _item in batch), timeout
                    )
                    _append_timeout_failures(results, plan.remaining, timeout)
                    if batch_size is None:
                        _mark_timeout_indices(results, range(len(results)), timeout)
                    timed_out = True
                    break

            futures: dict[Future[R], int] = {}
            for idx, item in batch:
                if store is not None:
                    fp = _CheckpointStore.fingerprint(item)
                    cached = store.get(idx, fp)
                    if cached is not None:
                        results[idx] = cached[0]
                        completed += 1
                        if on_progress:
                            on_progress(completed, _progress_total(plan.total, results))
                        continue
                    fingerprints[idx] = fp
                if bucket:
                    bucket.wait()
                futures[pool.submit(task_fn, item)] = idx

            try:
                for future in as_completed(futures, timeout=chunk_timeout):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        results[idx] = _Failure(exc)
                    else:
                        # Outside the try: a checkpoint write failure raises
                        # CheckpointError instead of mislabeling a genuine
                        # success as an item failure.
                        if store is not None:
                            store.put(idx, fingerprints.pop(idx), results[idx])
                    completed += 1
                    if on_progress:
                        on_progress(completed, _progress_total(plan.total, results))
            except TimeoutError:
                assert timeout is not None
                timed_out = True
                for f, _idx in futures.items():
                    if not f.done():
                        f.cancel()
                _mark_timeout_indices(results, futures.values(), timeout)
                _append_timeout_failures(results, plan.remaining, timeout)
                break
    finally:
        pool.shutdown(wait=not timed_out, cancel_futures=timed_out)
        if store is not None:
            store.close()

    return ParallelResult(results)


def _unpack_call(fn_and_args: tuple[Callable[..., Any], tuple[Any, ...]]) -> Any:
    """Unpack and call fn(*args) — picklable for process executor."""
    fn, args = fn_and_args
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
            )

    packed = [(fn, args) for args in items]
    return parallel_map(
        _unpack_call,
        packed,
        workers=workers,
        executor=executor,
        rate_limit=rate_limit,
        timeout=timeout,
        on_progress=on_progress,
        batch_size=batch_size,
        retry=retry,
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

    ``on_progress`` fires per *completed* item (before its yield). For
    unsized inputs ``total`` is the number of items consumed from the
    source so far, not the final count.

    Breaking out of the loop closes the generator: submission stops and
    not-yet-started tasks are cancelled. Tasks already running in a
    worker thread or process cannot be interrupted and finish in the
    background.

    Example::

        for item in parallel_iter(process, huge_list):
            if item.ok:
                db.save(item.value)
            else:
                log_error(item.index, item.error)
    """
    _validate_common(workers, executor, batch_size)

    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, executor, retry, bucket)
    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    window = (
        batch_size
        if batch_size is not None
        else 2 * _effective_workers(workers, executor)
    )
    total = _total_if_known(items)
    source = enumerate(items)
    seen = 0
    completed = 0
    in_flight: dict[Future[R], int] = {}
    buffered: dict[int, ItemResult[R]] = {}
    next_yield = 0

    pool = pool_cls(max_workers=workers)

    def _submit_next() -> bool:
        nonlocal seen
        try:
            idx, item = next(source)
        except StopIteration:
            return False
        seen += 1
        if bucket:
            bucket.wait()
        in_flight[pool.submit(task_fn, item)] = idx
        return True

    def _admit() -> None:
        # The engine invariant: in_flight + buffered never exceeds the
        # window. Gating admission on the sum (not on completions) is what
        # keeps ordered mode bounded when a straggler blocks the buffer.
        while len(in_flight) + len(buffered) < window and _submit_next():
            pass

    try:
        _admit()
        while in_flight:
            done, _pending = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                idx = in_flight.pop(future)
                result: ItemResult[R]
                try:
                    result = ItemResult(idx, value=future.result())
                except Exception as exc:
                    result = ItemResult(idx, error=exc)
                completed += 1
                if on_progress:
                    on_progress(completed, total if total is not None else seen)
                if ordered:
                    buffered[idx] = result
                else:
                    yield result
                    _admit()
            if ordered:
                while next_yield in buffered:
                    yield buffered.pop(next_yield)
                    next_yield += 1
                _admit()
    finally:
        # Runs on exhaustion, on caller break (generator close), and when
        # the items iterator itself raises — in-flight work is abandoned,
        # unstarted futures are cancelled, the error (if any) propagates.
        pool.shutdown(wait=False, cancel_futures=True)
