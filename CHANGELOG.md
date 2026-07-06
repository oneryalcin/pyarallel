# Changelog

## Unreleased — v0.7

**The parallelism-future bets.** Free-threaded CPython proven per-commit,
and a third executor for Python 3.14+.

- New: `executor="interpreter"` (Python 3.14+, PEP 734
  `InterpreterPoolExecutor`) — true CPU parallelism for pure-Python work
  on standard GIL builds, in one OS process, with ~30 ms workers instead
  of fork/spawn. Process constraint rules apply (importable module-level
  functions, picklable retry, no shared limiter, no contextvars), plus
  two interpreter-only rules: `__main__`-defined functions and
  `worker_init` are rejected upfront with an actionable error
  (sub-interpreters cannot see the parent's `__main__`), and
  `max_tasks_per_worker` is rejected (no worker recycling). Workers
  mirror the parent's `sys.path` — same behavior as multiprocessing
  spawn — so targets importable via runtime path additions resolve.
  Known boundary: C extensions without subinterpreter support (numpy
  among them) fail with `ImportError` inside workers; use
  `executor="process"` for those.
- The version gate lives on the pool path only: `sequential=True` with
  `executor="interpreter"` still runs inline on 3.12/3.13 — production
  configs stay one flag away from debuggable.
- `ExecutorType` widened to
  `Literal["thread", "process", "interpreter"]` — downstream code
  matching exhaustively on it will see the new member.
- CI now tests 3.14 and free-threaded 3.13t/3.14t (suite runs with the
  GIL off; a guard step fails the job if the GIL is silently re-enabled).
  New `test` dependency group for minimal installs.
- Docs: free-threading note (measured 2.4× CPU-bound at 4 workers on
  3.14t vs 1.0× under the GIL); interpreter executor guidance.

Plan and review history:
[v0.7 Plan](docs/development/plans/v0.7-free-threaded-and-interpreters.md).

## 0.6.0 — 2026-07-06

**One engine for every execution path.** All collected maps — sync and
async — now run through the same windowed engine that streaming and
`max_errors` already used. Behavior changes (pre-1.0, no deprecation
cycle):

- `batch_size` means one thing everywhere: the in-flight admission
  window (default `2 × workers` sync, `2 × concurrency` async). Chunk
  barriers no longer exist anywhere — a slow item never stalls the
  items behind it.
- Collected maps consume input lazily, one window ahead — generators
  are never materialized. The plain no-kwargs call moves from eager
  upfront submission to windowed admission (benchmarked: parity on
  ms-scale tasks; see the v0.6 plan for numbers).
- No-drain on stop, everywhere: after a timeout or abort the source
  iterator is never touched again. Unsized inputs return a *shorter*
  result instead of drained-and-appended failure placeholders.
- New: `ParallelResult.timed_out` / `.aborted` report how the run ended
  (at most one is set — first stop reason wins; both show up in the
  repr). This is the reliable truncation signal for unsized inputs,
  where a timed-out run can contain only successes.
- `on_progress` with unsized inputs reports items *admitted* so far as
  `total` (previously the final total, because input was materialized).
  Pass a sized input for a real total.
- Source-iterator errors surface mid-run instead of at materialization,
  and propagate promptly: queued work is cancelled, but sync tasks
  already running cannot be interrupted and may complete — side effects
  included — in the background. Materialize the input first
  (`list(items)`) if you need no-work-after-error guarantees, or use
  the async API (tasks are cancelled and awaited).
- Exception shape: errors raised from callbacks on the async plain path
  propagate plain — the `ExceptionGroup` wrapper went away with the
  removed `asyncio.TaskGroup`.
- `parallel_starmap` no longer materializes its input — generators of
  argument tuples stay lazy.
- Total `timeout=` now binds during cached checkpoint admission too: a
  checkpoint-heavy run can no longer overrun the deadline unnoticed.
- Fixed: a `TimeoutError` raised by the source iterable itself is an
  input error and propagates — it is no longer repackaged as deadline
  expiry.

Full contract and review history:
[v0.6 Engine Unification Plan](docs/development/plans/v0.6-engine-unification.md).

## 0.5.0 — 2026-07-05

Structural quality: sliding-window streaming (`ordered=True`, streaming
`on_progress`), `max_errors` early abort, `ItemResult.attempts`/
`.duration`, `ParallelResult.ok_values()`, `checkpoint_key=` (schema v2),
`sequential=True` debug mode, async total `timeout=`, contextvars
propagation, `worker_init=`/`max_tasks_per_worker=`, typed decorator
options via `Unpack[TypedDict]`. Details:
[v0.5.0 Plan](docs/development/plans/v0.5.0.md). Not published to PyPI.

## 0.4.0 — 2026-06

Repositioned as the fan-out layer for rate-limited APIs: server-driven
backoff (`retry_if`/`wait_from`), shareable `Limiter`, real token bucket
(`burst=`), `checkpoint=` resume, strict typing. Tag only, not published.
