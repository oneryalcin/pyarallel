"""Tests for parallel_starmap — multi-argument parallel execution."""

import time

from pyarallel import RateLimit, Retry


def _add(a, b):
    return a + b


class TestStarmapBasic:
    def test_unpacks_tuples(self):
        from pyarallel import parallel_starmap

        result = parallel_starmap(_add, [(1, 2), (3, 4), (5, 6)], workers=2)
        assert list(result) == [3, 7, 11]

    def test_preserves_order(self):
        from pyarallel import parallel_starmap

        def slow_add(a, b):
            if a == 0:
                time.sleep(0.05)
            return a + b

        result = parallel_starmap(slow_add, [(0, 1), (2, 3), (4, 5)], workers=3)
        assert list(result) == [1, 5, 9]

    def test_empty_input(self):
        from pyarallel import parallel_starmap

        result = parallel_starmap(_add, [])
        assert list(result) == []

    def test_single_item(self):
        from pyarallel import parallel_starmap

        result = parallel_starmap(_add, [(10, 20)])
        assert list(result) == [30]

    def test_works_with_kwargs_in_tuples(self):
        """Items can be dicts for kwargs unpacking."""
        from pyarallel import parallel_starmap

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = parallel_starmap(
            greet,
            [("Alice",), ("Bob",)],
            workers=2,
        )
        assert list(result) == ["Hello, Alice!", "Hello, Bob!"]

    def test_three_args(self):
        from pyarallel import parallel_starmap

        def add3(a, b, c):
            return a + b + c

        result = parallel_starmap(add3, [(1, 2, 3), (4, 5, 6)], workers=2)
        assert list(result) == [6, 15]


class TestStarmapErrorHandling:
    def test_error_in_one_item(self):
        from pyarallel import parallel_starmap

        def div(a, b):
            return a / b

        result = parallel_starmap(div, [(10, 2), (10, 0), (6, 3)], workers=2)
        assert not result.ok
        assert len(result.failures()) == 1
        assert len(result.successes()) == 2

    def test_with_retry(self):
        from pyarallel import parallel_starmap

        call_count = {}

        def flaky_add(a, b):
            key = (a, b)
            call_count[key] = call_count.get(key, 0) + 1
            if call_count[key] < 2:
                raise ValueError("not yet")
            return a + b

        result = parallel_starmap(
            flaky_add,
            [(1, 2), (3, 4)],
            workers=2,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert result.ok
        assert list(result) == [3, 7]


class TestStarmapWithOptions:
    def test_with_rate_limit(self):
        from pyarallel import parallel_starmap

        start = time.monotonic()
        parallel_starmap(
            _add,
            [(1, 2)] * 5,
            workers=5,
            rate_limit=RateLimit(10, "second"),
        )
        assert time.monotonic() - start >= 0.3

    def test_with_batch_size(self):
        from pyarallel import parallel_starmap

        result = parallel_starmap(
            _add,
            [(i, i) for i in range(20)],
            workers=4,
            batch_size=5,
        )
        assert list(result) == [i * 2 for i in range(20)]


class TestStarmapDecorator:
    def test_decorator_stream_has_starmap(self):
        """@parallel decorated functions should have .starmap() too."""
        from pyarallel import parallel

        @parallel(workers=2)
        def add(a, b):
            return a + b

        result = add.starmap([(1, 2), (3, 4)])
        assert list(result) == [3, 7]


class TestAsyncStarmap:
    async def test_basic(self):
        from pyarallel import async_parallel_starmap

        async def add(a, b):
            return a + b

        result = await async_parallel_starmap(add, [(1, 2), (3, 4)], concurrency=2)
        assert list(result) == [3, 7]

    async def test_with_retry(self):
        from pyarallel import async_parallel_starmap

        call_count = {}

        async def flaky_add(a, b):
            key = (a, b)
            call_count[key] = call_count.get(key, 0) + 1
            if call_count[key] < 2:
                raise ValueError("not yet")
            return a + b

        result = await async_parallel_starmap(
            flaky_add,
            [(1, 2), (3, 4)],
            concurrency=2,
            retry=Retry(attempts=3, backoff=0, jitter=False),
        )
        assert result.ok
        assert list(result) == [3, 7]

    async def test_async_decorator_starmap(self):
        from pyarallel import async_parallel

        @async_parallel(concurrency=2)
        async def add(a, b):
            return a + b

        result = await add.starmap([(1, 2), (3, 4)])
        assert list(result) == [3, 7]


class TestStarmapLaziness:
    """v0.6: sync starmap wraps fn instead of packing items into a list,
    so the engine's lazy-input contract holds for starmap too."""

    def test_generator_of_tuples_not_materialized(self):
        """Prevents: the packing list-comp returning — it consumed whole
        generators before the engine ever saw them."""
        import threading

        from pyarallel import parallel_starmap

        produced = []
        started = threading.Event()
        release = threading.Event()
        holder = {}

        def pairs():
            for i in range(100):
                produced.append(i)
                yield (i, i)

        def track_add(a, b):
            if a == 1:
                started.set()
            release.wait(timeout=5)
            return a + b

        def run():
            holder["result"] = parallel_starmap(track_add, pairs(), workers=2)

        t = threading.Thread(target=run)
        t.start()
        assert started.wait(timeout=5)
        time.sleep(0.2)
        assert len(produced) <= 4  # window = 2 * workers
        release.set()
        t.join(timeout=10)
        assert not t.is_alive()
        assert list(holder["result"]) == [i * 2 for i in range(100)]

    def test_sized_input_keeps_exact_progress_total(self):
        """Prevents: sized tuple lists losing their len() (and thus true
        progress totals) to a lazy wrapper."""
        from pyarallel import parallel_starmap

        totals = []
        parallel_starmap(
            _add,
            [(1, 2), (3, 4), (5, 6)],
            workers=2,
            on_progress=lambda done, total: totals.append(total),
        )
        assert totals == [3, 3, 3]
