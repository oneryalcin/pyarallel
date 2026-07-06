"""Tests for Limiter — shared token bucket with burst capacity and pause().

Math tests use a fake clock (no sleeping, fully deterministic).
Integration tests use real time with generous tolerances.

The acquire-loop contract under test: a token is consumed only at grant, a
failed attempt leaks nothing, and sleeping waiters re-check shared state —
so a pause() issued mid-sleep is honored, and release after a pause is
paced, never a herd.
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


def drain(limiter: Limiter, n: int = 1) -> None:
    for _ in range(n):
        assert limiter._try_acquire() == 0.0


class TestBucketMath:
    def test_burst_allows_immediate_calls(self):
        limiter, _ = make_limiter(rate=1, burst=3)
        drain(limiter, 3)
        assert limiter._try_acquire() == pytest.approx(1.0)

    def test_default_burst_gives_even_spacing(self):
        limiter, clock = make_limiter(rate=2, burst=1)
        drain(limiter)
        assert limiter._probe() == pytest.approx(0.5)
        clock.advance(0.5)
        drain(limiter)  # exactly one token accrued

    def test_failed_acquire_consumes_nothing(self):
        """The Sonnet finding: abandoned waiters must not leak capacity."""
        limiter, clock = make_limiter(rate=2, burst=1)
        drain(limiter)
        for _ in range(5):  # five impatient callers probe and give up
            assert limiter._try_acquire() > 0
        clock.advance(0.5)  # one interval later, ONE token exists — no debt
        drain(limiter)

    def test_refill_caps_at_burst_capacity(self):
        limiter, clock = make_limiter(rate=2, burst=2)
        drain(limiter, 2)
        clock.advance(100.0)  # long idle must not over-accumulate
        drain(limiter, 2)
        assert limiter._try_acquire() == pytest.approx(0.5)

    def test_pause_holds_slots_despite_available_tokens(self):
        limiter, _ = make_limiter(rate=10, burst=5)
        limiter.pause(30.0)
        assert limiter._try_acquire() == pytest.approx(30.0)

    def test_pause_furthest_deadline_wins(self):
        limiter, _ = make_limiter(rate=10, burst=1)
        limiter.pause(30.0)
        limiter.pause(5.0)  # shorter pause must not shrink the hold
        assert limiter._try_acquire() == pytest.approx(30.0)

    def test_pause_nonpositive_is_ignored(self):
        limiter, _ = make_limiter(rate=10, burst=1)
        limiter.pause(0.0)
        limiter.pause(-5.0)
        drain(limiter)

    def test_release_after_pause_is_paced_not_a_herd(self):
        """The Sonnet/Codex finding: a full bucket must not dump at the
        deadline of the very pause a server just mandated."""
        limiter, clock = make_limiter(rate=10, burst=5)  # bucket starts full
        limiter.pause(1.0)
        clock.advance(1.0)  # deadline reached
        drain(limiter)  # exactly one call admitted at the deadline
        assert limiter._try_acquire() == pytest.approx(0.1)  # rest at rate pace

    def test_pause_arriving_mid_sleep_is_honored(self):
        """The Codex finding: a waiter sleeping toward its slot must
        re-check and see a pause that landed while it slept."""
        limiter, clock = make_limiter(rate=2, burst=1)
        drain(limiter)
        assert limiter._try_acquire() == pytest.approx(0.5)  # told: wake at +0.5
        clock.advance(0.1)
        limiter.pause(2.0)  # pause lands while the waiter sleeps
        clock.advance(0.4)  # waiter wakes at +0.5 and re-checks
        assert limiter._try_acquire() == pytest.approx(1.6)  # held to deadline

    def test_fractional_token_grants_within_epsilon(self):
        """0.9999999999999996 tokens is a grant, not a 1e-17s spin."""
        limiter, _ = make_limiter(rate=10, burst=1)
        drain(limiter)
        limiter._tokens = 1.0 - 1e-12
        drain(limiter)


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
        # 10 grants against one 10/s budget → ~0.9s of spacing.
        # Two private limiters would finish in ~0.4s.
        assert elapsed >= 0.7

    def test_pause_mid_sleep_extends_real_wait(self):
        """End-to-end: a thread already sleeping in wait() honors a pause
        that arrives after its sleep began."""
        limiter = Limiter(RateLimit(2, "second"))
        limiter.wait()  # drain — next token due in ~0.5s

        start = time.monotonic()
        waiter = threading.Thread(target=limiter.wait)
        waiter.start()
        time.sleep(0.1)  # let it start sleeping toward +0.5s
        limiter.pause(1.0)  # hold until ~+1.1s
        waiter.join()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.9  # woke at +0.5, re-checked, honored the pause

    async def test_shared_limiter_spans_sync_and_async(self):
        limiter = Limiter(RateLimit(10, "second"))

        async def fetch(x):
            return x

        start = time.monotonic()
        sync_result = parallel_map(lambda x: x, range(3), rate_limit=limiter)
        async_result = await async_parallel_map(fetch, range(3), rate_limit=limiter)
        elapsed = time.monotonic() - start
        assert sync_result.ok and async_result.ok
        # 6 grants against one 10/s budget → ~0.5s of spacing.
        assert elapsed >= 0.4

    def test_limiter_accepted_by_parallel_iter(self):
        limiter = Limiter(RateLimit(100, "second", burst=100))
        results = list(parallel_iter(lambda x: x * 2, range(5), rate_limit=limiter))
        assert sorted(r.value for r in results) == [0, 2, 4, 6, 8]


class TestWaitTimeout:
    def test_wait_with_budget_gives_up_without_consuming(self):
        """A predicted wait beyond the budget returns False immediately —
        no sleeping the budget away, and no capacity consumed."""
        limiter, clock = make_limiter(rate=1, burst=1)
        drain(limiter)
        start = time.monotonic()
        assert limiter.wait(timeout=0.2) is False  # predicted 1s > 0.2s
        assert time.monotonic() - start < 0.1  # returned immediately
        clock.advance(1.0)
        drain(limiter)  # the failed wait leaked nothing

    def test_wait_without_budget_still_blocks_and_returns_true(self):
        limiter, _clock = make_limiter(rate=1000, burst=1)
        assert limiter.wait() is True
