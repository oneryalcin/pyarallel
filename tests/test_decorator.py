"""Tests for the @parallel decorator."""

from pyarallel import parallel, parallel_map


@parallel(workers=2, executor="process")
def _process_double(x):
    return x * 2


@parallel(workers=2, executor="process")
def _process_add(a, b):
    return a + b


class TestBasicDecorator:
    def test_single_call_returns_plain_value(self):
        @parallel(workers=2)
        def double(x):
            return x * 2

        assert double(5) == 10  # Returns int, NOT [int]

    def test_bare_decorator_no_parens(self):
        @parallel
        def double(x):
            return x * 2

        assert double(5) == 10

    def test_map_returns_parallel_result(self):
        @parallel(workers=2)
        def double(x):
            return x * 2

        results = double.map([1, 2, 3])
        assert list(results) == [2, 4, 6]

    def test_map_preserves_order(self):
        import time

        @parallel(workers=4)
        def slow_first(x):
            if x == 0:
                time.sleep(0.05)
            return x

        assert list(slow_first.map(range(5))) == [0, 1, 2, 3, 4]


class TestDecoratorOverrides:
    def test_override_workers(self):
        @parallel(workers=1)
        def identity(x):
            return x

        results = identity.map([1, 2, 3], workers=4)
        assert list(results) == [1, 2, 3]

    def test_override_rate_limit(self):
        import time

        @parallel(workers=4)
        def identity(x):
            return x

        start = time.monotonic()
        identity.map(range(5), rate_limit=10)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3

    def test_process_executor_map(self):
        results = _process_double.map([1, 2, 3])
        assert list(results) == [2, 4, 6]

    def test_process_executor_starmap(self):
        results = _process_add.starmap([(1, 2), (3, 4)])
        assert list(results) == [3, 7]

    def test_process_executor_stream(self):
        results = list(_process_double.stream([1, 2, 3]))
        results.sort(key=lambda item: item.index)
        assert [(item.index, item.value) for item in results] == [
            (0, 2),
            (1, 4),
            (2, 6),
        ]


class TestMethodSupport:
    def test_instance_method_single_call(self):
        class Processor:
            def __init__(self, factor):
                self.factor = factor

            @parallel(workers=2)
            def multiply(self, x):
                return x * self.factor

        p = Processor(3)
        assert p.multiply(5) == 15

    def test_instance_method_map(self):
        class Processor:
            def __init__(self, factor):
                self.factor = factor

            @parallel(workers=2)
            def multiply(self, x):
                return x * self.factor

        p = Processor(3)
        results = p.multiply.map([1, 2, 3])
        assert list(results) == [3, 6, 9]

    def test_different_instances(self):
        class Adder:
            def __init__(self, n):
                self.n = n

            @parallel(workers=2)
            def add(self, x):
                return x + self.n

        a = Adder(10)
        b = Adder(100)
        assert list(a.add.map([1, 2])) == [11, 12]
        assert list(b.add.map([1, 2])) == [101, 102]

    def test_static_method(self):
        class Math:
            @staticmethod
            @parallel(workers=2)
            def square(x):
                return x**2

        assert Math.square(3) == 9
        assert list(Math.square.map([1, 2, 3])) == [1, 4, 9]

    def test_parallel_map_with_bound_method(self):
        """parallel_map works directly with bound methods."""

        class Doubler:
            @parallel(workers=2)
            def go(self, x):
                return x * 2

        d = Doubler()
        results = parallel_map(d.go, [1, 2, 3], workers=2)
        assert list(results) == [2, 4, 6]


class TestPreservedSignature:
    def test_wraps_preserves_name(self):
        @parallel(workers=2)
        def my_function(x):
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_wrapped_attribute(self):
        @parallel
        def f(x):
            return x

        assert hasattr(f, "__wrapped__")


class TestExplicitNoneOverride:
    """v0.8 review: None meant "inherit the decorator default", so a
    caller could never turn OFF a non-None decorator default per-call.
    New contract: an unspecified option inherits; an explicit None
    overrides (an internal sentinel distinguishes the two).

    Note (v0.8 scope finding): decorator defaults only cover workers /
    executor / rate_limit — retry etc. are per-call-only. Widening the
    default surface is a v0.9 question, not part of this contract fix.
    """

    def test_explicit_rate_limit_none_disables_decorator_limit(self):
        import time

        from pyarallel import RateLimit, parallel

        @parallel(workers=4, rate_limit=RateLimit(2, "second"))
        def work(x):
            return x

        start = time.monotonic()
        r = work.map([1, 2, 3, 4], rate_limit=None)  # explicit None: no pacing
        elapsed = time.monotonic() - start
        assert r.ok
        # Paced at 2/s these 4 items take >= ~1.5s; unpaced they are instant.
        assert elapsed < 0.5

    def test_unspecified_still_inherits(self):
        import time

        from pyarallel import RateLimit, parallel

        @parallel(workers=4, rate_limit=RateLimit(2, "second"))
        def work(x):
            return x

        start = time.monotonic()
        r = work.map([1, 2, 3, 4])  # inherit: paced
        elapsed = time.monotonic() - start
        assert r.ok
        assert elapsed > 1.0


