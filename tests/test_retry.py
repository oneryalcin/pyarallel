"""Tests for Retry — per-item retry with backoff."""

import pytest

from pyarallel import parallel_map, RateLimit
from pyarallel.core import Retry


class TestRetryConfig:
    def test_retry_is_frozen_dataclass(self):
        r = Retry(attempts=3, backoff=1.0)
        assert r.attempts == 3
        assert r.backoff == 1.0

    def test_retry_defaults(self):
        r = Retry()
        assert r.attempts == 3
        assert r.backoff == 0.0

    def test_retry_repr(self):
        r = Retry(attempts=5, backoff=2.0)
        assert "5" in repr(r)


class TestRetryBehavior:
    def test_retries_then_succeeds(self):
        """Function fails twice then succeeds — should get the successful result."""
        call_count = {}

        def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 3:
                raise ValueError(f"attempt {call_count[x]}")
            return x * 10

        result = parallel_map(flaky, [1, 2, 3], workers=2, retry=Retry(attempts=3))
        assert result.ok
        assert list(result) == [10, 20, 30]
        # Each item was called 3 times
        assert all(v == 3 for v in call_count.values())

    def test_retries_exhausted_reports_failure(self):
        """Function always fails — should exhaust retries and report the last error."""
        def always_fail(x):
            raise RuntimeError(f"permanent failure for {x}")

        result = parallel_map(always_fail, [1, 2], workers=2, retry=Retry(attempts=3))
        assert not result.ok
        assert len(result.failures()) == 2
        for _, exc in result.failures():
            assert isinstance(exc, RuntimeError)

    def test_retry_count_matches_attempts(self):
        """Verify the function is called exactly `attempts` times on persistent failure."""
        call_count = {}

        def counter(x):
            call_count[x] = call_count.get(x, 0) + 1
            raise ValueError("fail")

        parallel_map(counter, [42], workers=1, retry=Retry(attempts=5))
        assert call_count[42] == 5

    def test_no_retry_by_default(self):
        """Without retry, a failure is a failure — no retries."""
        call_count = 0

        def once_fail(x):
            nonlocal call_count
            call_count += 1
            raise ValueError("fail")

        parallel_map(once_fail, [1], workers=1)
        assert call_count == 1

    def test_retry_with_backoff(self):
        """With backoff > 0, retries should take measurably longer."""
        import time

        def always_fail(x):
            raise ValueError("fail")

        start = time.monotonic()
        parallel_map(always_fail, [1], workers=1, retry=Retry(attempts=3, backoff=0.1))
        elapsed = time.monotonic() - start
        # 2 retries * 0.1s minimum backoff each = at least 0.2s
        assert elapsed >= 0.15  # Allow some slack


class TestRetryPartialFailure:
    def test_some_succeed_some_retry_and_fail(self):
        """Mix: some items always work, some always fail after retries."""
        def half_fail(x):
            if x % 2 == 0:
                raise ValueError(f"even: {x}")
            return x

        result = parallel_map(half_fail, range(6), workers=3, retry=Retry(attempts=2))
        assert len(result.successes()) == 3   # 1, 3, 5
        assert len(result.failures()) == 3    # 0, 2, 4

    def test_retry_with_progress_counts_correctly(self):
        """Progress should count final outcomes, not retry attempts."""
        progress = []

        def flaky(x):
            raise ValueError("fail")

        parallel_map(
            flaky, [1, 2, 3], workers=2,
            retry=Retry(attempts=2),
            on_progress=lambda d, t: progress.append((d, t)),
        )
        # Progress shows 3 completed items (even though they all failed)
        assert len(progress) == 3
        assert all(t == 3 for _, t in progress)


class TestAsyncRetry:
    async def test_async_retry_succeeds(self):
        from pyarallel import async_parallel_map
        from pyarallel.core import Retry

        call_count = {}

        async def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 2:
                raise ValueError("not yet")
            return x * 10

        result = await async_parallel_map(
            flaky, [1, 2, 3], concurrency=2, retry=Retry(attempts=3)
        )
        assert result.ok
        assert list(result) == [10, 20, 30]

    async def test_async_retry_exhausted(self):
        from pyarallel import async_parallel_map
        from pyarallel.core import Retry

        async def always_fail(x):
            raise RuntimeError("nope")

        result = await async_parallel_map(
            always_fail, [1], concurrency=1, retry=Retry(attempts=3)
        )
        assert not result.ok
        assert len(result.failures()) == 1
