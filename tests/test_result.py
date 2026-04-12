"""Tests for ParallelResult."""

import pytest

from pyarallel import ParallelResult
from pyarallel.core import _Failure


class TestSuccessfulResult:
    def test_ok_is_true(self):
        r = ParallelResult([1, 2, 3])
        assert r.ok is True

    def test_values(self):
        assert ParallelResult([10, 20]).values() == [10, 20]

    def test_iteration(self):
        assert list(ParallelResult([1, 2, 3])) == [1, 2, 3]

    def test_indexing(self):
        r = ParallelResult([10, 20, 30])
        assert r[0] == 10
        assert r[2] == 30

    def test_len(self):
        assert len(ParallelResult([1, 2])) == 2

    def test_bool_nonempty(self):
        assert bool(ParallelResult([1])) is True

    def test_bool_empty(self):
        assert bool(ParallelResult([])) is False

    def test_repr(self):
        r = ParallelResult([1, 2])
        assert "1" in repr(r)
        assert "ParallelResult" in repr(r)

    def test_successes(self):
        r = ParallelResult([10, 20])
        assert r.successes() == [(0, 10), (1, 20)]

    def test_failures_empty(self):
        r = ParallelResult([10, 20])
        assert r.failures() == []

    def test_raise_on_failure_noop(self):
        ParallelResult([1, 2]).raise_on_failure()  # Should not raise

    def test_none_is_a_valid_result(self):
        """fn() returning None should not be confused with a missing slot."""
        r = ParallelResult([None, 42, None])
        assert r.ok is True
        assert list(r) == [None, 42, None]


class TestFailedResult:
    def _make_mixed(self):
        return ParallelResult(
            [10, _Failure(ValueError("bad")), 30],
        )

    def test_ok_is_false(self):
        assert self._make_mixed().ok is False

    def test_successes(self):
        r = self._make_mixed()
        assert r.successes() == [(0, 10), (2, 30)]

    def test_failures(self):
        r = self._make_mixed()
        fails = r.failures()
        assert len(fails) == 1
        assert fails[0][0] == 1
        assert isinstance(fails[0][1], ValueError)

    def test_values_raises(self):
        with pytest.raises(ExceptionGroup):
            self._make_mixed().values()

    def test_iteration_raises(self):
        with pytest.raises(ExceptionGroup):
            list(self._make_mixed())

    def test_indexing_raises(self):
        with pytest.raises(ExceptionGroup):
            _ = self._make_mixed()[0]

    def test_raise_on_failure(self):
        with pytest.raises(ExceptionGroup) as exc_info:
            self._make_mixed().raise_on_failure()
        assert len(exc_info.value.exceptions) == 1
        assert "1 of 3" in str(exc_info.value)

    def test_repr_shows_counts(self):
        r = self._make_mixed()
        s = repr(r)
        assert "2 ok" in s
        assert "1 failed" in s

    def test_all_failed(self):
        r = ParallelResult(
            [_Failure(RuntimeError("a")), _Failure(RuntimeError("b"))],
        )
        assert len(r.failures()) == 2
        assert len(r.successes()) == 0
        with pytest.raises(ExceptionGroup) as exc_info:
            r.raise_on_failure()
        assert len(exc_info.value.exceptions) == 2


class TestValidation:
    def test_rate_limit_zero_raises(self):
        from pyarallel import RateLimit
        with pytest.raises(ValueError, match="positive"):
            RateLimit(0)

    def test_rate_limit_negative_raises(self):
        from pyarallel import RateLimit
        with pytest.raises(ValueError, match="positive"):
            RateLimit(-5)

    def test_retry_zero_attempts_raises(self):
        from pyarallel.core import Retry
        with pytest.raises(ValueError, match=">= 1"):
            Retry(attempts=0)

    def test_retry_negative_attempts_raises(self):
        from pyarallel.core import Retry
        with pytest.raises(ValueError, match=">= 1"):
            Retry(attempts=-1)

    def test_batch_size_zero_raises(self):
        from pyarallel import parallel_map
        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], batch_size=0)

    def test_batch_size_negative_raises(self):
        from pyarallel import parallel_map
        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], batch_size=-1)

    def test_workers_zero_raises(self):
        from pyarallel import parallel_map
        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], workers=0)

    def test_rate_limit_invalid_per_raises(self):
        from pyarallel import RateLimit
        with pytest.raises(ValueError, match='"second", "minute", or "hour"'):
            RateLimit(10, "minuet")

    def test_rate_limit_valid_per_accepted(self):
        from pyarallel import RateLimit
        for per in ("second", "minute", "hour"):
            r = RateLimit(10, per)
            assert r.per_second > 0
