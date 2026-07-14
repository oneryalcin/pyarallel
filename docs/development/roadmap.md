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

## Current (0.10.0 on PyPI)

- `parallel_map()` / `parallel_starmap()` / `parallel_iter()` and async
  mirrors; `@parallel` / `@async_parallel` decorators with `.map()` /
  `.starmap()` / `.stream()`
- One windowed engine for every non-sequential path: bounded in-flight
  `window_size`, lazy input, no batch barriers, source never drained
  after a stop
- `RateLimit` (token bucket with `burst`) + shareable `Limiter` (one
  budget across calls, functions, sync and async)
- `Retry` with backoff, jitter, `on`/`retry_if` filtering, and
  `wait_from` server-driven waits that pause the shared limiter;
  `Retry.for_http()` prewires the 429/`Retry-After` dance (both header
  dialects, no HTTP client import)
- `checkpoint=` / `checkpoint_key=` / `checkpoint_version=` — SQLite
  resume, fail-closed function identity + semantic version token,
  `0o600` creation, corruption → `CheckpointError`
- `max_errors=` early abort; `timeout=` total (sync + async),
  `task_timeout=` (async); `stop=StopToken()` cooperative cancel
  (`RunStatus.CANCELLED`, checkpoint rows kept)
- `AsyncIterable` sources with end-to-end backpressure; the streaming
  pull races beside the workers
- `ParallelResult.item_results()` — per-item `attempts`/`duration` on
  collected maps
- `on_result=` — live success/failure callbacks on collected map and
  starmap APIs, delivered on the driver in completion order
- `item_key=` / `ItemResult.key` — application identity across collected,
  callback, starmap, and streaming results; `checkpoint_key=` supplies the
  identity automatically when no separate item key is given
- The resilience demo (`examples/resilience_demo.py`) — headline claims
  self-asserted in CI against the installed wheel
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

## v0.8 — Honest contract *(shipped in 0.8.0)*

One deliberate contract-breaking release; everything conceptually wrong
with the public API fixed together, before 1.0 freezes it. Scope,
ground truth, and amendments: [v0.8 plan](plans/v0.8-honest-contract.md).
Delivered: `window_size` rename, `RunStatus`/`.ok` redesign, result
invariant hardening, failure provenance, typed unary `.map()`, decorator
override sentinel, policy numeric validation, checkpoint
security/corruption handling, CI/release trust (wheel gate, OS lanes,
trusted publishing), this roadmap rewrite.

## v0.9 — Real-job UX *(shipped in 0.9.0)*

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
- **Cooperative stop** *(done — on `v0.9-cooperative-stop`)* — a stop
  token that ceases admission, cancels queued work, preserves
  checkpoints, and reports `RunStatus.CANCELLED`. Honest about running
  sync threads being uninterruptible.
- **Collected-result metadata** *(done — on `v0.9-collected-metadata`)* —
  `ParallelResult.item_results()` exposes the `attempts`/`duration`
  already computed per item (previously discarded by collected maps),
  reusing the streaming `ItemResult` type. Smallest surface first; a
  stats object must be earned by concrete use cases.
- **The resilience demo** *(done — on `v0.9-resilience-demo`)* —
  deterministic, zero-credential, local: a fake API enforcing a quota,
  429 + `Retry-After` pausing the whole pool (gap measured
  server-side), a real SIGKILL mid-run, a rerun that resumes from
  SQLite, and a final report of avoided calls. Proves the entire
  thesis — and doubles as the launch write-up's centerpiece.

Design spikes (pre-freeze checks, no shipping):

- **Weighted/composite quotas** (requests/min + tokens/min, weighted
  acquisition) — verify the `Limiter` surface doesn't preclude it; 1.1
  headline at most.
- **Streaming checkpoint** — "cached computation" ≠ "sink committed";
  needs crisp at-least-once replay semantics before any implementation.

## v0.10 — Prove and package *(shipped in 0.10.0)*

The last stop before freeze — the items the external review's original
v0.10 proposed that weren't absorbed into v0.8/v0.9, plus two v0.9
stragglers. Nothing here changes the API's meaning; the launch write-up
can proceed in parallel.

- **Decorator default surface** — DECIDED: widen. `@parallel` /
  `@async_parallel` accept `retry`, `timeout` (+ `task_timeout` async),
  `window_size`, `max_errors`, and `on_progress` as defaults — they are
  properties of the function's behavior. `checkpoint*` and `stop` stay
  per-call-only with documented reasoning: a checkpoint file names a
  run (a shared default file means duplicate keys and wrong resumes)
  and a stop token is a campaign latch. Presence-sentinel merge rules
  (v0.8) apply unchanged.
- **Resource-lifecycle examples** — one reusable HTTP client around the
  fan-out; fix the `AsyncClient`-per-item pattern in README/cookbook
  (flagged in the very first external review).
- **Committed benchmark lab** *(done — on `v0.10-benchmark-lab`)* — the
  free-threading/interpreter numbers (2.4×/3.4×) came from throwaway
  scripts on one machine; `benchmarks/bench.py` is now a single
  stdlib-only harness whose benchmarks map 1:1 to those claims, with
  recorded runs in `benchmarks/RESULTS.md`. Not in CI (shared runners
  make wall-clock claims dishonest) — it's for humans re-verifying on
  their own hardware.
- **Executable cookbook examples** *(done — on `v0.10-executable-docs`)*
  — all 110 user-facing doc blocks compile-gated and API-shape-gated
  (keywords checked against real signatures); the flagship LLM recipe
  and streaming ETL execute end to end against fakes in CI.
- **Compatibility & deprecation policy** *(done — on
  `v0.10-freeze-prep`)* — [Compatibility & Policies](policies.md):
  semver from 1.0, deprecation windows, checkpoint persistence
  guarantees, Python-floor policy, and what "public" means.
- **Public API snapshot in CI** *(done — on `v0.10-freeze-prep`)* —
  `tests/api_snapshot.txt` renders the whole exported surface;
  accidental changes are a red build, deliberate ones a reviewed diff.

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
- The comparison page, cookbook, and one good write-up are published.
  [What Happens When Your 50,000-Item API Job Dies?](https://oneryalcin.hashnode.dev/what-happens-when-your-50-000-item-api-job-dies)
  demonstrates the server-driven-backoff and crash/resume case against a
  real public workload. The publication criterion is complete.

## Post-1.0 candidates

Tracked in one place —
[issue #32](https://github.com/oneryalcin/pyarallel/issues/32) — so 1.0
ships lean. All are additive (none would require a breaking change,
which is why none block the freeze). `on_result=` and stable item identity
(`item_key=` / `ItemResult.key`) were selected from this list and are
implemented in Unreleased. The next candidate selected for planning is
read-only checkpoint inspection. Weighted/composite quotas (see the
[design spike record](plans/design-spikes-pre-1.0.md)) and a reusable
execution session remain candidates. Selection happens on real user
feedback, not speculation.

## Distribution

Recipes (Cookbook) and the comparison page shipped with 0.7. The required
write-up is now published:
[What Happens When Your 50,000-Item API Job Dies?](https://oneryalcin.hashnode.dev/what-happens-when-your-50-000-item-api-job-dies),
a concrete server-driven-backoff and crash/resume case study. The 1.0
distribution criterion is complete; further promotion is ongoing product
work, not a release blocker.

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
