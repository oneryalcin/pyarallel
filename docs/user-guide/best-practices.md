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

### Worker Count Guidelines

- **CPU-bound**: `multiprocessing.cpu_count()` or fewer
- **I/O-bound**: 10-50x the number of cores, depending on latency

```python
import multiprocessing

# CPU-bound
results = parallel_map(crunch, data,
                       workers=multiprocessing.cpu_count(),
                       executor="process")

# I/O-bound
results = parallel_map(fetch, urls, workers=32)
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

### Collect-and-Retry

Inspect partial results, retry failures:

```python
result = parallel_map(process, items, workers=4)

# Save successes
for idx, value in result.successes():
    save(items[idx], value)

# Retry failures
if not result.ok:
    failed = [items[idx] for idx, _ in result.failures()]
    retry_result = parallel_map(process, failed, workers=2)
```

### Composing with Tenacity

Use tenacity for per-item retries inside your function:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential())
def resilient_fetch(url):
    return requests.get(url, timeout=10).json()

# Each item retries up to 3 times internally
results = parallel_map(resilient_fetch, urls, workers=10)
```

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
3. **Prefer threads for I/O** — processes have serialization overhead
4. **Check `result.ok` before iterating** — avoids surprise `ExceptionGroup` raises
5. **Use `on_progress` for long jobs** — visibility into what's happening
