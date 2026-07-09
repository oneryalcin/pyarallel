"""Tests for parallel_iter / .stream() — streaming results, constant memory."""

import asyncio
import time

import pytest

from pyarallel import RateLimit, Retry


class TestParallelIterBasic:
    def test_yields_item_result(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x * 2, [1, 2, 3], workers=2))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]
        assert all(item.ok for item in results)

    def test_empty_input(self):
        from pyarallel import parallel_iter

        assert list(parallel_iter(lambda x: x, [])) == []

    def test_single_item(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x * 10, [5], workers=1))
        assert len(results) == 1
        assert results[0].index == 0
        assert results[0].value == 50

    def test_accepts_any_iterable(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x, range(4), workers=2))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 0),
            (1, 1),
            (2, 2),
            (3, 3),
        ]

    def test_none_values_are_valid_results(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: None, [1, 2, 3], workers=2))
        results.sort(key=lambda item: item.index)
        assert all(item.ok for item in results)
        assert [item.value for item in results] == [None, None, None]


class TestParallelIterStreaming:
    def test_constant_memory_with_batching(self):
        """With window_size, only one batch of results exists at a time."""
        from pyarallel import parallel_iter

        yielded_count = 0
        for _item in parallel_iter(
            lambda x: x * 2,
            range(20),
            workers=2,
            window_size=5,
        ):
            yielded_count += 1

        assert yielded_count == 20

    def test_results_in_completion_order(self):
        """Without batching, fast tasks yield before slow ones."""
        from pyarallel import parallel_iter

        def speed_varies(x):
            if x == 0:
                time.sleep(0.1)
            return x

        results = list(parallel_iter(speed_varies, [0, 1, 2], workers=3))
        indices = [item.index for item in results]
        # Index 0 should come last (it sleeps)
        assert indices[-1] == 0


class TestParallelIterErrors:
    def test_errors_yielded_as_item_results(self):
        from pyarallel import parallel_iter

        def maybe_fail(x):
            if x == 2:
                raise ValueError("bad")
            return x

        results = list(parallel_iter(maybe_fail, [1, 2, 3], workers=2))
        successes = [item for item in results if item.ok]
        failures = [item for item in results if not item.ok]

        assert len(successes) == 2
        assert len(failures) == 1
        assert isinstance(failures[0].error, ValueError)

    def test_errors_with_retry(self):
        from pyarallel import parallel_iter

        call_count = {}

        def flaky(x):
            call_count[x] = call_count.get(x, 0) + 1
            if call_count[x] < 2:
                raise ValueError("not yet")
            return x * 10

        results = list(
            parallel_iter(
                flaky,
                [1, 2],
                workers=2,
                retry=Retry(attempts=3, backoff=0, jitter=False),
            )
        )
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [(0, 10), (1, 20)]


class TestParallelIterWithOptions:
    def test_with_rate_limit(self):
        from pyarallel import parallel_iter

        start = time.monotonic()
        list(
            parallel_iter(
                lambda x: x,
                range(5),
                workers=5,
                rate_limit=RateLimit(10, "second"),
            )
        )
        assert time.monotonic() - start >= 0.3

    def test_with_window_size(self):
        from pyarallel import parallel_iter

        results = list(
            parallel_iter(
                lambda x: x * 2,
                range(15),
                workers=3,
                window_size=5,
            )
        )
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (i, i * 2) for i in range(15)
        ]


class TestDecoratorStream:
    def test_stream_method(self):
        from pyarallel import parallel

        @parallel(workers=2)
        def double(x):
            return x * 2

        results = list(double.stream([1, 2, 3]))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]

    def test_instance_method_stream(self):
        from pyarallel import parallel

        class Mul:
            def __init__(self, factor):
                self.factor = factor

            @parallel(workers=2)
            def go(self, x):
                return x * self.factor

        m = Mul(3)
        results = list(m.go.stream([1, 2, 3]))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 3),
            (1, 6),
            (2, 9),
        ]


