"""Pyarallel: Parallel execution that doesn't hide the ball.

Simple, explicit parallel execution for Python. No magic type detection,
no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import functools
import random
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

# Sentinel for "slot not yet filled" — distinct from a legitimate None return.
_PENDING = object()


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

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError(f"RateLimit count must be positive, got {self.count}")

    @property
    def per_second(self) -> float:
        return self.count / {"second": 1, "minute": 60, "hour": 3600}[self.per]


@dataclass(frozen=True, slots=True)
class Retry:
    """Per-item retry with exponential backoff and jitter.

    Examples::

        Retry()                          # 3 attempts, 1s base, exponential + jitter
        Retry(attempts=5, backoff=2.0)   # 5 attempts, 2s base
        Retry(on=(ConnectionError, TimeoutError))  # only retry these
        Retry(jitter=False)              # deterministic delays (testing)

    Delay formula: ``min(backoff * 2^attempt, max_delay) * jitter``
    """

    attempts: int = 3
    backoff: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    on: tuple[type[Exception], ...] | None = None

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"Retry attempts must be >= 1, got {self.attempts}")

    def _delay(self, attempt: int) -> float:
        """Compute delay before retry *attempt* (0-indexed)."""
        delay = min(self.backoff * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        return delay

    def _should_retry(self, exc: Exception) -> bool:
        """True if this exception type is retryable."""
        if self.on is None:
            return True
        return isinstance(exc, self.on)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _Failure:
    """Sentinel wrapping a failed task result."""

    __slots__ = ("exception",)

    def __init__(self, exception: Exception) -> None:
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


def _merge_opts(defaults: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Merge decorator defaults with per-call overrides (skip None values)."""
    opts = dict(defaults)
    opts.update({k: v for k, v in overrides.items() if v is not None})
    return opts


def _make_chunks(n: int, batch_size: int | None) -> list[range]:
    """Split range(n) into chunks for batched processing."""
    if batch_size is not None and batch_size < n:
        return [
            range(start, min(start + batch_size, n))
            for start in range(0, n, batch_size)
        ]
    return [range(n)]


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

    __slots__ = ("_entries",)

    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

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
            (i, f.exception)
            for i, f in enumerate(self._entries)
            if isinstance(f, _Failure)
        ]

    def raise_on_failure(self) -> None:
        """Raise ``ExceptionGroup`` containing all task failures."""
        fails = self.failures()
        if fails:
            n = len(self._entries)
            raise ExceptionGroup(
                f"{len(fails)} of {n} tasks failed",
                [e for _, e in fails],
            )

    # --- list-like interface (raises on failure) ---

    def __iter__(self) -> Iterator[R]:
        return iter(self.values())

    def __getitem__(self, index: int | slice) -> Any:
        self.raise_on_failure()
        return self._entries[index]

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return len(self._entries) > 0

    def __repr__(self) -> str:
        if self.ok:
            return f"ParallelResult({list(self._entries)})"
        s, f = len(self.successes()), len(self.failures())
        return f"ParallelResult({s} ok, {f} failed)"


# ---------------------------------------------------------------------------
# parallel_map — the workhorse
# ---------------------------------------------------------------------------


def _run_with_retry(fn: Callable[..., Any], item: Any, retry: Retry) -> Any:
    """Call *fn(item)*, retrying on failure with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(retry.attempts):
        try:
            return fn(item)
        except Exception as exc:
            last_exc = exc
            if not retry._should_retry(exc):
                raise
            if attempt < retry.attempts - 1:
                delay = retry._delay(attempt)
                if delay > 0:
                    time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def parallel_map(
    fn: Callable[..., R],
    items: Iterable[Any],
    *,
    workers: int = 4,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
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
        batch_size: Process items in chunks of this size to control memory.
            Without batching, all items are submitted at once.
        retry: ``Retry`` object for per-item retry with backoff.

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")

    items_list = list(items)
    n = len(items_list)
    if n == 0:
        return ParallelResult([])

    task_fn = fn
    if retry is not None:
        task_fn = functools.partial(_run_with_retry, fn, retry=retry)

    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    bucket = _TokenBucket(rate_limit) if rate_limit else None
    results: list[Any] = [_PENDING] * n
    completed = 0
    deadline = (time.monotonic() + timeout) if timeout is not None else None

    with pool_cls(max_workers=workers) as pool:
        for chunk in _make_chunks(n, batch_size):
            # Compute remaining time budget for this batch
            chunk_timeout: float | None = None
            if deadline is not None:
                chunk_timeout = max(0.0, deadline - time.monotonic())
                if chunk_timeout <= 0:
                    for i in chunk:
                        if results[i] is _PENDING:
                            results[i] = _Failure(
                                TimeoutError(f"Task {i} did not complete within {timeout}s")
                            )
                    continue

            futures: dict[Future[R], int] = {}
            for i in chunk:
                if bucket:
                    bucket.wait()
                futures[pool.submit(task_fn, items_list[i])] = i

            try:
                for future in as_completed(futures, timeout=chunk_timeout):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        results[idx] = _Failure(exc)
                    completed += 1
                    if on_progress:
                        on_progress(completed, n)
            except TimeoutError:
                for f, idx in futures.items():
                    if not f.done():
                        f.cancel()
                        if results[idx] is _PENDING:
                            results[idx] = _Failure(
                                TimeoutError(f"Task {idx} did not complete within {timeout}s")
                            )

    return ParallelResult(results)


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
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        """Run this function over *items* in parallel."""
        return parallel_map(self._fn, items, **_merge_opts(
            self._defaults, workers=workers, executor=executor,
            rate_limit=rate_limit, timeout=timeout, on_progress=on_progress,
            batch_size=batch_size, retry=retry,
        ))


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
        batch_size: int | None = None,
        retry: Retry | None = None,
    ) -> ParallelResult[Any]:
        """Run this function over *items* in parallel."""
        return parallel_map(self.__wrapped__, items, **_merge_opts(
            self._defaults, workers=workers, executor=executor,
            rate_limit=rate_limit, timeout=timeout, on_progress=on_progress,
            batch_size=batch_size, retry=retry,
        ))


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
