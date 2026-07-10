# Testing Pyarallel Applications

Code built on pyarallel is testable without real HTTP, without sleeping
through backoff, and without flaky thread timing. Every snippet on this
page is API-checked in CI; the patterns below are the ones the library
itself is tested with.

## Run it inline: `sequential=True`

`sequential=True` runs every item in the calling thread — no pool,
deterministic order, real stack traces, working breakpoints and
`pdb`. Rate limits, retry, checkpointing, and `stop=` all still apply,
and `workers` is ignored rather than rejected, so the production call
site doesn't need to change shape:

```python
def test_enrichment():
    result = parallel_map(enrich, ROWS, sequential=True)
    assert result.values() == EXPECTED
```

Use it for every test that isn't specifically about concurrency.

## Kill the sleeps: `Retry(backoff=0, jitter=False)`

Exponential backoff is correct in production and agony in a test suite.
Zero it out — retries still happen, attempts are still counted in the
result metadata, and the test runs in microseconds:

```python
def test_transient_errors_are_retried():
    calls = {"n": 0}

    def flaky(x):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return x

    result = parallel_map(
        flaky,
        [1],
        retry=Retry(attempts=3, backoff=0, jitter=False),
        sequential=True,
    )
    assert result.ok
    assert result.item_results()[0].attempts == 3
```

Keep the production `Retry` in one module-level constant and override
it in tests, rather than scattering retry policies through call sites.

## Assert outcomes, not internals

A run ends in exactly one of four states, and your error-handling code
should have a test per state it handles. Each state is cheap to
manufacture:

```python
def test_dead_api_aborts_early():
    def boom(x):
        raise RuntimeError("api down")

    result = parallel_map(boom, range(100), max_errors=1, sequential=True)
    assert result.status is RunStatus.ABORTED
    assert result.ok_values() == []          # partials, honestly
    with pytest.raises(Aborted):
        result.values()                       # the whole list refuses to lie
```

```python
def test_wall_clock_budget():
    def slow(x):
        time.sleep(0.05)
        return x

    result = parallel_map(slow, range(50), timeout=0.1, workers=2)
    assert result.status is RunStatus.TIMED_OUT
    with pytest.raises(TimeoutError):
        result.values()


def test_operator_cancellation():
    token = StopToken()
    token.stop()  # pre-stopped: the run cancels at the first admission gate

    result = parallel_map(str, range(5), stop=token, sequential=True)
    assert result.status is RunStatus.CANCELLED
    with pytest.raises(Cancelled):
        result.values()
```

The contract these tests lean on: `values()` raises on any truncated
run (`TimeoutError` / `Aborted` / `Cancelled`) and on item failures
(`ExceptionGroup`); `ok_values()` is the explicit opt-in to partial
results. Test both sides of it.

## Test `Retry-After` handling without sleeping

Server-driven backoff is the code path most worth testing and the one
nobody wants to wait for. Hand-build the throttled response with a
`Retry-After: 0` header — the full 429 path executes, the pause is
zero:

```python
import io
import urllib.error
from email.message import Message


def throttled_then_ok():
    headers = Message()
    headers["Retry-After"] = "0"
    calls = {"n": 0}

    def fetch(x):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "http://api", 429, "Too Many Requests", headers, io.BytesIO()
            )
        return x

    return fetch


def test_429_is_retried():
    result = parallel_map(
        throttled_then_ok(),
        [1],
        retry=Retry.for_http(on=(urllib.error.HTTPError,), backoff=0, jitter=False),
        sequential=True,
    )
    assert result.ok
    assert result.item_results()[0].attempts == 2
```

The same shape works for `httpx.HTTPStatusError` or any client whose
exception exposes the response — pass the appropriate `on=` tuple.

## Checkpoint tests: count the calls

The checkpoint contract is "a rerun never re-spends completed work."
Assert exactly that, with a call counter and `tmp_path`:

```python
def test_rerun_skips_completed_work(tmp_path):
    ckpt = tmp_path / "run.ckpt"
    calls = {"n": 0}

    def work(x):
        calls["n"] += 1
        return x * 2

    parallel_map(work, range(5), checkpoint=ckpt, sequential=True)
    assert calls["n"] == 5

    result = parallel_map(work, range(5), checkpoint=ckpt, sequential=True)
    assert calls["n"] == 5                    # zero new calls
    assert result.values() == [0, 2, 4, 6, 8]
```

And the invalidation side — bumping `checkpoint_version` must refuse to
mix old rows into a new run:

```python
def test_version_bump_invalidates_old_rows(tmp_path):
    ckpt = tmp_path / "run.ckpt"
    parallel_map(str, range(5), checkpoint=ckpt, checkpoint_version="v1", sequential=True)

    with pytest.raises(CheckpointError):
        parallel_map(str, range(5), checkpoint=ckpt, checkpoint_version="v2", sequential=True)
```

## When you do test real concurrency

- **Process/interpreter executors** need module-level functions —
  a lambda or closure defined inside a test will fail to pickle.
  Define workers at test-module scope.
- **Don't assert completion order** on streaming calls unless you
  passed `ordered=True`; completion order is nondeterministic by
  design. Sort, or assert on the set.
- **Assert cost, not just correctness, for pacing.** A rate-limit test
  that only checks results can pass while pacing is broken; assert on
  elapsed time bounds (generously) or on a server-side counter, the way
  [`examples/resilience_demo.py`](https://github.com/oneryalcin/pyarallel/blob/main/examples/resilience_demo.py)
  has the fake server measure the pool's silence.

## Imports used above

```python
import time
import pytest
from pyarallel import (
    Aborted, Cancelled, CheckpointError, Retry, RunStatus, StopToken, parallel_map,
)
```
