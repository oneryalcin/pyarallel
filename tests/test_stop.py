"""Cooperative stop (v0.9): SIGTERM, notebook stop buttons, and spend
limits need a way to say "land the plane" — cease admission, cancel what
can be cancelled, keep completed checkpoints, report CANCELLED.

Production bug this prevents: the only way to stop an hour-long run was
killing the process — losing in-flight work and (without checkpoint)
everything else, with no structured way to tell "stopped" from "done".
"""

import threading
import time

import pytest

from pyarallel import (
    Cancelled,
    RunStatus,
    StopToken,
    async_parallel_map,
    parallel_map,
)


def _double(x):
    return x * 2


class TestStopToken:
    def test_stop_is_idempotent_and_observable(self):
        t = StopToken()
        assert t.stopped is False
        t.stop()
        t.stop()
        assert t.stopped is True


class TestSyncCancel:
    def test_pre_stopped_token_admits_nothing(self):
        t = StopToken()
        t.stop()
        pulls = 0

        def source():
            nonlocal pulls
            for i in range(100):
                pulls += 1
                yield i

        r = parallel_map(_double, source(), stop=t)
        assert r.status is RunStatus.CANCELLED
        assert r.ok is False
        assert pulls == 0  # nothing admitted, source untouched

    def test_stop_mid_run_keeps_completed_marks_rest(self):
        t = StopToken()
        started = threading.Event()

        def work(x):
            if x == 0:
                started.set()
            if x >= 2:
                time.sleep(5)  # would run forever without the stop
            return x * 2

        def stopper():
            started.wait(timeout=5)
            time.sleep(0.2)  # let items 0/1 complete
            t.stop()

        threading.Thread(target=stopper).start()
        start = time.monotonic()
        r = parallel_map(work, list(range(50)), workers=2, stop=t)
        elapsed = time.monotonic() - start
        assert r.status is RunStatus.CANCELLED
        assert elapsed < 3  # returned promptly, not after 50 slow items
        assert (0, 0) in r.successes()  # completed work kept
        # sized input: every unresolved slot is marked Cancelled
        assert all(isinstance(e, Cancelled) for _, e in r.failures())
        assert len(r) == 50

    def test_values_raises_cancelled(self):
        t = StopToken()
        t.stop()
        r = parallel_map(_double, (i for i in range(5)), stop=t)
        with pytest.raises(Cancelled):
            r.values()
        assert r.ok_values() == []  # explicit partial path works

    def test_derived_flags(self):
        t = StopToken()
        t.stop()
        r = parallel_map(_double, (i for i in range(5)), stop=t)
        assert r.status is RunStatus.CANCELLED
        assert r.timed_out is False
        assert r.aborted is False
        assert r.complete is False

    def test_sequential_path_stops_between_items(self):
        t = StopToken()
        ran = []

        def work(x):
            ran.append(x)
            if x == 1:
                t.stop()  # stop from inside item 1
            return x

        r = parallel_map(work, list(range(100)), sequential=True, stop=t)
        assert r.status is RunStatus.CANCELLED
        assert ran == [0, 1]  # item 2 never started

    def test_stop_from_another_thread_interrupts_completion_wait(self):
        """The driver may be parked in a blocking futures-wait — stop()
        from another thread (a signal handler's world) must still land
        promptly, not after the next task completion."""
        t = StopToken()

        def slow(x):
            time.sleep(10)
            return x

        threading.Timer(0.3, t.stop).start()
        start = time.monotonic()
        r = parallel_map(slow, [1, 2], workers=2, stop=t)
        assert r.status is RunStatus.CANCELLED
        assert time.monotonic() - start < 3

    def test_checkpoint_rows_survive_a_stop(self, tmp_path):
        t = StopToken()
        calls = {"n": 0}

        def work(x):
            calls["n"] += 1
            if x == 1:
                t.stop()
            return x * 2

        ckpt = str(tmp_path / "run.ckpt")
        first = parallel_map(
            work, [0, 1, 2, 3], sequential=True, stop=t, checkpoint=ckpt
        )
        assert first.status is RunStatus.CANCELLED
        done_before = calls["n"]
        # the morning rerun (no stop token) resumes from the checkpoint
        second = parallel_map(work, [0, 1, 2, 3], sequential=True, checkpoint=ckpt)
        assert second.ok
        assert calls["n"] == 4  # only the remainder executed
        assert done_before == 2

    def test_timeout_and_stop_first_writer_wins(self):
        """Both configured: whichever fires first names the status."""
        t = StopToken()
        t.stop()  # stop fires before any deadline can
        r = parallel_map(_double, (i for i in range(5)), stop=t, timeout=60)
        assert r.status is RunStatus.CANCELLED


class TestAsyncCancel:
    async def test_stop_cancels_in_flight_promptly(self):
        """Async CAN cancel running tasks — a stop must not wait for
        slow in-flight work (the sync engine can't do this; asymmetry
        is documented)."""
        import asyncio

        t = StopToken()

        async def slow(x):
            await asyncio.sleep(30)
            return x

        async def stopper():
            await asyncio.sleep(0.2)
            t.stop()

        asyncio.get_event_loop().call_later(0.2, t.stop)
        start = time.monotonic()
        r = await async_parallel_map(slow, list(range(10)), concurrency=4, stop=t)
        assert r.status is RunStatus.CANCELLED
        assert time.monotonic() - start < 3
        assert all(isinstance(e, Cancelled) for _, e in r.failures())

    async def test_pre_stopped_token_async(self):
        t = StopToken()
        t.stop()

        async def afn(x):
            return x

        r = await async_parallel_map(afn, (i for i in range(5)), stop=t)
        assert r.status is RunStatus.CANCELLED
        assert len(r) == 0  # unsized: nothing admitted, no placeholders

    async def test_stop_from_thread_wakes_async_driver(self):
        """stop() arrives from a non-loop thread (signal handlers,
        watchdogs) — the loop-safe bridge must wake the driver."""
        import asyncio

        t = StopToken()

        async def slow(x):
            await asyncio.sleep(30)
            return x

        threading.Timer(0.3, t.stop).start()
        start = time.monotonic()
        r = await async_parallel_map(slow, [1, 2, 3], stop=t)
        assert r.status is RunStatus.CANCELLED
        assert time.monotonic() - start < 3


class TestTokenReuse:
    def test_stopped_token_cancels_every_subsequent_run(self):
        """A token is a latch, not a pulse — once stopped, every run
        given it cancels immediately (use a fresh token per campaign)."""
        t = StopToken()
        t.stop()
        r1 = parallel_map(_double, (i for i in range(3)), stop=t)
        r2 = parallel_map(_double, (i for i in range(3)), stop=t)
        assert r1.status is RunStatus.CANCELLED
        assert r2.status is RunStatus.CANCELLED
