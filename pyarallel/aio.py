"""Async engine: mirror of the sync API.

One windowed collected-map engine plus a streaming twin, both driven by
``asyncio.wait(FIRST_COMPLETED)``; ``asyncio.Semaphore`` caps
concurrency inside the admission window.

TWIN FILE: ``core.py`` mirrors the engines here in sync form — see its
module docstring for why the duplication is deliberate (the ledger
extraction was reviewed and held). An engine fix here almost always
needs its mirror in ``core.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
)
from pathlib import Path
from typing import Any, TypedDict

from ._run import (
    _mark_timeout_indices,
    _progress_total,
    _RunStop,
    _timeout_failure,
    _total_if_known,
    _validate_max_errors,
    _validate_timeout,
)
from .checkpoint import _open_checkpoint
from .limiter import Limiter, _as_limiter
from .policies import RateLimit, Retry
from .result import (
    _PENDING,
    Aborted,
    Cancelled,
    ItemResult,
    ParallelResult,
    RunStatus,
    _Failure,
    _item_result,
    _Outcome,
    _stored_item_result,
)
from .stop import StopToken

_SOURCE_EXHAUSTED: Any = object()


async def _pull_once(source: AsyncIterator[Any]) -> Any:
    """One anext as a task-safe coroutine — StopAsyncIteration must not
    cross a Task boundary, so exhaustion travels as a sentinel."""
    try:
        return await anext(source)
    except StopAsyncIteration:
        return _SOURCE_EXHAUSTED


def _aiter_source(items: Iterable[Any] | AsyncIterable[Any]) -> AsyncIterator[Any]:
    """Normalize any source to an async iterator (v0.9).

    Async sources — cursors, paginated API generators — are consumed
    directly, so backpressure reaches all the way to the producer: the
    engine pulls one item as one window slot frees, and a million-row
    cursor never materializes. An object that is both ``Iterable`` and
    ``AsyncIterable`` is consumed async — the async protocol is why
    you'd build such a thing.

    Sync iterables are wrapped; the wrapper's ``anext`` runs the pull
    synchronously (no suspension point added), so admission behavior
    and event-loop blocking are exactly as before.

    Closure contract: an *idle* source is never touched or drained —
    but a pull in progress at a stop/close is cancelled, and that
    cancellation runs the source's ``finally`` (standard asyncio
    pipeline semantics). Final closing is the caller's job
    (``aclosing()``).
    """
    if isinstance(items, AsyncIterable):
        return aiter(items)
    iterator = iter(items)

    async def _wrap() -> AsyncIterator[Any]:
        for item in iterator:
            yield item

    return _wrap()


class AsyncMapOptions(TypedDict, total=False):
    """Per-call options of ``async_parallel_map`` — the async decorator
    ``.map()`` surface. An unpassed key inherits the decorator
    default; an explicitly passed key — even ``None`` — overrides."""

    concurrency: int
    rate_limit: Limiter | RateLimit | float | None
    timeout: float | None
    task_timeout: float | None
    on_progress: Callable[[int, int], None] | None
    on_result: Callable[[ItemResult[Any]], None] | None
    window_size: int | None
    retry: Retry | None
    checkpoint: str | Path | None
    checkpoint_key: Callable[[Any], str | int | bytes] | None
    checkpoint_version: str | int | bytes | tuple[str | int | bytes, ...] | None
    max_errors: int | None
    stop: StopToken | None


class AsyncStarmapOptions(TypedDict, total=False):
    """Per-call options of ``async_parallel_starmap`` (no checkpoint)."""

    concurrency: int
    rate_limit: Limiter | RateLimit | float | None
    timeout: float | None
    task_timeout: float | None
    on_progress: Callable[[int, int], None] | None
    on_result: Callable[[ItemResult[Any]], None] | None
    window_size: int | None
    retry: Retry | None


class AsyncStreamOptions(TypedDict, total=False):
    """Per-call options of ``async_parallel_iter`` — ``.stream()``."""

    concurrency: int
    rate_limit: Limiter | RateLimit | float | None
    task_timeout: float | None
    window_size: int | None
    retry: Retry | None
    ordered: bool | None
    on_progress: Callable[[int, int], None] | None
    max_errors: int | None


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
    items: Iterable[T] | AsyncIterable[T],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_result: Callable[[ItemResult[R]], None] | None = None,
    window_size: int | None = None,
    retry: Retry | None = None,
    checkpoint: str | Path | None = None,
    checkpoint_key: Callable[[T], str | int | bytes] | None = None,
    checkpoint_version: str | int | bytes | tuple[str | int | bytes, ...] | None = None,
    max_errors: int | None = None,
    stop: StopToken | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable — sync or async (v0.9). An async source
            (DB cursor, paginated API generator) is consumed directly
            with end-to-end backpressure: one item pulled as one window
            slot frees, nothing materialized. An idle source is never
            touched and a truncated run leaves it un-drained; a pull in
            progress at a stop is cancelled (its ``finally`` runs).
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` spec, ops-per-second as a number, or a
            shared ``Limiter`` instance to draw from one budget across
            multiple calls.
        timeout: Total wall-clock timeout in seconds for the whole
            operation — the mirror of the sync ``timeout``. On expiry,
            unfinished tasks are cancelled and the result's ``timed_out``
            flag is set; unfinished sized slots are marked with
            ``TimeoutError``, while unsized inputs return a shorter
            result — the source is never drained after a stop.
            Everything that completed keeps its result.
        task_timeout: Per-task timeout in seconds (each individual task).
        on_progress: ``callback(completed, total)`` after each task.
            When ``items`` has no known length, ``total`` is the number
            of items *admitted* so far (one window ahead of
            completions) — it grows as the run progresses, so a
            percentage over it is meaningless; pass a sized input for a
            real total.
        on_result: Synchronous ``callback(item_result)`` after each completed
            item, on the event-loop thread and in completion order. Receives
            success/failure metadata; checkpoint hits report ``attempts=0``.
            Exceptions propagate like ``on_progress``. Use
            ``async_parallel_iter`` when the callback itself must be awaited.
        window_size: The admission window — the maximum number of tasks
            created but not yet resolved (default ``2 × concurrency``).
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
            position. Requires ``checkpoint=``. Duplicate keys raise
            ``CheckpointError``; the item fingerprint check still applies.
        max_errors: Abort after this many failures (counted after retries
            are exhausted). Because admission is windowed, a dead API
            costs the abort-trigger point plus one window of calls, not
            thousands. Items that never ran are marked with ``Aborted``
            — distinguishable from real failures — and the result's
            ``aborted`` flag is set. The source is never consumed after
            the stop: sized inputs get one ``Aborted`` entry per unseen
            item, unsized inputs return a result covering only the items
            actually pulled.

    Returns:
        ``ParallelResult`` — same container as the sync API.
    """
    if window_size is not None and window_size < 1:
        raise ValueError(f"window_size must be >= 1, got {window_size}")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    _validate_timeout(timeout, "timeout")
    _validate_timeout(task_timeout, "task_timeout")
    _validate_max_errors(max_errors)
    if checkpoint_version is not None and checkpoint is None:
        raise ValueError("checkpoint_version requires checkpoint=")
    if checkpoint_key is not None and checkpoint is None:
        raise ValueError("checkpoint_key requires checkpoint= to be set")

    return await _async_collected_map(
        fn,
        items,
        concurrency=concurrency,
        rate_limit=rate_limit,
        timeout=timeout,
        task_timeout=task_timeout,
        on_progress=on_progress,
        on_result=on_result,
        window_size=window_size,
        retry=retry,
        checkpoint=checkpoint,
        checkpoint_key=checkpoint_key,
        checkpoint_version=checkpoint_version,
        max_errors=max_errors,
        stop=stop,
    )