class TestSlidingWindow:
    """v0.5 engine rewrite — each test names the v0.4 failure it prevents."""

    def test_straggler_does_not_stall_the_stream(self):
        """v0.4 batch barrier: one slow item blocked every later batch."""
        from pyarallel import parallel_iter

        def task(x):
            time.sleep(0.5 if x == 0 else 0.01)
            return x

        start = time.monotonic()
        order = [item.index for item in parallel_iter(task, range(10), workers=4)]
        elapsed = time.monotonic() - start
        assert order[-1] == 0  # straggler arrives last, others streamed past it
        assert set(order[:9]) == set(range(1, 10))
        assert elapsed < 1.5

    def test_window_size_is_an_in_flight_bound_not_a_barrier(self):
        """v0.4: window_size chunked with a barrier — items 2..5 could not
        start until the whole first chunk (including the straggler) drained."""
        from pyarallel import parallel_iter

        def task(x):
            time.sleep(0.4 if x == 0 else 0.01)
            return x

        order = [
            item.index
            for item in parallel_iter(task, range(6), workers=2, window_size=2)
        ]
        assert order[-1] == 0  # the old barrier forced 0 before 2..5

    def test_unbatched_input_is_not_materialized(self):
        """v0.4 without window_size pulled the whole iterator into memory.
        The <= 8 bound also pins the review amendment: with workers=2 the
        window is 4, not the thread pool's default of up to 64."""
        from pyarallel import parallel_iter

        advanced = 0

        def source():
            nonlocal advanced
            for i in range(10_000):
                advanced += 1
                yield i

        stream = parallel_iter(lambda x: x, source(), workers=2)
        try:
            for _ in range(3):
                next(stream)
            assert advanced <= 3 + 4 + 1  # yields + window (2 x workers) + slack
        finally:
            stream.close()

    def test_break_stops_submissions_and_bounds_leftover_work(self):
        """Cleanup contract: closing the generator stops the engine — no new
        submissions, leftover running work bounded by the window."""
        from pyarallel import parallel_iter

        started = []

        def slow(x):
            started.append(x)
            time.sleep(0.05)
            return x

        stream = parallel_iter(slow, range(100), workers=2, window_size=4)
        next(stream)
        next(stream)
        stream.close()
        time.sleep(0.3)  # let anything already handed to the pool finish
        after_close = len(started)
        assert after_close <= 2 + 4  # yields + window
        time.sleep(0.2)
        assert len(started) == after_close  # engine is genuinely stopped

    def test_items_iterator_error_propagates(self):
        """An input generator raising mid-stream must surface to the caller,
        not hang the driver or vanish."""
        from pyarallel import parallel_iter

        def poison():
            yield 1
            yield 2
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            list(parallel_iter(lambda x: x, poison(), workers=2))


class TestAsyncSlidingWindow:
    """Async mirrors of the v0.5 engine contracts."""

    async def test_straggler_does_not_stall_the_stream(self):
        from pyarallel import async_parallel_iter

        async def task(x):
            await asyncio.sleep(0.5 if x == 0 else 0.01)
            return x

        start = time.monotonic()
        order = []
        async for item in async_parallel_iter(task, range(10), concurrency=4):
            order.append(item.index)
        elapsed = time.monotonic() - start
        assert order[-1] == 0
        assert set(order[:9]) == set(range(1, 10))
        assert elapsed < 1.5

    async def test_unbatched_input_is_not_materialized(self):
        """v0.4 built list(items) whenever window_size was None."""
        from pyarallel import async_parallel_iter

        advanced = 0

        def source():
            nonlocal advanced
            for i in range(10_000):
                advanced += 1
                yield i

        async def identity(x):
            return x

        stream = async_parallel_iter(identity, source(), concurrency=2)
        try:
            got = 0
            async for _item in stream:
                got += 1
                if got == 3:
                    break
            assert advanced <= 3 + 4 + 1  # yields + window (2 x concurrency) + slack
        finally:
            await stream.aclose()

    async def test_break_cancels_in_flight_tasks(self):
        """Closing the async generator cancels running tasks — async tasks,
        unlike threads, must be genuinely cancelled."""
        from pyarallel import async_parallel_iter

        started = []

        async def slow(x):
            started.append(x)
            await asyncio.sleep(0.05)
            return x

        stream = async_parallel_iter(slow, range(100), concurrency=2)
        async for _item in stream:
            break
        await stream.aclose()
        after_close = len(started)
        assert after_close <= 1 + 4  # yields + window
        await asyncio.sleep(0.2)
        assert len(started) == after_close

    async def test_items_iterator_error_propagates(self):
        from pyarallel import async_parallel_iter

        def poison():
            yield 1
            yield 2
            raise RuntimeError("boom")

        async def identity(x):
            return x

        with pytest.raises(RuntimeError, match="boom"):
            async for _item in async_parallel_iter(identity, poison(), concurrency=2):
                pass


