# Roadmap

## Current (v0.2.0)

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
- **`max_tasks_per_worker`** — restart process workers after N tasks to prevent memory leaks in long-running pools. Passes through to `ProcessPoolExecutor(max_tasks_per_child=)`.
- **tqdm/rich integration** — built-in progress bar support

### Exploring

- **Free-threading support** — leverage Python 3.13+ no-GIL for true thread parallelism
- **Resource-aware scheduling** — adaptive worker counts based on system load
- **Per-task timeout (sync)** — Python threads cannot be cancelled mid-execution, so per-task timeout in sync is a hard problem with no clean solution. The async API supports `task_timeout` natively via `asyncio.wait_for`. For sync, use `timeout=` for total wall-clock limits and put timeouts inside your function (e.g., `requests.get(url, timeout=5)`).

---

Want to contribute? Check out our [Contributing Guide](CONTRIBUTING.md)!
