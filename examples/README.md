# Pyarallel Examples

These scripts are runnable examples for the current `pyarallel` API.

## Prerequisites

Install from PyPI:

```bash
pip install pyarallel
```

Or from source:

```bash
uv sync --group dev
```

## Examples Overview

### 01_basic_usage.py

Shows the core pattern: direct calls stay scalar, and `.map(...)` runs work in parallel.

```bash
python examples/01_basic_usage.py
```

### 02_api_calls_with_rate_limiting.py

Shows thread-based I/O with numeric rate limits and `RateLimit(...)` objects.

```bash
python examples/02_api_calls_with_rate_limiting.py
```

### 03_cpu_bound_processing.py

Shows when to use `executor="process"` for CPU-heavy work.

```bash
python examples/03_cpu_bound_processing.py
```

### 04_batch_processing.py

Shows how to control memory by passing `window_size` to `.map(...)`.

```bash
python examples/04_batch_processing.py
```

### 05_configuration.py

Shows decorator defaults, per-call overrides, retry, rate limiting, and timeout behavior.

```bash
python examples/05_configuration.py
```

### 06_resilient_api_jobs.py

The v0.5 overnight-job toolkit: sliding-window streaming with
`ordered=True` and per-item `attempts`/`duration`, `max_errors` early
abort (a dead API costs tens of calls, not thousands), `checkpoint_key=`
resume that survives evolving inputs, and the `sequential=True` debug
flag.

```bash
python examples/06_resilient_api_jobs.py
```

### 07_production_api_job.py

**The golden template** — the shape to copy for a real production API job,
runnable against a built-in local fake API (zero credentials): one reusable
HTTP client, `Retry.for_http`, a shared `Limiter`, checkpoint with a stable
`checkpoint_key` and `checkpoint_version`, `max_errors` early abort,
SIGTERM/SIGINT → `StopToken` graceful shutdown, and explicit `RunStatus`
handling of partial results. Self-asserting; runs in CI against the built
wheel.

```bash
python examples/07_production_api_job.py
```

### resilience_demo.py

The headline claims, proven on your laptop in ~10 seconds: a real 429 with
`Retry-After` pauses the *whole* pool (the server measures the silence), a
client-side `RateLimit` prevents throttling entirely, and a checkpointed run
that gets SIGKILLED mid-flight resumes without repeating paid-for calls.
Self-asserting; runs in CI against the built wheel.

```bash
python examples/resilience_demo.py
```

## Quick Reference

### Basic Pattern

```python
from pyarallel import parallel

@parallel(workers=4)
def process_item(item):
    return item * 2

single = process_item(5)
many = process_item.map([1, 2, 3, 4, 5])
```

### Common Parameters

| Parameter | Where | Example |
|-----------|-------|---------|
| `workers` | `@parallel(...)` or `.map(...)` | `workers=4` |
| `executor` | `@parallel(...)`, `.map(...)`, or `parallel_map(...)` | `executor="process"` |
| `window_size` | `.map(...)` | `window_size=100` |
| `rate_limit` | `@parallel(...)` or `.map(...)` | `rate_limit=10` or `RateLimit(100, "minute")` |
| `retry` | `.map(...)` or `parallel_map(...)` | `retry=Retry(attempts=3)` |
| `timeout` | `.map(...)` or `parallel_map(...)` | `timeout=30.0` |
| `max_errors` | `.map(...)`, `.stream(...)`, or engine functions | `max_errors=10` |
| `checkpoint` / `checkpoint_key` | `.map(...)` or `parallel_map(...)` | `checkpoint="run.ckpt", checkpoint_key=lambda u: u.id` |
| `sequential` | sync `.map(...)` / `.stream(...)` | `sequential=True` (debug mode) |

## Running All Examples

```bash
for example in examples/*.py; do
  echo "Running $example..."
  python "$example"
done
```

## Notes

- Use `.map(...)` for ordered, accumulated results.
- Use `.stream(...)` or `parallel_iter(...)` when results should be consumed incrementally.
- For `executor="process"`, keep worker functions at module scope.