class TestWidenedDecoratorDefaults:
    """v0.10: decorator defaults widen beyond workers/executor/rate_limit.
    Production evidence for the decision: the v0.8 red tests instinctively
    wrote @parallel(retry=...) and got TypeError — the natural spelling
    didn't exist. Retry/timeout/window/max_errors/on_progress are
    properties of the function's behavior and belong at the decorator;
    checkpoint*/stop stay per-call-only (a checkpoint file names a RUN —
    a shared default file means duplicate keys and wrong resumes; a stop
    token is a campaign latch)."""

    def test_retry_as_decorator_default(self):
        from pyarallel import Retry, parallel

        calls = {"n": 0}

        @parallel(workers=1, retry=Retry(attempts=3, backoff=0.0, jitter=False))
        def flaky(x):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("first call fails")
            return x

        r = flaky.map([1])
        assert r.ok
        assert calls["n"] == 2  # the decorator's retry policy applied

    def test_percall_still_overrides_decorator_retry(self):
        from pyarallel import Retry, parallel

        calls = {"n": 0}

        @parallel(workers=1, retry=Retry(attempts=5, backoff=0.0, jitter=False))
        def flaky(x):
            calls["n"] += 1
            raise ValueError("always fails")

        r = flaky.map([1], retry=None)  # explicit None: retry OFF (v0.8 rule)
        assert not r.ok
        assert calls["n"] == 1

    def test_max_errors_and_window_as_defaults(self):
        from pyarallel import parallel

        @parallel(workers=1, max_errors=2, window_size=2)
        def boom(x):
            raise ValueError(f"item {x}")

        r = boom.map(list(range(50)))
        assert r.aborted

    def test_timeout_as_default(self):
        import time as _time

        from pyarallel import parallel

        @parallel(workers=2, timeout=0.2)
        def slow(x):
            _time.sleep(5)
            return x

        r = slow.map([1, 2])
        assert r.timed_out

    def test_on_progress_as_default(self):
        from pyarallel import parallel

        seen = []

        @parallel(workers=1, on_progress=lambda d, t: seen.append(d))
        def work(x):
            return x

        work.map([1, 2, 3])
        assert seen == [1, 2, 3]

    def test_checkpoint_rejected_as_decorator_default(self):
        """A checkpoint file names a run, not a function — two .map()
        calls sharing one default file would collide keys and serve
        wrong resumes. Rejected loudly at decoration time."""
        import pytest

        from pyarallel import parallel

        with pytest.raises(TypeError):

            @parallel(workers=1, checkpoint="run.ckpt")  # type: ignore[call-arg]
            def fn(x):
                return x

    def test_stop_rejected_as_decorator_default(self):
        import pytest

        from pyarallel import StopToken, parallel

        with pytest.raises(TypeError):

            @parallel(workers=1, stop=StopToken())  # type: ignore[call-arg]
            def fn(x):
                return x

    async def test_async_decorator_widened(self):
        from pyarallel import Retry, async_parallel

        calls = {"n": 0}

        @async_parallel(
            concurrency=1, retry=Retry(attempts=3, backoff=0.0, jitter=False)
        )
        async def flaky(x):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("first fails")
            return x

        r = await flaky.map([1])
        assert r.ok
        assert calls["n"] == 2

    async def test_async_task_timeout_as_default(self):
        import asyncio

        from pyarallel import async_parallel

        @async_parallel(concurrency=2, task_timeout=0.1)
        async def slow(x):
            await asyncio.sleep(5)
            return x

        r = await slow.map([1])
        assert not r.ok  # per-task timeout from the decorator applied

    def test_timeout_default_does_not_break_stream(self):
        """timeout= is a collected-API option; the streaming engines have
        no total deadline. A decorator default must not crash .stream()."""
        from pyarallel import parallel

        @parallel(workers=1, timeout=30.0)
        def work(x):
            return x

        got = sorted(item.value for item in work.stream([1, 2, 3]))
        assert got == [1, 2, 3]  # sorted: .stream() yields in completion order

    async def test_async_timeout_default_does_not_break_stream(self):
        from pyarallel import async_parallel

        @async_parallel(concurrency=1, timeout=30.0)
        async def work(x):
            return x

        got = sorted([item.value async for item in work.stream([1, 2])])
        assert got == [1, 2]  # sorted: completion order
