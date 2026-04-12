"""Pyarallel: Parallel execution that doesn't hide the ball.

Simple, explicit parallel execution for Python. No magic type detection,
no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import functools
import threading
import time
from concurrent.futures import (
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterable, Iterator, Literal, TypeVar

T = TypeVar("T")
R = TypeVar("R")

ExecutorType = Literal["thread", "process"]


# ---------------------------------------------------------------------------
# Configuration objects — small, frozen, composable
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Rate limit specification.

    Examples::

        RateLimit(10)              # 10 per second
        RateLimit(100, "minute")   # 100 per minute
        RateLimit(1000, "hour")    # 1000 per hour
    """

    count: float
    per: Literal["second", "minute", "hour"] = "second"

    @property
    def per_second(self) -> float:
        return self.count / {"second": 1, "minute": 60, "hour": 3600}[self.per]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _Failure:
    """Sentinel wrapping a failed task result."""

    __slots__ = ("index", "exception")

    def __init__(self, index: int, exception: Exception) -> None:
        self.index = index
        self.exception = exception


class _TokenBucket:
    """Thread-safe token bucket rate limiter.

    Each caller claims a time-slot; callers sleep until their slot arrives.
    The lock is held only for the bookkeeping — never during sleep.
    """

    __slots__ = ("_interval", "_next", "_lock")

    def __init__(self, rate_limit: RateLimit) -> None:
        self._interval = 1.0 / rate_limit.per_second
        self._next = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            target = max(self._next, now)
            self._next = target + self._interval
            delay = target - now
        if delay > 0:
            time.sleep(delay)


# ---------------------------------------------------------------------------
# ParallelResult
# ---------------------------------------------------------------------------


class ParallelResult(Generic[R]):
    """Results from parallel execution.

    Behaves like a ``list[R]`` when every task succeeded.
    When some tasks failed, use ``.successes()``, ``.failures()``,
    or ``.raise_on_failure()`` for structured access.

    Iterating or calling ``.values()`` raises ``ExceptionGroup``
    if any task failed — you always see errors, never silently.
    """

    __slots__ = ("_entries", "_n")

    def __init__(self, entries: list[Any], n: int) -> None:
        self._entries = entries
        self._n = n

    # --- Introspection ---

    @property
    def ok(self) -> bool:
        """True when every task succeeded."""
        return not any(isinstance(e, _Failure) for e in self._entries)

    def values(self) -> list[R]:
        """All results in input order. Raises if any task failed."""
        self.raise_on_failure()
        return list(self._entries)

    def successes(self) -> list[tuple[int, R]]:
        """``(index, value)`` for each task that succeeded."""
        return [
            (i, v)
            for i, v in enumerate(self._entries)
            if not isinstance(v, _Failure)
        ]

    def failures(self) -> list[tuple[int, Exception]]:
        """``(index, exception)`` for each task that failed."""
        return [
            (f.index, f.exception)
            for f in self._entries
            if isinstance(f, _Failure)
        ]

    def raise_on_failure(self) -> None:
        """Raise ``ExceptionGroup`` containing all task failures."""
        fails = self.failures()
        if fails:
            raise ExceptionGroup(
                f"{len(fails)} of {self._n} tasks failed",
                [e for _, e in fails],
            )

    # --- list-like interface (raises on failure) ---

    def __iter__(self) -> Iterator[R]:
        return iter(self.values())

    def __getitem__(self, index: int | slice) -> Any:
        return self.values()[index]

    def __len__(self) -> int:
        return self._n

    def __bool__(self) -> bool:
        return self._n > 0

    def __repr__(self) -> str:
        if self.ok:
            return f"ParallelResult({list(self._entries)})"
        s, f = len(self.successes()), len(self.failures())
        return f"ParallelResult({s} ok, {f} failed)"


# ---------------------------------------------------------------------------
# parallel_map — the workhorse
# ---------------------------------------------------------------------------


