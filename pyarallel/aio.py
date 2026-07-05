"""Async engine: mirror of the sync API.

Uses ``asyncio.TaskGroup`` for structured concurrency and
``asyncio.Semaphore`` for concurrency control.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from ._plan import (
    _PENDING,
    _plan_collected_map,
    _progress_total,
)
from .checkpoint import CheckpointError, _CheckpointStore, _task_signature
from .limiter import Limiter, _as_limiter
from .policies import RateLimit, Retry
from .result import ItemResult, ParallelResult, _Failure


async def _async_run_with_retry(
    fn: Callable[..., Any],
    item: Any,
    retry: Retry,
    task_timeout: float | None = None,
    limiter: Limiter | None = None,
) -> Any:
    """Call async *fn(item)*, retrying with backoff or server-driven waits.

    Every retry attempt is a fresh API call, so it draws a fresh token from
    the shared *limiter* (the first attempt's token was paid before entry).
    A server-mandated wait (``Retry.wait_from``) also pauses the limiter —
    even on the final attempt, so a 429 from a task that is about to give
    up still slows the rest of the pool.
    """
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            if attempt > 0 and limiter is not None:
                await limiter.wait_async()
            if task_timeout is not None:
                return await asyncio.wait_for(fn(item), timeout=task_timeout)
            return await fn(item)
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
                    await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def async_parallel_map[T, R](
    fn: Callable[[T], Awaitable[R]],
    items: Iterable[T],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    checkpoint: str | Path | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable.
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` spec, ops-per-second as a number, or a
            shared ``Limiter`` instance to draw from one budget across
            multiple calls.
        task_timeout: Per-task timeout in seconds (each individual task).
        on_progress: ``callback(completed, total)`` after each task.
            When ``items`` has no known length and ``batch_size`` is set,
            ``total`` is the number of items seen so far rather than the
            final input size.
        batch_size: Process items in chunks. With ``batch_size`` set,
            unsized iterables (for example generators) are consumed lazily
            one batch at a time.
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
        ``ParallelResult`` — same container as the sync API.
    """
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")

    plan = _plan_collected_map(items, batch_size)
    if plan.total == 0:
        return ParallelResult([])
    results = plan.results
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)
    completed = 0

    store = (
        _CheckpointStore(checkpoint, _task_signature(fn))
        if checkpoint is not None
        else None
    )
    fingerprints: dict[int, bytes] = {}

    async def _run(i: int, item: Any) -> None:
        nonlocal completed
        async with semaphore:
            if limiter:
                await limiter.wait_async()
            try:
                if retry is not None:
                    result = await _async_run_with_retry(
                        fn, item, retry, task_timeout=task_timeout, limiter=limiter
                    )
                elif task_timeout is not None:
                    result = await asyncio.wait_for(fn(item), timeout=task_timeout)
                else:
                    result = await fn(item)
            except Exception as exc:
                results[i] = _Failure(exc)
            else:
                results[i] = result
                # Outside the try: a checkpoint write failure raises
                # CheckpointError (aborting the TaskGroup) instead of
                # mislabeling a genuine success as an item failure.
                if store is not None:
                    store.put(i, fingerprints.pop(i), result)
            completed += 1
            if on_progress:
                on_progress(completed, _progress_total(plan.total, results))

    try:
        for batch in plan.batches:
            if batch_size is not None:
                results.extend([_PENDING] * len(batch))
            async with asyncio.TaskGroup() as tg:
                for i, item in batch:
                    if store is not None:
                        fp = _CheckpointStore.fingerprint(item)
                        cached = store.get(i, fp)
                        if cached is not None:
                            results[i] = cached[0]
                            completed += 1
                            if on_progress:
                                on_progress(
                                    completed, _progress_total(plan.total, results)
                                )
                            continue
                        fingerprints[i] = fp
                    tg.create_task(_run(i, item))
    except* CheckpointError as eg:
        # The TaskGroup wraps child exceptions in an ExceptionGroup; re-raise
        # the CheckpointError plainly so `except CheckpointError` works the
        # same for sync and async callers, preserving its original cause.
        error = eg.exceptions[0]
        raise error from error.__cause__
    finally:
        if store is not None:
            store.close()

    return ParallelResult(results)


async def async_parallel_starmap[R](
    fn: Callable[..., Awaitable[R]],
    items: Iterable[tuple[Any, ...]],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> ParallelResult[R]:
    """Like ``async_parallel_map`` but unpacks each item as ``fn(*args)``."""

    async def _unpack(args: tuple[Any, ...]) -> R:
        return await fn(*args)

    return await async_parallel_map(
        _unpack,
        items,
        concurrency=concurrency,
        rate_limit=rate_limit,
        task_timeout=task_timeout,
        on_progress=on_progress,
        batch_size=batch_size,
        retry=retry,
    )


async def async_parallel_iter[T, R](
    fn: Callable[[T], Awaitable[R]],
    items: Iterable[T],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    task_timeout: float | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> AsyncIterator[ItemResult[R]]:
    """Execute async *fn* over *items*, yielding ``ItemResult`` in
    completion order.

    Results stream as tasks finish — a bounded window of items is in
    flight at any moment, so memory stays constant and a straggler delays
    only itself, never the items behind it. Input is consumed lazily,
    exactly one window ahead of the yields; generators are never
    materialized.

    ``batch_size`` sets the window: the maximum number of
    started-but-unyielded tasks (default ``2 × concurrency``). It is a
    memory/lookahead bound, not a chunk size — there are no barriers.

    Breaking out of the loop closes the generator: submission stops and
    in-flight tasks are cancelled (async tasks, unlike threads, are
    genuinely cancellable).
    """
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")

    window = batch_size if batch_size is not None else 2 * concurrency
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)

    async def _run(item: Any) -> R:
        async with semaphore:
            if limiter:
                await limiter.wait_async()
            if retry is not None:
                result: R = await _async_run_with_retry(
                    fn, item, retry, task_timeout=task_timeout, limiter=limiter
                )
                return result
            if task_timeout is not None:
                return await asyncio.wait_for(fn(item), timeout=task_timeout)
            return await fn(item)

    source = enumerate(items)
    in_flight: dict[asyncio.Task[R], int] = {}

    def _submit_next() -> bool:
        try:
            idx, item = next(source)
        except StopIteration:
            return False
        in_flight[asyncio.create_task(_run(item))] = idx
        return True

    try:
        while len(in_flight) < window and _submit_next():
            pass
        while in_flight:
            done, _pending = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                idx = in_flight.pop(task)
                result: ItemResult[R]
                try:
                    result = ItemResult(idx, value=task.result())
                except Exception as exc:
                    result = ItemResult(idx, error=exc)
                yield result
                _submit_next()
    finally:
        # Runs on exhaustion, on caller break (generator close), and when
        # the items iterator itself raises: cancel everything in flight.
        for task in in_flight:
            task.cancel()
        for task in in_flight:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
