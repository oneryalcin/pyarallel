"""Regression tests for stopping a run while items are still running.

Production bug these pin: a sync run that stops (StopToken, timeout,
max_errors, Ctrl-C) marks every unresolved slot ``Cancelled`` and returns
with ``shutdown(wait=False)``. Items already running keep going in the
background and finish their side effects (a file written, an email sent, a
paid call made), but the result says ``Cancelled`` and the checkpoint is
already closed, so a resume runs them again.

The invariant the new features must give: once the call returns, an item is
reported as a success exactly when its side effect happened.

Both features are proposals, so their tests are strict xfails on the missing
parameter (``raises=TypeError``): any other failure is a real one. When a
feature lands, strict mode fails the suite until its marker is removed.

- #55: opt-in, bounded ``drain=`` on stop
- #56: ``interrupt="stop"`` turns the first Ctrl-C into a graceful stop
"""

import os
import signal
import threading
import time
from collections.abc import Iterator

import pytest

from pyarallel import Cancelled, RunStatus, StopToken, parallel_map

RUNNING = 4  # items 0..3 are mid-flight when the stop arrives
TOTAL = 8


class _Job:
    """Items below RUNNING block until released; the rest return at once.

    ``calls`` records every item whose side effect actually happened.
    """

    def __init__(self, *, block: bool = True) -> None:
        self.calls: set[int] = set()
        self._lock = threading.Lock()
        self.all_running = threading.Barrier(RUNNING + 1 if block else 1, timeout=5)
        self.release = threading.Event()
        if not block:
            self.release.set()

    def run(self, i: int) -> int:
        if i < RUNNING:
            self.all_running.wait()
            self.release.wait(timeout=5)
        with self._lock:
            self.calls.add(i)
        return i * 10

    def in_background(self, *steps) -> None:
        """Once items 0..3 are all running, do ``steps`` on another thread."""

        def go() -> None:
            self.all_running.wait()
            for step in steps:
                step()

        threading.Thread(target=go, daemon=True).start()


# checkpoint= refuses callable objects (their state is invisible to it), so
# every run goes through this module-level function.
_current: _Job | None = None


def _run(i: int) -> int:
    assert _current is not None
    return _current.run(i)


@pytest.fixture
def job() -> Iterator[_Job]:
    global _current
    _current = _Job()
    yield _current
    _current.release.set()  # never leave a background thread blocked


def _use(new: _Job) -> _Job:
    global _current
    _current = new
    return new


# The driver checks a StopToken every 0.1 s. Releasing the running items well
# after that means a run without drain has already returned (and marked them
# Cancelled) before they finish, which is the bug; with drain it is waiting.
RELEASE_AFTER_STOP = 0.5


def _pause() -> None:
    time.sleep(RELEASE_AFTER_STOP)


def _succeeded(r) -> set[int]:
    return {i for i, _ in r.successes()}


class TestStopWithoutDrain:
    """Today's behavior, which #55 keeps as the default. Passes now."""

    def test_a_stop_returns_promptly_even_if_items_hang(self, job):
        token = StopToken()
        job.in_background(token.stop)  # never released: the items hang

        start = time.monotonic()
        r = parallel_map(_run, range(TOTAL), workers=RUNNING, stop=token)

        assert time.monotonic() - start < 2
        assert r.status is RunStatus.CANCELLED


@pytest.mark.xfail(strict=True, raises=TypeError, reason="#55: drain= not implemented")
class TestDrain:
    def test_running_items_report_their_real_outcome(self, job):
        token = StopToken()
        job.in_background(token.stop, _pause, job.release.set)

        r = parallel_map(_run, range(TOTAL), workers=RUNNING, stop=token, drain=5.0)

        assert r.status is RunStatus.CANCELLED
        assert set(range(RUNNING)) <= _succeeded(r)
        assert _succeeded(r) == job.calls
        assert all(isinstance(e, Cancelled) for _, e in r.failures())

    def test_late_completions_are_checkpointed_so_resume_skips_them(
        self, job, tmp_path
    ):
        ckpt = str(tmp_path / "run.ckpt")
        token = StopToken()
        job.in_background(token.stop, _pause, job.release.set)

        parallel_map(
            _run, range(TOTAL), workers=RUNNING, stop=token, drain=5.0,
            checkpoint=ckpt, checkpoint_version="v1",
        )  # fmt: skip
        first = set(job.calls)
        assert set(range(RUNNING)) <= first

        resumed = _use(_Job(block=False))
        r = parallel_map(
            _run, range(TOTAL), workers=RUNNING,
            checkpoint=ckpt, checkpoint_version="v1",
        )  # fmt: skip

        assert r.ok
        assert resumed.calls == set(range(TOTAL)) - first  # nothing paid for twice

    def test_drain_is_bounded_by_its_timeout(self, job):
        token = StopToken()
        job.in_background(token.stop)  # never released: the items hang

        start = time.monotonic()
        r = parallel_map(_run, range(TOTAL), workers=RUNNING, stop=token, drain=0.3)

        assert time.monotonic() - start < 2
        assert len(r.failures()) == TOTAL
        assert all(isinstance(e, Cancelled) for _, e in r.failures())


def _ctrl_c() -> None:
    os.kill(os.getpid(), signal.SIGINT)


@pytest.mark.skipif(os.name == "nt", reason="SIGINT via os.kill is POSIX-only")
@pytest.mark.xfail(
    strict=True, raises=TypeError, reason="#56: interrupt='stop' not implemented"
)
class TestInterruptStop:
    """SIGINT is sent only once items are running, so a call that fails up
    front sends nothing. A KeyboardInterrupt that escapes would abort the
    whole pytest session, so it is turned into an ordinary failure."""

    def test_first_ctrl_c_returns_a_cancelled_result(self, job):
        job.in_background(_ctrl_c, _pause, job.release.set)

        try:
            r = parallel_map(
                _run, range(TOTAL), workers=RUNNING, interrupt="stop", drain=5.0
            )
        except KeyboardInterrupt:
            pytest.fail("the first Ctrl-C escaped instead of stopping the run")

        assert r.status is RunStatus.CANCELLED
        assert set(range(RUNNING)) <= _succeeded(r)
        assert _succeeded(r) == job.calls

    def test_second_ctrl_c_raises_at_once(self, job):
        job.in_background(_ctrl_c, lambda: time.sleep(0.1), _ctrl_c)

        start = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            parallel_map(
                _run, range(TOTAL), workers=RUNNING, interrupt="stop", drain=30.0
            )

        assert time.monotonic() - start < 2  # did not sit out the 30 s drain

    def test_the_previous_sigint_handler_is_restored(self):
        before = signal.getsignal(signal.SIGINT)

        r = parallel_map(_double, range(3), interrupt="stop")

        assert r.ok
        assert signal.getsignal(signal.SIGINT) is before


def _double(x: int) -> int:
    return x * 2