def parallel_map(
    fn: Callable[..., R],
    items: Iterable[Any],
    *,
    workers: int = 4,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ParallelResult[R]:
    """Execute *fn* over *items* in parallel, returning ordered results.

    Args:
        fn: Function applied to each item.
        items: Any iterable (list, generator, range, …).
        workers: Number of parallel workers.
        executor: ``"thread"`` for I/O-bound, ``"process"`` for CPU-bound.
        rate_limit: ``RateLimit`` object, or a plain number (ops per second).
        timeout: Total wall-clock timeout in seconds for the whole operation.
        on_progress: ``callback(completed, total)`` fired after each task.

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    # Normalise rate_limit
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)

    items_list = list(items)
    n = len(items_list)
    if n == 0:
        return ParallelResult([], 0)

    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    bucket = _TokenBucket(rate_limit) if rate_limit else None
    results: list[Any] = [None] * n
    completed = 0

    with pool_cls(max_workers=workers) as pool:
        futures: dict[Future[R], int] = {}
        for i, item in enumerate(items_list):
            if bucket:
                bucket.wait()
            futures[pool.submit(fn, item)] = i

        try:
            for future in as_completed(futures, timeout=timeout):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    results[idx] = _Failure(idx, exc)
                completed += 1
                if on_progress:
                    on_progress(completed, n)
        except TimeoutError:
            for f, idx in futures.items():
                if not f.done():
                    f.cancel()
                    if results[idx] is None:
                        results[idx] = _Failure(
                            idx,
                            TimeoutError(
                                f"Task {idx} did not complete within {timeout}s"
                            ),
                        )

    return ParallelResult(results, n)


# ---------------------------------------------------------------------------
# @parallel decorator
# ---------------------------------------------------------------------------


class _BoundParallel:
    """Bound version of a @parallel method (carries self/cls)."""

    __slots__ = ("_fn", "_defaults")

    def __init__(self, fn: Callable[..., Any], defaults: dict[str, Any]) -> None:
        self._fn = fn
        self._defaults = defaults

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._fn(*args, **kwargs)

    def map(
        self,
        items: Iterable[Any],
        *,
        workers: int | None = None,
        executor: ExecutorType | None = None,
        rate_limit: RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ParallelResult[Any]:
        """Run this function over *items* in parallel."""
        opts = dict(self._defaults)
        if workers is not None:
            opts["workers"] = workers
        if executor is not None:
            opts["executor"] = executor
        if rate_limit is not None:
            opts["rate_limit"] = rate_limit
        if timeout is not None:
            opts["timeout"] = timeout
        if on_progress is not None:
            opts["on_progress"] = on_progress
        return parallel_map(self._fn, items, **opts)


class _ParallelFunc:
    """Wrapper returned by ``@parallel``.  Adds ``.map()`` without
    changing the original function's call signature or return type.
    Also participates in the descriptor protocol so ``.map()`` works
    on instance methods.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        workers: int,
        executor: ExecutorType,
        rate_limit: RateLimit | float | None,
    ) -> None:
        self.__wrapped__ = fn
        self._defaults: dict[str, Any] = {"workers": workers, "executor": executor}
        if rate_limit is not None:
            self._defaults["rate_limit"] = rate_limit
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.__wrapped__(*args, **kwargs)

    # Descriptor — makes .map() work on instance methods
    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
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
        rate_limit: RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ParallelResult[Any]:
        """Run this function over *items* in parallel."""
        opts = dict(self._defaults)
        if workers is not None:
            opts["workers"] = workers
        if executor is not None:
            opts["executor"] = executor
        if rate_limit is not None:
            opts["rate_limit"] = rate_limit
        if timeout is not None:
            opts["timeout"] = timeout
        if on_progress is not None:
            opts["on_progress"] = on_progress
        return parallel_map(self.__wrapped__, items, **opts)


def parallel(
    fn: Callable[..., R] | None = None,
    *,
    workers: int = 4,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
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

    def decorator(fn: Callable[..., R]) -> _ParallelFunc:
        return _ParallelFunc(
            fn, workers=workers, executor=executor, rate_limit=rate_limit
        )

    if fn is not None:
        return decorator(fn)
    return decorator
