# Roadmap

This roadmap should be read together with the [DevX Principles](devx-principles.md).

## Positioning

Pyarallel is **the fan-out layer for rate-limited APIs**: apply one function to
many inputs against services that throttle you — LLM calls, embeddings,
scraping, SaaS APIs — with the policies that workload actually needs built in.

Today everyone hand-rolls the same stack for this job: a semaphore, tenacity,
a rate limiter, ad-hoc 429 handling, and a prayer that the job doesn't crash at
item 40,000. Each piece exists as a separate library; nothing owns the
combination. Pyarallel's structural advantage is owning **both the retry policy
and the concurrency/rate gate in one place** — which is exactly what makes
server-driven backoff and resumable jobs possible, and what a pile of separate
libraries can't do cleanly.

The library stays general-purpose (`parallel_map` over anything), but every
roadmap decision is judged against one question: *does this make pyarallel
decisively better than hand-rolling for API fan-out?*

## Current (v0.5.0)

- `parallel_map()` / `.map()` — explicit parallel execution over iterables
- `parallel_starmap()` / `.starmap()` — multi-argument parallel execution
- `parallel_iter()` / `.stream()` — sliding-window streaming: bounded
  in-flight window, lazy input, no batch barriers; `ordered=True` for
  input-order yields with a window-bounded reorder buffer
- `@parallel` and `@async_parallel` decorators — preserve function signature *and types*; per-call options typed once per engine via `Unpack[TypedDict]`
- Full async support via `asyncio.TaskGroup`
- `ParallelResult` with structured error handling (`ExceptionGroup`), `ok_values()` for the partial-failure path
- `ItemResult.attempts` / `.duration` — per-item accounting on the streaming APIs
- `RateLimit` — token-bucket spec with `burst` capacity
- `Limiter` — shareable rate-limit runtime: one budget across calls, functions, sync and async
- `Retry` — per-item retry with backoff, jitter, exception filtering, `retry_if` predicate, and `wait_from` server-driven waits that pause the shared limiter
- `max_errors=` — abort cheaply once N items fail (windowed admission; unrun items marked `Aborted`)
- `checkpoint=` — resumable runs (SQLite-backed, stdlib only); `checkpoint_key=` keys rows by item identity so evolving inputs keep completed work
- `sequential=True` — debug mode: run inline, no pool, real stack traces
- Contextvars propagate into thread workers (correlation IDs survive)
- `worker_init=` / `max_tasks_per_worker=` — worker lifecycle control
- `batch_size` — in-flight window for streaming; chunking for collected maps
- Progress callbacks via `on_progress` (collected and streaming)
- Timeout support (`timeout` total on sync *and* async, `task_timeout` async per-task)
- Instance method support via descriptor protocol
- `mypy --strict` clean, with typed-interface assertions in CI (`tests/typing_assertions.py`)

---

## v0.4 — Own the niche *(shipped in 0.4.0)*

The features that make pyarallel decisively better than hand-rolling.
Nothing else ships before these.

### 1. Server-driven backoff

`Retry` today only sees exception *types*, and the rate limiter runs at a
blind fixed rate. Real APIs speak 429 + `Retry-After`. Add:

```python
Retry(
    attempts=5,
    on=(httpx.HTTPStatusError,),
    retry_if=lambda exc: exc.response.status_code in (429, 503),
    wait_from=lambda exc: parse_retry_after(exc),  # seconds, or None → backoff
)
```

- `retry_if` — predicate on the exception instance, composable with `on`.
- `wait_from` — extract the server-mandated wait (e.g. `Retry-After` header)
  from the exception; `None` falls back to exponential backoff.

This is the killer feature: tenacity can't do it *well* because it doesn't own
the concurrency layer — one task learning "back off 30s" should be able to
inform the shared limiter, not just its own sleep. Design spike required for
that coupling (see Limiter below); the `retry_if`/`wait_from` API itself is
straightforward.

### 2. Shared `Limiter`

Real quotas are per-API-key, not per-`.map()`-call. Today every call builds a
private token bucket, so two concurrent maps — or `.map()` in a loop — blow
the quota. Split the concept:

- `RateLimit` stays the immutable **spec** (current behavior: passing it
  creates a private limiter for that call).
- New `Limiter(RateLimit(...))` is a shareable **instance**: pass the same
  object to multiple functions and calls, sync or async, and they draw from
  one budget.

```python
limiter = Limiter(RateLimit(100, "minute"))
users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)  # same quota
```

Without this, rate limiting is honestly broken for its main use case.

### 3. Real token bucket (burst)

What we call a token bucket is an even-spacing pacer: `RateLimit(100,
"minute")` issues one request per 0.6s with no burst. An API allowing 100/min
lets you fire 100 *now*. Add burst capacity:

```python
RateLimit(100, "minute", burst=20)   # up to 20 immediately, then refill pace
```

Default stays `burst=1` (current smooth pacing) — it's the safest default
against secondary per-second limits — but the option must exist, and the docs
must stop calling the pacer a token bucket until it is one.

### 4. Checkpoint / resume

A 50k-item embedding job that dies at item 40k restarts from zero. No
micro-library offers resume; it's the feature that turns approval into
adoption.

