"""Async parallel execution — mirror of the sync API.

Uses ``asyncio.TaskGroup`` for structured concurrency and
``asyncio.Semaphore`` for concurrency control.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from .core import (
    _PENDING,
    ParallelResult,
    RateLimit,
    Retry,
    _Failure,
    _iter_batches,
    _make_chunks,
    _merge_opts,
    _total_if_known,
)

# ---------------------------------------------------------------------------
# Async rate limiter
# ---------------------------------------------------------------------------


class _AsyncTokenBucket:
    """Async-friendly rate limiter.  Same slot-claiming algorithm as the
    sync ``_TokenBucket``, but sleeps with ``asyncio.sleep``."""

    __slots__ = ("_interval", "_next", "_lock")

    def __init__(self, rate_limit: RateLimit) -> None:
        self._interval = 1.0 / rate_limit.per_second
        self._next: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._next == 0.0:
                self._next = now
            target = max(self._next, now)
            self._next = target + self._interval
            delay = target - now
        if delay > 0:
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# async_parallel_map
# ---------------------------------------------------------------------------


async def _async_run_with_retry(
    fn: Callable[..., Any],
    item: Any,
    retry: Retry,
    task_timeout: float | None = None,
) -> Any:
    """Call async *fn(item)*, retrying on failure with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            if task_timeout is not None:
                return await asyncio.wait_for(fn(item), timeout=task_timeout)
            return await fn(item)
        except Exception as exc:
            last_exc = exc
            if not retry._should_retry(exc):
                raise
            if attempt < retry.attempts - 1:
                delay = retry._delay(attempt)
                if delay > 0:
                    await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def async_parallel_map[R](
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    workers: int | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable.
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` object, or ops-per-second as a number.
        task_timeout: Per-task timeout in seconds (each individual task).
        on_progress: ``callback(completed, total)`` after each task.
        batch_size: Process items in chunks to control memory.
        retry: ``Retry`` object for per-item retry with backoff.
        workers: Alias for ``concurrency`` (for convenience when switching
            from sync API). Emits a warning; prefer ``concurrency``.

    Returns:
        ``ParallelResult`` — same container as the sync API.
    """
    if workers is not None:
        import warnings

        warnings.warn(
            "workers= is not used in async_parallel_map — "
            "did you mean concurrency=? Setting concurrency to the workers value.",
            stacklevel=2,
        )
        concurrency = workers
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")

    if batch_size is None:
        items_list = list(items)
        n = len(items_list)
        if n == 0:
            return ParallelResult([])

        results: list[Any] = [_PENDING] * n
        semaphore = asyncio.Semaphore(concurrency)
        limiter = _AsyncTokenBucket(rate_limit) if rate_limit else None
        completed = 0

        async def _run(i: int, item: Any) -> None:
            nonlocal completed
            async with semaphore:
                if limiter:
                    await limiter.wait()
                try:
                    if retry is not None:
                        result = await _async_run_with_retry(
                            fn, item, retry, task_timeout=task_timeout
                        )
                    elif task_timeout is not None:
                        result = await asyncio.wait_for(fn(item), timeout=task_timeout)
                    else:
                        result = await fn(item)
                    results[i] = result
                except Exception as exc:
                    results[i] = _Failure(exc)
                completed += 1
                if on_progress:
                    on_progress(completed, n)

        for chunk in _make_chunks(n, batch_size):
            async with asyncio.TaskGroup() as tg:
                for i in chunk:
                    tg.create_task(_run(i, items_list[i]))

        return ParallelResult(results)

    total = _total_if_known(items)
    results: list[Any] = []
    semaphore = asyncio.Semaphore(concurrency)
    limiter = _AsyncTokenBucket(rate_limit) if rate_limit else None
    completed = 0

    async def _run(i: int, item: Any) -> None:
        nonlocal completed
        async with semaphore:
            if limiter:
                await limiter.wait()
            try:
                if retry is not None:
                    result = await _async_run_with_retry(
                        fn, item, retry, task_timeout=task_timeout
                    )
                elif task_timeout is not None:
                    result = await asyncio.wait_for(fn(item), timeout=task_timeout)
                else:
                    result = await fn(item)
                results[i] = result
            except Exception as exc:
                results[i] = _Failure(exc)
            completed += 1
            if on_progress:
                on_progress(completed, total if total is not None else len(results))

    for batch in _iter_batches(items, batch_size):
        results.extend([_PENDING] * len(batch))
        async with asyncio.TaskGroup() as tg:
            for i, item in batch:
                tg.create_task(_run(i, item))

    return ParallelResult(results)


async def async_parallel_starmap[R](
    fn: Callable[..., Any],
    items: Iterable[tuple[Any, ...]],
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
    task_timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> ParallelResult[R]:
    """Like ``async_parallel_map`` but unpacks each item as ``fn(*args)``."""

    async def _unpack(args: tuple[Any, ...]) -> Any:
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


async def async_parallel_iter(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
    task_timeout: float | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> AsyncIterator[tuple[int, Any]]:
    """Execute async *fn* over *items*, yielding ``(index, result)``
    in completion order. Constant memory — results are not accumulated.
    """
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")

    items_list = list(items)
    n = len(items_list)
    if n == 0:
        return

    semaphore = asyncio.Semaphore(concurrency)
    limiter = _AsyncTokenBucket(rate_limit) if rate_limit else None
    queue: asyncio.Queue[tuple[int, Any] | None] = asyncio.Queue()

    async def _run(i: int, item: Any) -> None:
        async with semaphore:
            if limiter:
                await limiter.wait()
            try:
                if retry is not None:
                    result = await _async_run_with_retry(
                        fn, item, retry, task_timeout=task_timeout
                    )
                elif task_timeout is not None:
                    result = await asyncio.wait_for(fn(item), timeout=task_timeout)
                else:
                    result = await fn(item)
                await queue.put((i, result))
            except Exception as exc:
                await queue.put((i, exc))

    active_tasks: list[asyncio.Task[None]] = []
    try:
        for chunk in _make_chunks(n, batch_size):
            chunk_tasks = []
            for i in chunk:
                t = asyncio.create_task(_run(i, items_list[i]))
                chunk_tasks.append(t)
                active_tasks.append(t)

            yielded_in_chunk = 0
            while yielded_in_chunk < len(chunk_tasks):
                item = await queue.get()
                if item is not None:
                    yield item
                    yielded_in_chunk += 1

            for t in chunk_tasks:
                await t
            active_tasks.clear()
    finally:
        for t in active_tasks:
            t.cancel()
        for t in active_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


# ---------------------------------------------------------------------------
# @async_parallel decorator
# ---------------------------------------------------------------------------


class _BoundAsyncParallel:
    """Bound version for async methods."""

    __slots__ = ("_fn", "_defaults")

    def __init__(self, fn: Callable[..., Any], defaults: dict[str, Any]) -> None:
        self._fn = fn
        self._defaults = defaults

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return await self._fn(*args, **kwargs)

    async def map(
        self,
        items: Iterable[Any],
        *,
        concurrency: int | None = None,
        rate_limit: RateLimit | float | None = None,
        task_timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        return await async_parallel_map(
            self._fn,
            items,
            **_merge_opts(
                self._defaults,
                concurrency=concurrency,
                rate_limit=rate_limit,
                task_timeout=task_timeout,
                on_progress=on_progress,
                batch_size=batch_size,
                retry=retry,
            ),
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **kwargs: Any
    ) -> ParallelResult[Any]:
        return await async_parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        )

    async def stream(
        self, items: Iterable[Any], **kwargs: Any
    ) -> AsyncIterator[tuple[int, Any]]:
        async for item in async_parallel_iter(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        ):
            yield item


class _AsyncParallelFunc:
    """Wrapper returned by ``@async_parallel``."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        concurrency: int,
        rate_limit: RateLimit | float | None,
    ) -> None:
        self.__wrapped__ = fn
        self._defaults: dict[str, Any] = {"concurrency": concurrency}
        if rate_limit is not None:
            self._defaults["rate_limit"] = rate_limit
        functools.update_wrapper(self, fn)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__wrapped__(*args, **kwargs)

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        bound = functools.partial(self.__wrapped__, obj)
        return _BoundAsyncParallel(bound, self._defaults)

    async def map(
        self,
        items: Iterable[Any],
        *,
        concurrency: int | None = None,
        rate_limit: RateLimit | float | None = None,
        task_timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        return await async_parallel_map(
            self.__wrapped__,
            items,
            **_merge_opts(
                self._defaults,
                concurrency=concurrency,
                rate_limit=rate_limit,
                task_timeout=task_timeout,
                on_progress=on_progress,
                batch_size=batch_size,
                retry=retry,
            ),
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **kwargs: Any
    ) -> ParallelResult[Any]:
        return await async_parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        )

    async def stream(
        self, items: Iterable[Any], **kwargs: Any
    ) -> AsyncIterator[tuple[int, Any]]:
        async for item in async_parallel_iter(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        ):
            yield item


def async_parallel(
    fn: Callable[..., Any] | None = None,
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
) -> Any:
    """Decorator: adds ``.map()`` for async parallel execution.

    Examples::

        @async_parallel(concurrency=10)
        async def fetch(url):
            async with httpx.AsyncClient() as c:
                return (await c.get(url)).json()

        data   = await fetch("http://example.com")       # normal call
        results = await fetch.map(["u1", "u2", "u3"])     # parallel
    """

    def decorator(fn: Callable[..., Any]) -> _AsyncParallelFunc:
        return _AsyncParallelFunc(fn, concurrency=concurrency, rate_limit=rate_limit)

    if fn is not None:
        return decorator(fn)
    return decorator
