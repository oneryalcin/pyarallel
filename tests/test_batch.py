"""Tests for batch_size — the in-flight admission window (v0.6: one
engine for all collected maps; there are no chunk barriers)."""

import threading
import time

import pytest

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

    def test_batch_size_consumes_generator_lazily(self):
        produced = []
        started = threading.Event()
        release = threading.Event()
        current = 0
        lock = threading.Lock()
        holder = {}

        def items():
            for i in range(6):
                produced.append(i)
                yield i

        def track(x):
            nonlocal current
            with lock:
                current += 1
                if current == 2:
                    started.set()
            release.wait(timeout=2)
            with lock:
                current -= 1
            return x

        def run():
            holder["result"] = parallel_map(track, items(), workers=2, batch_size=2)

        t = threading.Thread(target=run)
        t.start()
        assert started.wait(timeout=2)
        assert produced == [0, 1]
        release.set()
        t.join(timeout=2)
        assert not t.is_alive()
        assert list(holder["result"]) == list(range(6))


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

    def test_timeout_with_list_does_not_double_count_remaining_items(self):
        def slow(x):
            time.sleep(0.2)
            return x

        result = parallel_map(
            slow, list(range(10)), workers=2, batch_size=3, timeout=0.1
        )
        assert len(result) == 10
        assert len(result.failures()) == 10


class TestUnifiedPlainPath:
    """v0.6 engine unification: the plain collected path (no max_errors)
    runs through the windowed engine. Each test names the regression it
    prevents."""

    def test_plain_map_does_not_materialize_generator(self):
        """Prevents: silent return of upfront list(items) materialization.
        No batch_size, workers=2 -> default window 4: with all workers
        blocked, the source must not be consumed past the window."""
        produced = []
        started = threading.Event()
        release = threading.Event()
        holder = {}

        def items():
            for i in range(100):
                produced.append(i)
                yield i

        def track(x):
            if x == 1:
                started.set()
            release.wait(timeout=5)
            return x

        def run():
            holder["result"] = parallel_map(track, items(), workers=2)

        t = threading.Thread(target=run)
        t.start()
        assert started.wait(timeout=5)
        time.sleep(0.2)  # give a broken engine time to over-consume
        assert len(produced) <= 4  # window = 2 * workers
        release.set()
        t.join(timeout=10)
        assert not t.is_alive()
        assert list(holder["result"]) == list(range(100))

    def test_timeout_on_unsized_input_returns_shorter_result_not_drain(self):
        """Prevents: drain-on-timeout returning — the run must never pull
        from the source after the deadline, so an unsized input yields a
        shorter result with timed_out set, not appended placeholders."""
        produced = []

        def items():
            for i in range(1000):
                produced.append(i)
                yield i

        def slow(x):
            time.sleep(10)
            return x

        result = parallel_map(slow, items(), workers=2, timeout=0.3)
        assert result.timed_out
        assert len(result) < 1000  # no placeholder drain
        assert len(produced) <= 8  # source consumption stopped at the window

    def test_timeout_zero_runs_nothing_on_plain_path(self):
        """Prevents: an already-expired deadline admitting work (the
        max_errors path had this guarantee; the plain path must too)."""
        calls = []

        result = parallel_map(calls.append, range(10), workers=4, timeout=0)
        assert calls == []
        assert result.timed_out
        assert len(result.failures()) == 10  # sized: every slot marked

    def test_source_error_propagates_without_hanging(self):
        """Prevents: a wedged pool when the input iterator itself raises
        mid-run (lazy consumption surfaces source errors mid-flight)."""

        def poison():
            yield 1
            yield 2
            raise ValueError("bad source")

        with pytest.raises(ValueError, match="bad source"):
            parallel_map(lambda x: x, poison(), workers=2)

    def test_timeout_beats_rate_limit_pacing_on_plain_path(self):
        """Prevents: the v0.5 bypassed-timeout bug returning via the
        routing change — a driver blocked in limiter pacing must still
        honor the total deadline (no max_errors set)."""
        start = time.monotonic()
        result = parallel_map(
            lambda x: x,
            range(5),
            workers=2,
            rate_limit=RateLimit(1, "second"),  # tokens 1s apart
            timeout=0.2,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # did not sleep through the pacing
        assert result.timed_out
        assert len(result) == 5  # sized: unrun slots marked, not dropped


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

    async def test_async_plain_map_does_not_materialize_generator(self):
        """Prevents: silent return of upfront materialization on the
        async plain path (no batch_size, no max_errors) — window must
        default to 2 x concurrency."""
        import asyncio

        from pyarallel import async_parallel_map

        produced = []
        release = asyncio.Event()

        def items():
            for i in range(100):
                produced.append(i)
                yield i

        async def track(x):
            await release.wait()
            return x

        async def run():
            return await async_parallel_map(track, items(), concurrency=2)

        task = asyncio.ensure_future(run())
        await asyncio.sleep(0.2)  # give a broken engine time to over-consume
        assert len(produced) <= 4  # window = 2 * concurrency
        release.set()
        result = await asyncio.wait_for(task, timeout=10)
        assert list(result) == list(range(100))

    async def test_async_source_error_propagates_without_hanging(self):
        """Prevents: a wedged event loop when the input iterator raises
        mid-run on the async plain path."""
        import pytest

        from pyarallel import async_parallel_map

        def poison():
            yield 1
            yield 2
            raise ValueError("bad source")

        async def identity(x):
            return x

        with pytest.raises(ValueError, match="bad source"):
            await async_parallel_map(identity, poison(), concurrency=2)

    async def test_async_batch_consumes_generator_lazily(self):
        import asyncio

        from pyarallel import async_parallel_map

        produced = []
        started = threading.Event()
        release = threading.Event()
        current = 0
        lock = threading.Lock()
        holder = {}

        def items():
            for i in range(6):
                produced.append(i)
                yield i

        async def track(x):
            nonlocal current
            with lock:
                current += 1
                if current == 2:
                    started.set()
            await asyncio.to_thread(release.wait, 2)
            with lock:
                current -= 1
            return x

        def run():
            holder["result"] = asyncio.run(
                async_parallel_map(track, items(), concurrency=2, batch_size=2)
            )

        t = threading.Thread(target=run)
        t.start()
        assert started.wait(timeout=2)
        assert produced == [0, 1]
        release.set()
        t.join(timeout=2)
        assert not t.is_alive()
        assert list(holder["result"]) == list(range(6))