class TestOrderedStreaming:
    """ordered=True — input-order yields with a bounded reorder buffer."""

    def test_ordered_yields_input_order_despite_reversed_completion(self):
        from pyarallel import parallel_iter

        def task(x):
            time.sleep((5 - x) * 0.03)  # item 0 finishes last
            return x * 10

        results = list(parallel_iter(task, range(6), workers=6, ordered=True))
        assert [item.index for item in results] == list(range(6))
        assert [item.value for item in results] == [x * 10 for x in range(6)]

    def test_ordered_reorder_buffer_is_bounded(self):
        """The review finding: refill-on-completion admits unboundedly when
        item 0 is slow and successors are fast. The invariant is measured,
        not asserted: advanced - yielded == in_flight + buffered + 1 at
        every source pull, and must never exceed the window."""
        from pyarallel import parallel_iter

        window = 8
        advanced = 0
        yielded = 0
        peak = 0

        def source():
            nonlocal advanced, peak
            for i in range(200):
                advanced += 1
                peak = max(peak, advanced - yielded)
                yield i

        def slow_first(x):
            time.sleep(0.3 if x == 0 else 0.001)
            return x

        for item in parallel_iter(
            slow_first, source(), workers=4, window_size=window, ordered=True
        ):
            assert item.ok
            yielded += 1

        assert yielded == 200
        assert peak <= window

    def test_ordered_failures_keep_their_position(self):
        from pyarallel import parallel_iter

        def maybe_fail(x):
            if x == 1:
                raise ValueError("bad")
            return x

        results = list(parallel_iter(maybe_fail, range(4), workers=4, ordered=True))
        assert [item.index for item in results] == [0, 1, 2, 3]
        assert not results[1].ok
        assert isinstance(results[1].error, ValueError)

    async def test_async_ordered_yields_input_order(self):
        from pyarallel import async_parallel_iter

        async def task(x):
            await asyncio.sleep((5 - x) * 0.03)
            return x * 10

        results = []
        async for item in async_parallel_iter(
            task, range(6), concurrency=6, ordered=True
        ):
            results.append(item)
        assert [item.index for item in results] == list(range(6))
        assert [item.value for item in results] == [x * 10 for x in range(6)]

    async def test_async_ordered_reorder_buffer_is_bounded(self):
        from pyarallel import async_parallel_iter

        window = 8
        advanced = 0
        yielded = 0
        peak = 0

        def source():
            nonlocal advanced, peak
            for i in range(200):
                advanced += 1
                peak = max(peak, advanced - yielded)
                yield i

        async def slow_first(x):
            await asyncio.sleep(0.3 if x == 0 else 0.001)
            return x

        async for item in async_parallel_iter(
            slow_first, source(), concurrency=4, window_size=window, ordered=True
        ):
            assert item.ok
            yielded += 1

        assert yielded == 200
        assert peak <= window


class TestStreamingProgress:
    """on_progress for streaming — same contract as parallel_map."""

    def test_progress_final_call_is_n_of_n_for_sized_input(self):
        from pyarallel import parallel_iter

        calls = []
        list(
            parallel_iter(
                lambda x: x,
                range(10),
                workers=4,
                on_progress=lambda done, total: calls.append((done, total)),
            )
        )
        assert calls[-1] == (10, 10)
        assert [done for done, _ in calls] == list(range(1, 11))  # monotone

    def test_progress_total_grows_for_unsized_input(self):
        from pyarallel import parallel_iter

        calls = []
        list(
            parallel_iter(
                lambda x: x,
                (i for i in range(10)),
                workers=2,
                on_progress=lambda done, total: calls.append((done, total)),
            )
        )
        assert len(calls) == 10
        assert calls[-1][0] == 10
        assert all(done <= total for done, total in calls)

    async def test_async_progress_final_call_is_n_of_n(self):
        from pyarallel import async_parallel_iter

        async def identity(x):
            return x

        calls = []
        async for _item in async_parallel_iter(
            identity,
            range(10),
            concurrency=4,
            on_progress=lambda done, total: calls.append((done, total)),
        ):
            pass
        assert calls[-1] == (10, 10)
        assert [done for done, _ in calls] == list(range(1, 11))


class TestAsyncParallelIter:
    async def test_basic(self):
        from pyarallel import async_parallel_iter

        async def double(x):
            return x * 2

        results = []
        async for item in async_parallel_iter(double, [1, 2, 3], concurrency=2):
            results.append(item)

        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]

    async def test_async_decorator_stream(self):
        from pyarallel import async_parallel

        @async_parallel(concurrency=2)
        async def double(x):
            return x * 2

        results = []
        async for item in double.stream([1, 2, 3]):
            results.append(item)

        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]

    async def test_none_values_are_valid_results(self):
        from pyarallel import async_parallel_iter

        results = []
        async for item in async_parallel_iter(_async_none, [1, 2, 3], concurrency=2):
            results.append(item)

        results.sort(key=lambda item: item.index)
        assert all(item.ok for item in results)
        assert [item.value for item in results] == [None, None, None]

    async def test_window_size_consumes_generator_lazily(self):
        import asyncio
        import threading

        from pyarallel import async_parallel_iter

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
            async def collect():
                collected = []
                async for item in async_parallel_iter(
                    track, items(), concurrency=2, window_size=2
                ):
                    collected.append(item)
                holder["result"] = collected

            asyncio.run(collect())

        t = threading.Thread(target=run)
        t.start()
        assert started.wait(timeout=2)
        assert produced == [0, 1]
        release.set()
        t.join(timeout=2)
        assert not t.is_alive()
        holder["result"].sort(key=lambda item: item.index)
        assert [item.value for item in holder["result"]] == list(range(6))


async def _async_none(_x):
    return None
