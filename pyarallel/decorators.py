"""Decorator API: ``@parallel`` and ``@async_parallel``.

Adds ``.map()`` / ``.starmap()`` / ``.stream()`` to a function without
changing its call signature or return type — and without erasing its types:
the wrappers are generic over the function's parameters and return type, so
``fetch.map(urls)`` is a ``ParallelResult`` of what ``fetch`` returns.
Participates in the descriptor protocol so decorated instance methods work.

Input typing (v0.8): single-parameter functions — the ``.map()`` case —
get a specialized wrapper whose ``.map()``/``.stream()`` bind the item
type: ``fetch.map([1])`` type-checks, ``fetch.map(["wrong"])`` doesn't,
in both mypy and pyright. Multi-parameter functions fall back to
``Iterable[Any]``: a precise ``.starmap()`` needs the parameter list as
a tuple type, which neither checker can express from a ParamSpec
(prototyped and rejected — ``*Ts`` is invalid inside a ParamSpec list).
The runtime object is always the specialized class; only the static
types differ.

Per-call options are typed once per engine (``SyncMapOptions`` etc.,
declared next to the engine functions) and threaded through with
``Unpack`` — the engine's explicit signature stays the source of truth,
and a signature change no longer touches five hand-copied keyword lists.
Keyword *presence* is the inherit/override sentinel (v0.8): an unpassed
option inherits the decorator default; an explicitly passed option —
even ``None`` — overrides it, so ``fetch.map(urls, rate_limit=None)``
genuinely turns the decorator's rate limit off.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from typing import Any, Unpack, cast, overload

from .aio import (
    AsyncMapOptions,
    AsyncStarmapOptions,
    AsyncStreamOptions,
    async_parallel_iter,
    async_parallel_map,
    async_parallel_starmap,
)
from .core import (
    ExecutorType,
    SyncMapOptions,
    SyncStarmapOptions,
    SyncStreamOptions,
    parallel_iter,
    parallel_map,
    parallel_starmap,
)
from .limiter import Limiter
from .policies import RateLimit
from .result import ItemResult, ParallelResult


def _merge_opts(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge decorator defaults with per-call overrides.

    Presence is the sentinel: *overrides* holds only the keywords the
    caller actually passed (``**opts``), so an unpassed option inherits
    and an explicit ``None`` overrides — no None-skipping.
    """
    opts = dict(defaults)
    opts.update(overrides)
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
        self, items: Iterable[Any], **opts: Unpack[SyncMapOptions]
    ) -> ParallelResult[R]:
        """Run this function over *items* in parallel."""
        return parallel_map(self._fn, items, **_merge_opts(self._defaults, dict(opts)))

    def starmap(
        self, items: Iterable[tuple[Any, ...]], **opts: Unpack[SyncStarmapOptions]
    ) -> ParallelResult[R]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, dict(opts))
        )

    def stream(
        self, items: Iterable[Any], **opts: Unpack[SyncStreamOptions]
    ) -> Iterator[ItemResult[R]]:
        """Yield ``ItemResult`` as tasks finish — constant memory."""
        return parallel_iter(self._fn, items, **_merge_opts(self._defaults, dict(opts)))


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
        self, items: Iterable[Any], **opts: Unpack[SyncMapOptions]
    ) -> ParallelResult[R]:
        """Run this function over *items* in parallel."""
        return parallel_map(
            cast("Callable[[Any], R]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, dict(opts)),
        )

    def starmap(
        self, items: Iterable[tuple[Any, ...]], **opts: Unpack[SyncStarmapOptions]
    ) -> ParallelResult[R]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, dict(opts))
        )

    def stream(
        self, items: Iterable[Any], **opts: Unpack[SyncStreamOptions]
    ) -> Iterator[ItemResult[R]]:
        """Yield ``ItemResult`` as tasks finish — constant memory."""
        return parallel_iter(
            cast("Callable[[Any], R]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, dict(opts)),
        )


class _UnaryParallelFunc[T, R](_ParallelFunc[[T], R]):
    """``_ParallelFunc`` for single-parameter functions: the item type
    binds, so ``.map()``/``.stream()`` check their inputs. No runtime
    behaviour of its own — the decorator always constructs this class;
    multi-parameter functions are simply *typed* as the base class.
    """

    def map(
        self, items: Iterable[T], **opts: Unpack[SyncMapOptions]
    ) -> ParallelResult[R]:
        """Run this function over *items* in parallel."""
        return _ParallelFunc.map(self, items, **opts)

    def stream(
        self, items: Iterable[T], **opts: Unpack[SyncStreamOptions]
    ) -> Iterator[ItemResult[R]]:
        """Yield ``ItemResult`` as tasks finish — constant memory."""
        return _ParallelFunc.stream(self, items, **opts)


class _ParallelDecorator:
    """Returned by ``@parallel(...)`` — the overloaded ``__call__`` keeps
    the unary item-type specialization in the factory spelling too."""

    __slots__ = ("_kw",)

    def __init__(self, kw: dict[str, Any]) -> None:
        self._kw = kw

    @overload
    def __call__[T, R](self, fn: Callable[[T], R]) -> _UnaryParallelFunc[T, R]: ...
    @overload
    def __call__[**P, R](self, fn: Callable[P, R]) -> _ParallelFunc[P, R]: ...
    def __call__(self, fn: Callable[..., Any]) -> Any:
        return _UnaryParallelFunc(fn, **self._kw)


@overload
def parallel[T, R](fn: Callable[[T], R]) -> _UnaryParallelFunc[T, R]: ...
@overload
def parallel[**P, R](fn: Callable[P, R]) -> _ParallelFunc[P, R]: ...
@overload
def parallel(
    fn: None = None,
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: Limiter | RateLimit | float | None = None,
) -> _ParallelDecorator: ...
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

    Single-parameter functions get item-typed ``.map()``/``.stream()``
    (``double.map(["x"])`` is a type error below); multi-parameter
    functions fall back to ``Iterable[Any]``.

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
    kw: dict[str, Any] = {
        "workers": workers,
        "executor": executor,
        "rate_limit": rate_limit,
    }
    if fn is not None:
        return _UnaryParallelFunc(fn, **kw)
    return _ParallelDecorator(kw)


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
        self, items: Iterable[Any], **opts: Unpack[AsyncMapOptions]
    ) -> ParallelResult[R]:
        return await async_parallel_map(
            self._fn, items, **_merge_opts(self._defaults, dict(opts))
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **opts: Unpack[AsyncStarmapOptions]
    ) -> ParallelResult[R]:
        return await async_parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, dict(opts))
        )

    async def stream(
        self, items: Iterable[Any], **opts: Unpack[AsyncStreamOptions]
    ) -> AsyncIterator[ItemResult[R]]:
        async for item in async_parallel_iter(
            self._fn, items, **_merge_opts(self._defaults, dict(opts))
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
        self, items: Iterable[Any], **opts: Unpack[AsyncMapOptions]
    ) -> ParallelResult[R]:
        return await async_parallel_map(
            cast("Callable[[Any], Awaitable[R]]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, dict(opts)),
        )

    async def starmap(
        self, items: Iterable[tuple[Any, ...]], **opts: Unpack[AsyncStarmapOptions]
    ) -> ParallelResult[R]:
        return await async_parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, dict(opts))
        )

    async def stream(
        self, items: Iterable[Any], **opts: Unpack[AsyncStreamOptions]
    ) -> AsyncIterator[ItemResult[R]]:
        async for item in async_parallel_iter(
            cast("Callable[[Any], Awaitable[R]]", self.__wrapped__),
            items,
            **_merge_opts(self._defaults, dict(opts)),
        ):
            yield item


