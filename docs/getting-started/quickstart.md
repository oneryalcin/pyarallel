# Quick Start

## The Function: `parallel_map`

The simplest way to parallelize work:

```python
from pyarallel import parallel_map

def fetch_url(url):
    import requests
    return requests.get(url).json()

urls = ["https://api.example.com/1", "https://api.example.com/2"]
results = parallel_map(fetch_url, urls, workers=4)

for item in results:
    print(item)
```

`parallel_map` accepts **any iterable** — lists, generators, ranges, sets:

```python
# All of these work
parallel_map(process, [1, 2, 3], workers=4)
parallel_map(process, range(100), workers=4)
parallel_map(process, (x for x in data), workers=4)
```

## The Decorator: `@parallel`

For functions you use repeatedly. The function keeps its normal behavior — `.map()` is explicit:

```python
from pyarallel import parallel

@parallel(workers=4)
def fetch(url):
    return requests.get(url).json()

# Normal call — returns dict
data = fetch("http://example.com")

# Parallel call — returns ParallelResult
results = fetch.map(["http://a.com", "http://b.com"])
```

## CPU-Bound Work

Use `executor="process"` for CPU-intensive tasks:

```python
def crunch(data):
    return heavy_computation(data)

results = parallel_map(crunch, datasets, workers=4, executor="process")
```

!!! note
    Functions must be picklable for process execution — use module-level functions, not lambdas.

## Rate Limiting

Control execution rate for API calls:

```python
from pyarallel import RateLimit

# 100 operations per minute
results = parallel_map(call_api, ids, workers=4,
                       rate_limit=RateLimit(100, "minute"))

# Shorthand: 10 per second
results = parallel_map(call_api, ids, workers=4, rate_limit=10)
```

## Retry

Built-in per-item retry with exponential backoff — no tenacity needed:

```python
from pyarallel import Retry

# Retry flaky network calls up to 3 times with exponential backoff
results = parallel_map(fetch, urls, workers=10, retry=Retry(attempts=3, backoff=1.0))

# Only retry network errors, fail immediately on bad input
results = parallel_map(fetch, urls, workers=10,
                       retry=Retry(on=(ConnectionError, TimeoutError)))
```

## Batching

Control memory for large datasets — process in chunks:

```python
# Without batching: 500K futures in memory at once
# With batching: only 500 at a time
results = parallel_map(process, huge_list, workers=8, batch_size=500)
```

For unsized iterables such as generators, `batch_size` also keeps input
consumption lazy one batch at a time.

## Error Handling

Errors are never silently lost. `ParallelResult` gives you structured access:

```python
result = parallel_map(process, items, workers=4)

if result.ok:
    # All succeeded
    for value in result:
        print(value)
else:
    # Some failed — inspect both
    print(f"{len(result.successes())} succeeded")
    print(f"{len(result.failures())} failed")

    for idx, exc in result.failures():
        print(f"  Item {idx}: {exc}")

    # Or raise all errors at once
    result.raise_on_failure()  # ExceptionGroup
```

## Async

Mirror API for async functions:

```python
from pyarallel import async_parallel_map

async def fetch(url):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()

results = await async_parallel_map(fetch, urls, concurrency=10)
```

## Next Steps

- [Advanced Features](../user-guide/advanced-features.md) — timeouts, progress, methods
- [Best Practices](../user-guide/best-practices.md) — choosing executors, error patterns
- [API Reference](../api-reference/core.md) — full parameter docs
