"""Tests for max_errors — stop paying for a dead API.

The core contract under test (the review finding that shaped the design):
with max_errors set, work is admitted through a bounded window, so a dead
API costs tens of calls, not thousands. Admission bounds are measured with
instrumented counters, never just asserted.
"""

import threading
import time

import pytest

from pyarallel import (
    Aborted,
    CheckpointError,
    RateLimit,
    Retry,
    async_parallel_map,
    parallel_iter,
    parallel_map,
)

WINDOW_SLACK = 8  # scheduling races may admit a few extra before abort lands


class Counter:
    """Thread-safe execution counter."""

    def __init__(self):
        self.n = 0
        self._lock = threading.Lock()

    def bump(self):
        with self._lock:
            self.n += 1


class TestCollectedMapAbort:
    def test_dead_api_aborts_with_bounded_cost(self):
        """The point of the feature: 2000 items against an always-failing
        API with max_errors=10 must execute ~10+window calls, not 2000."""
        calls = Counter()

        def dead_api(x):
            calls.bump()
            raise ConnectionError("api is down")

        result = parallel_map(dead_api, range(2000), workers=8, max_errors=10)

        assert len(result) == 2000
        assert not result.ok
        window = 16  # 2 x workers
        assert calls.n <= 10 + window + WINDOW_SLACK
        real = [e for _, e in result.failures() if not isinstance(e, Aborted)]
        aborted = [e for _, e in result.failures() if isinstance(e, Aborted)]
        assert len(real) >= 10
        assert all(isinstance(e, ConnectionError) for e in real)
        assert len(real) + len(aborted) == 2000

    def test_successes_before_abort_are_preserved(self):
        """Anything that finished before the abort stays a success."""

        def fail_late(x):
            if x >= 5:
                raise ValueError("bad")
            return x * 10

        # workers=1 makes completion order deterministic: 0-4 succeed,
        # then 5, 6, 7 fail and trigger max_errors=3.
        result = parallel_map(fail_late, range(50), workers=1, max_errors=3)

        assert dict(result.successes()) == {i: i * 10 for i in range(5)}
        real = [i for i, e in result.failures() if not isinstance(e, Aborted)]
        # 5, 6, 7 trigger the abort; completions that raced the stop (at
        # most one window = 2) are salvaged as real results, not Aborted.
        assert real[:3] == [5, 6, 7]
        assert len(real) <= 3 + 2
        assert all(
            isinstance(e, Aborted) for i, e in result.failures() if i > max(real)
        )

    def test_aborted_message_names_the_threshold(self):
        result = parallel_map(lambda x: 1 // 0, range(100), workers=2, max_errors=2)
        aborted = [e for _, e in result.failures() if isinstance(e, Aborted)]
        assert aborted
        assert "max_errors=2" in str(aborted[0])

    def test_max_errors_larger_than_input_never_triggers(self):
        def maybe_fail(x):
            if x % 2:
                raise ValueError("odd")
            return x

        result = parallel_map(maybe_fail, range(10), workers=4, max_errors=100)
        assert len(result.failures()) == 5
        assert not any(isinstance(e, Aborted) for _, e in result.failures())
        assert len(result.successes()) == 5

    def test_all_success_path_is_unaffected(self):
        result = parallel_map(lambda x: x * 2, range(20), workers=4, max_errors=3)
        assert result.ok
        assert result.values() == [x * 2 for x in range(20)]

    def test_max_errors_zero_rejected(self):
        with pytest.raises(ValueError, match="max_errors"):
            parallel_map(lambda x: x, [1], max_errors=0)

    def test_retry_success_does_not_count_as_failure(self):
        """An item that fails then succeeds on retry is a success — it must
        not push the run toward abort."""
        attempts = {}
        lock = threading.Lock()

        def flaky(x):
            with lock:
                attempts[x] = attempts.get(x, 0) + 1
                if attempts[x] == 1:
                    raise ValueError("transient")
            return x

        result = parallel_map(
            flaky,
            range(10),
            workers=2,
            retry=Retry(attempts=2, backoff=0, jitter=False),
            max_errors=1,  # a single counted failure would abort
        )
        assert result.ok

    def test_timeout_still_works_on_the_windowed_path(self):
        """timeout= and max_errors compose; here timeout fires first."""

        def slow(x):
            time.sleep(0.5)
            return x

        start = time.monotonic()
        result = parallel_map(slow, range(50), workers=2, max_errors=5, timeout=0.3)
        assert time.monotonic() - start < 2.0
        assert not result.ok
        errors = [e for _, e in result.failures()]
        assert any(isinstance(e, TimeoutError) for e in errors)
        assert not any(isinstance(e, Aborted) for e in errors)

    def test_on_progress_fires_on_windowed_path(self):
        calls = []
        result = parallel_map(
            lambda x: x,
            range(10),
            workers=2,
            max_errors=5,
            on_progress=lambda done, total: calls.append((done, total)),
        )
        assert result.ok
        assert calls[-1] == (10, 10)


class TestAsyncCollectedMapAbort:
    async def test_dead_api_aborts_with_bounded_cost(self):
        calls = Counter()

        async def dead_api(x):
            calls.bump()
            raise ConnectionError("api is down")

        result = await async_parallel_map(
            dead_api, range(2000), concurrency=8, max_errors=10
        )

        assert len(result) == 2000
        window = 16  # 2 x concurrency
        assert calls.n <= 10 + window + WINDOW_SLACK
        real = [e for _, e in result.failures() if not isinstance(e, Aborted)]
        assert len(real) >= 10
        assert all(isinstance(e, ConnectionError) for e in real)

    async def test_successes_preserved_and_all_slots_accounted(self):
        async def fail_late(x):
            if x >= 5:
                raise ValueError("bad")
            return x * 10

        result = await async_parallel_map(
            fail_late, range(50), concurrency=1, max_errors=3
        )
        assert dict(result.successes()) == {i: i * 10 for i in range(5)}
        assert len(result) == 50

    async def test_max_errors_zero_rejected(self):
        async def identity(x):
            return x

        with pytest.raises(ValueError, match="max_errors"):
            await async_parallel_map(identity, [1], max_errors=0)

    async def test_all_success_path_is_unaffected(self):
        async def double(x):
            return x * 2

        result = await async_parallel_map(double, range(20), max_errors=3)
        assert result.ok
        assert result.values() == [x * 2 for x in range(20)]


class TestStreamingAbort:
    def test_stream_ends_after_nth_failure(self):
        """Streaming: yield the failures that occurred, then just stop —
        no placeholder items for unseen input."""
        calls = Counter()

        def dead_api(x):
            calls.bump()
            raise ConnectionError("down")

        results = list(parallel_iter(dead_api, range(1000), workers=4, max_errors=5))
        errors = [r for r in results if not r.ok]
        assert len(errors) == 5
        assert len(results) < 1000
        window = 8  # 2 x workers
        assert calls.n <= 5 + window + WINDOW_SLACK

    def test_ordered_stream_ends_after_nth_failure_in_order(self):
        def fail_from_ten(x):
            if x >= 10:
                raise ValueError("bad")
            return x

        results = list(
            parallel_iter(
                fail_from_ten, range(1000), workers=4, ordered=True, max_errors=3
            )
        )
        assert [r.index for r in results] == list(range(len(results)))
        assert sum(1 for r in results if not r.ok) == 3
        assert len(results) < 50  # ended promptly, not after 1000

    async def test_async_stream_ends_after_nth_failure(self):
        calls = Counter()

        async def dead_api(x):
            calls.bump()
            raise ConnectionError("down")

        results = []
        async for item in parallel_iter_async_helper(dead_api, max_errors=5):
            results.append(item)
        errors = [r for r in results if not r.ok]
        assert len(errors) == 5
        assert len(results) < 1000
        assert calls.n <= 5 + 8 + WINDOW_SLACK


def parallel_iter_async_helper(fn, *, max_errors):
    from pyarallel import async_parallel_iter

    return async_parallel_iter(fn, range(1000), concurrency=4, max_errors=max_errors)


class TestCheckpointPlusMaxErrors:
    def test_overnight_job_story(self, tmp_path):
        """The 'dead API overnight job': run 1 aborts on failures, run 2
        resumes — completed successes load from disk and never re-execute."""
        ckpt = tmp_path / "job.ckpt"
        calls = Counter()

        def api(x):
            calls.bump()
            if x >= 5 and BROKEN["now"]:
                raise ConnectionError("down")
            return x * 10

        BROKEN["now"] = True
        first = parallel_map(
            api, range(50), workers=1, max_errors=3, checkpoint=str(ckpt)
        )
        assert not first.ok
        assert dict(first.successes()) == {i: i * 10 for i in range(5)}
        run1_calls = calls.n

        BROKEN["now"] = False
        second = parallel_map(
            api, range(50), workers=1, max_errors=3, checkpoint=str(ckpt)
        )
        assert second.ok
        assert second.values() == [i * 10 for i in range(50)]
        # Items 0-4 came from the checkpoint: exactly 45 new executions.
        assert calls.n - run1_calls == 45


BROKEN = {"now": True}


class TestDecoratorMaxErrors:
    def test_map_accepts_max_errors(self):
        from pyarallel import parallel

        @parallel(workers=2)
        def dead(x):
            raise ValueError("down")

        result = dead.map(range(100), max_errors=2)
        assert len(result) == 100
        assert any(isinstance(e, Aborted) for _, e in result.failures())

    async def test_async_map_accepts_max_errors(self):
        from pyarallel import async_parallel

        @async_parallel(concurrency=2)
        async def dead(x):
            raise ValueError("down")

        result = await dead.map(range(100), max_errors=2)
        assert len(result) == 100
        assert any(isinstance(e, Aborted) for _, e in result.failures())

    def test_stream_accepts_max_errors(self):
        from pyarallel import parallel

        @parallel(workers=2)
        def dead(x):
            raise ValueError("down")

        results = list(dead.stream(range(1000), max_errors=3))
        assert sum(1 for r in results if not r.ok) == 3
        assert len(results) < 1000


class TestMidPlanReviewFindings:
    """Regression tests from the mid-plan review cycle — each names the
    finding it prevents."""

    def test_poison_source_is_not_consumed_after_abort(self):
        """Codex adversarial [high]: the abort path drained unsized sources
        to append placeholders — consuming (or raising from) input that
        must never be touched after the stop."""
        consumed = Counter()

        def source():
            for i in range(1000):
                consumed.bump()
                yield i
                if consumed.n > 50:
                    raise RuntimeError("source poisoned past the abort point")

        def dead(x):
            raise ConnectionError("down")

        result = parallel_map(dead, source(), workers=2, max_errors=3)
        assert not result.ok  # returned a result — the poison never raised
        assert consumed.n <= 3 + 4 + WINDOW_SLACK  # trigger + window (2x2)
        # Unsized input: the result covers only the items actually pulled.
        assert len(result) == consumed.n

    async def test_async_poison_source_is_not_consumed_after_abort(self):
        consumed = Counter()

        def source():
            for i in range(1000):
                consumed.bump()
                yield i

        async def dead(x):
            raise ConnectionError("down")

        result = await async_parallel_map(dead, source(), concurrency=2, max_errors=3)
        assert not result.ok
        assert consumed.n <= 3 + 4 + WINDOW_SLACK
        assert len(result) == consumed.n

    def test_timeout_already_expired_runs_nothing(self):
        """Codex review [P2]: an expired deadline must not admit work —
        timeout=0 means zero tasks run, not a full window of them."""
        calls = Counter()

        def task(x):
            calls.bump()
            return x

        result = parallel_map(task, range(50), workers=4, max_errors=5, timeout=0)
        assert calls.n == 0
        assert len(result) == 50
        assert all(isinstance(e, TimeoutError) for _, e in result.failures())

    def test_rate_limited_dead_api_stops_admission_mid_fill(self):
        """Codex adversarial [medium]: with a rate limiter the driver paid
        one paced wait per window slot even after the first failure had
        already completed. Admission must stop mid-fill."""
        calls = Counter()

        def dead(x):
            calls.bump()
            raise ConnectionError("down")

        start = time.monotonic()
        result = parallel_map(
            dead,
            range(1000),
            workers=8,
            max_errors=1,
            rate_limit=RateLimit(10, "second"),
        )
        elapsed = time.monotonic() - start
        assert not result.ok
        assert calls.n <= 5  # not a full window of 16 paced submissions
        assert elapsed < 2.0  # not 16 x 0.1s of pacing

    def test_streaming_rate_limited_dead_api_stops_admission_mid_fill(self):
        calls = Counter()

        def dead(x):
            calls.bump()
            raise ConnectionError("down")

        start = time.monotonic()
        results = list(
            parallel_iter(
                dead,
                range(1000),
                workers=8,
                max_errors=1,
                rate_limit=RateLimit(10, "second"),
            )
        )
        elapsed = time.monotonic() - start
        assert sum(1 for r in results if not r.ok) == 1
        assert calls.n <= 5
        assert elapsed < 2.0

    def test_checkpoint_write_failure_aborts_loudly_on_windowed_path(self, tmp_path):
        """Opus finding: the CheckpointError fail-loud contract was
        untested on the windowed (max_errors) engine."""

        def unpicklable_result(x):
            return lambda: x

        with pytest.raises(CheckpointError):
            parallel_map(
                unpicklable_result,
                range(10),
                workers=2,
                max_errors=5,
                checkpoint=str(tmp_path / "bad.ckpt"),
            )

    def test_ordered_abort_ends_after_straggler_in_order(self):
        """Codex adversarial [high]: the ordered+max_errors contract, made
        explicit — the stream ends after the Nth failure is yielded in
        input order; failures buffered behind a straggler wait for it, but
        admission has already stopped, so cost stays window-bounded."""
        calls = Counter()

        def slow_head_then_dead(x):
            calls.bump()
            if x == 0:
                time.sleep(0.4)
                return x
            raise ConnectionError("down")

        results = list(
            parallel_iter(
                slow_head_then_dead,
                range(1000),
                workers=4,
                batch_size=8,
                ordered=True,
                max_errors=3,
            )
        )
        assert results[0].index == 0 and results[0].ok  # straggler, in order
        assert [r.index for r in results] == list(range(len(results)))
        assert sum(1 for r in results if not r.ok) == 3
        assert calls.n <= 1 + 8 + WINDOW_SLACK  # admission stopped at window