class _UnaryAsyncParallelFunc[T, R](_AsyncParallelFunc[[T], R]):
    """``_AsyncParallelFunc`` for single-parameter functions — see
    ``_UnaryParallelFunc``."""

    async def map(
        self, items: Iterable[T], **opts: Unpack[AsyncMapOptions]
    ) -> ParallelResult[R]:
        return await _AsyncParallelFunc.map(self, items, **opts)

    async def stream(
        self, items: Iterable[T], **opts: Unpack[AsyncStreamOptions]
    ) -> AsyncIterator[ItemResult[R]]:
        async for item in _AsyncParallelFunc.stream(self, items, **opts):
            yield item


class _AsyncParallelDecorator:
    """Returned by ``@async_parallel(...)`` — see ``_ParallelDecorator``."""

    __slots__ = ("_kw",)

    def __init__(self, kw: dict[str, Any]) -> None:
        self._kw = kw

    @overload
    def __call__[T, R](
        self, fn: Callable[[T], Awaitable[R]]
    ) -> _UnaryAsyncParallelFunc[T, R]: ...
    @overload
    def __call__[**P, R](
        self, fn: Callable[P, Awaitable[R]]
    ) -> _AsyncParallelFunc[P, R]: ...
    def __call__(self, fn: Callable[..., Any]) -> Any:
        return _UnaryAsyncParallelFunc(fn, **self._kw)


@overload
def async_parallel[T, R](
    fn: Callable[[T], Awaitable[R]],
) -> _UnaryAsyncParallelFunc[T, R]: ...
@overload
def async_parallel[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> _AsyncParallelFunc[P, R]: ...
@overload
def async_parallel(
    fn: None = None,
    *,
    concurrency: int = 4,
    rate_limit: Limiter | RateLimit | float | None = None,
) -> _AsyncParallelDecorator: ...
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
    kw: dict[str, Any] = {"concurrency": concurrency, "rate_limit": rate_limit}
    if fn is not None:
        return _UnaryAsyncParallelFunc(fn, **kw)
    return _AsyncParallelDecorator(kw)
