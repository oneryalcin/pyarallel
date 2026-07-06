"""executor="interpreter" (PEP 734, Python 3.14+).

The contract under test: process constraint semantics in one OS process —
importable module-level functions, picklable retry callables, no shared
limiter, no contextvars — plus two interpreter-only rules: ``__main__``
targets are rejected upfront, and ``max_tasks_per_worker`` is rejected
(no worker recycling). Version gate lives on the pool path only, so
``sequential=True`` works on every supported Python.
"""

import contextvars
import sys
import time

import pytest

from pyarallel import RateLimit, Retry, parallel, parallel_iter, parallel_map
from pyarallel.core import parallel_starmap

requires_314 = pytest.mark.skipif(
    sys.version_info < (3, 14), reason="InterpreterPoolExecutor is 3.14+"
)
below_314 = pytest.mark.skipif(
    sys.version_info >= (3, 14), reason="exercises the pre-3.14 gate"
)


# Module-level targets: sub-interpreters re-import this module by name.
def _square(x):
    return x * x


def _add(a, b):
    return a + b


def _fail_on_2(x):
    if x == 2:
        raise ValueError("bad value")
    return x


def _boom(x):
    raise ValueError(f"kapow {x}")


def _sleepy(x):
    time.sleep(1.0)
    return x


_G = "unset"


def _mutate_global(x):
    global _G
    _G = "mutated"
    return x


_CV: contextvars.ContextVar[str] = contextvars.ContextVar("cv", default="default")


def _read_cv(_):
    return _CV.get()


def _init_marker():
    global _G
    _G = "initialized"


def _read_global(_):
    return _G


def _ckpt_double(x):
    return x * 2


@parallel(workers=2)
def _decorated_double(x):
    return x * 2


@requires_314
class TestInterpreterParity:
    def test_basic_map_order_and_values(self):
        """Prevents: the interpreter path silently diverging from the
        engine contract (ordered results, all items run)."""
        result = parallel_map(_square, [1, 2, 3, 4], workers=2, executor="interpreter")
        assert list(result) == [1, 4, 9, 16]

    def test_exception_crosses_boundary_typed(self):
        """Prevents: worker errors arriving wrapped so a user's
        ``isinstance(exc, ValueError)`` handling breaks."""
        result = parallel_map(_fail_on_2, [1, 2, 3], workers=2, executor="interpreter")
        failures = result.failures()
        assert [idx for idx, _ in failures] == [1]
        assert isinstance(failures[0][1], ValueError)
        assert [v for _, v in result.successes()] == [1, 3]

    def test_lambda_target_fails_per_item_not_whole_run(self):
        """Prevents: one unshareable function poisoning the run with a
        hang or an escaped NotShareableError instead of structured
        per-item failures."""
        result = parallel_map(lambda x: x, [1, 2], workers=2, executor="interpreter")
        assert not result.ok
        assert len(result.failures()) == 2

    def test_starmap(self):
        """Prevents: the (module, qualname) resolution special-case
        missing the interpreter branch in parallel_starmap."""
        result = parallel_starmap(
            _add, [(1, 2), (3, 4)], workers=2, executor="interpreter"
        )
        assert list(result) == [3, 7]

    def test_streaming(self):
        """Prevents: parallel_iter building its pool differently and
        bypassing the interpreter gate/plumbing."""
        items = list(
            parallel_iter(_square, range(5), workers=2, executor="interpreter")
        )
        assert sorted(i.value for i in items) == [0, 1, 4, 9, 16]

    def test_decorated_function_resolves_in_worker(self):
        """Prevents: decorated targets (no longer the module-global
        binding) failing to re-import inside interpreter workers."""
        result = _decorated_double.map([1, 2, 3], executor="interpreter")
        assert list(result) == [2, 4, 6]

    def test_retry_crosses_boundary_and_counts(self):
        """Prevents: Retry state failing to cross so attempts silently
        become one."""
        [item] = list(
            parallel_iter(
                _boom,
                [7],
                workers=1,
                executor="interpreter",
                retry=Retry(attempts=3, backoff=0.001, jitter=False),
            )
        )
        assert item.attempts == 3
        assert isinstance(item.error, ValueError)

    def test_rate_limit_accepted(self):
        """Prevents: submission-time pacing being wired thread-only and
        rejecting or breaking interpreter runs."""
        result = parallel_map(
            _square,
            [1, 2, 3],
            workers=2,
            executor="interpreter",
            rate_limit=RateLimit(1000, "second"),
        )
        assert list(result) == [1, 4, 9]

    def test_module_globals_are_isolated(self):
        """Prevents: documenting shared-state semantics wrong — a worker
        write must not be visible to the parent (and this is the honest,
        timing-free replacement for an 'is it really parallel' assert)."""
        result = parallel_map(_mutate_global, [1], workers=1, executor="interpreter")
        assert result.ok
        assert _G == "unset"

    def test_thread_executor_shares_module_globals(self):
        """Contrast pin for the isolation contract above: the same write
        IS visible under the thread executor."""
        global _G
        _G = "unset"
        parallel_map(_mutate_global, [1], workers=1, executor="thread")
        assert _G == "mutated"

    def test_contextvars_do_not_propagate(self):
        """Prevents: docs claiming contextvars propagation that cannot
        exist across interpreters."""
        token = _CV.set("parent-value")
        try:
            result = parallel_map(_read_cv, [1], workers=1, executor="interpreter")
            assert list(result) == ["default"]
        finally:
            _CV.reset(token)

    def test_worker_init_runs_in_each_interpreter(self):
        """Prevents: initializer support regressing — the init must run
        inside the worker interpreter, where the task can see its effect."""
        result = parallel_map(
            _read_global,
            [1],
            workers=1,
            executor="interpreter",
            worker_init=_init_marker,
        )
        assert list(result) == ["initialized"]


