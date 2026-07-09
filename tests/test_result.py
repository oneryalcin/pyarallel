"""Tests for ParallelResult and ItemResult."""

import pytest

from pyarallel import ItemResult, ParallelResult, RunStatus
from pyarallel.result import _Failure


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
        from pyarallel import Retry

        with pytest.raises(ValueError, match=">= 1"):
            Retry(attempts=0)

    def test_retry_negative_attempts_raises(self):
        from pyarallel import Retry

        with pytest.raises(ValueError, match=">= 1"):
            Retry(attempts=-1)

    def test_window_size_zero_raises(self):
        from pyarallel import parallel_map

        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], window_size=0)

    def test_window_size_negative_raises(self):
        from pyarallel import parallel_map

        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], window_size=-1)

    def test_workers_zero_raises(self):
        from pyarallel import parallel_map

        with pytest.raises(ValueError, match=">= 1"):
            parallel_map(lambda x: x, [1], workers=0)

    def test_task_timeout_not_in_sync_signature(self):
        """Sync parallel_map deliberately has no task_timeout — threads
        can't be cancelled. The parameter was removed rather than kept
        as a NotImplementedError trap."""
        from pyarallel import parallel_map

        with pytest.raises(TypeError, match="task_timeout"):
            parallel_map(lambda x: x, [1], task_timeout=5.0)

    def test_rate_limit_invalid_per_raises(self):
        from pyarallel import RateLimit

        with pytest.raises(ValueError, match='"second", "minute", or "hour"'):
            RateLimit(10, "minuet")

    def test_rate_limit_valid_per_accepted(self):
        from pyarallel import RateLimit

        for per in ("second", "minute", "hour"):
            r = RateLimit(10, per)
            assert r.per_second > 0


class TestItemResult:
    def test_success(self):
        item = ItemResult[int](index=2, value=42)
        assert item.ok is True
        assert item.index == 2
        assert item.value == 42
        assert item.error is None

    def test_failure(self):
        err = ValueError("bad")
        item = ItemResult[int](index=3, error=err)
        assert item.ok is False
        assert item.index == 3
        assert item.value is None
        assert item.error is err

    def test_requires_exactly_one_of_value_or_error(self):
        with pytest.raises(ValueError):
            ItemResult[int](index=0)

        with pytest.raises(ValueError):
            ItemResult[int](index=0, value=1, error=ValueError("bad"))


class TestRunStatus:
    """v0.6 D7: timed_out/aborted report how the run ended — the one
    reliable truncation signal when unsized inputs can't get placeholder
    failure entries."""

    def test_defaults_false_on_manual_construction(self):
        r = ParallelResult([1, 2])
        assert r.timed_out is False
        assert r.aborted is False

    def test_status_settable_at_construction(self):
        r = ParallelResult([], status=RunStatus.TIMED_OUT)
        assert r.timed_out is True
        assert r.aborted is False  # one status — contradiction unrepresentable

    def test_clean_run_reports_neither(self):
        from pyarallel import parallel_map

        r = parallel_map(lambda x: x, range(5), workers=2)
        assert r.timed_out is False
        assert r.aborted is False

    def test_timeout_sets_timed_out(self):
        """Prevents: the silent-truncation hole — a timeout on an unsized
        input can return only successes; the flag must still say so."""
        import time

        from pyarallel import parallel_map

        def slow(x):
            time.sleep(10)
            return x

        r = parallel_map(slow, range(8), workers=2, timeout=0.2)
        assert r.timed_out is True
        assert r.aborted is False

    def test_max_errors_abort_sets_aborted(self):
        from pyarallel import parallel_map

        def boom(x):
            raise ValueError("dead")

        r = parallel_map(boom, range(50), workers=2, max_errors=3)
        assert r.aborted is True
        assert r.timed_out is False

    def test_sequential_engine_sets_status_too(self):
        """Prevents: debug mode diverging from the pool engines on the
        status contract (one flag flips prod to sequential)."""
        from pyarallel import parallel_map

        def boom(x):
            raise ValueError("dead")

        r = parallel_map(boom, range(10), sequential=True, max_errors=2)
        assert r.aborted is True

    async def test_async_engine_sets_status_too(self):
        from pyarallel import async_parallel_map

        async def boom(x):
            raise ValueError("dead")

        r = await async_parallel_map(boom, range(50), concurrency=2, max_errors=3)
        assert r.aborted is True
        assert r.timed_out is False


class TestRunStatusExclusivityAndRepr:
    """Round 2 review (v0.6): the flags are exclusive (first stop reason
    wins) and visible in the repr — a truncated run must not log like a
    complete one."""

    def test_repr_shows_timed_out_on_all_success_truncation(self):
        r = ParallelResult([1, 2, 3], status=RunStatus.TIMED_OUT)
        assert "timed_out" in repr(r)

    def test_repr_shows_aborted(self):
        r = ParallelResult([1, _Failure(ValueError("x"))], status=RunStatus.ABORTED)
        assert "aborted" in repr(r)

    def test_repr_unchanged_for_clean_runs(self):
        assert repr(ParallelResult([1, 2])) == "ParallelResult([1, 2])"

    def test_engine_timeout_reports_timed_out_not_aborted(self):
        """With both timeout= and max_errors= configured, a deadline stop
        must not also claim an abort."""
        import time

        from pyarallel import parallel_map

        def slow(x):
            time.sleep(10)
            return x

        r = parallel_map(slow, range(20), workers=2, max_errors=5, timeout=0.2)
        assert r.timed_out is True
        assert r.aborted is False


class TestItemResultInvariants:
    """v0.8 review: ItemResult(0, error=None) passed the exactly-one check
    (the parameter *was* supplied) and built a fake success — .ok True,
    .value None. Invalid states must be unconstructable."""

    def test_explicit_none_error_rejected(self):
        with pytest.raises(ValueError):
            ItemResult(0, error=None)

    def test_error_must_be_exception(self):
        with pytest.raises(ValueError):
            ItemResult(0, error="not an exception")  # type: ignore[arg-type]

    def test_explicit_none_value_is_a_real_success(self):
        """fn returning None is legitimate — must stay constructable."""
        it = ItemResult(0, value=None)
        assert it.ok is True
        assert it.value is None


class TestFailureProvenance:
    """v0.8 review: raise_on_failure() flattened failures into an
    ExceptionGroup with no way back to *which item* failed. PEP 678 notes
    carry the index without changing exception types, so
    `except* ConnectionError` matching is untouched."""

    def test_notes_carry_item_index(self):
        r = ParallelResult([10, _Failure(ValueError("bad")), 30])
        with pytest.raises(ExceptionGroup) as excinfo:
            r.raise_on_failure()
        (sub,) = excinfo.value.exceptions
        assert any("1" in note for note in sub.__notes__)

    def test_except_star_matching_survives_notes(self):
        r = ParallelResult([_Failure(ConnectionError("down"))])
        matched = False
        try:
            r.raise_on_failure()
        except* ConnectionError:
            matched = True
        assert matched


class TestProvenanceNoteIdempotence:
    """v0.8 review follow-up: raise_on_failure() mutates the stored
    exceptions (PEP 678 notes) — calling it twice must not append the
    same index note twice."""

    def test_repeated_calls_do_not_duplicate_notes(self):
        r = ParallelResult([10, _Failure(ValueError("bad"))])
        for _ in range(3):
            with pytest.raises(ExceptionGroup):
                r.raise_on_failure()
        (idx, exc) = r.failures()[0]
        notes = [n for n in exc.__notes__ if "item index" in n]
        assert len(notes) == 1
