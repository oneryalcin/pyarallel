"""Tests for batch_size — controls memory by processing in chunks."""

import threading
import time

from pyarallel import RateLimit, parallel_map


class TestBatchBasic:
    def test_batch_size_produces_correct_results(self):
        """Batching doesn't change the output — same results, same order."""
        result = parallel_map(lambda x: x * 2, range(20), workers=4, batch_size=5)
        assert list(result) == [x * 2 for x in range(20)]

    def test_batch_size_one_is_same_as_no_batch(self):
        result = parallel_map(lambda x: x + 1, [10, 20, 30], workers=2, batch_size=1)
        assert list(result) == [11, 21, 31]

    def test_batch_size_larger_than_items(self):
        """batch_size > len(items) should just process everything in one batch."""
        result = parallel_map(lambda x: x, [1, 2, 3], workers=2, batch_size=100)
        assert list(result) == [1, 2, 3]

    def test_batch_size_equal_to_items(self):
        result = parallel_map(lambda x: x, [1, 2, 3], workers=2, batch_size=3)
        assert list(result) == [1, 2, 3]


class TestBatchMemoryControl:
    def test_limits_concurrent_futures(self):
        """The whole point of batching: only batch_size futures exist at once.
        With 20 items and batch_size=5, at most 5 futures should be in-flight."""
        max_concurrent = 0
        current = 0
        lock = threading.Lock()

        def track(x):
            nonlocal max_concurrent, current
            with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            time.sleep(0.05)  # Hold the slot briefly
            with lock:
                current -= 1
            return x

        parallel_map(track, range(20), workers=4, batch_size=5)
        # At most batch_size items should have been submitted at once
        assert max_concurrent <= 5

    def test_all_items_processed(self):
        """Even with batching, every item gets processed exactly once."""
        seen = []
        lock = threading.Lock()

        def collect(x):
            with lock:
                seen.append(x)
            return x

        result = parallel_map(collect, range(17), workers=3, batch_size=4)
        assert sorted(seen) == list(range(17))
        assert list(result) == list(range(17))


class TestBatchErrorHandling:
    def test_error_in_one_batch_still_processes_others(self):
        """Failures in batch N shouldn't prevent batch N+1 from running."""

        def fail_on_5(x):
            if x == 5:
                raise ValueError("five")
            return x

        result = parallel_map(fail_on_5, range(20), workers=4, batch_size=5)
        assert not result.ok
        assert len(result.failures()) == 1
        assert len(result.successes()) == 19

    def test_multiple_errors_across_batches(self):
        def fail_even(x):
            if x % 2 == 0:
                raise ValueError(f"even: {x}")
            return x

        result = parallel_map(fail_even, range(10), workers=3, batch_size=3)
        assert len(result.failures()) == 5  # 0, 2, 4, 6, 8
        assert len(result.successes()) == 5  # 1, 3, 5, 7, 9


class TestBatchWithOtherFeatures:
    def test_batch_with_progress(self):
        progress = []
        parallel_map(
            lambda x: x,
            range(12),
            workers=2,
            batch_size=4,
            on_progress=lambda d, t: progress.append((d, t)),
        )
        assert len(progress) == 12
        assert all(t == 12 for _, t in progress)

    def test_batch_with_rate_limit(self):
        start = time.monotonic()
        parallel_map(
            lambda x: x,
            range(6),
            workers=4,
            batch_size=3,
            rate_limit=RateLimit(10, "second"),
        )
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4  # 6 items at 10/sec


class TestAsyncBatch:
    async def test_async_batch_produces_correct_results(self):
        from pyarallel import async_parallel_map

        async def double(x):
            return x * 2

        result = await async_parallel_map(
            double, range(15), concurrency=3, batch_size=5
        )
        assert list(result) == [x * 2 for x in range(15)]

    async def test_async_batch_limits_concurrency(self):
        import asyncio

        from pyarallel import async_parallel_map

        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        async def track(x):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return x

        await async_parallel_map(track, range(20), concurrency=3, batch_size=5)
        assert max_concurrent <= 5  # batch_size caps in-flight tasks
