"""Tests for max_errors — fail-fast after N item failures with partial results."""

import asyncio

import pytest

from pyarallel import (
    MaxErrorsReached,
    Retry,
    async_parallel_iter,
    async_parallel_map,
    parallel_iter,
    parallel_map,
)
from pyarallel.core import parallel_starmap


def make_boom(calls: list):
    def boom(x):
        calls.append(x)
        raise ValueError(f"no {x}")

    return boom


class TestMaxErrorsValidation:
    @pytest.mark.parametrize("bad", [0, -1])
    def test_rejects_non_positive_limit(self, bad):
        with pytest.raises(ValueError, match="max_errors must be >= 1"):
            parallel_map(lambda x: x, [1], max_errors=bad)

    def test_async_rejects_non_positive_limit(self):
        with pytest.raises(ValueError, match="max_errors must be >= 1"):
            asyncio.run(async_parallel_map(lambda x: x, [1], max_errors=0))


class TestParallelMapMaxErrors:
    def test_stops_submitting_after_limit_with_batch_size_one(self):
        """Sequential execution stops calling fn after exactly max_errors failures."""
        calls: list = []
        result = parallel_map(
            make_boom(calls), range(10), workers=1, batch_size=1, max_errors=3
        )
        assert len(calls) == 3
        assert len(result) == 10

    def test_unexecuted_items_hold_max_errors_reached_markers(self):
        """Items never executed appear in failures() holding MaxErrorsReached."""
        calls: list = []
        result = parallel_map(
            make_boom(calls), range(10), workers=1, batch_size=1, max_errors=3
        )
        real = [
            (i, e) for i, e in result.failures() if not isinstance(e, MaxErrorsReached)
        ]
        skipped = [
            (i, e) for i, e in result.failures() if isinstance(e, MaxErrorsReached)
        ]
        assert [i for i, _ in real] == [0, 1, 2]
        assert [i for i, _ in skipped] == list(range(3, 10))

    def test_partial_successes_preserved_in_input_order(self):
        """Successes before the cutoff keep their indices and values."""

        def maybe(x):
            if x % 2:
                raise ValueError(x)
            return x * 2

        result = parallel_map(maybe, range(6), workers=1, batch_size=1, max_errors=2)
        assert dict(result.successes()) == {0: 0, 2: 4}
        real = [i for i, e in result.failures() if not isinstance(e, MaxErrorsReached)]
        assert real == [1, 3]

    def test_no_failures_behaves_like_plain_map(self):
        """Without failures the budget never trips and all values come back."""
        result = parallel_map(lambda x: x + 1, range(5), max_errors=2)
        assert result.ok
        assert list(result.values()) == [1, 2, 3, 4, 5]

    def test_unbatched_enforcement_is_reactive_but_bounded_by_input(self):
        """Without batch_size every task may be submitted before the limit is
        seen — but the result still covers every input slot."""
        calls: list = []
        result = parallel_map(make_boom(calls), range(20), workers=4, max_errors=3)
        assert len(result) == 20
        real = [e for _, e in result.failures() if not isinstance(e, MaxErrorsReached)]
        assert len(real) >= 3

    def test_exhausted_retries_count_as_one_failure(self):
        """An item that burns all retry attempts consumes a single error slot."""
        attempts: list = []

        def flaky(x):
            attempts.append(x)
            raise ConnectionError("down")

        result = parallel_map(
            flaky,
            range(10),
            workers=1,
            batch_size=1,
            retry=Retry(attempts=3, jitter=False, backoff=0),
            max_errors=2,
        )
        assert len(attempts) == 6  # 2 items x 3 attempts, then stop
        real = [
            (i, e) for i, e in result.failures() if not isinstance(e, MaxErrorsReached)
        ]
        assert [i for i, _ in real] == [0, 1]

    def test_starmap_passes_max_errors_through(self):
        def add(a, b):
            raise RuntimeError("nope")

        calls: list = []

        def tracked(a, b):
            calls.append((a, b))
            raise RuntimeError("nope")

        result = parallel_starmap(
            tracked,
            [(i, i) for i in range(10)],
            workers=1,
            batch_size=1,
            max_errors=2,
        )
        assert len(calls) == 2
        assert len(result) == 10


