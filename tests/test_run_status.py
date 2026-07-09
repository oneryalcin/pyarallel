"""RunStatus contract: a truncated run must never look like a clean one.

Production bug pinned here (pre-v0.8): an unsized run that hit ``timeout=``
could return only successful items — ``result.ok`` was ``True`` and
``result.values()`` returned normally, so the caller had no signal that the
run was a truncation rather than a completion. Both deterministic shapes of
that footgun are reproduced below.
"""

import time

import pytest

from pyarallel import (
    ParallelResult,
    RunStatus,
    async_parallel_map,
    parallel_map,
)


def _double(x):
    return x * 2


def _fail_even(x):
    if x % 2 == 0:
        raise ValueError(f"item {x}")
    return x


class TestTimedOutNeverOk:
    def test_timeout_zero_unsized_source(self):
        """timeout=0 over a generator admits nothing and returns an empty,
        timed-out result — which used to report ok=True."""
        r = parallel_map(_double, (i for i in range(10)), timeout=0)
        assert r.status is RunStatus.TIMED_OUT
        assert r.ok is False
        assert r.complete is False
        assert len(r) == 0

    def test_partial_successes_then_timeout(self):
        """Deadline expires after the last absorb (slow on_progress eats it):
        the result holds only successes but the run is a truncation."""

        def slow_report(done, total):
            time.sleep(0.4)

        r = parallel_map(
            _double,
            (i for i in range(10)),
            workers=1,
            window_size=1,
            timeout=0.3,
            on_progress=slow_report,
        )
        assert r.status is RunStatus.TIMED_OUT
        assert len(r.failures()) == 0  # every returned item succeeded...
        assert r.ok is False  # ...but the run must not read as ok
        assert r.complete is False

    def test_values_raises_on_truncated_run(self):
        """.values() promises 'all results' — on a truncated run there is
        no such thing, so it must raise instead of returning quietly."""
        r = parallel_map(_double, (i for i in range(10)), timeout=0)
        with pytest.raises(TimeoutError):
            r.values()

    async def test_async_timeout_zero_unsized_source(self):
        async def adouble(x):
            return x * 2

        r = await async_parallel_map(adouble, (i for i in range(10)), timeout=0)
        assert r.status is RunStatus.TIMED_OUT
        assert r.ok is False


class TestCompletedSemantics:
    def test_completed_all_ok(self):
        r = parallel_map(_double, [1, 2, 3])
        assert r.status is RunStatus.COMPLETED
        assert r.complete is True
        assert r.ok is True

    def test_completed_with_failures_is_not_ok(self):
        """Source exhausted, every item resolved, some failed: the run is
        COMPLETED (nothing was truncated) but must not be ok."""
        r = parallel_map(_fail_even, [1, 2, 3, 4])
        assert r.status is RunStatus.COMPLETED
        assert r.complete is True
        assert r.ok is False

    def test_aborted_status(self):
        r = parallel_map(_fail_even, list(range(100)), workers=1, max_errors=2)
        assert r.status is RunStatus.ABORTED
        assert r.ok is False
        assert r.complete is False

    def test_derived_flags_match_status(self):
        """timed_out/aborted stay as derived reads of status."""
        r = parallel_map(_double, (i for i in range(10)), timeout=0)
        assert r.timed_out is True
        assert r.aborted is False
        c = parallel_map(_double, [1, 2])
        assert c.timed_out is False
        assert c.aborted is False


class TestStatusUnrepresentable:
    def test_constructor_takes_one_status(self):
        """The old two-bool constructor could represent the contradictory
        timed_out+aborted state; the status enum cannot."""
        r = ParallelResult([1, 2], status=RunStatus.TIMED_OUT)
        assert r.timed_out is True
        assert r.aborted is False

    def test_default_status_is_completed(self):
        assert ParallelResult([1]).status is RunStatus.COMPLETED


class TestTruncatedAccessRaisesClearly:
    """v0.8 review follow-up: on a truncated all-success run, every
    'whole result' accessor — values(), iteration, indexing — must raise
    a clear run-incomplete error, not return quietly and not raise a
    misleading (empty) ExceptionGroup."""

    def _truncated(self):
        r = parallel_map(_double, (i for i in range(10)), timeout=0)
        assert r.status is RunStatus.TIMED_OUT
        assert not r.failures()  # all-success truncation — the sharp case
        return r

    def test_values_raises_timeout_not_exception_group(self):
        with pytest.raises(TimeoutError) as excinfo:
            self._truncated().values()
        assert "not exhausted" in str(excinfo.value)
        assert not isinstance(excinfo.value, ExceptionGroup)

    def test_iteration_raises(self):
        with pytest.raises(TimeoutError):
            list(self._truncated())

    def test_indexing_raises(self):
        r = parallel_map(
            _double,
            (i for i in range(10)),
            workers=1,
            window_size=1,
            timeout=0.3,
            on_progress=lambda d, t: time.sleep(0.4),
        )
        assert r.status is RunStatus.TIMED_OUT
        assert len(r) == 1  # one real success is in there...
        with pytest.raises(TimeoutError):
            r[0]  # ...but positional access must not pretend completeness
        assert r.ok_values() == [0]  # the explicit partial accessor works
