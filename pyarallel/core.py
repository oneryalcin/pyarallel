"""Sync engine: parallel_map, parallel_starmap, parallel_iter.

Simple, explicit parallel execution over ``concurrent.futures``. No magic
type detection, no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from pathlib import Path
from typing import Any, Literal

from ._plan import (
    _PENDING,
    _append_timeout_failures,
    _mark_timeout_indices,
    _plan_collected_map,
    _progress_total,
)
from ._procexec import _call_resolved, _call_resolved_args, _resolve_process_target
from .checkpoint import _CheckpointStore
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

    A server-mandated wait (``Retry.wait_from``) also pauses the shared
    *limiter* — even on the final attempt, so a 429 from a task that is
    about to give up still slows the rest of the pool.
    """
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
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
    task_timeout: float | None = None,
    checkpoint: str | Path | None = None,
) -> ParallelResult[R]:
    """Execute *fn* over *items* in parallel, returning ordered results.

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
            items execute. Items and results must be picklable; a result
            that cannot be pickled is reported as that item's failure.

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    _validate_common(workers, executor, batch_size)
    if task_timeout is not None:
        raise NotImplementedError(
            "task_timeout is not supported in sync parallel_map — Python "
            "threads cannot be cancelled mid-execution. Use timeout= for a "
            "total wall-clock limit, or put timeouts inside your function "
            "(e.g. requests.get(url, timeout=5)). "
            "For per-task timeouts, use async_parallel_map with task_timeout=."
        )

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

    store = _CheckpointStore(checkpoint) if checkpoint is not None else None
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
                            on_progress(
                                completed, _progress_total(plan.total, results)
                            )
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
                        if store is not None:
                            store.put(idx, fingerprints.pop(idx), results[idx])
                    except Exception as exc:
                        results[idx] = _Failure(exc)
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
) -> Iterator[ItemResult[R]]:
    """Execute *fn* over *items* in parallel, yielding ``ItemResult`` in
    completion order.

    Unlike ``parallel_map``, results are not accumulated in memory — they
    are yielded as they complete.

    Example::

        for item in parallel_iter(process, huge_list, batch_size=1000):
            if item.ok:
                db.save(item.value)
            else:
                log_error(item.index, item.error)
    """
    _validate_common(workers, executor, batch_size)

    bucket = _as_limiter(rate_limit)
    task_fn = _build_task_fn(fn, executor, retry, bucket)
    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    it = iter(items)
    index = 0

    pool = pool_cls(max_workers=workers)
    try:
        while True:
            # Consume one chunk from the iterable lazily
            chunk_items: list[tuple[int, Any]] = []
            for item in it:
                chunk_items.append((index, item))
                index += 1
                if batch_size is not None and len(chunk_items) >= batch_size:
                    break
            if not chunk_items:
                break

            futures: dict[Future[R], int] = {}
            for i, item in chunk_items:
                if bucket:
                    bucket.wait()
                futures[pool.submit(task_fn, item)] = i

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    yield ItemResult(idx, value=future.result())
                except Exception as exc:
                    yield ItemResult(idx, error=exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
