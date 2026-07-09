"""Static typing assertions — checked by mypy --strict, never executed.

This file is the typed-interface contract: if inference regresses (a
decorator erases types, a map stops binding T/R), mypy fails the lint here.
Not collected by pytest (no test_ prefix, no test functions).
"""

from collections.abc import AsyncIterator, Iterator
from typing import assert_type

from pyarallel import (
    ItemResult,
    Limiter,
    ParallelResult,
    RateLimit,
    Retry,
    async_parallel,
    async_parallel_iter,
    async_parallel_map,
    parallel,
    parallel_iter,
    parallel_map,
    parallel_starmap,
)


def fetch(url: str) -> dict[str, int]:
    return {}


def add(a: int, b: int) -> int:
    return a + b


async def fetch_async(url: str) -> bytes:
    return b""


def check_parallel_map() -> None:
    result = parallel_map(fetch, ["a", "b"])
    assert_type(result, ParallelResult[dict[str, int]])
    assert_type(result.values(), list[dict[str, int]])
    assert_type(result.successes(), list[tuple[int, dict[str, int]]])

    parallel_map(fetch, [1, 2])  # type: ignore[arg-type]  # wrong item type


def check_parallel_starmap() -> None:
    assert_type(parallel_starmap(add, [(1, 2), (3, 4)]), ParallelResult[int])


def check_parallel_iter() -> None:
    it = parallel_iter(fetch, ["a"])
    assert_type(it, Iterator[ItemResult[dict[str, int]]])


async def check_async_parallel_map() -> None:
    result = await async_parallel_map(fetch_async, ["a"])
    assert_type(result, ParallelResult[bytes])

    stream = async_parallel_iter(fetch_async, ["a"])
    assert_type(stream, AsyncIterator[ItemResult[bytes]])


@parallel
def double(x: int) -> int:
    return x * 2


@parallel(workers=4, rate_limit=RateLimit(10))
def triple(x: int) -> str:
    return str(x * 3)


@parallel
def concat(a: int, b: str) -> bytes:
    return b""


def check_decorator() -> None:
    assert_type(double(5), int)  # normal call keeps its signature
    assert_type(double.map([1, 2]), ParallelResult[int])
    assert_type(triple(1), str)
    assert_type(triple.map([1]), ParallelResult[str])
    assert_type(triple.starmap([(1,)]), ParallelResult[str])

    double("nope")  # type: ignore[arg-type]  # wrong call arg is still an error


def check_unary_item_typing() -> None:
    """v0.8: single-parameter functions bind the item type on .map()
    and .stream() — in the bare AND factory decorator spellings.
    Multi-parameter functions fall back to Iterable[Any] (a precise
    .starmap() is not expressible from a ParamSpec — prototyped in the
    v0.8 plan; *Ts inside a ParamSpec list is invalid in both checkers)."""
    # positive: correct item types accepted, R flows through
    assert_type(double.map([1, 2]), ParallelResult[int])  # bare
    assert_type(triple.map([1]), ParallelResult[str])  # factory
    assert_type(next(iter(triple.stream([1]))), ItemResult[str])

    # negative: wrong item types rejected
    double.map(["wrong"])  # type: ignore[list-item]  # str is not int
    triple.map(["wrong"])  # type: ignore[list-item]
    triple.stream(["wrong"])  # type: ignore[list-item]

    # multi-parameter: loose fallback — accepted, and starmap works
    assert_type(concat.starmap([(1, "x")]), ParallelResult[bytes])


@async_parallel(concurrency=8)
async def fetch_many(url: str) -> bytes:
    return b""


async def check_async_decorator() -> None:
    assert_type(await fetch_many("u"), bytes)
    assert_type(await fetch_many.map(["u"]), ParallelResult[bytes])

    # v0.8 unary item typing, async side
    await fetch_many.map([1])  # type: ignore[list-item]  # int is not str
    fetch_many.stream([1])  # type: ignore[list-item]


def check_decorator_option_keys() -> None:
    """Phase 7 contract: the Unpack[TypedDict] surfaces accept every
    engine option, keep `| None` (explicit None = inherit), and reject
    unknown keys at type-check time."""
    limiter = Limiter(RateLimit(10))

    assert_type(
        triple.map(
            [1],
            workers=8,
            executor="process",
            rate_limit=limiter,
            timeout=30.0,
            on_progress=lambda done, total: None,
            window_size=100,
            retry=Retry(attempts=2),
            checkpoint="run.ckpt",
            checkpoint_key=lambda item: str(item),
            max_errors=10,
            sequential=True,
            worker_init=lambda: None,
            max_tasks_per_worker=50,
        ),
        ParallelResult[str],
    )
    # v0.8: an explicitly passed option overrides the decorator default —
    # workers=None is a valid override (engine auto-sizing); executor has
    # no None value, so passing None must be a type error.
    assert_type(triple.map([1], workers=None), ParallelResult[str])
    triple.map([1], executor=None)  # type: ignore[arg-type]  # None not a value

    assert_type(
        triple.stream([1], ordered=True, max_errors=3, sequential=None),
        Iterator[ItemResult[str]],
    )
    assert_type(triple.starmap([(1,)], sequential=True), ParallelResult[str])

    triple.map([1], wrokers=4)  # type: ignore[call-arg]  # typo is caught


async def check_async_decorator_option_keys() -> None:
    assert_type(
        await fetch_many.map(
            ["u"],
            concurrency=8,
            timeout=60.0,
            task_timeout=5.0,
            retry=Retry(attempts=2),
            checkpoint="run.ckpt",
            checkpoint_key=lambda item: str(item),
            max_errors=10,
        ),
        ParallelResult[bytes],
    )
    stream = fetch_many.stream(["u"], ordered=True, max_errors=3)
    assert_type(stream, AsyncIterator[ItemResult[bytes]])

    await fetch_many.map(["u"], sequential=True)  # type: ignore[call-arg]  # sync-only option


def check_policy_arguments() -> None:
    limiter = Limiter(RateLimit(100, "minute", burst=20))
    parallel_map(fetch, ["a"], rate_limit=limiter)
    parallel_map(fetch, ["a"], rate_limit=RateLimit(10))
    parallel_map(fetch, ["a"], rate_limit=5.0)
    parallel_map(
        fetch,
        ["a"],
        retry=Retry(
            attempts=5,
            retry_if=lambda exc: True,
            wait_from=lambda exc: getattr(exc, "retry_after", None),
        ),
    )
