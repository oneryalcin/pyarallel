"""Tests for async parallel execution."""

import asyncio
import time

import pytest

from pyarallel import RateLimit, async_parallel, async_parallel_map


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestAsyncParallelMap:
    @pytest.mark.asyncio
    async def test_basic(self):
        async def double(x):
            return x * 2

        result = await async_parallel_map(double, [1, 2, 3], concurrency=2)
        assert list(result) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_preserves_order(self):
        async def slow_first(x):
            if x == 0:
                await asyncio.sleep(0.05)
            return x

        result = await async_parallel_map(slow_first, range(4), concurrency=4)
        assert list(result) == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_empty_input(self):
        async def noop(x):
            return x

        result = await async_parallel_map(noop, [])
        assert list(result) == []
        assert result.ok

    @pytest.mark.asyncio
    async def test_accepts_any_iterable(self):
        async def double(x):
            return x * 2

        result = await async_parallel_map(double, range(4), concurrency=2)
        assert list(result) == [0, 2, 4, 6]

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        running = 0
        max_running = 0
        lock = asyncio.Lock()

        async def track(x):
            nonlocal running, max_running
            async with lock:
                running += 1
                max_running = max(max_running, running)
            await asyncio.sleep(0.05)
            async with lock:
                running -= 1
            return x

        await async_parallel_map(track, range(10), concurrency=3)
        assert max_running <= 3


class TestAsyncErrorHandling:
    @pytest.mark.asyncio
    async def test_single_failure(self):
        async def fail_on_2(x):
            if x == 2:
                raise ValueError("bad")
            return x

        result = await async_parallel_map(fail_on_2, [1, 2, 3], concurrency=2)
        assert not result.ok
        assert len(result.failures()) == 1
        assert len(result.successes()) == 2

    @pytest.mark.asyncio
    async def test_multiple_failures_all_collected(self):
        async def fail_even(x):
            if x % 2 == 0:
                raise ValueError(f"even: {x}")
            return x

        result = await async_parallel_map(fail_even, range(6), concurrency=3)
        assert len(result.failures()) == 3  # 0, 2, 4
        assert len(result.successes()) == 3  # 1, 3, 5


class TestAsyncTimeout:
    @pytest.mark.asyncio
    async def test_per_task_timeout(self):
        async def slow(x):
            await asyncio.sleep(2.0)
            return x

        result = await async_parallel_map(
            slow, [1, 2], concurrency=2, task_timeout=0.1
        )
        assert not result.ok
        assert len(result.failures()) == 2
        for _, exc in result.failures():
            assert isinstance(exc, asyncio.TimeoutError)


class TestAsyncRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit(self):
        async def noop(x):
            return x

        start = time.monotonic()
        await async_parallel_map(
            noop, range(5), concurrency=5,
            rate_limit=RateLimit(10, "second"),
        )
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3

    @pytest.mark.asyncio
    async def test_rate_limit_shorthand(self):
        async def noop(x):
            return x

        start = time.monotonic()
        await async_parallel_map(noop, range(5), concurrency=5, rate_limit=10)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3


class TestAsyncProgress:
    @pytest.mark.asyncio
    async def test_progress_callback(self):
        progress = []

        async def noop(x):
            return x

        await async_parallel_map(
            noop, [1, 2, 3], concurrency=2,
            on_progress=lambda done, total: progress.append((done, total)),
        )
        assert len(progress) == 3
        assert all(total == 3 for _, total in progress)


class TestAsyncDecorator:
    @pytest.mark.asyncio
    async def test_single_call(self):
        @async_parallel(concurrency=2)
        async def double(x):
            return x * 2

        assert await double(5) == 10

    @pytest.mark.asyncio
    async def test_bare_decorator(self):
        @async_parallel
        async def double(x):
            return x * 2

        assert await double(5) == 10

    @pytest.mark.asyncio
    async def test_map(self):
        @async_parallel(concurrency=2)
        async def double(x):
            return x * 2

        results = await double.map([1, 2, 3])
        assert list(results) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_instance_method(self):
        class Fetcher:
            def __init__(self, prefix):
                self.prefix = prefix

            @async_parallel(concurrency=2)
            async def fetch(self, x):
                return f"{self.prefix}-{x}"

        f = Fetcher("data")
        assert await f.fetch("a") == "data-a"
        results = await f.fetch.map(["a", "b", "c"])
        assert list(results) == ["data-a", "data-b", "data-c"]


class TestAsyncWorkersAlias:
    async def test_workers_warns_and_works(self):
        async def double(x):
            return x * 2

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = await async_parallel_map(double, [1, 2, 3], workers=2)
            assert len(w) == 1
            assert "did you mean concurrency" in str(w[0].message)
        assert list(result) == [2, 4, 6]
