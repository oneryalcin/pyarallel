"""Pyarallel: Parallel execution that doesn't hide the ball.

Simple, explicit parallel execution for Python. No magic type detection,
no global config singletons, no enterprise astronautics.
"""

from __future__ import annotations

import functools
import importlib
import random
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass
from itertools import islice
from typing import Any, Literal

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

    _VALID_INTERVALS = {"second": 1, "minute": 60, "hour": 3600}

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError(f"RateLimit count must be positive, got {self.count}")
        if self.per not in self._VALID_INTERVALS:
            raise ValueError(
                f'RateLimit per must be "second", "minute", or "hour", got {self.per!r}'
            )

    @property
    def per_second(self) -> float:
        return self.count / self._VALID_INTERVALS[self.per]


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
        delay = min(self.backoff * (2**attempt), self.max_delay)
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


def _iter_batches(
    items: Iterable[Any], batch_size: int
) -> Iterator[list[tuple[int, Any]]]:
    """Yield indexed item batches lazily from *items*."""
    it = iter(items)
    index = 0
    while True:
        batch = list(islice(it, batch_size))
        if not batch:
            break
        start = index
        index += len(batch)
        yield list(enumerate(batch, start))


def _total_if_known(items: Iterable[Any]) -> int | None:
    """Return len(items) when available without forcing materialization."""
    try:
        return len(items)  # type: ignore[arg-type]
    except TypeError:
        return None


def _resolve_process_target(
    fn: Callable[..., Any],
) -> tuple[str, str] | None:
    """Return a module/qualname pair when *fn* can be re-imported in a worker.

    This keeps process execution working for decorated top-level functions,
    whose original function object is no longer the module-global binding.
    """
    if getattr(fn, "__self__", None) is not None:
        return None

    module_name = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if not module_name or not qualname or "<locals>" in qualname:
        return None
    return (module_name, qualname)


@functools.cache
def _load_process_target(module_name: str, qualname: str) -> Callable[..., Any]:
    """Import and resolve a callable by module and qualname."""
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{module_name}.{qualname} is not callable")
    return target


def _call_resolved(item: Any, *, module_name: str, qualname: str) -> Any:
    """Resolve a callable inside the worker process and call it with one item."""
    return _load_process_target(module_name, qualname)(item)


def _call_resolved_args(
    args: tuple[Any, ...], *, module_name: str, qualname: str
) -> Any:
    """Resolve a callable inside the worker process and call it with *args."""
    return _load_process_target(module_name, qualname)(*args)


# ---------------------------------------------------------------------------
# ParallelResult
# ---------------------------------------------------------------------------


class ParallelResult[R]:
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
            (i, v) for i, v in enumerate(self._entries) if not isinstance(v, _Failure)
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


def parallel_map[R](
    fn: Callable[..., R],
    items: Iterable[Any],
    *,
    workers: int | None = None,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
    task_timeout: float | None = None,
) -> ParallelResult[R]:
    """Execute *fn* over *items* in parallel, returning ordered results.

    Args:
        fn: Function applied to each item.
        items: Any iterable (list, generator, range, …).
        workers: Number of parallel workers. Defaults to ``None`` which lets
            the executor decide — ``min(32, cpu_count+4)`` for threads,
            ``cpu_count()`` for processes.
        executor: ``"thread"`` for I/O-bound, ``"process"`` for CPU-bound.
        rate_limit: ``RateLimit`` object, or a plain number (ops per second).
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

    Returns:
        ``ParallelResult`` — acts like a list when all tasks succeed.
    """
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if workers is not None and workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if executor not in ("thread", "process"):
        raise ValueError(f'executor must be "thread" or "process", got {executor!r}')
    if task_timeout is not None:
        raise NotImplementedError(
            "task_timeout is not supported in sync parallel_map — Python "
            "threads cannot be cancelled mid-execution. Use timeout= for a "
            "total wall-clock limit, or put timeouts inside your function "
            "(e.g. requests.get(url, timeout=5)). "
            "For per-task timeouts, use async_parallel_map with task_timeout=."
        )

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
        task_fn = functools.partial(_run_with_retry, fn, retry=retry)
        if executor == "process":
            resolved = _resolve_process_target(fn)
            if resolved is not None:
                module_name, qualname = resolved
                task_fn = functools.partial(
                    _run_with_retry,
                    functools.partial(
                        _call_resolved,
                        module_name=module_name,
                        qualname=qualname,
                    ),
                    retry=retry,
                )

    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    bucket = _TokenBucket(rate_limit) if rate_limit else None
    completed = 0
    deadline = (time.monotonic() + timeout) if timeout is not None else None
    timed_out = False

    if batch_size is None:
        items_list = list(items)
        n = len(items_list)
        if n == 0:
            return ParallelResult([])
        results: list[Any] = [_PENDING] * n

        pool = pool_cls(max_workers=workers)
        try:
            for chunk in _make_chunks(n, batch_size):
                chunk_timeout: float | None = None
                if deadline is not None:
                    chunk_timeout = max(0.0, deadline - time.monotonic())
                    if chunk_timeout <= 0:
                        for i in chunk:
                            if results[i] is _PENDING:
                                results[i] = _Failure(
                                    TimeoutError(
                                        f"Task {i} did not complete within {timeout}s"
                                    )
                                )
                        timed_out = True
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
                    timed_out = True
                    for f, idx in futures.items():
                        if not f.done():
                            f.cancel()
                            if results[idx] is _PENDING:
                                results[idx] = _Failure(
                                    TimeoutError(
                                        f"Task {idx} did not complete within {timeout}s"
                                    )
                                )
        finally:
            pool.shutdown(wait=not timed_out, cancel_futures=timed_out)

        return ParallelResult(results)

    total = _total_if_known(items)
    results: list[Any] = []

    pool = pool_cls(max_workers=workers)
    try:
        for batch in _iter_batches(items, batch_size):
            chunk_timeout: float | None = None
            if deadline is not None:
                chunk_timeout = max(0.0, deadline - time.monotonic())
                if chunk_timeout <= 0:
                    for idx, _item in batch:
                        results.append(
                            _Failure(
                                TimeoutError(
                                    f"Task {idx} did not complete within {timeout}s"
                                )
                            )
                        )
                    for item in items:
                        idx = len(results)
                        _ = item
                        results.append(
                            _Failure(
                                TimeoutError(
                                    f"Task {idx} did not complete within {timeout}s"
                                )
                            )
                        )
                    timed_out = True
                    break

            results.extend([_PENDING] * len(batch))

            futures: dict[Future[R], int] = {}
            for idx, item in batch:
                if bucket:
                    bucket.wait()
                futures[pool.submit(task_fn, item)] = idx

            try:
                for future in as_completed(futures, timeout=chunk_timeout):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        results[idx] = _Failure(exc)
                    completed += 1
                    if on_progress:
                        on_progress(
                            completed,
                            total if total is not None else len(results),
                        )
            except TimeoutError:
                timed_out = True
                for f, idx in futures.items():
                    if not f.done():
                        f.cancel()
                        if results[idx] is _PENDING:
                            results[idx] = _Failure(
                                TimeoutError(
                                    f"Task {idx} did not complete within {timeout}s"
                                )
                            )
                for item in items:
                    idx = len(results)
                    _ = item
                    results.append(
                        _Failure(
                            TimeoutError(
                                f"Task {idx} did not complete within {timeout}s"
                            )
                        )
                    )
                break
    finally:
        pool.shutdown(wait=not timed_out, cancel_futures=timed_out)

    return ParallelResult(results)


