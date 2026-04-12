# Roadmap

## Current (v0.2.0)

- `parallel_map()` — explicit parallel execution over iterables
- `@parallel` decorator with `.map()` — preserves function signature
- `async_parallel_map()` and `@async_parallel` — full async support
- `ParallelResult` with structured error handling (`ExceptionGroup`)
- `RateLimit` — token bucket rate limiting (sync and async)
- `Retry` — per-item retry with exponential backoff, jitter, and exception filtering
- `batch_size` — process items in chunks to control memory
- Progress callbacks via `on_progress`
- Timeout support (total for sync, per-task for async)
- Instance method support via descriptor protocol

## Planned

### Near Term

- **`starmap` support** — map over multi-argument inputs
- **Result streaming** — yield results as they complete (iterator API)
- **tqdm/rich integration** — built-in progress bar support

### Medium Term

- **Per-task timeout (sync)** — individual task deadlines for thread/process pools

### Exploring

- **Free-threading support** — leverage Python 3.13+ no-GIL for true thread parallelism
- **Resource-aware scheduling** — adaptive worker counts based on system load

---

Want to contribute? Check out our [Contributing Guide](CONTRIBUTING.md)!
