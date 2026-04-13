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

Shows how to control memory by passing `batch_size` to `.map(...)`.

```bash
python examples/04_batch_processing.py
```

### 05_configuration.py

Shows decorator defaults, per-call overrides, retry, rate limiting, and timeout behavior.

```bash
python examples/05_configuration.py
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
| `batch_size` | `.map(...)` | `batch_size=100` |
| `rate_limit` | `@parallel(...)` or `.map(...)` | `rate_limit=10` or `RateLimit(100, "minute")` |
| `retry` | `.map(...)` or `parallel_map(...)` | `retry=Retry(attempts=3)` |
| `timeout` | `.map(...)` or `parallel_map(...)` | `timeout=30.0` |

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
