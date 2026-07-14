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


async def _ints() -> AsyncIterator[int]:
    yield 1


def add(a: int, b: int) -> int:
    return a + b


async def fetch_async(url: str) -> bytes:
    return b""


def check_parallel_map() -> None:
    result = parallel_map(fetch, ["a", "b"], item_key=lambda url: url)
    assert_type(result, ParallelResult[dict[str, int]])
    assert_type(result.values(), list[dict[str, int]])
    assert_type(result.successes(), list[tuple[int, dict[str, int]]])
    assert_type(result.item_results(), list[ItemResult[dict[str, int]]])

    parallel_map(fetch, [1, 2])  # type: ignore[arg-type]  # wrong item type


def check_parallel_starmap() -> None:
    assert_type(
        parallel_starmap(
            add,
            [(1, 2), (3, 4)],
            item_key=lambda args: f"{args[0]}:{args[1]}",
        ),
        ParallelResult[int],
    )


def check_parallel_iter() -> None:
    it = parallel_iter(fetch, ["a"], item_key=lambda url: url)
    assert_type(it, Iterator[ItemResult[dict[str, int]]])


async def check_async_parallel_map() -> None:
    result = await async_parallel_map(fetch_async, ["a"], item_key=lambda url: url)
    assert_type(result, ParallelResult[bytes])

    stream = async_parallel_iter(fetch_async, ["a"], item_key=lambda url: url)
    assert_type(stream, AsyncIterator[ItemResult[bytes]])


@parallel
def double(x: int) -> int:
    return x * 2


@parallel(workers=4, rate_limit=RateLimit(10))
def triple(x: int) -> str:
    return str(x * 3)


# v0.10: decorator defaults widened — properties of the function's
# behavior are declarable at the decorator; run-scoped options
# (checkpoint*, stop) stay per-call-only.
@parallel(workers=2, retry=Retry(attempts=2), timeout=30.0, max_errors=5)
def widened(x: int) -> int:
    return x


def check_widened_decorator_defaults() -> None:
    assert_type(widened.map([1]), ParallelResult[int])
    parallel(checkpoint="run.ckpt")  # type: ignore[call-overload]  # run-scoped


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


def check_known_typing_limitations() -> None:
    """Documented narrowings (v0.8 plan A1) — pinned so a checker
    upgrade changing them is noticed, not silently shipped.

    - Unannotated lambdas through the factory spelling confuse both
      checkers: mypy collapses the wrapper to Any (no checking at all);
      pyright leaves T unsolved and false-positives on the lambda body.
      Use a def; the suppression below keeps the pyright CI step green.
    - functools.partial: pyright binds the remaining parameter
      (Unary[int, int]); mypy collapses the item type to Any — a
      mypy-specific hole, so no negative assertion is possible here.
    """
    _lam = parallel()(lambda x: x * 2)  # pyright: ignore[reportOperatorIssue]

    import functools

    _padd = parallel()(functools.partial(add, 5))
    _padd.map([1])  # OK in both; mypy would also accept wrong types (Any)


@async_parallel(concurrency=8)
async def fetch_many(url: str) -> bytes:
    return b""


async def check_async_decorator() -> None:
    assert_type(await fetch_many("u"), bytes)
    assert_type(await fetch_many.map(["u"]), ParallelResult[bytes])

    # v0.9: AsyncIterable sources accepted, item types still bind
    async def urls() -> AsyncIterator[str]:
        yield "u"

    assert_type(await fetch_many.map(urls()), ParallelResult[bytes])
    assert_type(await async_parallel_map(fetch_async, urls()), ParallelResult[bytes])
    await fetch_many.map(_ints())  # type: ignore[arg-type]  # wrong item type

    # v0.8 unary item typing, async side
    await fetch_many.map([1])  # type: ignore[list-item]  # int is not str
    fetch_many.stream([1])  # type: ignore[list-item]


def check_decorator_option_keys() -> None:
    """The Unpack[TypedDict] surfaces accept every engine option and
    reject unknown keys at type-check time. v0.8: presence is the
    inherit/override sentinel — an explicitly passed None *overrides*;
    options with no valid None value (executor, concurrency) don't
    admit None in their types."""
    limiter = Limiter(RateLimit(10))

    def collect_str(item: ItemResult[str]) -> None:
        assert_type(item, ItemResult[str])

    assert_type(
        triple.map(
            [1],
            workers=8,
            executor="process",
            rate_limit=limiter,
            timeout=30.0,
            on_progress=lambda done, total: None,
            on_result=collect_str,
            window_size=100,
            retry=Retry(attempts=2),
            item_key=lambda item: f"item-{item}",
            checkpoint="run.ckpt",
            checkpoint_key=lambda item: str(item),
            checkpoint_version=("classify-v3", "gpt-4o"),
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
        triple.stream(
            [1],
            item_key=lambda item: f"item-{item}",
            ordered=True,
            max_errors=3,
            sequential=None,
        ),
        Iterator[ItemResult[str]],
    )
    assert_type(
        triple.starmap(
            [(1,)], item_key=lambda args: f"item-{args[0]}", sequential=True
        ),
        ParallelResult[str],
    )

    triple.map([1], wrokers=4)  # type: ignore[call-arg]  # typo is caught


async def check_async_decorator_option_keys() -> None:
    def collect_bytes(item: ItemResult[bytes]) -> None:
        assert_type(item, ItemResult[bytes])

    assert_type(
        await fetch_many.map(
            ["u"],
            concurrency=8,
            timeout=60.0,
            task_timeout=5.0,
            on_result=collect_bytes,
            retry=Retry(attempts=2),
            item_key=lambda item: item,
            checkpoint="run.ckpt",
            checkpoint_key=lambda item: str(item),
            max_errors=10,
        ),
        ParallelResult[bytes],
    )
    stream = fetch_many.stream(
        ["u"], item_key=lambda item: item, ordered=True, max_errors=3
    )
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


def _consume_string_result(item: ItemResult[str]) -> None:
    item.value.upper() if item.value is not None else None


@parallel(on_result=_consume_string_result)  # type: ignore[arg-type]
def _decorated_int_result(value: int) -> int:
    """The decorator callback type must match the function result type."""
    return value


@async_parallel(on_result=_consume_string_result)  # type: ignore[arg-type]
async def _async_decorated_int_result(value: int) -> int:
    return value
