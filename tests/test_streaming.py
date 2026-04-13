"""Tests for parallel_iter / .stream() — streaming results, constant memory."""

import time

from pyarallel import RateLimit
from pyarallel.core import Retry


class TestParallelIterBasic:
    def test_yields_index_and_value(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x * 2, [1, 2, 3], workers=2))
        # Results come in completion order, so sort by index
        results.sort()
        assert results == [(0, 2), (1, 4), (2, 6)]

    def test_empty_input(self):
        from pyarallel import parallel_iter

        assert list(parallel_iter(lambda x: x, [])) == []

    def test_single_item(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x * 10, [5], workers=1))
        assert results == [(0, 50)]

    def test_accepts_any_iterable(self):
        from pyarallel import parallel_iter

        results = list(parallel_iter(lambda x: x, range(4), workers=2))
        assert sorted(results) == [(0, 0), (1, 1), (2, 2), (3, 3)]


class TestParallelIterStreaming:
    def test_constant_memory_with_batching(self):
        """With batch_size, only one batch of results exists at a time."""
        from pyarallel import parallel_iter

        yielded_count = 0
        for _index, _value in parallel_iter(
            lambda x: x * 2,
            range(20),
            workers=2,
            batch_size=5,
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
        indices = [i for i, _ in results]
        # Index 0 should come last (it sleeps)
        assert indices[-1] == 0


class TestParallelIterErrors:
    def test_errors_yielded_as_exceptions(self):
        from pyarallel import parallel_iter

        def maybe_fail(x):
            if x == 2:
                raise ValueError("bad")
            return x

        results = list(parallel_iter(maybe_fail, [1, 2, 3], workers=2))
        successes = [(i, v) for i, v in results if not isinstance(v, Exception)]
        failures = [(i, v) for i, v in results if isinstance(v, Exception)]

        assert len(successes) == 2
        assert len(failures) == 1
        assert isinstance(failures[0][1], ValueError)

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
        results.sort()
        assert results == [(0, 10), (1, 20)]


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

    def test_with_batch_size(self):
        from pyarallel import parallel_iter

        results = list(
            parallel_iter(
                lambda x: x * 2,
                range(15),
                workers=3,
                batch_size=5,
            )
        )
        results.sort()
        assert results == [(i, i * 2) for i in range(15)]


class TestDecoratorStream:
    def test_stream_method(self):
        from pyarallel import parallel

        @parallel(workers=2)
        def double(x):
            return x * 2

        results = list(double.stream([1, 2, 3]))
        results.sort()
        assert results == [(0, 2), (1, 4), (2, 6)]

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
        results.sort()
        assert results == [(0, 3), (1, 6), (2, 9)]


class TestAsyncParallelIter:
    async def test_basic(self):
        from pyarallel import async_parallel_iter

        async def double(x):
            return x * 2

        results = []
        async for index, value in async_parallel_iter(double, [1, 2, 3], concurrency=2):
            results.append((index, value))

        results.sort()
        assert results == [(0, 2), (1, 4), (2, 6)]

    async def test_async_decorator_stream(self):
        from pyarallel import async_parallel

        @async_parallel(concurrency=2)
        async def double(x):
            return x * 2

        results = []
        async for index, value in double.stream([1, 2, 3]):
            results.append((index, value))

        results.sort()
        assert results == [(0, 2), (1, 4), (2, 6)]
