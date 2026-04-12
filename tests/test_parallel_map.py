"""Tests for parallel_map — the core workhorse."""

import time

import pytest

from pyarallel import ParallelResult, RateLimit, parallel_map


# Module-level function (picklable — needed for process executor tests)
def _square(x: int) -> int:
    return x * x


class TestBasic:
    def test_returns_ordered_results(self):
        result = parallel_map(lambda x: x * 2, [1, 2, 3, 4, 5], workers=3)
        assert list(result) == [2, 4, 6, 8, 10]

    def test_preserves_order_despite_varying_speed(self):
        def slow_first(x):
            if x == 0:
                time.sleep(0.1)
            return x

        result = parallel_map(slow_first, [0, 1, 2, 3], workers=4)
        assert list(result) == [0, 1, 2, 3]

    def test_empty_input(self):
        result = parallel_map(lambda x: x, [])
        assert list(result) == []
        assert len(result) == 0
        assert result.ok

    def test_single_item(self):
        result = parallel_map(lambda x: x * 2, [42], workers=1)
        assert list(result) == [84]

    def test_returns_parallel_result(self):
        result = parallel_map(lambda x: x, [1, 2, 3])
        assert isinstance(result, ParallelResult)


class TestIterableInputs:
    """parallel_map accepts any iterable — not just lists."""

    def test_generator(self):
        result = parallel_map(lambda x: x * 2, (x for x in [1, 2, 3]), workers=2)
        assert list(result) == [2, 4, 6]

    def test_range(self):
        result = parallel_map(lambda x: x * 2, range(5), workers=2)
        assert list(result) == [0, 2, 4, 6, 8]

    def test_set(self):
        result = parallel_map(lambda x: x * 2, {10, 20, 30}, workers=2)
        assert sorted(result) == [20, 40, 60]

    def test_tuple(self):
        result = parallel_map(lambda x: x + 1, (10, 20, 30), workers=2)
        assert list(result) == [11, 21, 31]

    def test_map_object(self):
        result = parallel_map(lambda x: x * 2, map(int, ["1", "2", "3"]), workers=2)
        assert list(result) == [2, 4, 6]


class TestProcessExecutor:
    def test_basic_process_pool(self):
        result = parallel_map(_square, [1, 2, 3, 4], workers=2, executor="process")
        assert list(result) == [1, 4, 9, 16]


class TestErrorHandling:
    def test_single_failure_raises_exception_group(self):
        def fail_on_2(x):
            if x == 2:
                raise ValueError("bad value")
            return x

        result = parallel_map(fail_on_2, [1, 2, 3], workers=2)
        assert not result.ok
        with pytest.raises(ExceptionGroup) as exc_info:
            result.values()
        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], ValueError)

    def test_multiple_failures_all_reported(self):
        def fail_on_even(x):
            if x % 2 == 0:
                raise ValueError(f"even: {x}")
            return x

        result = parallel_map(fail_on_even, [1, 2, 3, 4, 5], workers=3)
        assert not result.ok
        assert len(result.successes()) == 3
        assert len(result.failures()) == 2

        with pytest.raises(ExceptionGroup) as exc_info:
            result.raise_on_failure()
        assert len(exc_info.value.exceptions) == 2

    def test_partial_results_accessible(self):
        def maybe_fail(x):
            if x == 5:
                raise RuntimeError("boom")
            return x * 10

        result = parallel_map(maybe_fail, [1, 5, 3], workers=2)
        successes = result.successes()
        failures = result.failures()

        assert len(successes) == 2
        success_values = {v for _, v in successes}
        assert success_values == {10, 30}

        assert len(failures) == 1
        idx, exc = failures[0]
        assert idx == 1
        assert isinstance(exc, RuntimeError)

    def test_iterating_failed_result_raises(self):
        def always_fail(x):
            raise RuntimeError("nope")

        result = parallel_map(always_fail, [1, 2], workers=2)
        with pytest.raises(ExceptionGroup):
            list(result)

    def test_indexing_failed_result_raises(self):
        def always_fail(x):
            raise RuntimeError("nope")

        result = parallel_map(always_fail, [1], workers=1)
        with pytest.raises(ExceptionGroup):
            _ = result[0]


class TestRateLimiting:
    def test_rate_limit_object(self):
        start = time.monotonic()
        parallel_map(
            lambda x: x, range(5), workers=5,
            rate_limit=RateLimit(10, "second"),
        )
        elapsed = time.monotonic() - start
        # 5 items at 10/sec → at least 0.4s of inter-token spacing
        assert elapsed >= 0.3

    def test_rate_limit_shorthand(self):
        start = time.monotonic()
        parallel_map(lambda x: x, range(5), workers=5, rate_limit=10)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3


class TestTimeout:
    def test_total_timeout(self):
        def slow(x):
            time.sleep(2.0)
            return x

        result = parallel_map(slow, [1, 2, 3], workers=3, timeout=0.3)
        assert not result.ok
        failures = result.failures()
        assert len(failures) > 0
        assert all(isinstance(e, TimeoutError) for _, e in failures)


class TestProgress:
    def test_progress_callback_called(self):
        progress = []
        parallel_map(
            lambda x: x, [1, 2, 3], workers=2,
            on_progress=lambda done, total: progress.append((done, total)),
        )
        assert len(progress) == 3
        assert all(total == 3 for _, total in progress)
        assert sorted(done for done, _ in progress) == [1, 2, 3]
