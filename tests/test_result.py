"""Tests for ParallelResult."""

import pytest

from pyarallel import ParallelResult
from pyarallel.core import _Failure


class TestSuccessfulResult:
    def test_ok_is_true(self):
        r = ParallelResult([1, 2, 3], 3)
        assert r.ok is True

    def test_values(self):
        assert ParallelResult([10, 20], 2).values() == [10, 20]

    def test_iteration(self):
        assert list(ParallelResult([1, 2, 3], 3)) == [1, 2, 3]

    def test_indexing(self):
        r = ParallelResult([10, 20, 30], 3)
        assert r[0] == 10
        assert r[2] == 30

    def test_len(self):
        assert len(ParallelResult([1, 2], 2)) == 2

    def test_bool_nonempty(self):
        assert bool(ParallelResult([1], 1)) is True

    def test_bool_empty(self):
        assert bool(ParallelResult([], 0)) is False

    def test_repr(self):
        r = ParallelResult([1, 2], 2)
        assert "1" in repr(r)
        assert "ParallelResult" in repr(r)

    def test_successes(self):
        r = ParallelResult([10, 20], 2)
        assert r.successes() == [(0, 10), (1, 20)]

    def test_failures_empty(self):
        r = ParallelResult([10, 20], 2)
        assert r.failures() == []

    def test_raise_on_failure_noop(self):
        ParallelResult([1, 2], 2).raise_on_failure()  # Should not raise


class TestFailedResult:
    def _make_mixed(self):
        return ParallelResult(
            [10, _Failure(1, ValueError("bad")), 30],
            3,
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
            [_Failure(0, RuntimeError("a")), _Failure(1, RuntimeError("b"))],
            2,
        )
        assert len(r.failures()) == 2
        assert len(r.successes()) == 0
        with pytest.raises(ExceptionGroup) as exc_info:
            r.raise_on_failure()
        assert len(exc_info.value.exceptions) == 2
