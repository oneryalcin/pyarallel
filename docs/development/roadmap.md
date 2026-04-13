# Roadmap

This roadmap should be read together with the [DevX Principles](devx-principles.md).

## Current (v0.3.0)

- `parallel_map()` / `.map()` — explicit parallel execution over iterables
- `parallel_starmap()` / `.starmap()` — multi-argument parallel execution
- `parallel_iter()` / `.stream()` — streaming results in completion order, constant memory
- `@parallel` and `@async_parallel` decorators — preserves function signature
- Full async support via `asyncio.TaskGroup`
- `ParallelResult` with structured error handling (`ExceptionGroup`)
- `RateLimit` — token bucket rate limiting (sync and async)
- `Retry` — per-item retry with exponential backoff, jitter, and exception filtering
- `batch_size` — process items in chunks to control memory
- Progress callbacks via `on_progress`
- Timeout support (`timeout` for sync total, `task_timeout` for async per-task)
- Instance method support via descriptor protocol

## Planned

### Near Term

- **`max_errors`** — stop early after N failures instead of processing all items. When hitting a dead API, don't waste 10,000 calls when the first 10 all failed. Returns partial results.
- **Ordered streaming** — `parallel_iter(..., ordered=True)` yields results in input order instead of completion order. Essential for ETL, CSV writing, and pipelines where output order must match input.
- **`async_parallel_map` with `timeout=`** — sync `parallel_map` has total wall-clock timeout, async doesn't. Add parity so async workloads can enforce an overall deadline.
- **Context variable propagation** — copy `contextvars.Context` into worker threads so structured logging (correlation IDs) and request tracing work correctly. Without this, correlation IDs and request traces silently disappear in worker threads.
- **`max_tasks_per_worker`** — restart process workers after N tasks to prevent memory leaks in long-running pools. Passes through to `ProcessPoolExecutor(max_tasks_per_child=)`.
- **Sequential/testing mode** — `workers=0` or `sequential=True` runs everything in the calling thread. Makes debugging deterministic, breakpoints work, stack traces are readable.
- **Worker initializer** — `worker_init=` callback run once per worker thread/process (e.g., open a DB connection, load a model). Passes through to executor `initializer=`.
- **`on_progress` for streaming** — `parallel_iter` currently has no progress callback. Add it for parity with `parallel_map`.

### Exploring

- **`.then()` chaining** — `parallel_map(fn, items).then(fn2)` for lightweight pipelines without building a DAG engine. Chain a second parallel operation on results without extracting values manually.
- **Circuit breaker** — composable with `Retry` for API-heavy workloads. When a downstream service is failing, stop hammering it and fail fast for a cooldown period.
- **Shared kwargs** — `kwargs={"timeout": 5}` passed to every `fn` call alongside the item. Convenience over `functools.partial`, but partial is one line and well-known.
- **Free-threading support** — leverage Python 3.13+ no-GIL for true thread parallelism.
- **Per-task timeout (sync)** — Python threads can't be cancelled mid-execution, so this is a hard problem. For now, use `timeout=` for total wall-clock or put timeouts inside your function.

## Not Planned

Things we've considered and decided against. Pyarallel is a parallelization library, not a distributed computing framework.

- **Telemetry / metrics dashboards** — use your existing observability stack (OpenTelemetry, Prometheus, Datadog). We give you `on_progress` and `ParallelResult`; instrument from there.
- **CLI tools** — no dashboard, no profiler. Use `htop`, `py-spy`, or your IDE.
- **Logging framework** — Python's `logging` module is fine. We don't add our own.
- **Smart scheduling** (priority queues, deadlines, DAGs) — use Celery, Airflow, or Prefect.
- **Dead letter queues** — message queue concept, not a parallelization concern.
- **Resource monitoring** (memory, CPU, disk, network) — OS-level. `batch_size` is the right abstraction for memory control.
- **Resource-aware scheduling** — adaptive worker counts based on system load. This is autoscaling, a different problem domain. Set `workers` explicitly based on your environment.
- **Plugin / hook system** — slippery slope. `on_progress` callback covers the real use case.

---

Want to contribute? Check out our [Contributing Guide](CONTRIBUTING.md)!