async def _async_collected_map(
    fn: Callable[..., Awaitable[Any]],
    items: Iterable[Any] | AsyncIterable[Any],
    *,
    concurrency: int,
    rate_limit: Limiter | RateLimit | float | None,
    timeout: float | None,
    task_timeout: float | None,
    on_progress: Callable[[int, int], None] | None,
    on_result: Callable[[ItemResult[Any]], None] | None,
    window_size: int | None,
    retry: Retry | None,
    checkpoint: str | Path | None,
    checkpoint_key: Callable[[Any], str | int | bytes] | None,
    checkpoint_version: Any = None,
    max_errors: int | None,
    stop: StopToken | None = None,
) -> ParallelResult[Any]:
    """The async collected-map engine: bounded admission through a window.

    Async twin of ``core._collected_map`` — one engine for every
    collected map. Lazy input, at most ``window`` unresolved tasks, the
    source never touched after a stop; the window is also what makes
    ``max_errors`` cheap (abort-trigger plus one window, not thousands
    of upfront task creations).
    """
    window = window_size if window_size is not None else 2 * concurrency
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _as_limiter(rate_limit)
    total = _total_if_known(items)
    source = _aiter_source(items)
    results: list[Any] = []
    # Per-index (attempts, duration), grown in lockstep with results.
    # Default (0, 0.0) = "nothing ran this run" (cache hit / truncation
    # placeholder); completion consumption overwrites with the task's receipt.
    meta: list[tuple[int, float]] = []
    in_flight: dict[asyncio.Task[_Outcome], int] = {}
    completions: asyncio.Queue[
        tuple[asyncio.Task[Any] | None, int, _Outcome | None, BaseException | None]
    ] = asyncio.Queue()
    completed = 0
    failures = 0
    halt = _RunStop()
    # asyncio.timeout() enforces the deadline at await points only; this
    # mirror of it lets the synchronous cached-admission loop bind too.
    deadline = (time.monotonic() + timeout) if timeout is not None else None

    store = (
        _open_checkpoint(checkpoint, fn, checkpoint_key, checkpoint_version)
        if checkpoint is not None
        else None
    )

    # StopToken bridge: stop() may arrive from any thread (a signal
    # handler, a watchdog) — call_soon_threadsafe wakes the driver's
    # wait, where the stop task sits beside the workers.
    stop_task: asyncio.Task[Any] | None = None
    unregister: Callable[[], None] | None = None
    if stop is not None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _wake_driver() -> None:
            # Must work from any thread (signal handlers, watchdogs).
            # The run may have ended between stop()'s callback copy and
            # this call — a closed loop is a no-op, not an error.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(stop_event.set)

        unregister = stop._register(_wake_driver)
        stop_task = asyncio.create_task(stop_event.wait())

    async def _run(item: Any, idx: int) -> _Outcome:
        """Run one item and enqueue its completion before returning.

        ``asyncio.wait`` returns an unordered set. Enqueuing here, on the
        event-loop thread at the exact end of each task, gives callbacks one
        FIFO shared by live work and checkpoint hits while keeping callback
        execution itself on the driver.
        """
        task = asyncio.current_task()
        assert task is not None
        try:
            async with semaphore:
                if limiter:
                    await limiter.wait_async()
                outcome = await _async_execute_outcome(
                    fn, item, retry, task_timeout=task_timeout, limiter=limiter
                )
        except BaseException as exc:
            completions.put_nowait((task, idx, None, exc))
            raise
        completions.put_nowait((task, idx, outcome, None))
        return outcome

    def _report() -> None:
        if on_progress:
            on_progress(completed, _progress_total(total, results))

    def _report_result(idx: int) -> None:
        if on_result:
            attempts, duration = meta[idx]
            on_result(_stored_item_result(idx, results[idx], attempts, duration))

    def _consume_completion(
        event: tuple[
            asyncio.Task[Any] | None,
            int,
            _Outcome | None,
            BaseException | None,
        ],
    ) -> None:
        nonlocal completed, failures
        task, idx, outcome, infrastructure_error = event
        if task is None:  # checkpoint hit
            completed += 1
            _report_result(idx)
            _report()
            return

        in_flight.pop(task, None)
        error: Exception | None
        if infrastructure_error is not None:
            # Mark an infrastructure exception retrieved; task failures from
            # the user function already travel inside _Outcome and never raise.
            task.exception()
            if not isinstance(infrastructure_error, Exception):
                raise infrastructure_error
            error = infrastructure_error
            meta[idx] = (1, 0.0)
        else:
            assert outcome is not None
            error = outcome.error
            meta[idx] = (outcome.attempts, outcome.duration)
        if error is not None:
            results[idx] = _Failure(error)
            failures += 1
            if max_errors is not None and failures >= max_errors:
                halt.stop(RunStatus.ABORTED)
        else:
            assert outcome is not None
            results[idx] = outcome.value
            if store is not None:
                store.put(idx, results[idx])
        completed += 1
        _report_result(idx)
        _report()

    def _drain_completions() -> int:
        drained = 0
        while True:
            try:
                event = completions.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            _consume_completion(event)
            drained += 1

    async def _submit_next() -> bool:
        nonlocal completed
        while True:
            # Cached checkpoint hits consume real time (lookup + key_fn)
            # without ever reaching an await, so asyncio.timeout() cannot
            # preempt them — the deadline must bind here too. (A genuinely
            # async source pull IS preemptible: asyncio.timeout cancels
            # it, so a stuck cursor cannot outlive the deadline — for
            # cancellation-respecting sources; one that swallows the
            # CancelledError and blocks is a hang, as everywhere in
            # asyncio. The cancellation lands in the source and runs its
            # finally — the documented exception to never-touching it.)
            # Stop first: a user's cancel beats the clock.
            if stop is not None and stop.stopped:
                halt.stop(RunStatus.CANCELLED)
                return False
            if deadline is not None and time.monotonic() >= deadline:
                halt.stop(RunStatus.TIMED_OUT)
                return False
            if stop_task is None:
                try:
                    item = await anext(source)
                except StopAsyncIteration:
                    return False
            else:
                # Race the pull against the stop token (Codex adversarial:
                # a wedged cursor made cancel latency = source latency).
                # Cancelling the losing pull lands in the source and runs
                # its finally — the documented closure-contract exception.
                pull = asyncio.ensure_future(_pull_once(source))
                race: set[asyncio.Task[Any]] = {pull, stop_task}
                await asyncio.wait(race, return_when=asyncio.FIRST_COMPLETED)
                if not pull.done():
                    pull.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pull
                    halt.stop(RunStatus.CANCELLED)
                    return False
                item = pull.result()  # source errors propagate here
                if item is _SOURCE_EXHAUSTED:
                    return False
            idx = len(results)
            results.append(_PENDING)
            meta.append((0, 0.0))
            if store is not None:
                cached = store.lookup(idx, item)
                if cached is not None:
                    results[idx] = cached[0]
                    completions.put_nowait((None, idx, None, None))
                    _drain_completions()
                    if halt.stopped:
                        return False
                    continue  # cached — keep looking for work to admit
            task = asyncio.create_task(_run(item, idx))
            in_flight[task] = idx
            return True

    async def _drive() -> None:
        # Same lap as the sync driver (see core.py's DRIVER LOOP map):
        # fill window → stop check → block → absorb → lap. Two laps are
        # simpler here: the deadline gate lives in asyncio.timeout()
        # around this whole call (plus the pre-check for timeout<=0),
        # and there is no mid-fill sweep — task creation never blocks,
        # limiter waits happen inside tasks, which are cancelled on
        # abort before they can call the API.
        while True:
            while not halt.stopped and len(in_flight) < window and await _submit_next():
                pass
            if halt.stopped or not in_flight:
                break
            if stop_task is None:
                _consume_completion(await completions.get())
                _drain_completions()
                continue

            next_completion = asyncio.create_task(completions.get())
            try:
                done, _pending = await asyncio.wait(
                    {next_completion, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # A deadline can cancel the driver after Queue.get() has
                # removed an event but before this frame consumes it. Restore
                # ownership so timeout aftermath can salvage that completion.
                if next_completion.done() and not next_completion.cancelled():
                    completions.put_nowait(next_completion.result())
                raise
            finally:
                if not next_completion.done():
                    next_completion.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await next_completion
            # Completion wins a simultaneous wake, preserving the existing
            # first-classification rule for max_errors versus StopToken.
            stopped_now = stop_task in done
            if next_completion in done:
                _consume_completion(next_completion.result())
                _drain_completions()
            if stopped_now:
                halt.stop(RunStatus.CANCELLED)
            if halt.stopped:
                break

    try:
        if stop is not None and stop.stopped:
            # Stop beats the clock — same ordering as every sync gate.
            halt.stop(RunStatus.CANCELLED)
        elif timeout is not None and timeout <= 0:
            # Already expired: admit no work (asyncio.timeout alone
            # cancels only at the first suspension point).
            halt.stop(RunStatus.TIMED_OUT)
        elif timeout is not None:
            scope = asyncio.timeout(timeout)
            try:
                async with scope:
                    await _drive()
            except TimeoutError:
                # Only our own deadline is a timeout-stop. A TimeoutError
                # raised by the source iterator itself is an input error
                # and must propagate, not be repackaged as expiry.
                if not scope.expired():
                    raise
                halt.stop(RunStatus.TIMED_OUT)
        else:
            await _drive()

        if halt.timed_out:
            assert timeout is not None
            # Tasks enqueue before they return, so this FIFO contains every
            # completion available to salvage without unordered task scans.
            _drain_completions()
            _mark_timeout_indices(results, in_flight.values(), timeout)
            if total is not None:
                for idx in range(len(results), total):
                    results.append(_timeout_failure(timeout, idx))
                    meta.append((0, 0.0))
        elif halt.aborted:
            # Salvage completions that raced the stop, cancel the rest.
            _drain_completions()
            reason = f"aborted after {failures} failures (max_errors={max_errors})"
            for idx in in_flight.values():
                if results[idx] is _PENDING:
                    results[idx] = _Failure(Aborted(reason))
            # Never touch the source after the stop: a poison, blocking, or
            # infinite input must not be consumed post-abort. Sized inputs
            # get placeholders by count; unsized yield a shorter result.
            if total is not None:
                pad = total - len(results)
                results.extend(_Failure(Aborted(reason)) for _ in range(pad))
                meta.extend((0, 0.0) for _ in range(pad))
        elif halt.cancelled:
            # Salvage completions that raced the stop; asyncio CAN
            # cancel in-flight tasks (the honest asymmetry vs sync,
            # where running threads finish in the background). The
            # cancellations happen in the finally below; here every
            # unresolved slot is marked.
            _drain_completions()
            reason = "cancelled by StopToken"
            for idx in in_flight.values():
                if results[idx] is _PENDING:
                    results[idx] = _Failure(Cancelled(reason))
            if total is not None:
                pad = total - len(results)
                results.extend(_Failure(Cancelled(reason)) for _ in range(pad))
                meta.extend((0, 0.0) for _ in range(pad))
    finally:
        if unregister is not None:
            unregister()
        if stop_task is not None:
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task
        for task in in_flight:
            task.cancel()
        for task in in_flight:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if store is not None:
            store.close()

    return ParallelResult(results, status=halt.status, meta=meta)


async def async_parallel_starmap[R](
    fn: Callable[..., Awaitable[R]],
    items: Iterable[tuple[Any, ...]] | AsyncIterable[tuple[Any, ...]],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    timeout: float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_result: Callable[[ItemResult[R]], None] | None = None,
    window_size: int | None = None,
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
        on_result=on_result,
        window_size=window_size,
        retry=retry,
    )


async def async_parallel_iter[T, R](
    fn: Callable[[T], Awaitable[R]],
    items: Iterable[T] | AsyncIterable[T],
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
    task_timeout: float | None = None,
    window_size: int | None = None,
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

    ``window_size`` sets the window: the maximum number of
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
    if window_size is not None and window_size < 1:
        raise ValueError(f"window_size must be >= 1, got {window_size}")
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    _validate_timeout(task_timeout, "task_timeout")
    _validate_max_errors(max_errors)

    window = window_size if window_size is not None else 2 * concurrency
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
    source = _aiter_source(items)
    seen = 0
    completed = 0
    failures = 0
    yielded_failures = 0
    exhausted = False
    in_flight: dict[asyncio.Task[_Outcome], int] = {}
    buffered: dict[int, ItemResult[R]] = {}
    next_yield = 0

    # THE PULL RACES AS A TASK (v0.9 review): parking the driver in
    # `await anext(source)` hid already-completed results from the
    # consumer — a paginated source stalling on a page fetch stalled the
    # yields of everything that had finished. At most one pull is
    # outstanding; it sits in the same asyncio.wait as the worker tasks,
    # so a slow producer delays only admission, never delivery.
    _EXHAUSTED: Any = object()
    pull: asyncio.Task[Any] | None = None

    async def _pull_once() -> Any:
        # StopAsyncIteration must not cross a Task boundary — sentinel it.
        try:
            return await anext(source)
        except StopAsyncIteration:
            return _EXHAUSTED

    def _maybe_start_pull() -> None:
        # The engine invariant: in_flight + buffered never exceeds the
        # window. Gating admission on the sum (not on completions) is
        # what keeps ordered mode bounded when a straggler blocks the
        # buffer. The single outstanding pull starts only when a slot is
        # free, so admitting its item cannot overshoot.
        nonlocal pull
        if (
            pull is None
            and not exhausted
            and (max_errors is None or failures < max_errors)
            and len(in_flight) + len(buffered) < window
        ):
            pull = asyncio.create_task(_pull_once())

    def _absorb_pull(task: asyncio.Task[Any]) -> None:
        # A raising source is an input error and must propagate —
        # task.result() re-raises it here, in the driver.
        nonlocal pull, seen, exhausted
        pull = None
        item = task.result()
        if item is _EXHAUSTED:
            exhausted = True
            return
        idx = seen  # admission order is the item's index
        seen += 1
        in_flight[asyncio.create_task(_run(item))] = idx
        _maybe_start_pull()

    def _yield_ends_stream(item_result: ItemResult[R]) -> bool:
        nonlocal yielded_failures
        if item_result.ok or max_errors is None:
            return False
        yielded_failures += 1
        return yielded_failures >= max_errors

    try:
        _maybe_start_pull()
        while in_flight or pull is not None:
            waiting: set[asyncio.Task[Any]] = set(in_flight)
            if pull is not None:
                waiting.add(pull)
            done, _pending = await asyncio.wait(
                waiting, return_when=asyncio.FIRST_COMPLETED
            )
            if pull is not None and pull in done:
                done.discard(pull)
                _absorb_pull(pull)
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
            if ordered:
                while next_yield in buffered:
                    ordered_result = buffered.pop(next_yield)
                    next_yield += 1
                    yield ordered_result
                    if _yield_ends_stream(ordered_result):
                        return
            _maybe_start_pull()
    finally:
        # Runs on exhaustion, on caller break (generator close), and when
        # the items iterator itself raises: cancel everything in flight,
        # including an in-progress pull — the cancellation lands in the
        # source and runs its finally (standard asyncio pipeline
        # semantics; an *idle* source is never touched).
        if pull is not None:
            pull.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pull
        for task in in_flight:
            task.cancel()
        for task in in_flight:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
