"""Async parallel execution — mirror of the sync API.

Uses ``asyncio.TaskGroup`` for structured concurrency and
``asyncio.Semaphore`` for concurrency control.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Iterable, TypeVar

from .core import ParallelResult, RateLimit, Retry, _Failure

R = TypeVar("R")


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


async def _async_run_with_retry(fn: Callable[..., Any], item: Any, retry: Retry) -> Any:
    """Call async *fn(item)*, retrying up to *retry.attempts* times."""
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            return await fn(item)
        except Exception as exc:
            last_exc = exc
            if attempt < retry.attempts - 1 and retry.backoff > 0:
                await asyncio.sleep(retry.backoff * (attempt + 1))
    raise last_exc  # type: ignore[misc]


async def async_parallel_map(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable.
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` object, or ops-per-second as a number.
        timeout: Per-task timeout in seconds.
        on_progress: ``callback(completed, total)`` after each task.
        batch_size: Process items in chunks to control memory.
        retry: ``Retry`` object for per-item retry with backoff.

    Returns:
        ``ParallelResult`` — same container as the sync API.
    """
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)

    items_list = list(items)
    n = len(items_list)
    if n == 0:
        return ParallelResult([], 0)

    results: list[Any] = [None] * n
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
                    result = await _async_run_with_retry(fn, item, retry)
                elif timeout is not None:
                    result = await asyncio.wait_for(fn(item), timeout=timeout)
                else:
                    result = await fn(item)
                results[i] = result
            except Exception as exc:
                results[i] = _Failure(i, exc)
            completed += 1
            if on_progress:
                on_progress(completed, n)

    # Batched or all-at-once
    if batch_size is not None and batch_size < n:
        chunks = [
            range(start, min(start + batch_size, n))
            for start in range(0, n, batch_size)
        ]
    else:
        chunks = [range(n)]

    for chunk in chunks:
        async with asyncio.TaskGroup() as tg:
            for i in chunk:
                tg.create_task(_run(i, items_list[i]))

    return ParallelResult(results, n)


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
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        opts = dict(self._defaults)
        if concurrency is not None:
            opts["concurrency"] = concurrency
        if rate_limit is not None:
            opts["rate_limit"] = rate_limit
        if timeout is not None:
            opts["timeout"] = timeout
        if on_progress is not None:
            opts["on_progress"] = on_progress
        if batch_size is not None:
            opts["batch_size"] = batch_size
        if retry is not None:
            opts["retry"] = retry
        return await async_parallel_map(self._fn, items, **opts)


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
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        opts = dict(self._defaults)
        if concurrency is not None:
            opts["concurrency"] = concurrency
        if rate_limit is not None:
            opts["rate_limit"] = rate_limit
        if timeout is not None:
            opts["timeout"] = timeout
        if on_progress is not None:
            opts["on_progress"] = on_progress
        if batch_size is not None:
            opts["batch_size"] = batch_size
        if retry is not None:
            opts["retry"] = retry
        return await async_parallel_map(self.__wrapped__, items, **opts)


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
        return _AsyncParallelFunc(
            fn, concurrency=concurrency, rate_limit=rate_limit
        )

    if fn is not None:
        return decorator(fn)
    return decorator
