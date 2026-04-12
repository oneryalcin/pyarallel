"""Async parallel execution — mirror of the sync API.

Uses ``asyncio.TaskGroup`` for structured concurrency and
``asyncio.Semaphore`` for concurrency control.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Iterable, TypeVar

from .core import ParallelResult, RateLimit, _Failure

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


async def async_parallel_map(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    concurrency: int = 4,
    rate_limit: RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ParallelResult[R]:
    """Execute an async *fn* over *items* concurrently.

    Args:
        fn: Async function applied to each item.
        items: Any iterable.
        concurrency: Maximum number of tasks running at once.
        rate_limit: ``RateLimit`` object, or ops-per-second as a number.
        timeout: Per-task timeout in seconds.
        on_progress: ``callback(completed, total)`` after each task.

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
                if timeout is not None:
                    results[i] = await asyncio.wait_for(fn(item), timeout=timeout)
                else:
                    results[i] = await fn(item)
            except Exception as exc:
                results[i] = _Failure(i, exc)
            completed += 1
            if on_progress:
                on_progress(completed, n)

    async with asyncio.TaskGroup() as tg:
        for i, item in enumerate(items_list):
            tg.create_task(_run(i, item))

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