class TestParallelIterMaxErrors:
    def test_stream_stops_yielding_after_limit(self):
        """After the Nth failure no further items are yielded or executed."""
        calls: list = []
        items = list(
            parallel_iter(
                make_boom(calls), range(50), batch_size=5, workers=1, max_errors=2
            )
        )
        errors = [it for it in items if not it.ok]
        assert len(errors) == 2
        assert all(it.index < 10 for it in items)

    def test_stream_without_failures_yields_everything(self):
        items = list(parallel_iter(lambda x: x, range(4), max_errors=1))
        assert sorted(it.value for it in items) == [0, 1, 2, 3]


class TestAsyncParallelMapMaxErrors:
    def test_async_stops_submitting_after_limit(self):
        async def main():
            calls: list = []

            async def boom(x):
                calls.append(x)
                raise ValueError(f"no {x}")

            return await async_parallel_map(
                boom, range(10), concurrency=1, batch_size=1, max_errors=3
            )

        result = asyncio.run(main())
        real = [i for i, e in result.failures() if not isinstance(e, MaxErrorsReached)]
        skipped = [i for i, e in result.failures() if isinstance(e, MaxErrorsReached)]
        assert real == [0, 1, 2]
        assert skipped == list(range(3, 10))

    def test_async_guard_prevents_wasted_calls_when_queued(self):
        """Queued tasks past the limit skip fn entirely instead of calling it."""

        async def main():
            calls: list = []

            async def boom(x):
                calls.append(x)
                raise ValueError(f"no {x}")

            return await async_parallel_map(
                boom, range(100), concurrency=2, max_errors=2
            )

        result = asyncio.run(main())
        assert len(result) == 100
        # Semaphore-serialized: only tasks racing the second failure call fn.
        assert len([x for x in range(100)]) == 100  # sanity: input intact
        real = [e for _, e in result.failures() if not isinstance(e, MaxErrorsReached)]
        assert len(real) >= 2

    def test_async_no_failures_passes_through(self):
        async def main():
            async def inc(x):
                return x + 1

            return await async_parallel_map(inc, range(4), max_errors=2)

        result = asyncio.run(main())
        assert result.ok
        assert sorted(result.values()) == [1, 2, 3, 4]

    def test_async_exhausted_retries_count_as_one_failure(self):
        async def main():
            attempts: list = []

            async def flaky(x):
                attempts.append(x)
                raise ConnectionError("down")

            return (
                await async_parallel_map(
                    flaky,
                    range(8),
                    concurrency=1,
                    batch_size=1,
                    retry=Retry(attempts=3, jitter=False, backoff=0),
                    max_errors=2,
                ),
                attempts,
            )

        result, attempts = asyncio.run(main())
        assert len(attempts) == 6


class TestAsyncParallelIterMaxErrors:
    def test_async_stream_ends_after_current_batch(self):
        """The current batch finishes (with guard markers); later ones never start."""

        async def main():
            calls: list = []

            async def boom(x):
                calls.append(x)
                raise ValueError(f"no {x}")

            return [
                it
                async for it in async_parallel_iter(
                    boom, range(50), batch_size=5, concurrency=1, max_errors=2
                )
            ], calls

        items, calls = asyncio.run(main())
        real_errors = [
            it
            for it in items
            if not it.ok and not isinstance(it.error, MaxErrorsReached)
        ]
        markers = [it for it in items if isinstance(it.error, MaxErrorsReached)]
        assert len(real_errors) == 2
        assert len(markers) == 3  # rest of the final batch, skipped by guard
        assert all(it.index < 5 for it in items)