def _unpack_call(fn_and_args: tuple[Callable[..., Any], tuple[Any, ...]]) -> Any:
    """Unpack and call fn(*args) — picklable for process executor."""
    fn, args = fn_and_args
    return fn(*args)


def parallel_starmap[R](
    fn: Callable[..., R],
    items: Iterable[tuple[Any, ...]],
    *,
    workers: int = 4,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
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


def parallel_iter[R](
    fn: Callable[..., R],
    items: Iterable[Any],
    *,
    workers: int = 4,
    executor: ExecutorType = "thread",
    rate_limit: RateLimit | float | None = None,
    batch_size: int | None = None,
    retry: Retry | None = None,
) -> Iterator[tuple[int, R | Exception]]:
    """Execute *fn* over *items* in parallel, yielding ``(index, result)``
    in completion order.

    Unlike ``parallel_map``, results are not accumulated in memory — they
    are yielded as they complete. For failed tasks, the value is the
    exception instance.

    Example::

        for index, value in parallel_iter(process, huge_list, batch_size=1000):
            if isinstance(value, Exception):
                log_error(index, value)
            else:
                db.save(value)
    """
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    if batch_size is not None and batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if executor not in ("thread", "process"):
        raise ValueError(f'executor must be "thread" or "process", got {executor!r}')

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
        task_fn = functools.partial(_run_with_retry, fn, retry=retry)
        if executor == "process":
            resolved = _resolve_process_target(fn)
            if resolved is not None:
                module_name, qualname = resolved
                task_fn = functools.partial(
                    _run_with_retry,
                    functools.partial(
                        _call_resolved,
                        module_name=module_name,
                        qualname=qualname,
                    ),
                    retry=retry,
                )

    pool_cls = ThreadPoolExecutor if executor == "thread" else ProcessPoolExecutor
    bucket = _TokenBucket(rate_limit) if rate_limit else None
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
                    yield (idx, future.result())
                except Exception as exc:
                    yield (idx, exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


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
            ),
        )

    def starmap(
        self,
        items: Iterable[tuple[Any, ...]],
        **kwargs: Any,
    ) -> ParallelResult[Any]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self._fn, items, **_merge_opts(self._defaults, **kwargs)
        )

    def stream(
        self,
        items: Iterable[Any],
        **kwargs: Any,
    ) -> Iterator[tuple[int, Any]]:
        """Yield ``(index, result)`` in completion order — constant memory."""
        return parallel_iter(self._fn, items, **_merge_opts(self._defaults, **kwargs))


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
        workers: int | None,
        executor: ExecutorType,
        rate_limit: RateLimit | float | None,
    ) -> None:
        self.__wrapped__ = fn
        self._defaults: dict[str, Any] = {"executor": executor}
        if workers is not None:
            self._defaults["workers"] = workers
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
        return parallel_map(
            self.__wrapped__,
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
            ),
        )

    def starmap(
        self,
        items: Iterable[tuple[Any, ...]],
        **kwargs: Any,
    ) -> ParallelResult[Any]:
        """Like ``.map()`` but unpacks each item as ``fn(*args)``."""
        return parallel_starmap(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        )

    def stream(
        self,
        items: Iterable[Any],
        **kwargs: Any,
    ) -> Iterator[tuple[int, Any]]:
        """Yield ``(index, result)`` in completion order — constant memory."""
        return parallel_iter(
            self.__wrapped__, items, **_merge_opts(self._defaults, **kwargs)
        )


def parallel[R](
    fn: Callable[..., R] | None = None,
    *,
    workers: int | None = None,
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
