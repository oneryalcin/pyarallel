"""Tests for result metadata: ItemResult.attempts/.duration, ok_values().

duration contract: wall time from the start of attempt 1 to the final
outcome — including retry backoff sleeps, excluding queue wait. Lower
bounds are asserted (sleep guarantees them); upper bounds are avoided
(they flake).
"""

import asyncio
import time

from pyarallel import (
    ItemResult,
    ParallelResult,
    Retry,
    async_parallel_iter,
    parallel_iter,
    parallel_map,
)
from pyarallel.result import _Failure


class TestStreamingMetadata:
    def test_success_without_retry_reports_one_attempt(self):
        results = list(parallel_iter(lambda x: x, [1, 2, 3], workers=2))
        assert all(r.attempts == 1 for r in results)
        assert all(r.duration >= 0.0 for r in results)

    def test_duration_covers_the_task_body(self):
        def slow(x):
            time.sleep(0.25)
            return x

        [result] = list(parallel_iter(slow, [1], workers=1))
        assert result.duration >= 0.2

    def test_failure_after_retries_reports_all_attempts(self):
        """attempts on a final failure = every attempt actually made."""

        def dead(x):
            raise ValueError("no")

        [result] = list(
            parallel_iter(
                dead,
                [1],
                workers=1,
                retry=Retry(attempts=3, backoff=0.0, jitter=False),
            )
        )
        assert not result.ok
        assert result.attempts == 3

    def test_success_after_retry_reports_actual_attempts(self):
        calls = {}

        def flaky(x):
            calls[x] = calls.get(x, 0) + 1
            if calls[x] < 2:
                raise ValueError("transient")
            return x

        [result] = list(
            parallel_iter(
                flaky,
                [1],
                workers=1,
                retry=Retry(attempts=3, backoff=0.0, jitter=False),
            )
        )
        assert result.ok
        assert result.attempts == 2

    def test_duration_includes_backoff_sleeps(self):
        """The contract: duration spans first attempt to outcome, backoff
        sleeps included — the number a latency budget needs."""

        def dead(x):
            raise ValueError("no")

        [result] = list(
            parallel_iter(
                dead,
                [1],
                workers=1,
                retry=Retry(attempts=2, backoff=0.3, jitter=False),
            )
        )
        assert result.attempts == 2
        assert result.duration >= 0.25  # includes the 0.3s backoff sleep

    def test_non_retryable_failure_reports_attempts_made(self):
        def dead(x):
            raise KeyError("no")

        [result] = list(
            parallel_iter(
                dead,
                [1],
                workers=1,
                retry=Retry(attempts=5, on=(ValueError,), backoff=0.0),
            )
        )
        assert not result.ok
        assert result.attempts == 1  # KeyError is not retryable here

    def test_metadata_survives_process_executor(self):
        """_Outcome must pickle across the process boundary."""
        results = list(
            parallel_iter(_double_for_process, [1, 2], workers=2, executor="process")
        )
        results.sort(key=lambda r: r.index)
        assert [r.value for r in results] == [2, 4]
        assert all(r.attempts == 1 for r in results)
        assert all(r.duration >= 0.0 for r in results)

    async def test_async_metadata_mirrors_sync(self):
        calls = {}

        async def flaky(x):
            calls[x] = calls.get(x, 0) + 1
            if calls[x] < 2:
                raise ValueError("transient")
            await asyncio.sleep(0.05)
            return x

        results = []
        async for item in async_parallel_iter(
            flaky,
            [1],
            concurrency=1,
            retry=Retry(attempts=3, backoff=0.0, jitter=False),
        ):
            results.append(item)

        [result] = results
        assert result.ok
        assert result.attempts == 2
        assert result.duration >= 0.04


class TestItemResultDefaults:
    def test_manual_construction_defaults(self):
        item = ItemResult(0, value="x")
        assert item.attempts == 1
        assert item.duration == 0.0


class TestOkValues:
    def test_full_success(self):
        result = parallel_map(lambda x: x * 2, [1, 2, 3], workers=2)
        assert result.ok_values() == [2, 4, 6]

    def test_partial_failure_never_raises(self):
        entries = [1, _Failure(ValueError("bad")), 3]
        result = ParallelResult(entries)
        assert result.ok_values() == [1, 3]  # input order, failures skipped

    def test_empty(self):
        assert ParallelResult([]).ok_values() == []


def _double_for_process(x):
    return x * 2
