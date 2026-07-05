"""Tests for Limiter — shared token bucket with burst capacity and pause().

Math tests use a fake clock (no sleeping, fully deterministic).
Integration tests use real time with generous tolerances.
"""

import threading
import time

import pytest

from pyarallel import (
    Limiter,
    RateLimit,
    async_parallel_map,
    parallel_iter,
    parallel_map,
)


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(rate: float, burst: int = 1) -> tuple[Limiter, FakeClock]:
    limiter = Limiter(RateLimit(rate, "second", burst=burst))
    clock = FakeClock()
    limiter._clock = clock
    limiter._updated = clock()
    return limiter, clock


class TestBucketMath:
    def test_burst_allows_immediate_calls(self):
        limiter, _ = make_limiter(rate=1, burst=3)
        waits = [limiter._reserve() for _ in range(5)]
        assert waits == [0.0, 0.0, 0.0, 1.0, 2.0]

    def test_default_burst_gives_even_spacing(self):
        limiter, _ = make_limiter(rate=2, burst=1)
        waits = [limiter._reserve() for _ in range(4)]
        assert waits == [0.0, 0.5, 1.0, 1.5]

    def test_refill_caps_at_burst_capacity(self):
        limiter, clock = make_limiter(rate=2, burst=2)
        limiter._reserve()
        limiter._reserve()  # drain
        clock.advance(100.0)  # long idle must not over-accumulate
        waits = [limiter._reserve() for _ in range(3)]
        assert waits == [0.0, 0.0, 0.5]

    def test_fractional_refill(self):
        limiter, clock = make_limiter(rate=1, burst=1)
        assert limiter._reserve() == 0.0
        clock.advance(0.25)  # a quarter token refilled — need 0.75 more
        assert limiter._reserve() == pytest.approx(0.75)

    def test_pause_holds_slots_despite_available_tokens(self):
        limiter, _ = make_limiter(rate=10, burst=5)
        limiter.pause(30.0)
        assert limiter._reserve() == pytest.approx(30.0)
        assert limiter._reserve() == pytest.approx(30.0)  # burst inside the hold

    def test_pause_furthest_deadline_wins(self):
        limiter, _ = make_limiter(rate=10, burst=1)
        limiter.pause(30.0)
        limiter.pause(5.0)  # shorter pause must not shrink the hold
        assert limiter._reserve() == pytest.approx(30.0)

    def test_pause_nonpositive_is_ignored(self):
        limiter, _ = make_limiter(rate=10, burst=1)
        limiter.pause(0.0)
        limiter.pause(-5.0)
        assert limiter._reserve() == 0.0


class TestRateLimitBurst:
    def test_burst_must_be_at_least_one(self):
        with pytest.raises(ValueError, match="burst"):
            RateLimit(10, "second", burst=0)

    def test_burst_default_is_one(self):
        assert RateLimit(10).burst == 1


class TestIntegration:
    def test_burst_removes_startup_pacing(self):
        start = time.monotonic()
        result = parallel_map(
            lambda x: x,
            range(5),
            workers=5,
            rate_limit=RateLimit(5, "second", burst=5),
        )
        elapsed = time.monotonic() - start
        assert result.ok
        # With burst=1 this would take >= 0.8s of spacing; burst=5 is immediate.
        assert elapsed < 0.5

    def test_shared_limiter_enforces_combined_budget(self):
        limiter = Limiter(RateLimit(10, "second"))

        def run():
            parallel_map(lambda x: x, range(5), workers=5, rate_limit=limiter)

        threads = [threading.Thread(target=run) for _ in range(2)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start
        # 10 reservations against one 10/s budget → ~0.9s of spacing.
        # Two private limiters would finish in ~0.4s.
        assert elapsed >= 0.7

    async def test_shared_limiter_spans_sync_and_async(self):
        limiter = Limiter(RateLimit(10, "second"))

        async def fetch(x):
            return x

        start = time.monotonic()
        sync_result = parallel_map(lambda x: x, range(3), rate_limit=limiter)
        async_result = await async_parallel_map(fetch, range(3), rate_limit=limiter)
        elapsed = time.monotonic() - start
        assert sync_result.ok and async_result.ok
        # 6 reservations against one 10/s budget → ~0.5s of spacing.
        assert elapsed >= 0.4

    def test_limiter_accepted_by_parallel_iter(self):
        limiter = Limiter(RateLimit(100, "second", burst=100))
        results = list(parallel_iter(lambda x: x * 2, range(5), rate_limit=limiter))
        assert sorted(r.value for r in results) == [0, 2, 4, 6, 8]
