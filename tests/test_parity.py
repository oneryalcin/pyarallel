"""Tests for the v0.5 parity batch: sequential=, async timeout=,
contextvars propagation, worker_init=, max_tasks_per_worker=.
"""

import contextvars
import threading
import time

import pytest

from pyarallel import (
    Aborted,
    RateLimit,
    async_parallel_map,
    parallel,
    parallel_iter,
    parallel_map,
    parallel_starmap,
)

# --- module-level helpers (process-executor tests need picklables) ---

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="unset"
)


def _read_request_id(_x):
    return _request_id.get()


def _set_request_id(x):
    _request_id.set(f"task-{x}")
    return _request_id.get()


def _square(x):
    return x * x


def _noop_init():
    pass


class TestSequential:
    def test_runs_every_item_in_the_calling_thread(self):
        """The debug-mode contract: no pool — breakpoints and stack traces
        belong to the caller's thread."""
        caller = threading.get_ident()
        idents = list(
            parallel_map(lambda x: threading.get_ident(), range(5), sequential=True)
        )
        assert set(idents) == {caller}

    def test_deterministic_input_order_execution(self):
        seen = []

        def track(x):
            seen.append(x)
            return x

        parallel_map(track, range(10), sequential=True)
        assert seen == list(range(10))

    def test_workers_is_ignored_not_rejected(self):
        """One env flag must be able to flip production code into debug
        mode — passing both is allowed."""
        result = parallel_map(lambda x: x, [1, 2], workers=8, sequential=True)
        assert result.ok

    def test_rate_limit_still_paces(self):
        start = time.monotonic()
        parallel_map(
            lambda x: x,
            range(4),
            sequential=True,
            rate_limit=RateLimit(10, "second"),
        )
        assert time.monotonic() - start >= 0.25  # 3 paced intervals of 0.1s

    def test_checkpoint_resumes(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(x):
            calls.append(x)
            return x * 10

        assert parallel_map(work, [1, 2, 3], sequential=True, checkpoint=ckpt).ok
        calls.clear()
        result = parallel_map(work, [1, 2, 3], sequential=True, checkpoint=ckpt)
        assert list(result) == [10, 20, 30]
        assert calls == []

    def test_max_errors_aborts_with_full_accounting(self):
        calls = []

        def fail_from_five(x):
            calls.append(x)
            if x >= 5:
                raise ValueError("bad")
            return x

        result = parallel_map(fail_from_five, range(100), sequential=True, max_errors=3)
        assert not result.ok
        assert calls == list(range(8))  # 0-4 ok, 5,6,7 fail, stop
        assert len(result) == 100  # sized input: aborted fill by count
        assert sum(1 for _, e in result.failures() if isinstance(e, Aborted)) == 100 - 8

    def test_timeout_checked_between_items(self):
        """An in-flight item cannot be interrupted — documented."""

        def slow(x):
            time.sleep(0.3)
            return x

        start = time.monotonic()
        result = parallel_map(slow, range(10), sequential=True, timeout=0.4)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # stopped after ~2 items, not 10
        assert not result.ok
        assert len(result) == 10
        assert any(isinstance(e, TimeoutError) for _, e in result.failures())

    def test_streaming_sequential_is_inherently_ordered(self):
        caller = threading.get_ident()
        results = list(
            parallel_iter(
                lambda x: (x, threading.get_ident()), range(5), sequential=True
            )
        )
        assert [r.index for r in results] == list(range(5))
        assert all(r.value[1] == caller for r in results)

    def test_streaming_sequential_max_errors_stops(self):
        def dead(x):
            raise ValueError("bad")

        results = list(parallel_iter(dead, range(100), sequential=True, max_errors=3))
        assert len(results) == 3
        assert all(not r.ok for r in results)

    def test_worker_init_runs_once_in_caller(self):
        ran = []
        parallel_map(
            lambda x: x,
            range(5),
            sequential=True,
            worker_init=lambda: ran.append(threading.get_ident()),
        )
        assert ran == [threading.get_ident()]

    def test_starmap_sequential(self):
        result = parallel_starmap(lambda a, b: a + b, [(1, 2), (3, 4)], sequential=True)
        assert list(result) == [3, 7]


class TestAsyncTotalTimeout:
    async def test_partial_results_and_slot_marking(self):
        """Mirror of the sync contract: finished work kept, unfinished
        slots marked with the same failure text."""
        import asyncio

        async def task(x):
            await asyncio.sleep(0.01 if x < 2 else 10)
            return x * 10

        result = await async_parallel_map(task, range(6), concurrency=6, timeout=0.4)
        assert not result.ok
        assert dict(result.successes()) == {0: 0, 1: 10}
        for _idx, exc in result.failures():
            assert isinstance(exc, TimeoutError)
            assert "did not complete within" in str(exc)  # sync parity text

    async def test_lazy_input_timeout_returns_shorter_result(self):
        """v0.6 no-drain contract (rewritten from the v0.5 drain
        behavior): the source is never consumed after the deadline, so
        an unsized input yields a shorter result with ``timed_out`` set
        — not drained-and-appended placeholders."""
        import asyncio

        produced = []

        def items():
            for i in range(1000):
                produced.append(i)
                yield i

        async def hang(x):
            await asyncio.sleep(10)
            return x

        result = await async_parallel_map(
            hang,
            items(),
            concurrency=2,
            window_size=2,
            timeout=0.3,
        )
        assert result.timed_out
        assert len(result) < 1000  # no placeholder drain
        assert len(produced) <= 4  # consumption stopped at the window
        assert all(isinstance(e, TimeoutError) for _, e in result.failures())

    async def test_timeout_with_max_errors_marks_timeout_not_aborted(self):
        import asyncio

        async def slow(x):
            await asyncio.sleep(10)
            return x

        start = time.monotonic()
        result = await async_parallel_map(
            slow, range(50), concurrency=2, max_errors=5, timeout=0.3
        )
        assert time.monotonic() - start < 2.0
        errors = [e for _, e in result.failures()]
        assert any(isinstance(e, TimeoutError) for e in errors)
        assert not any(isinstance(e, Aborted) for e in errors)

    async def test_no_timeout_unchanged(self):
        async def quick(x):
            return x

        result = await async_parallel_map(quick, range(5), timeout=30.0)
        assert result.ok

    async def test_decorator_map_accepts_timeout(self):
        import asyncio

        from pyarallel import async_parallel

        @async_parallel(concurrency=4)
        async def hang(x):
            await asyncio.sleep(10)
            return x

        result = await hang.map(range(4), timeout=0.3)
        assert not result.ok
        assert all(isinstance(e, TimeoutError) for _, e in result.failures())


class TestContextvarPropagation:
    def test_caller_context_visible_in_thread_tasks(self):
        """The correlation-id story: a ContextVar set by the caller must
        survive into worker threads (v0.4 workers saw an empty context)."""
        token = _request_id.set("req-123")
        try:
            result = parallel_map(_read_request_id, range(8), workers=4)
            assert list(result) == ["req-123"] * 8
        finally:
            _request_id.reset(token)

    def test_task_writes_do_not_leak_back(self):
        token = _request_id.set("caller-value")
        try:
            result = parallel_map(_set_request_id, range(4), workers=2)
            assert set(result) == {f"task-{i}" for i in range(4)}
            assert _request_id.get() == "caller-value"
        finally:
            _request_id.reset(token)

    def test_streaming_path_propagates_too(self):
        token = _request_id.set("stream-ctx")
        try:
            values = [
                r.value for r in parallel_iter(_read_request_id, range(4), workers=2)
            ]
            assert values == ["stream-ctx"] * 4
        finally:
            _request_id.reset(token)

    def test_windowed_path_propagates_too(self):
        token = _request_id.set("windowed-ctx")
        try:
            result = parallel_map(_read_request_id, range(4), workers=2, max_errors=10)
            assert list(result) == ["windowed-ctx"] * 4
        finally:
            _request_id.reset(token)

    def test_process_executor_documented_skip_does_not_crash(self):
        """Contexts don't pickle, so pyarallel makes no per-item
        propagation promise for process workers — and nothing blows up.
        What a worker actually sees is platform-dependent: fresh default
        under spawn (macOS/Windows), the fork-time snapshot under fork
        (Linux) — either way it is the OS start method talking, not
        per-item propagation."""
        token = _request_id.set("not-propagated")
        try:
            result = parallel_map(
                _read_request_id, range(2), workers=2, executor="process"
            )
            assert result.ok
            assert all(v in ("unset", "not-propagated") for v in result)
        finally:
            _request_id.reset(token)


class TestWorkerInit:
    def test_thread_initializer_runs_once_per_worker(self):
        inits = []
        lock = threading.Lock()

        def init():
            with lock:
                inits.append(threading.get_ident())

        def work(x):
            time.sleep(0.01)
            return x

        result = parallel_map(work, range(12), workers=3, worker_init=init)
        assert result.ok
        assert 1 <= len(inits) <= 3
        assert len(inits) == len(set(inits))  # once per worker, not per task

    def test_unpicklable_init_with_process_fails_fast(self):
        with pytest.raises(ValueError, match="worker_init"):
            parallel_map(
                _square,
                [1],
                executor="process",
                worker_init=lambda: None,
            )

    def test_module_level_init_with_process_works(self):
        result = parallel_map(
            _square, [2, 3], workers=2, executor="process", worker_init=_noop_init
        )
        assert sorted(result) == [4, 9]

    def test_streaming_accepts_worker_init(self):
        inits = []
        results = list(
            parallel_iter(
                lambda x: x, range(4), workers=2, worker_init=lambda: inits.append(1)
            )
        )
        assert len(results) == 4
        assert 1 <= len(inits) <= 2


class TestMaxTasksPerWorker:
    def test_thread_executor_rejected(self):
        with pytest.raises(ValueError, match="max_tasks_per_worker"):
            parallel_map(lambda x: x, [1], max_tasks_per_worker=10)

    def test_zero_rejected(self):
        with pytest.raises(ValueError, match="max_tasks_per_worker"):
            parallel_map(_square, [1], executor="process", max_tasks_per_worker=0)

    def test_process_executor_accepts_and_completes(self):
        result = parallel_map(
            _square,
            range(4),
            workers=2,
            executor="process",
            max_tasks_per_worker=1,
        )
        assert sorted(result) == [0, 1, 4, 9]


class TestDecoratorParity:
    def test_map_sequential_passthrough(self):
        @parallel(workers=4)
        def double(x):
            return x * 2

        caller = threading.get_ident()

        @parallel(workers=4)
        def ident(x):
            return threading.get_ident()

        assert list(double.map([1, 2, 3], sequential=True)) == [2, 4, 6]
        assert set(ident.map([1, 2], sequential=True)) == {caller}

    def test_stream_sequential_passthrough(self):
        @parallel(workers=4)
        def double(x):
            return x * 2

        results = list(double.stream(range(4), sequential=True))
        assert [r.index for r in results] == [0, 1, 2, 3]


class TestFinalReviewFindings:
    """Regression tests from the final Codex review."""

    async def test_async_timeout_already_expired_runs_nothing(self):
        """Codex [P2]: asyncio.timeout(0) cancels only at the first
        suspension point — immediate coroutines used to run and return
        successes despite a zero total timeout."""
        calls = []

        async def task(x):
            calls.append(x)
            return x

        result = await async_parallel_map(task, range(20), timeout=0)
        assert calls == []
        assert len(result) == 20
        assert all(isinstance(e, TimeoutError) for _, e in result.failures())

    async def test_async_timeout_already_expired_windowed_path(self):
        calls = []

        async def task(x):
            calls.append(x)
            return x

        result = await async_parallel_map(task, range(20), timeout=0, max_errors=5)
        assert calls == []
        assert len(result) == 20
        assert all(isinstance(e, TimeoutError) for _, e in result.failures())

    def test_sequential_skips_pool_validation(self):
        """Codex [P2]: the one-flag debug promise — a process-configured
        call with a local worker_init must still run inline."""
        ran = []
        result = parallel_map(
            lambda x: x * 2,
            range(3),
            executor="process",
            worker_init=lambda: ran.append(1),  # unpicklable, pool would reject
            max_tasks_per_worker=10,  # thread pool would reject, ignored here
            sequential=True,
        )
        assert list(result) == [0, 2, 4]
        assert ran == [1]

    def test_sequential_streaming_skips_pool_validation(self):
        results = list(
            parallel_iter(
                lambda x: x,
                range(3),
                executor="process",
                worker_init=lambda: None,
                sequential=True,
            )
        )
        assert len(results) == 3


class TestTimeoutBeatsRateLimitPacing:
    """Codex adversarial (final gate) [high]: the sync driver blocked in
    bucket.wait() with no deadline bound — timeout= was bypassed whenever
    rate limiting was active. Each engine must give up mid-pacing."""

    def test_collected_map(self):
        start = time.monotonic()
        result = parallel_map(
            lambda x: x,
            range(3),
            workers=1,
            rate_limit=RateLimit(1, "second"),
            timeout=0.15,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # not 2s of token pacing
        assert not result.ok
        assert len(result) == 3
        assert any(isinstance(e, TimeoutError) for _, e in result.failures())

    def test_windowed_map_with_max_errors(self):
        start = time.monotonic()
        result = parallel_map(
            lambda x: x,
            range(3),
            workers=1,
            rate_limit=RateLimit(1, "second"),
            timeout=0.15,
            max_errors=10,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
        assert not result.ok
        assert len(result) == 3
        assert any(isinstance(e, TimeoutError) for _, e in result.failures())

    def test_sequential(self):
        start = time.monotonic()
        result = parallel_map(
            lambda x: x,
            range(3),
            rate_limit=RateLimit(1, "second"),
            timeout=0.15,
            sequential=True,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
        assert not result.ok
        assert len(result) == 3
        assert any(isinstance(e, TimeoutError) for _, e in result.failures())