```python
result = parallel_map(embed, chunks, checkpoint="embeddings.ckpt")
# after a crash, rerun the same line: completed items load from disk,
# only the remainder executes
```

- SQLite-backed (stdlib — zero-deps preserved), keyed by item index plus an
  input fingerprint to detect changed inputs.
- Results must be picklable; documented honestly as the constraint.
- Failures are not checkpointed — a resumed run retries them.

### 5. Typing that matches the principles

DevX principle #3 promises strong typing; the implementation returns `Any`.
`items: Iterable[Any]` doesn't bind to `fn`'s parameter, and the decorators
erase everything — `fetch.map(urls)` has zero autocomplete or checking.

- `parallel_map[T, R](fn: Callable[[T], R], items: Iterable[T]) -> ParallelResult[R]`
- Generic `_ParallelFunc` / `_AsyncParallelFunc` so decorated functions keep
  their signature and `.map()` returns `ParallelResult[R]`.
- `mypy --strict` on the package and on a typing test file in CI.

Pre-1.0 is the time (principle #7); this may not survive as a pure-addition
change and that's acceptable now.

---

## v0.5 — Structural quality *(shipped in 0.5.0)*

Implementation contract and review-amendment history:
[v0.5.0 Plan](plans/v0.5.0.md). Delivered: sliding-window streaming with
`ordered=True` and streaming `on_progress`; `max_errors` early abort
with windowed admission; `ItemResult.attempts`/`.duration` and
`ParallelResult.ok_values()`; `checkpoint_key=` with schema-v2
type-tagged keys; `sequential=True`; async total `timeout=`;
contextvars propagation; `worker_init=`; `max_tasks_per_worker=`;
decorator signature dedup via `Unpack[TypedDict]`. Two four-review
cycles (Codex ×2, independent code review, adversarial design review)
ran during development; every finding is triaged in the plan document.

---

## v0.6 — Forward bets

### Free-threaded Python

CI on 3.13t/3.14t free-threaded builds; document what actually parallelizes.
No-GIL makes `executor="thread"` viable for CPU-bound work, and pyarallel is
positioned as the ergonomic layer over it. Cheap to verify, real story to tell.

### `executor="interpreter"`

Python 3.14's `InterpreterPoolExecutor` (PEP 734) as a third executor —
near-pass-through implementation, guarded by version check.

### Collected-map engine unification

The plain collected path still batches with a barrier while the
`max_errors` path admits through a window (documented coupling, flagged
in the v0.5 design review). Unify collected maps onto the windowed
engine so `batch_size` means one thing everywhere.

---

## v1.0 — Freeze

Criteria, not features:

- API surface frozen; anything conceptually wrong was fixed in 0.4–0.6
  (principle #7 — pre-v1 is the time).
- Typing complete, `mypy --strict` clean.
- Comparison page shipped: vs tenacity + semaphore hand-roll, aiometer, mpire,
  joblib — including honest "when *not* to use pyarallel."
- Recipes shipped for the workloads people actually search for: embed 100k
  documents against a rate-limited API with resume; polite scraping at scale;
  CPU-bound fan-out with processes.

## Distribution

Not a code tier, but the step that decides whether any of this matters —
sequenced after v0.4 lands, not before:

1. **Recipes page** targeting the niche by name (LLM calls, embeddings,
   scraping). These are the queries people actually type.
2. **Comparison page** (see v1.0 criteria) — credibility through honesty.
3. **One good write-up** (blog / Show HN / r/Python) demonstrating the
   before/after with server-driven backoff and resume. Without this step the
   engineering work is unfalsifiable effort.

---

## Not Planned

Things we've considered and decided against. Pyarallel is a parallelization
library, not a distributed computing framework.

- **`.then()` chaining** — the first step toward a pipeline framework we've
  promised not to build. Use two calls and a variable.
- **Shared kwargs** (`kwargs={...}` passed to every call) — `functools.partial`
  is one line and well-known.
- **Circuit breaker** as a separate abstraction — `max_errors` plus
  server-driven backoff covers the real use case without new machinery.
- **Telemetry / metrics dashboards** — use your existing observability stack.
  We give you `on_progress`, result metadata, and `ParallelResult`; instrument
  from there.
- **CLI tools** — no dashboard, no profiler. Use `htop`, `py-spy`, or your IDE.
- **Logging framework** — Python's `logging` module is fine.
- **Smart scheduling** (priority queues, deadlines, DAGs) — use Celery,
  Airflow, or Prefect.
- **Dead letter queues** — message queue concept, not a parallelization concern.
- **Resource monitoring** (memory, CPU, disk, network) — OS-level. `batch_size`
  is the right abstraction for memory control.
- **Resource-aware scheduling** — adaptive worker counts based on system load
  is autoscaling, a different problem domain. Set `workers` explicitly.
- **Plugin / hook system** — slippery slope. Callbacks cover the real use case.
- **Per-task timeout (sync)** — Python threads can't be cancelled
  mid-execution. Use `timeout=` for total wall-clock, put timeouts inside your
  function, or use `async_parallel_map` with `task_timeout=`.

---

Want to contribute? Check out our [Contributing Guide](CONTRIBUTING.md)!
