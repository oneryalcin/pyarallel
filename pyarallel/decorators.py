"""Decorator API: ``@parallel`` and ``@async_parallel``.

Adds ``.map()`` / ``.starmap()`` / ``.stream()`` to a function without
changing its call signature or return type — and without erasing its types:
the wrappers are generic over the function's parameters and return type, so
``fetch.map(urls)`` is a ``ParallelResult`` of what ``fetch`` returns.
Participates in the descriptor protocol so decorated instance methods work.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast, overload

from .aio import async_parallel_iter, async_parallel_map, async_parallel_starmap
from .core import ExecutorType, parallel_iter, parallel_map, parallel_starmap
from .limiter import Limiter
from .policies import RateLimit, Retry
from .result import ItemResult, ParallelResult


def _merge_opts(defaults: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Merge decorator defaults with per-call overrides (skip None values)."""
    opts = dict(defaults)
    opts.update({k: v for k, v in overrides.items() if v is not None})
    return opts


# ---------------------------------------------------------------------------
# @parallel — sync
# ---------------------------------------------------------------------------


class _BoundParallel[R]:
    """Bound version of a @parallel method (carries self/cls)."""

    __slots__ = ("_fn", "_defaults")

    def __init__(self, fn: Callable[..., R], defaults: dict[str, Any]) -> None:
        self._fn = fn
        self._defaults = defaults

    def __call__(self, *args: Any, **kwargs: Any) -> R:
        return self._fn(*args, **kwargs)

    def map(
        self,
        items: Iterable[Any],
        *,
        workers: int | None = None,
        executor: ExecutorType | None = None,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
        checkpoint: str | Path | None = None,
        checkpoint_key: Callable[[Any], str | int | bytes] | None = None,
        max_errors: int | None = None,
        sequential: bool | None = None,
        worker_init: Callable[[], None] | None = None,
        max_tasks_per_worker: int | None = None,
    ) -> ParallelResult[R]:
        """Run this function over *items* in parallel."""
        return parallel_map(
            self._fn,
            items,
            **_merge_opts(
                self._defaults,
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
                sequential=sequential,
                worker_init=worker_init,
                max_tasks_per_worker=max_tasks_per_worker,
            ),
        )

    def starmap(
        self,
        items: Iterable[tuple[Any, ...]],
        **kwargs: Any,
    ) -> ParallelResult[R]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        )

    def stream(
        self,
        items: Iterable[Any],
        **kwargs: Any,
    ) -> Iterator[ItemResult[R]]:
        """Yield ``ItemResult`` in completion order — constant memory."""
        return parallel_iter(self._fn, items, **_merge_opts(self._defaults, **kwargs))


