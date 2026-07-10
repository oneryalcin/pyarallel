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
    async_parallel_map,
    parallel_iter,
    parallel_map,
    parallel_starmap,
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


class TestCollectedMetadata:
    """Collected maps must surface the attempts/duration the workers
    already computed — the streaming/collected asymmetry (v0.9)."""

    def test_success_metadata_and_order(self):
        """item_results() preserves input order and carries values —
        prevents a regression where collected receipts drift from input."""
        result = parallel_map(lambda x: x * 2, [10, 20, 30], workers=3)
        items = result.item_results()
        assert [i.index for i in items] == [0, 1, 2]
        assert [i.value for i in items] == [20, 40, 60]
        assert all(i.ok and i.attempts == 1 for i in items)

    def test_retry_attempts_flow_on_success(self):
        """A collected success after one retry must report attempts=2 —
        the receipt the streaming API already exposes, no longer discarded."""
        calls = {}

        def flaky(x):
            calls[x] = calls.get(x, 0) + 1
            if calls[x] < 2:
                raise ValueError("transient")
            return x

        result = parallel_map(
            flaky, [1], workers=1, retry=Retry(attempts=3, backoff=0.0, jitter=False)
        )
        [item] = result.item_results()
        assert item.ok
        assert item.attempts == 2

    def test_failure_carries_attempts(self):
        """A collected final failure must report every attempt made
        (attempts=3 after 2 retries) and carry the exception itself —
        item_results() must never raise on a failed run."""

        def dead(x):
            raise ValueError("no")

        result = parallel_map(
            dead, [1], workers=1, retry=Retry(attempts=3, backoff=0.0, jitter=False)
        )
        [item] = result.item_results()
        assert not item.ok
        assert item.attempts == 3
        assert isinstance(item.error, ValueError)

    def test_duration_covers_real_work(self):
        """duration for a collected item must span the task body —
        a latency budget reads this number, it cannot be a hardcoded 0."""

        def slow(x):
            time.sleep(0.25)
            return x

        result = parallel_map(slow, [1], workers=1)
        [item] = result.item_results()
        assert item.duration >= 0.2

    def test_mixed_success_and_failure_order(self):
        """Both kinds keep their slot: a failure between two successes
        must not shift indices or swallow the successes' metadata."""

        def maybe(x):
            if x == 2:
                raise ValueError("bad")
            return x

        result = parallel_map(maybe, [1, 2, 3], workers=3)
        items = result.item_results()
        assert [i.index for i in items] == [0, 1, 2]
        assert items[0].value == 1 and items[0].ok
        assert not items[1].ok and isinstance(items[1].error, ValueError)
        assert items[2].value == 3 and items[2].ok

    def test_starmap_composes(self):
        """starmap flows through the same engine — item_results() must
        work for free, not need a second wiring."""
        result = parallel_starmap(lambda a, b: a + b, [(1, 2), (3, 4)], workers=2)
        items = result.item_results()
        assert [i.value for i in items] == [3, 7]
        assert all(i.attempts == 1 for i in items)

    def test_checkpoint_hit_reports_zero_attempts(self, tmp_path):
        """A cached checkpoint hit made no attempt THIS run — attempts=0
        is the honest count, not a fabricated 1."""
        db = str(tmp_path / "ckpt.db")
        calls = []

        def once(x):
            calls.append(x)
            return x * 2

        first = parallel_map(once, [1, 2, 3], workers=1, checkpoint=db)
        assert first.ok
        assert sorted(calls) == [1, 2, 3]

        calls.clear()
        second = parallel_map(once, [1, 2, 3], workers=1, checkpoint=db)
        assert calls == []  # all cache hits — nothing re-executed
        items = second.item_results()
        assert [i.value for i in items] == [2, 4, 6]
        assert all(i.attempts == 0 and i.duration == 0.0 for i in items)

    def test_timeout_placeholder_reports_zero_attempts(self):
        """Sized-input timeout placeholders never ran this run —
        attempts=0, and item_results() must not raise on a truncated run."""
        result = parallel_map(lambda x: x, [1, 2, 3], workers=1, timeout=0.0)
        assert result.timed_out
        items = result.item_results()
        assert len(items) == 3
        assert all(not i.ok and i.attempts == 0 and i.duration == 0.0 for i in items)
        assert all(isinstance(i.error, TimeoutError) for i in items)

    def test_sequential_engine_metadata(self):
        """The sequential debug engine must carry the same receipts —
        one vocabulary across every engine."""
        result = parallel_map(lambda x: x + 1, [1, 2], sequential=True)
        items = result.item_results()
        assert [i.value for i in items] == [2, 3]
        assert all(i.attempts == 1 for i in items)

    def test_no_meta_synthesizes_defaults(self):
        """A hand-constructed ParallelResult has no metadata — synthesize
        attempts=1, duration=0.0 rather than crash on a None lookup."""
        result = ParallelResult([1, _Failure(ValueError("bad")), 3])
        items = result.item_results()
        assert [i.index for i in items] == [0, 1, 2]
        assert [i.value for i in items] == [1, None, 3]
        assert items[1].error is not None
        assert all(i.attempts == 1 and i.duration == 0.0 for i in items)

    async def test_async_collected_parity(self):
        """Async collected maps must expose the same receipts as sync —
        no runtime-specific metadata hole."""
        calls = {}

        async def flaky(x):
            calls[x] = calls.get(x, 0) + 1
            if calls[x] < 2:
                raise ValueError("transient")
            await asyncio.sleep(0.05)
            return x

        result = await async_parallel_map(
            flaky,
            [1],
            concurrency=1,
            retry=Retry(attempts=3, backoff=0.0, jitter=False),
        )
        [item] = result.item_results()
        assert item.ok
        assert item.attempts == 2
        assert item.duration >= 0.04


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
