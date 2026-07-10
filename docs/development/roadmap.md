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

The best direction toward 1.0 is not more kinds of parallelism — it is being
**the most trustworthy single-machine runtime for expensive, rate-limited
fan-out jobs**.

## Current (0.7.0 on PyPI, 0.8 on main)

- `parallel_map()` / `parallel_starmap()` / `parallel_iter()` and async
  mirrors; `@parallel` / `@async_parallel` decorators with `.map()` /
  `.starmap()` / `.stream()`
- One windowed engine for every non-sequential path: bounded in-flight
  `window_size`, lazy input, no batch barriers, source never drained
  after a stop
- `RateLimit` (token bucket with `burst`) + shareable `Limiter` (one
  budget across calls, functions, sync and async)
- `Retry` with backoff, jitter, `on`/`retry_if` filtering, and
  `wait_from` server-driven waits that pause the shared limiter
- `checkpoint=` / `checkpoint_key=` — SQLite resume, fail-closed
  function identity, `0o600` creation, corruption → `CheckpointError`
- `max_errors=` early abort; `timeout=` total (sync + async),
  `task_timeout=` (async)
- `ParallelResult` with `RunStatus` (`.ok` = completed AND all
  succeeded; truncated runs never read as clean), PEP 678 failure
  provenance; `ItemResult` with `attempts`/`duration`
- `sequential=True` debug mode; contextvars propagation; `worker_init=`
  / `max_tasks_per_worker=`
- Executors: `thread` / `process` / `interpreter` (3.14+, PEP 734);
  free-threaded 3.13t/3.14t tested per-commit
- Typing: `mypy --strict` + pyright; single-arg functions bind item
  types on `.map()`/`.stream()`
- CI: 3.12/3.13/3.14 + free-threaded + macOS/Windows smoke +
  wheel/sdist clean-install gate; releases via PyPI trusted publishing

Shipped-version history and full review trails live in the plan
documents: [v0.5](plans/v0.5.0.md),
[v0.6 engine unification](plans/v0.6-engine-unification.md),
[v0.7 free-threaded & interpreters](plans/v0.7-free-threaded-and-interpreters.md),
[v0.8 honest contract](plans/v0.8-honest-contract.md).

---

## v0.8 — Honest contract *(in progress on `v0.8-honest-contract`)*

One deliberate contract-breaking release; everything conceptually wrong
with the public API fixed together, before 1.0 freezes it. Scope,
ground truth, and amendments: [v0.8 plan](plans/v0.8-honest-contract.md).
Delivered: `window_size` rename, `RunStatus`/`.ok` redesign, result
invariant hardening, failure provenance, typed unary `.map()`, decorator
override sentinel, policy numeric validation, checkpoint
security/corruption handling, CI/release trust (wheel gate, OS lanes,
trusted publishing), this roadmap rewrite.

## v0.9 — Real-job UX

Features earned by real workloads, sequenced after the contract is
honest:

- **`Retry.for_http()`** *(done — on `v0.9-retry-for-http`)* —
  dependency-free helper for the 429/`Retry-After` dance every recipe
  hand-rolled: numeric *and* date-form `Retry-After`, clean fallback on
  malformed values, duck-typed response extraction, no HTTP client
  import.
- **`AsyncIterable` sources** *(done — on `v0.9-async-sources`)* —
  async cursors and paginated generators with end-to-end backpressure
  (no-drain, closure, cancellation tests); `timeout=` binds during a
  stuck source await.
- **`checkpoint_version=`** *(done — on `v0.9-checkpoint-version`)* —
  a user-supplied semantic token (model name, prompt SHA…) joining
  checkpoint identity, for the config automatic function inspection
  cannot see. Fails closed on change, both tokens in the error.
- **Cooperative stop** — a stop token that ceases admission, cancels
  queued work, preserves checkpoints, and reports
  `RunStatus.CANCELLED`. Honest about running sync threads being
  uninterruptible.
- **Collected-result metadata** *(done — on `v0.9-collected-metadata`)* —
  `ParallelResult.item_results()` exposes the `attempts`/`duration`
  already computed per item (previously discarded by collected maps),
  reusing the streaming `ItemResult` type. Smallest surface first; a
  stats object must be earned by concrete use cases.
- **Decorator default surface** — decide whether decorator defaults
  widen beyond `workers`/`executor`/`rate_limit` (e.g. `retry=`), or the
  asymmetry is documented as intentional.
- **Resource-lifecycle examples** — one reusable HTTP client around the
  fan-out; fix the `AsyncClient`-per-item pattern in README/cookbook.
- **The resilience demo** — deterministic, zero-credential, local: a
  fake API enforcing a quota, 429 + `Retry-After` pausing the whole
  pool, a mid-run kill, a rerun that resumes from SQLite, and a final
  report of avoided calls. One demo that proves the entire thesis — and
  doubles as the launch write-up's centerpiece.

Design spikes (pre-freeze checks, no shipping):

- **Weighted/composite quotas** (requests/min + tokens/min, weighted
  acquisition) — verify the `Limiter` surface doesn't preclude it; 1.1
  headline at most.
- **Streaming checkpoint** — "cached computation" ≠ "sink committed";
  needs crisp at-least-once replay semantics before any implementation.

## v1.0 — Freeze

Criteria, not features. No headline feature ships in 1.0.

- Public vocabulary internally consistent; no known "documented
  limitation, fix later" contract issues.
- Typing complete where expressible; narrowings documented
  (multi-arg `.starmap()`, bound methods).
- Compatibility, deprecation, and checkpoint-persistence policies
  documented.
- Release artifacts and docs generated from the same tag; API/typing
  snapshots enforced in CI.
- The comparison page, cookbook, and one good write-up (blog / Show HN /
  r/Python) published — distribution is a 1.0 criterion, not an
  afterthought.

## Distribution

Recipes (Cookbook) and the comparison page shipped with 0.7. Remaining:
**one good write-up** demonstrating server-driven backoff + resume
(and/or the free-threading angle), submitted to the channels where this
niche looks for tools. Without it the engineering work is unfalsifiable
effort.

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
- **Resource monitoring** (memory, CPU, disk, network) — OS-level. `window_size`
  is the right abstraction for memory control.
- **Resource-aware scheduling** — adaptive worker counts based on system load
  is autoscaling, a different problem domain. Set `workers` explicitly.
- **Plugin / hook system** — slippery slope. Callbacks cover the real use case.
- **Per-task timeout (sync)** — Python threads can't be cancelled
  mid-execution. Use `timeout=` for total wall-clock, put timeouts inside your
  function, or use `async_parallel_map` with `task_timeout=`.
- **Additional executor types** — thread/process/interpreter cover the
  ground; more executors don't deepen the niche.
- **Python 3.11 support** — data-gated, not assumed: measure installer
  rejections and actual demand first.

---

Want to contribute? Check out our [Contributing Guide](CONTRIBUTING.md)!