@requires_314
class TestInterpreterStopPaths:
    """The paths where executor-blindness is earned, not assumed."""

    def test_checkpoint_cached_hits_respect_total_timeout(self, tmp_path):
        """Prevents: the v0.6 cached-admission deadline fix silently not
        holding for the interpreter pool type."""
        ckpt = str(tmp_path / "interp.ckpt")

        def slow_key(item):
            time.sleep(0.03)
            return str(item)

        parallel_map(
            _ckpt_double,
            range(6),
            workers=2,
            executor="interpreter",
            checkpoint=ckpt,
            checkpoint_key=slow_key,
        )

        produced = []

        def source():
            for i in range(6):
                produced.append(i)
                yield i

        result = parallel_map(
            _ckpt_double,
            source(),
            workers=2,
            executor="interpreter",
            checkpoint=ckpt,
            checkpoint_key=slow_key,
            timeout=0.05,
        )
        assert result.timed_out
        assert len(produced) < 6

    def test_poison_source_with_slow_tasks_raises_promptly(self):
        """Prevents: the v0.5 poison-source hang reappearing on the new
        executor — a source error must not wait out slow in-flight work."""

        def poison():
            yield 1
            yield 2
            raise RuntimeError("boom")

        start = time.monotonic()
        with pytest.raises(RuntimeError, match="boom"):
            parallel_map(_sleepy, poison(), workers=2, executor="interpreter")
        assert time.monotonic() - start < 0.8

    def test_streaming_break_ends_cleanly(self):
        """Prevents: generator-close/cancel hanging against
        InterpreterPoolExecutor shutdown semantics."""
        stream = parallel_iter(_square, range(100), workers=2, executor="interpreter")
        for _item in stream:
            break
        stream.close()  # must return, not hang


class TestInterpreterValidation:
    @below_314
    def test_gate_below_314_names_the_requirement(self):
        """Prevents: a cryptic ImportError deep in pool construction on
        older CPython instead of one actionable error."""
        with pytest.raises(ValueError, match="requires Python 3.14"):
            parallel_map(_square, [1], executor="interpreter")

    def test_sequential_ignores_gate_collected(self):
        """Prevents: the version gate breaking the debug contract — a
        3.14-production config must run with sequential=True anywhere."""
        result = parallel_map(_square, [1, 2], executor="interpreter", sequential=True)
        assert list(result) == [1, 4]

    def test_sequential_ignores_gate_streaming(self):
        items = list(
            parallel_iter(_square, [1, 2], executor="interpreter", sequential=True)
        )
        assert sorted(i.value for i in items) == [1, 4]

    @requires_314
    def test_main_target_rejected_upfront(self):
        """Prevents: a __main__-defined function producing N identical
        per-item AttributeErrors instead of one actionable ValueError
        (process bootstraps __main__; interpreters cannot)."""

        def fn(x):
            return x

        fn.__module__ = "__main__"
        fn.__qualname__ = "fn"
        with pytest.raises(ValueError, match="__main__"):
            parallel_map(fn, [1], executor="interpreter")

    @requires_314
    def test_main_target_rejected_upfront_starmap(self):
        """Prevents: starmap's pre-resolution wrapper hiding the target's
        qualname so a __main__ function slips past the upfront check into
        the per-item failure storm (Codex adversarial, implementation
        round)."""

        def fn(a, b):
            return a + b

        fn.__module__ = "__main__"
        fn.__qualname__ = "fn"
        with pytest.raises(ValueError, match="__main__"):
            parallel_starmap(fn, [(1, 2)], executor="interpreter")

    def test_main_target_sequential_starmap_runs_inline(self):
        """Prevents: the starmap __main__ rejection firing for
        sequential=True, where the run is inline and __main__ is visible."""

        def fn(a, b):
            return a + b

        fn.__module__ = "__main__"
        fn.__qualname__ = "fn"
        main_mod = sys.modules["__main__"]
        # A real script's __main__ actually holds the function — mirror that.
        main_mod.fn = fn  # type: ignore[attr-defined]
        try:
            result = parallel_starmap(
                fn, [(1, 2)], executor="interpreter", sequential=True
            )
            assert list(result) == [3]
        finally:
            del main_mod.fn  # type: ignore[attr-defined]

    @requires_314
    def test_max_tasks_per_worker_rejected(self):
        """Prevents: the option being silently ignored while the user
        believes worker recycling happens."""
        with pytest.raises(ValueError, match="max_tasks_per_worker"):
            parallel_map(_square, [1], executor="interpreter", max_tasks_per_worker=5)

    @requires_314
    def test_worker_init_closure_rejected_upfront(self):
        """Prevents: BrokenInterpreterPool + a stderr traceback dump at
        first submit instead of an upfront ValueError."""
        y = []
        with pytest.raises(ValueError, match="worker_init"):
            parallel_map(_square, [1], executor="interpreter", worker_init=lambda: y)

    @requires_314
    def test_worker_init_main_rejected_upfront(self):
        """Prevents: the __main__ worker_init variant of the same bug —
        it pickles fine by reference, then breaks the pool."""

        def init():
            pass

        init.__module__ = "__main__"
        with pytest.raises(ValueError, match="__main__"):
            parallel_map(_square, [1], executor="interpreter", worker_init=init)

    @requires_314
    def test_unpicklable_retry_message_names_interpreter(self):
        """Prevents: the retry pickle pre-check blaming
        executor='process' when the caller passed interpreter."""
        with pytest.raises(ValueError, match="interpreter"):
            parallel_map(
                _square,
                [1],
                executor="interpreter",
                retry=Retry(attempts=2, retry_if=lambda e: True),
            )
