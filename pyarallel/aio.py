"""Async engine: mirror of the sync API.

Uses ``asyncio.TaskGroup`` for structured concurrency and
``asyncio.Semaphore`` for concurrency control.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from ._plan import (
    _append_timeout_failures,
    _mark_timeout_indices,
    _plan_collected_map,
    _progress_total,
    _timeout_failure,
    _total_if_known,
    _validate_max_errors,
)
from .checkpoint import CheckpointError, _open_checkpoint
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


async def _async_execute_outcome(
    fn: Callable[..., Any],
    item: Any,
    retry: Retry | None = None,
    task_timeout: float | None = None,
    limiter: Limiter | None = None,
) -> _Outcome:
    """Run one async item to its final outcome, counting attempts and timing.

    Async twin of ``core._execute_outcome``: the clock starts here (queue
    and semaphore wait excluded) and stops at the final success or failure,
    retry backoff sleeps included. Task exceptions travel inside the
    outcome; ``CancelledError`` still propagates.

    Every retry attempt is a fresh API call, so it draws a fresh token from
    the shared *limiter* (the first attempt's token was paid before entry).
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
                await limiter.wait_async()
            if task_timeout is not None:
                value = await asyncio.wait_for(fn(item), timeout=task_timeout)
            else:
                value = await fn(item)
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
                    await asyncio.sleep(delay)
        else:
            return _Outcome(value, None, made, time.perf_counter() - start)
    return _Outcome(None, last_exc, made, time.perf_counter() - start)


async def async_parallel_map[T, R](
    fn: Callable[[T], Awaitable[R]],
    items: Iterable[T],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    checkpoint: str | Path | None = None,
    checkpoint_key: Callable[[T], str | int | bytes] | None = None,
    max_errors: int | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable.
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` spec, ops-per-second as a number, or a
            shared ``Limiter`` instance to draw from one budget across
            multiple calls.
        timeout: Total wall-clock timeout in seconds for the whole
            operation — the mirror of the sync ``timeout``. On expiry,
            unfinished tasks are cancelled and their slots (plus any
            unseen lazy input) are marked with ``TimeoutError`` failures;
            everything that completed keeps its result.
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
            ``CheckpointError``. Rows are positional by default —
            reordering or inserting inputs forces shifted items to
            recompute; pass ``checkpoint_key`` when inputs evolve.
        checkpoint_key: Stable identity for each item (returns str, int,
            or bytes) — checkpoint rows are then keyed by identity, not
            position. Requires ``checkpoint=``. Duplicate keys raise
            ``CheckpointError``; the item fingerprint check still applies.
        max_errors: Abort after this many failures (counted after retries
            are exhausted). Tasks are then created through a bounded
            window rather than all upfront, so a dead API costs tens of
            calls, not thousands. Items that never ran are marked with
            ``Aborted`` — distinguishable from real failures. The source
            is never consumed after the stop: sized inputs get one
            ``Aborted`` entry per unseen item, unsized inputs return a
            result covering only the items actually pulled.

    Returns:
        ``ParallelResult`` — same container as the sync API.
    """
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    _validate_max_errors(max_errors)
    if checkpoint_key is not None and checkpoint is None:
        raise ValueError("checkpoint_key requires checkpoint= to be set")

    if max_errors is not None:
        return await _async_collected_map_windowed(
            fn,
            items,
            concurrency=concurrency,
            rate_limit=rate_limit,
            timeout=timeout,
            task_timeout=task_timeout,
            on_progress=on_progress,
            batch_size=batch_size,
            retry=retry,
            checkpoint=checkpoint,
            checkpoint_key=checkpoint_key,
            max_errors=max_errors,
        )

    plan = _plan_collected_map(items, batch_size)
    if plan.total == 0:
        return ParallelResult([])
    results = plan.results
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)
    completed = 0

    store = (
        _open_checkpoint(checkpoint, fn, checkpoint_key)
        if checkpoint is not None
        else None
    )

    async def _run(i: int, item: Any) -> None:
        nonlocal completed
        async with semaphore:
            if limiter:
                await limiter.wait_async()
            outcome = await _async_execute_outcome(
                fn, item, retry, task_timeout=task_timeout, limiter=limiter
            )
            if outcome.error is not None:
                results[i] = _Failure(outcome.error)
            else:
                results[i] = outcome.value
                # A checkpoint write failure raises CheckpointError
                # (aborting the TaskGroup) instead of mislabeling a
                # genuine success as an item failure.
                if store is not None:
                    store.put(i, outcome.value)
            completed += 1
            if on_progress:
                on_progress(completed, _progress_total(plan.total, results))

    async def _consume() -> None:
        nonlocal completed
        for batch in plan.batches:
            if batch_size is not None:
                results.extend([_PENDING] * len(batch))
            async with asyncio.TaskGroup() as tg:
                for i, item in batch:
                    if store is not None:
                        cached = store.lookup(i, item)
                        if cached is not None:
                            results[i] = cached[0]
                            completed += 1
                            if on_progress:
                                on_progress(
                                    completed, _progress_total(plan.total, results)
                                )
                            continue
                    tg.create_task(_run(i, item))

    try:
        if timeout is not None:
            async with asyncio.timeout(timeout):
                await _consume()
        else:
            await _consume()
    except* CheckpointError as eg:
        # The TaskGroup wraps child exceptions in an ExceptionGroup; re-raise
        # the CheckpointError plainly so `except CheckpointError` works the
        # same for sync and async callers, preserving its original cause.
        error = eg.exceptions[0]
        raise error from error.__cause__
    except* TimeoutError:
        # asyncio.timeout expiry — the TaskGroup has already cancelled its
        # children. Mirror of the sync contract: mark unfinished slots and
        # unseen lazy input with the same failure text.
        assert timeout is not None
        _mark_timeout_indices(results, range(len(results)), timeout)
        _append_timeout_failures(results, plan.remaining, timeout)
    finally:
        if store is not None:
            store.close()

    return ParallelResult(results)


async def _async_collected_map_windowed(
    fn: Callable[..., Awaitable[Any]],
    items: Iterable[Any],
    *,
    concurrency: int,
    rate_limit: Limiter | RateLimit | float | None,
    timeout: float | None,
    task_timeout: float | None,
    on_progress: Callable[[int, int], None] | None,
    batch_size: int | None,
    retry: Retry | None,
    checkpoint: str | Path | None,
    checkpoint_key: Callable[[Any], str | int | bytes] | None,
    max_errors: int,
) -> ParallelResult[Any]:
    """Async collected map with bounded admission — the ``max_errors`` engine.

    Mirrors the sync version: creating every task upfront would let a
    dead API burn thousands of calls before the Nth failure is observed.
    """
    window = batch_size if batch_size is not None else 2 * concurrency
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)
    total = _total_if_known(items)
    source = iter(items)
    results: list[Any] = []
    in_flight: dict[asyncio.Task[_Outcome], int] = {}
    completed = 0
    failures = 0
    aborted = False
    timed_out = False

    store = (
        _open_checkpoint(checkpoint, fn, checkpoint_key)
        if checkpoint is not None
        else None
    )

    async def _run(item: Any) -> _Outcome:
        async with semaphore:
            if limiter:
                await limiter.wait_async()
            return await _async_execute_outcome(
                fn, item, retry, task_timeout=task_timeout, limiter=limiter
            )

    def _report() -> None:
        if on_progress:
            on_progress(completed, _progress_total(total, results))

    def _submit_next() -> bool:
        nonlocal completed
        while True:
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
            in_flight[asyncio.create_task(_run(item))] = idx
            return True

    def _absorb(task: asyncio.Task[_Outcome], idx: int) -> None:
        nonlocal completed, failures, aborted
        error: Exception | None
        try:
            outcome = task.result()
        except Exception as exc:
            error = exc  # infrastructure failure — task errors ride the outcome
        else:
            error = outcome.error
        if error is not None:
            results[idx] = _Failure(error)
            failures += 1
            if failures >= max_errors:
                aborted = True
        else:
            results[idx] = outcome.value
            # Outside the except: a checkpoint write failure raises
            # CheckpointError instead of mislabeling a success as a failure.
            if store is not None:
                store.put(idx, results[idx])
        completed += 1
        _report()

    async def _drive() -> None:
        while True:
            # No mid-fill absorption here (unlike the sync twin): task
            # creation never blocks — limiter waits happen inside tasks,
            # which are cancelled on abort before they can call the API.
            while not aborted and len(in_flight) < window and _submit_next():
                pass
            if not in_flight:
                break
            done, _pending = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                _absorb(task, in_flight.pop(task))
            if aborted:
                break

    try:
        try:
            if timeout is not None:
                async with asyncio.timeout(timeout):
                    await _drive()
            else:
                await _drive()
        except TimeoutError:
            timed_out = True

        if timed_out:
            assert timeout is not None
            # Salvage completions that raced the deadline, drop the rest.
            for task in [t for t in in_flight if t.done()]:
                _absorb(task, in_flight.pop(task))
            _mark_timeout_indices(results, in_flight.values(), timeout)
            if total is not None:
                for idx in range(len(results), total):
                    results.append(_timeout_failure(timeout, idx))
        elif aborted:
            # Salvage completions that raced the stop, cancel the rest.
            for task in [t for t in in_flight if t.done()]:
                _absorb(task, in_flight.pop(task))
            reason = f"aborted after {failures} failures (max_errors={max_errors})"
            for idx in in_flight.values():
                if results[idx] is _PENDING:
                    results[idx] = _Failure(Aborted(reason))
            # Never touch the source after the stop: a poison, blocking, or
            # infinite input must not be consumed post-abort. Sized inputs
            # get placeholders by count; unsized yield a shorter result.
            if total is not None:
                results.extend(
                    _Failure(Aborted(reason)) for _ in range(total - len(results))
                )
    finally:
        for task in in_flight:
            task.cancel()
        for task in in_flight:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if store is not None:
            store.close()

    return ParallelResult(results)


async def async_parallel_starmap[R](
    fn: Callable[..., Awaitable[R]],
    items: Iterable[tuple[Any, ...]],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
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
        timeout=timeout,
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
    ordered: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    max_errors: int | None = None,
) -> AsyncIterator[ItemResult[R]]:
    """Execute async *fn* over *items*, yielding ``ItemResult`` in
    completion order (or input order with ``ordered=True``).

    Results stream as tasks finish — a bounded window of items is in
    flight at any moment, so memory stays constant and a straggler delays
    only itself, never the items behind it. Input is consumed lazily,
    exactly one window ahead of the yields; generators are never
    materialized.

    ``batch_size`` sets the window: the maximum number of
    started-but-unyielded tasks (default ``2 × concurrency``). It is a
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

    Cleanup requires closing the generator: unlike sync generators,
    ``break`` alone does **not** finalize an async generator promptly —
    Python defers it to the event loop's shutdown. Wrap the stream in
    ``contextlib.aclosing`` (or call ``await stream.aclose()``) to stop
    submission and cancel in-flight tasks the moment you stop consuming::

        async with contextlib.aclosing(async_parallel_iter(fn, items)) as s:
            async for item in s:
                if found(item):
                    break  # aclosing cancels in-flight tasks here
    """
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    _validate_max_errors(max_errors)

    window = batch_size if batch_size is not None else 2 * concurrency
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)

    async def _run(item: Any) -> _Outcome:
        async with semaphore:
            if limiter:
                await limiter.wait_async()
            return await _async_execute_outcome(
                fn, item, retry, task_timeout=task_timeout, limiter=limiter
            )

    total = _total_if_known(items)
    source = enumerate(items)
    seen = 0
    completed = 0
    failures = 0
    yielded_failures = 0
    in_flight: dict[asyncio.Task[_Outcome], int] = {}
    buffered: dict[int, ItemResult[R]] = {}
    next_yield = 0

    def _submit_next() -> bool:
        nonlocal seen
        try:
            idx, item = next(source)
        except StopIteration:
            return False
        seen += 1
        in_flight[asyncio.create_task(_run(item))] = idx
        return True

    def _admit() -> None:
        # The engine invariant: in_flight + buffered never exceeds the
        # window. Gating admission on the sum (not on completions) is what
        # keeps ordered mode bounded when a straggler blocks the buffer.
        #
        # No mid-fill failure peek here (unlike the sync driver): filling
        # never blocks — limiter waits happen inside tasks, which are
        # cancelled on abort before they can call the API.
        if max_errors is not None and failures >= max_errors:
            return  # aborting — no new work
        while len(in_flight) + len(buffered) < window and _submit_next():
            pass

    def _yield_ends_stream(item_result: ItemResult[R]) -> bool:
        nonlocal yielded_failures
        if item_result.ok or max_errors is None:
            return False
        yielded_failures += 1
        return yielded_failures >= max_errors

    try:
        _admit()
        while in_flight:
            done, _pending = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                idx = in_flight.pop(task)
                result: ItemResult[R]
                try:
                    result = _item_result(idx, task.result())
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
        # the items iterator itself raises: cancel everything in flight.
        for task in in_flight:
            task.cancel()
        for task in in_flight:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
