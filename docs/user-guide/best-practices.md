# Best Practices

## Choosing the Right Executor

### Threads (`executor="thread"`, default)

Best for **I/O-bound** work where tasks spend most time waiting:

- HTTP requests
- Database queries
- File I/O
- API calls

```python
results = parallel_map(fetch_url, urls, workers=20, executor="thread")
```

### Processes (`executor="process"`)

Best for **CPU-bound** work that needs true parallelism:

- Data crunching
- Image/video processing
- Scientific computation

```python
results = parallel_map(compute, data, workers=4, executor="process")
```

!!! warning
    Process executor requires picklable functions. Use module-level named functions, not lambdas or closures.

### Worker Count

By default, `workers=None` — the stdlib picks a sensible number automatically:

- **Threads**: `min(32, cpu_count + 4)` — Python's `ThreadPoolExecutor` default
- **Processes**: `cpu_count()` — Python's `ProcessPoolExecutor` default

Most of the time you don't need to set `workers` at all. Override only when you have a reason:

```python
# Just use the defaults — they're good
results = parallel_map(fetch, urls)
results = parallel_map(crunch, data, executor="process")

# Override when you know better
results = parallel_map(fetch, urls, workers=100)       # high concurrency for fast APIs
results = parallel_map(crunch, data, executor="process",
                       workers=multiprocessing.cpu_count() - 1)  # leave a core free
```

## Rate Limiting

### Respecting API Limits

Leave a buffer below the actual limit:

```python
from pyarallel import RateLimit

# API allows 100/min — use 90 for safety
results = parallel_map(call_api, ids, workers=4,
                       rate_limit=RateLimit(90, "minute"))
```

### Shorthand

For simple per-second limits, pass a number:

```python
results = parallel_map(fn, items, rate_limit=10)  # 10 per second
```

### One Limiter per API Key

A `RateLimit` passed directly creates a fresh private limiter each call —
two concurrent maps would each assume they own the full quota. Whenever two
calls spend the same budget, share a `Limiter`:

```python
from pyarallel import Limiter, RateLimit

OPENAI_LIMITER = Limiter(RateLimit(450, "minute"))  # one per API key

# every job against this key, sync or async, passes the same instance
parallel_map(embed, texts, rate_limit=OPENAI_LIMITER)
```

### Burst: Default to Smooth

`burst=1` (the default) spaces requests evenly — the safest choice against
secondary per-second limits. Raise it only when you know the quota
genuinely allows bursts, and keep it well under the documented burst
allowance.

## Memory Control with Batching

For large datasets, use `batch_size` to limit how many futures exist at once:

```python
# 500K items — only 1000 futures in memory at a time
results = parallel_map(process, huge_list, workers=8, batch_size=1000)
```

Without `batch_size`, all items are submitted at once. With `batch_size` set,
unsized iterables are consumed lazily one batch at a time. On
memory-constrained environments (K8s pods, Lambda), this helps prevent OOM
kills.

## Error Handling Patterns

### Fail-Fast

Iterate the result — first `ExceptionGroup` stops you:

```python
try:
    for value in parallel_map(process, items, workers=4):
        save(value)
except ExceptionGroup as eg:
    for exc in eg.exceptions:
        log.error(exc)
```

### Built-in Retry

Use `Retry` for automatic per-item retry with exponential backoff:

```python
from pyarallel import Retry

# Retry transient failures, fail fast on bad input
results = parallel_map(
    fetch, urls, workers=10,
    retry=Retry(attempts=3, backoff=1.0, on=(ConnectionError, TimeoutError)),
)
```

### Collect-and-Retry Manually

For more control, inspect partial results and retry selectively:

```python
result = parallel_map(process, items, workers=4)

for idx, value in result.successes():
    save(items[idx], value)

if not result.ok:
    failed = [items[idx] for idx, _ in result.failures()]
    retry_result = parallel_map(process, failed, workers=2)
```

### Honor 429s with `wait_from`

When an API sends `Retry-After`, use it — the server knows its own load
better than your backoff curve does. With a shared `Limiter`, the wait
pauses the whole pool, not just the throttled task:

```python
def retry_after(exc):
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response else None
    return float(header) if header else None

results = parallel_map(
    call_api, ids,
    rate_limit=shared_limiter,
    retry=Retry(attempts=4, on=(ApiError,), wait_from=retry_after),
)
```

Write `retry_if`/`wait_from` defensively — they receive every exception
that `on=` lets through, and a predicate that raises replaces the real
error in the failure report.

### Composing with Tenacity

For complex retry strategies (circuit breakers, custom stop conditions), use tenacity inside your function:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential())
def resilient_fetch(url):
    return requests.get(url, timeout=10).json()

results = parallel_map(resilient_fetch, urls, workers=10)
```

## Long Jobs: Checkpoint Early

Any run long enough that restarting it hurts should carry a checkpoint:

```python
results = parallel_map(process, fifty_thousand_items, checkpoint="run.ckpt")
```

Rules of thumb:

- One checkpoint file per (function, input list) pair. Don't share a file
  between different jobs — the function-identity guard will fail closed.
- Delete the file when you *want* a full recompute (model upgrade, prompt
  change that the code digest can't see, reordered inputs).
- Checkpointing requires picklable items and results; an unpicklable
  result stops the run with `CheckpointError` rather than pretending.

## Testing

`parallel_map` with `workers=1` runs sequentially — deterministic for tests:

```python
def test_processing():
    result = parallel_map(process, [1, 2, 3], workers=1)
    assert list(result) == [expected_1, expected_2, expected_3]
```

The `@parallel` decorator preserves normal call behavior:

```python
def test_decorated_function():
    @parallel(workers=2)
    def double(x):
        return x * 2

    # Test the function directly — no parallel overhead
    assert double(5) == 10

    # Test parallel execution
    assert list(double.map([1, 2, 3])) == [2, 4, 6]
```

## Performance Tips

1. **Match workers to workload** — too many workers waste resources on context switching
2. **Use rate limiting for external APIs** — protects you and the service
3. **Share one `Limiter` per API key** — separate specs per call each assume the full quota
4. **Prefer threads for I/O** — processes have serialization overhead
5. **Check `result.ok` before iterating** — avoids surprise `ExceptionGroup` raises
6. **Use `on_progress` for long jobs** — for unsized iterables with batching,
   `total` is items seen so far, not the final size
7. **Checkpoint anything you'd hate to restart** — `checkpoint="run.ckpt"` is one argument
