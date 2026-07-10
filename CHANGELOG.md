# Changelog

## Unreleased

- New: **`AsyncIterable` sources** — `async_parallel_map` / `_starmap` /
  `_iter` (and the decorator `.map()`/`.starmap()`/`.stream()`) accept
  async sources directly: DB cursors, paginated API generators. No more
  draining a million-row cursor into a list to feed it back out —
  backpressure reaches the producer (one item pulled as one window slot
  frees), a stopped run never pulls from the source again, and an *idle*
  source is never touched — a pull in progress at a stop/close is
  cancelled (your `finally` runs; standard asyncio pipeline semantics);
  final closing stays the caller's job (`aclosing()`). In streaming,
  the pull races beside the workers, so a stalled page fetch delays
  only admission — completed results keep yielding. Bonus over sync
  sources: `timeout=` binds *during* a stuck source await — a dead
  paginator cannot outlive the deadline (unless it swallows
  cancellation). Sync iterables unchanged.
- New: **`Retry.for_http()`** — the 429/`Retry-After` dance, prewired
  and dependency-free. Handles both header dialects (numeric seconds
  *and* HTTP-date — homemade parsers routinely crash or stampede on the
  date form), falls back to exponential backoff on malformed values,
  and duck-types the response (httpx/requests `.response`, aiohttp's
  exception-is-the-response) with no HTTP client import. `statuses=`
  defaults to `{429, 503}`; statusless exceptions in `on=` (connection
  errors) are retried on the type filter alone. Returns a plain frozen
  `Retry`, so the shared-limiter pause and process-executor pickling
  compose unchanged. Cookbook recipes (LLM batch, embeddings, Docker
  registry) now use it; the GitHub recipe documents why its
  header-aware 403 logic can't (status alone can't distinguish
  throttling from no-permission).

## 0.8.0 — 2026-07-10 — the honest-contract release

One deliberate contract-breaking release before 1.0: everywhere the
public vocabulary lied, it now tells the truth. Full rationale and
review trail: `docs/development/plans/v0.8-honest-contract.md`.

**Breaking:**

- `batch_size` → **`window_size`** everywhere. It was always the
  admission window — the maximum number of unresolved items — never a
  chunk size. Passing `batch_size=` now raises `TypeError`.
  Migration: rename the keyword.
- **`result.ok` is now honest**: `True` only when the run *completed*
  (source exhausted) and every task succeeded. Previously an unsized
  run that hit `timeout=` could return only successful items and report
  `ok=True` — a silent truncation. `.values()`, iteration, and indexing
  now raise (`TimeoutError`/`Aborted`) on truncated runs; use
  `.successes()` / `.ok_values()` to consume partial results.
- `ParallelResult` is constructed with `status=RunStatus.…` instead of
  `timed_out=`/`aborted=` bools (constructor use is rare;
  `.timed_out`/`.aborted` remain as derived read properties).
- Decorator per-call **`None` now overrides** the decorator default
  instead of silently inheriting: `fetch.map(urls, rate_limit=None)`
  turns the decorator's rate limit off. Unpassed options inherit as
  before. `executor=None` / `concurrency=None` (previously no-op
  spellings of "inherit") are now type errors with clear runtime
  `ValueError`s.

  ⚠️ **Audit call sites that pass computed `None`s.** The common idiom

  ```python
  rl = user_limit or None            # "no override" ... in 0.7
  fetch.map(urls, rate_limit=rl)     # 0.8: DISABLES rate limiting
  ```

  inherited the decorator's rate limit in 0.7 and **silently disables
  it** in 0.8 — full-speed calls against a throttled API, discovered
  via 429s in production, not test failures. Same for `retry=None`
  (retries off). To mean "inherit", don't pass the keyword:

  ```python
  opts = {} if user_limit is None else {"rate_limit": user_limit}
  fetch.map(urls, **opts)
  ```
- `ItemResult(error=None)` — previously constructed a fake success — now
  raises `ValueError`; an explicitly passed `error` must be an
  `Exception` instance.
- `RateLimit` / `Retry` reject NaN, infinite, and negative numerics at
  construction (`RateLimit(float("nan"))` was silently accepted and
  poisoned the bucket math). Same rule on the engines: `timeout=` /
  `task_timeout=` must be finite and >= 0 (`timeout=float("nan")`
  silently disabled the deadline).
- `.values()`/iteration/indexing check truncation **before** per-item
  failures: a timed-out sized run raises `TimeoutError` (not an
  `ExceptionGroup` of placeholder markers), an aborted run raises
  `Aborted` — one exception surface per event, regardless of input
  sizing. `.failures()` keeps the per-item detail.

**New:**

- `RunStatus` (`COMPLETED` / `TIMED_OUT` / `ABORTED`) exported;
  `result.status` is the source of truth for how a run ended, and
  `result.complete` reports source exhaustion independently of item
  failures.
- `raise_on_failure()` attaches each failure's item index as a PEP 678
  note — provenance in tracebacks without changing exception types
  (`except* ConnectionError` matching untouched).
- Typed item binding: single-parameter functions get `.map()`/`.stream()`
  that check their input types in both mypy and pyright, through both
  decorator spellings. Multi-parameter `.starmap()` stays
  `tuple[Any, ...]` (a ParamSpec cannot be bound to a tuple type —
  prototyped, documented in the plan).
- Checkpoint files are created `0o600` at creation time (POSIX; existing
  files keep their permissions). Corrupted checkpoint rows raise
  `CheckpointError` with delete-to-start-fresh instructions instead of
  leaking raw unpickling errors. Docs now state the trust boundary:
  checkpoints contain pickle — treat the file like code.
- CI: wheel/sdist build + clean-install + py.typed gate, macOS/Windows
  smoke lanes, pyright on the typing assertions. Releases publish via
  PyPI trusted publishing (OIDC + attestations) on `v*` tags; manual
  twine uploads retired. Version single-sourced from
  `pyarallel/__init__.py`.

### Also in this release

- Docs: the single-page real-world guide is now a **Cookbook** — one
  recipe per page (each independently searchable/linkable), with four new
  recipes for workloads where repeating work is expensive or the rate
  budget is shared: bulk GitHub repo changes across an org, Docker
  registry tag cleanup, secrets rotation (identity-keyed resume so a
  crash never double-rotates), and batch NCBI/Entrez fetches. The old
  `user-guide/real-world-patterns/` URL redirects to the cookbook.
- Docs: publish `llms.txt` so AI assistants can ingest and cite the docs.

## 0.7.0 — 2026-07-06

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
- Docs: new comparison page (vs the tenacity+ThreadPoolExecutor
  hand-roll, aiometer, mpire, joblib — including when *not* to use
  pyarallel) and two new recipes (batch LLM calls, CPU-bound fan-out).

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
