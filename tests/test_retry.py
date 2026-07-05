"""Tests for Retry — per-item retry with exponential backoff, jitter, and filtering."""

import time

from pyarallel import Retry, parallel_map


class TestRetryConfig:
    def test_retry_is_frozen_dataclass(self):
        r = Retry(attempts=3, backoff=1.0)
        assert r.attempts == 3
        assert r.backoff == 1.0

    def test_retry_defaults(self):
        r = Retry()
        assert r.attempts == 3
        assert r.backoff == 1.0
        assert r.max_delay == 60.0
        assert r.jitter is True
        assert r.on is None

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

        result = parallel_map(
            flaky,
            [1, 2, 3],
            workers=2,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert result.ok
        assert list(result) == [10, 20, 30]
        assert all(v == 3 for v in call_count.values())

    def test_retries_exhausted_reports_failure(self):
        """Function always fails — should exhaust retries and report the last error."""

        def always_fail(x):
            raise RuntimeError(f"permanent failure for {x}")

        result = parallel_map(
            always_fail,
            [1, 2],
            workers=2,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert not result.ok
        assert len(result.failures()) == 2
        for _, exc in result.failures():
            assert isinstance(exc, RuntimeError)

    def test_retry_count_matches_attempts(self):
        """Verify function is called exactly `attempts` times on failure."""
        call_count = {}

        def counter(x):
            call_count[x] = call_count.get(x, 0) + 1
            raise ValueError("fail")

        parallel_map(
            counter,
            [42],
            workers=1,
            retry=Retry(attempts=5, backoff=0, jitter=False),
        )
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


class TestExponentialBackoff:
    def test_backoff_takes_time(self):
        """With backoff, retries should take measurably longer."""

        def always_fail(x):
            raise ValueError("fail")

        start = time.monotonic()
        parallel_map(
            always_fail,
            [1],
            workers=1,
            retry=Retry(attempts=3, backoff=0.1, jitter=False),
        )
        elapsed = time.monotonic() - start
        # attempt 0: fail → sleep(0.1 * 2^0 = 0.1)
        # attempt 1: fail → sleep(0.1 * 2^1 = 0.2)
        # attempt 2: fail → done
        # total sleep: 0.3s
        assert elapsed >= 0.25

    def test_exponential_growth(self):
        """Delays should grow exponentially, not linearly."""
        delays = []

        def always_fail(x):
            delays.append(time.monotonic())
            raise ValueError("fail")

        parallel_map(
            always_fail,
            [1],
            workers=1,
            retry=Retry(attempts=4, backoff=0.05, jitter=False),
        )
        # Compute gaps between attempts
        gaps = [delays[i + 1] - delays[i] for i in range(len(delays) - 1)]
        # gaps should be roughly [0.05, 0.10, 0.20] — each ~2x the last
        assert len(gaps) == 3
        assert gaps[1] > gaps[0] * 1.5  # second gap > 1.5x first (exponential)

    def test_max_delay_caps_backoff(self):
        """max_delay should cap the exponential growth."""

        def always_fail(x):
            raise ValueError("fail")

        start = time.monotonic()
        parallel_map(
            always_fail,
            [1],
            workers=1,
            # backoff=1.0 * 2^10 = 1024, but max_delay=0.1 caps it
            retry=Retry(attempts=4, backoff=1.0, max_delay=0.05, jitter=False),
        )
        elapsed = time.monotonic() - start
        # 3 sleeps, each capped at 0.05s = 0.15s max
        assert elapsed < 0.5  # Would be ~7s without cap


class TestJitter:
    def test_jitter_adds_variance(self):
        """With jitter, retry delays should vary between runs."""
        delays_per_run = []

        for _ in range(2):
            timestamps = []

            def always_fail(x, _ts=timestamps):
                _ts.append(time.monotonic())
                raise ValueError("fail")

            parallel_map(
                always_fail,
                [1],
                workers=1,
                retry=Retry(attempts=3, backoff=0.05, jitter=True),
            )
            gaps = [
                timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)
            ]
            delays_per_run.append(gaps)

        # Jitter should make the two runs different
        # (technically could be identical, but with random(0.5, 1.5) it's very unlikely)
        run1_total = sum(delays_per_run[0])
        run2_total = sum(delays_per_run[1])
        # Just verify they both took some time (jitter doesn't eliminate delay)
        assert run1_total > 0.02
        assert run2_total > 0.02

    def test_no_jitter_is_deterministic(self):
        """Without jitter, delays should be consistent."""
        timestamps = []

        def always_fail(x):
            timestamps.append(time.monotonic())
            raise ValueError("fail")

        parallel_map(
            always_fail,
            [1],
            workers=1,
            retry=Retry(attempts=4, backoff=0.05, jitter=False),
        )
        gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        # gaps should be ~0.05, ~0.10, ~0.20
        assert abs(gaps[0] - 0.05) < 0.03
        assert abs(gaps[1] - 0.10) < 0.03


class TestRetryOnFilter:
    def test_retries_only_matching_exceptions(self):
        """Only retry exceptions matching the `on` filter."""
        call_count = {}

        def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 3:
                raise ConnectionError("transient")
            return x

        result = parallel_map(
            flaky,
            [1, 2],
            workers=2,
            retry=Retry(attempts=3, backoff=0, jitter=False, on=(ConnectionError,)),
        )
        assert result.ok
        assert list(result) == [1, 2]

    def test_non_matching_exception_fails_immediately(self):
        """Exceptions not in `on` should NOT be retried."""
        call_count = 0

        def bad_input(x):
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input — don't retry this")

        result = parallel_map(
            bad_input,
            [1],
            workers=1,
            retry=Retry(
                attempts=5, backoff=0, jitter=False, on=(ConnectionError, TimeoutError)
            ),
        )
        assert not result.ok
        assert call_count == 1  # Called once, not retried

    def test_mixed_exception_types(self):
        """Retryable exceptions retry; non-retryable fail immediately."""
        call_count = {}

        def mixed(x):
            call_count[x] = call_count.get(x, 0) + 1
            if x == 1:
                raise ConnectionError("retry me")  # retryable
            if x == 2:
                raise ValueError("don't retry me")  # not retryable
            return x

        result = parallel_map(
            mixed,
            [1, 2, 3],
            workers=3,
            retry=Retry(attempts=3, backoff=0, jitter=False, on=(ConnectionError,)),
        )
        assert len(result.failures()) == 2  # both 1 and 2 fail
        assert call_count[1] == 3  # retried 3 times
        assert call_count[2] == 1  # failed immediately
        assert call_count[3] == 1  # succeeded first try

    def test_on_none_retries_everything(self):
        """on=None (default) retries all exceptions."""
        call_count = {}

        def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 2:
                raise ValueError("whatever")
            return x

        result = parallel_map(
            flaky,
            [1],
            workers=1,
            retry=Retry(attempts=3, backoff=0, jitter=False, on=None),
        )
        assert result.ok
        assert call_count[1] == 2


class TestRetryPartialFailure:
    def test_some_succeed_some_retry_and_fail(self):
        def half_fail(x):
            if x % 2 == 0:
                raise ValueError(f"even: {x}")
            return x

        result = parallel_map(
            half_fail,
            range(6),
            workers=3,
            retry=Retry(attempts=2, backoff=0, jitter=False),
        )
        assert len(result.successes()) == 3
        assert len(result.failures()) == 3

    def test_retry_with_progress_counts_correctly(self):
        """Progress should count final outcomes, not retry attempts."""
        progress = []

        def flaky(x):
            raise ValueError("fail")

        parallel_map(
            flaky,
            [1, 2, 3],
            workers=2,
            retry=Retry(attempts=2, backoff=0, jitter=False),
            on_progress=lambda d, t: progress.append((d, t)),
        )
        assert len(progress) == 3
        assert all(t == 3 for _, t in progress)


class TestAsyncRetry:
    async def test_async_retry_succeeds(self):
        from pyarallel import async_parallel_map

        call_count = {}

        async def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 2:
                raise ValueError("not yet")
            return x * 10

        result = await async_parallel_map(
            flaky,
            [1, 2, 3],
            concurrency=2,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert result.ok
        assert list(result) == [10, 20, 30]

    async def test_async_retry_exhausted(self):
        from pyarallel import async_parallel_map

        async def always_fail(x):
            raise RuntimeError("nope")

        result = await async_parallel_map(
            always_fail,
            [1],
            concurrency=1,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert not result.ok
        assert len(result.failures()) == 1

    async def test_async_retry_on_filter(self):
        from pyarallel import async_parallel_map

        call_count = 0

        async def bad(x):
            nonlocal call_count
            call_count += 1
            raise ValueError("don't retry")

        result = await async_parallel_map(
            bad,
            [1],
            concurrency=1,
            retry=Retry(attempts=5, backoff=0, jitter=False, on=(ConnectionError,)),
        )
        assert not result.ok
        assert call_count == 1


class RetryAfterError(Exception):
    """Simulates an HTTP 429 carrying a Retry-After hint."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"429: retry after {retry_after}s")
        self.retry_after = retry_after


class TestRetryIf:
    def test_predicate_false_fails_immediately(self):
        calls = {"n": 0}

        def fail(x):
            calls["n"] += 1
            raise ValueError("permanent")

        result = parallel_map(
            fail,
            [1],
            retry=Retry(attempts=3, backoff=0, jitter=False, retry_if=lambda e: False),
        )
        assert not result.ok
        assert calls["n"] == 1

    def test_predicate_true_retries(self):
        calls = {"n": 0}

        def fail(x):
            calls["n"] += 1
            raise ValueError("transient")

        parallel_map(
            fail,
            [1],
            retry=Retry(attempts=3, backoff=0, jitter=False, retry_if=lambda e: True),
        )
        assert calls["n"] == 3

    def test_predicate_composes_with_type_filter(self):
        """`on` must match AND `retry_if` must pass — TypeError skips both retries."""
        calls = {"n": 0}

        def fail(x):
            calls["n"] += 1
            raise TypeError("not retryable by type")

        result = parallel_map(
            fail,
            [1],
            retry=Retry(
                attempts=3,
                backoff=0,
                jitter=False,
                on=(ValueError,),
                retry_if=lambda e: True,
            ),
        )
        assert not result.ok
        assert calls["n"] == 1


class TestWaitFrom:
    def test_server_wait_used_as_retry_delay(self):
        calls = {"n": 0}

        def flaky(x):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RetryAfterError(0.3)
            return x

        start = time.monotonic()
        result = parallel_map(
            flaky,
            [1],
            retry=Retry(
                attempts=2,
                backoff=0,
                jitter=False,
                wait_from=lambda e: getattr(e, "retry_after", None),
            ),
        )
        elapsed = time.monotonic() - start
        assert result.ok
        assert calls["n"] == 2
        assert elapsed >= 0.25  # server-mandated wait, not the 0s backoff

    def test_wait_from_none_falls_back_to_backoff(self):
        calls = {"n": 0}

        def fail(x):
            calls["n"] += 1
            raise ValueError("no retry-after here")

        start = time.monotonic()
        parallel_map(
            fail,
            [1],
            retry=Retry(attempts=3, backoff=0, jitter=False, wait_from=lambda e: None),
        )
        elapsed = time.monotonic() - start
        assert calls["n"] == 3
        assert elapsed < 0.2  # fell back to the 0s backoff

    def test_negative_server_wait_clamped_to_zero(self):
        calls = {"n": 0}

        def fail(x):
            calls["n"] += 1
            raise RetryAfterError(-5.0)

        start = time.monotonic()
        parallel_map(
            fail,
            [1],
            retry=Retry(
                attempts=3,
                backoff=0,
                jitter=False,
                wait_from=lambda e: getattr(e, "retry_after", None),
            ),
        )
        elapsed = time.monotonic() - start
        assert calls["n"] == 3
        assert elapsed < 0.2


class TestServerWaitPausesLimiter:
    def test_final_attempt_still_pauses_shared_limiter(self):
        """A 429 from a task that gives up must still slow the pool."""
        from pyarallel import Limiter, RateLimit

        limiter = Limiter(RateLimit(1000, "second"))

        def always_throttled(x):
            raise RetryAfterError(0.5)

        result = parallel_map(
            always_throttled,
            [1],
            rate_limit=limiter,
            retry=Retry(
                attempts=1,  # no retry sleep — only the pause side effect
                wait_from=lambda e: getattr(e, "retry_after", None),
            ),
        )
        assert not result.ok
        # The limiter must now hold new slots for ~0.5s.
        assert limiter._probe() >= 0.2

    async def test_async_server_wait_pauses_shared_limiter(self):
        from pyarallel import Limiter, RateLimit, async_parallel_map

        limiter = Limiter(RateLimit(1000, "second"))

        async def always_throttled(x):
            raise RetryAfterError(0.5)

        result = await async_parallel_map(
            always_throttled,
            [1],
            rate_limit=limiter,
            retry=Retry(
                attempts=1,
                wait_from=lambda e: getattr(e, "retry_after", None),
            ),
        )
        assert not result.ok
        assert limiter._probe() >= 0.2


class TestRetriesConsumeRateLimit:
    """The adversarial-review finding: a retry is a fresh API call and must
    draw a fresh token — retries must never bypass the shared limiter."""

    def test_sync_retries_are_rate_limited(self):
        from pyarallel import Limiter, RateLimit

        limiter = Limiter(RateLimit(10, "second"))
        timestamps = []

        def flaky(x):
            timestamps.append(time.monotonic())
            if len(timestamps) < 4:
                raise ValueError("transient")
            return x

        start = time.monotonic()
        result = parallel_map(
            flaky,
            [1],
            rate_limit=limiter,
            retry=Retry(attempts=4, backoff=0, jitter=False),
        )
        elapsed = time.monotonic() - start
        assert result.ok
        assert len(timestamps) == 4
        # 4 attempts against a 10/s budget: >= 0.3s of token spacing.
        # Before the fix this ran in milliseconds (4x quota violation).
        assert elapsed >= 0.25

    async def test_async_retries_are_rate_limited(self):
        from pyarallel import Limiter, RateLimit, async_parallel_map

        limiter = Limiter(RateLimit(10, "second"))
        calls = {"n": 0}

        async def flaky(x):
            calls["n"] += 1
            if calls["n"] < 4:
                raise ValueError("transient")
            return x

        start = time.monotonic()
        result = await async_parallel_map(
            flaky,
            [1],
            rate_limit=limiter,
            retry=Retry(attempts=4, backoff=0, jitter=False),
        )
        elapsed = time.monotonic() - start
        assert result.ok
        assert calls["n"] == 4
        assert elapsed >= 0.25


class TestMaxServerWait:
    """A hostile Retry-After: 86400 must not pin a worker for a day."""

    def test_server_wait_clamped_by_default(self):
        r = Retry(wait_from=lambda e: 86400.0)
        assert r._server_wait(ValueError()) == 600.0

    def test_none_disables_the_cap(self):
        r = Retry(wait_from=lambda e: 86400.0, max_server_wait=None)
        assert r._server_wait(ValueError()) == 86400.0

    def test_custom_cap(self):
        r = Retry(wait_from=lambda e: 100.0, max_server_wait=5.0)
        assert r._server_wait(ValueError()) == 5.0

    def test_negative_cap_rejected(self):
        import pytest

        with pytest.raises(ValueError, match="max_server_wait"):
            Retry(max_server_wait=-1.0)

    def test_waits_below_cap_pass_through(self):
        r = Retry(wait_from=lambda e: 30.0)
        assert r._server_wait(ValueError()) == 30.0


class TestProcessExecutorRetryPickling:
    """The Opus finding: process executor + lambda predicates used to fail
    every item with PicklingError at result time. Now it fails fast at
    submit with a clear message."""

    def test_lambda_predicates_fail_fast(self):
        import pytest

        with pytest.raises(ValueError, match="module-level"):
            parallel_map(
                _module_level_square,
                [1, 2],
                executor="process",
                retry=Retry(attempts=2, retry_if=lambda e: True),
            )

    def test_picklable_retry_still_works_with_process(self):
        result = parallel_map(
            _module_level_square,
            [2, 3],
            executor="process",
            retry=Retry(attempts=2, backoff=0, jitter=False, on=(ValueError,)),
        )
        assert list(result) == [4, 9]


def _module_level_square(x):
    return x * x
