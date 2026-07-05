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


def check_decorator() -> None:
    assert_type(double(5), int)  # normal call keeps its signature
    assert_type(double.map([1, 2]), ParallelResult[int])
    assert_type(triple(1), str)
    assert_type(triple.map([1]), ParallelResult[str])
    assert_type(triple.starmap([(1,)]), ParallelResult[str])

    double("nope")  # type: ignore[arg-type]  # wrong call arg is still an error


@async_parallel(concurrency=8)
async def fetch_many(url: str) -> bytes:
    return b""


async def check_async_decorator() -> None:
    assert_type(await fetch_many("u"), bytes)
    assert_type(await fetch_many.map(["u"]), ParallelResult[bytes])


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