class _ParallelFunc[**P, R]:
    """Wrapper returned by ``@parallel``.  Adds ``.map()`` without
    changing the original function's call signature or return type.
    Also participates in the descriptor protocol so ``.map()`` works
    on instance methods.
    """

    def __init__(
        self,
        fn: Callable[P, R],
        *,
        workers: int | None,
        executor: ExecutorType,
        rate_limit: Limiter | RateLimit | float | None,
    ) -> None:
        self.__wrapped__: Callable[P, R] = fn
        self._defaults: dict[str, Any] = {"executor": executor}
        if workers is not None:
            self._defaults["workers"] = workers
        if rate_limit is not None:
            self._defaults["rate_limit"] = rate_limit
        functools.update_wrapper(self, fn)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.__wrapped__(*args, **kwargs)

    @overload
    def __get__(
        self, obj: None, objtype: type | None = None
    ) -> _ParallelFunc[P, R]: ...
    @overload
    def __get__(self, obj: Any, objtype: type | None = None) -> _BoundParallel[R]: ...
    def __get__(
        self, obj: Any, objtype: type | None = None
    ) -> _ParallelFunc[P, R] | _BoundParallel[R]:
        if obj is None:
            return self
        bound = functools.partial(self.__wrapped__, obj)
        return _BoundParallel(bound, self._defaults)

    def map(
        self,
        items: Iterable[Any],
        *,
        workers: int | None = None,
        executor: ExecutorType | None = None,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
        checkpoint: str | Path | None = None,
        checkpoint_key: Callable[[Any], str | int | bytes] | None = None,
        max_errors: int | None = None,
        sequential: bool | None = None,
        worker_init: Callable[[], None] | None = None,
        max_tasks_per_worker: int | None = None,
    ) -> ParallelResult[R]:
        """Run this function over *items* in parallel."""
        return parallel_map(
            cast("Callable[[Any], R]", self.__wrapped__),
            items,
            **_merge_opts(
                self._defaults,
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
                sequential=sequential,
                worker_init=worker_init,
                max_tasks_per_worker=max_tasks_per_worker,
            ),
        )

    def starmap(
        self,
        items: Iterable[tuple[Any, ...]],
        **kwargs: Any,
    ) -> ParallelResult[R]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        )

    def stream(
        self,
        items: Iterable[Any],
        **kwargs: Any,
    ) -> Iterator[ItemResult[R]]:
        """Yield ``ItemResult`` in completion order — constant memory."""
        return parallel_iter(
            cast("Callable[[Any], R]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, **kwargs),
        )


@overload
def parallel[**P, R](fn: Callable[P, R]) -> _ParallelFunc[P, R]: ...
@overload
def parallel[**P, R](
    fn: None = None,
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
) -> Callable[[Callable[P, R]], _ParallelFunc[P, R]]: ...
def parallel(
    fn: Callable[..., Any] | None = None,
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
) -> Any:
    """Decorator: adds ``.map()`` for parallel execution.

    The decorated function **keeps its original behaviour**.
    Single calls return ``T``, not ``list[T]``.
    Call ``.map(items)`` for parallel execution.

    Examples::

        @parallel(workers=4)
        def fetch(url):
            return requests.get(url).json()

        result  = fetch("http://example.com")      # dict  (normal call)
        results = fetch.map(["u1", "u2", "u3"])     # ParallelResult[dict]

        @parallel
        def double(x):
            return x * 2

        double(5)             # 10
        double.map([1,2,3])   # ParallelResult([2, 4, 6])
    """

    def decorator(fn: Callable[..., Any]) -> _ParallelFunc[..., Any]:
        return _ParallelFunc(
            fn, workers=workers, executor=executor, rate_limit=rate_limit
        )

    if fn is not None:
        return decorator(fn)
    return decorator


# ---------------------------------------------------------------------------
# @async_parallel — async
# ---------------------------------------------------------------------------


class _BoundAsyncParallel[R]:
    """Bound version for async methods."""

    __slots__ = ("_fn", "_defaults")

    def __init__(
        self, fn: Callable[..., Awaitable[R]], defaults: dict[str, Any]
    ) -> None:
        self._fn = fn
        self._defaults = defaults

    async def __call__(self, *args: Any, **kwargs: Any) -> R:
        return await self._fn(*args, **kwargs)

    async def map(
        self,
        items: Iterable[Any],
        *,
        concurrency: int | None = None,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        task_timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
        checkpoint: str | Path | None = None,
        checkpoint_key: Callable[[Any], str | int | bytes] | None = None,
        max_errors: int | None = None,
    ) -> ParallelResult[R]:
        return await async_parallel_map(
            self._fn,
            items,
            **_merge_opts(
                self._defaults,
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
            ),
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **kwargs: Any
    ) -> ParallelResult[R]:
        return await async_parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        )

    async def stream(
        self, items: Iterable[Any], **kwargs: Any
    ) -> AsyncIterator[ItemResult[R]]:
        async for item in async_parallel_iter(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        ):
            yield item


class _AsyncParallelFunc[**P, R]:
    """Wrapper returned by ``@async_parallel``."""

    def __init__(
        self,
        fn: Callable[P, Awaitable[R]],
        *,
        concurrency: int,
        rate_limit: Limiter | RateLimit | float | None,
    ) -> None:
        self.__wrapped__: Callable[P, Awaitable[R]] = fn
        self._defaults: dict[str, Any] = {"concurrency": concurrency}
        if rate_limit is not None:
            self._defaults["rate_limit"] = rate_limit
        functools.update_wrapper(self, fn)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return await self.__wrapped__(*args, **kwargs)

    @overload
    def __get__(
        self, obj: None, objtype: type | None = None
    ) -> _AsyncParallelFunc[P, R]: ...
    @overload
    def __get__(
        self, obj: Any, objtype: type | None = None
    ) -> _BoundAsyncParallel[R]: ...
    def __get__(
        self, obj: Any, objtype: type | None = None
    ) -> _AsyncParallelFunc[P, R] | _BoundAsyncParallel[R]:
        if obj is None:
            return self
        bound = functools.partial(self.__wrapped__, obj)
        return _BoundAsyncParallel(bound, self._defaults)

    async def map(
        self,
        items: Iterable[Any],
        *,
        concurrency: int | None = None,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        task_timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        retry: Retry | None = None,
        checkpoint: str | Path | None = None,
        checkpoint_key: Callable[[Any], str | int | bytes] | None = None,
        max_errors: int | None = None,
    ) -> ParallelResult[R]:
        return await async_parallel_map(
            cast("Callable[[Any], Awaitable[R]]", self.__wrapped__),
            items,
            **_merge_opts(
                self._defaults,
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
            ),
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **kwargs: Any
    ) -> ParallelResult[R]:
        return await async_parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        )

    async def stream(
        self, items: Iterable[Any], **kwargs: Any
    ) -> AsyncIterator[ItemResult[R]]:
        async for item in async_parallel_iter(
            cast("Callable[[Any], Awaitable[R]]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, **kwargs),
        ):
            yield item


@overload
def async_parallel[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> _AsyncParallelFunc[P, R]: ...
@overload
def async_parallel[**P, R](
    fn: None = None,
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], _AsyncParallelFunc[P, R]]: ...
def async_parallel(
    fn: Callable[..., Any] | None = None,
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
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

    def decorator(fn: Callable[..., Any]) -> _AsyncParallelFunc[..., Any]:
        return _AsyncParallelFunc(fn, concurrency=concurrency, rate_limit=rate_limit)

    if fn is not None:
        return decorator(fn)
    return decorator
