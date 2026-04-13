# Pyarallel

[![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)

Simple, explicit parallel execution for Python. No magic, no global config, no enterprise astronautics.

## What It Does

`concurrent.futures` with better ergonomics: structured error handling, rate limiting, retry, batching, streaming, and async support.

```python
from pyarallel import parallel_map, RateLimit, Retry

results = parallel_map(
    fetch_url, urls,
    workers=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, on=(ConnectionError, TimeoutError)),
)
```

## Install

```bash
pip install pyarallel
```

## Quick Start

### The Function — `parallel_map`

```python
from pyarallel import parallel_map, RateLimit, Retry

# Basic — fan out over a list, get ordered results
results = parallel_map(fetch_url, urls, workers=10)

# With rate limiting
results = parallel_map(call_api, ids, rate_limit=RateLimit(100, "minute"))

# With per-item retry for flaky calls
results = parallel_map(fetch, urls, retry=Retry(attempts=3, backoff=1.0))

# Batched — controls memory for large datasets (no K8s OOM kills)
results = parallel_map(process, million_items, batch_size=500)

# CPU-bound work with processes
results = parallel_map(crunch, data, executor="process")

# Progress tracking
results = parallel_map(process, items,
                       on_progress=lambda done, total: print(f"{done}/{total}"))
```

### The Decorator — `@parallel`

For functions you call repeatedly. Adds `.map()` without changing the function:

```python
from pyarallel import parallel

@parallel(workers=4, rate_limit=RateLimit(100, "minute"))
def fetch(url):
    return requests.get(url).json()

# Normal call — returns dict, not [dict]
data = fetch("http://example.com")

# Parallel — explicit .map()
results = fetch.map(["http://a.com", "http://b.com", "http://c.com"])
```

### Multi-Argument Functions — `starmap`

```python
from pyarallel import parallel_starmap

def fetch_with_auth(url, token):
    return requests.get(url, headers={"Authorization": token}).json()

results = parallel_starmap(fetch_with_auth, [(url, token) for url in urls])
```

### Streaming — Constant Memory

For large-scale processing where results shouldn't accumulate:

```python
from pyarallel import parallel_iter

for item in parallel_iter(process, ten_million_items, batch_size=1000):
    if item.ok:
        db.save(item.value)  # yielded and discarded — no accumulation
    else:
        log_error(item.index, item.error)
```

### Async Support

Mirror API using `asyncio.TaskGroup` for structured concurrency:

```python
from pyarallel import async_parallel_map

results = await async_parallel_map(fetch_async, urls, concurrency=10, task_timeout=5.0)
```

## Error Handling

No more lost exceptions. All errors collected, never silently swallowed:

```python
result = parallel_map(process, items)

if result.ok:
    values = result.values()
else:
    for idx, val in result.successes():
        print(f"Item {idx}: {val}")
    for idx, exc in result.failures():
        print(f"Item {idx} failed: {exc}")

    result.raise_on_failure()  # ExceptionGroup with all errors
```

## Methods

Works with instance methods and static methods:

```python
class Scraper:
    def __init__(self, session):
        self.session = session

    @parallel(workers=4)
    def fetch(self, url):
        return self.session.get(url).text

s = Scraper(requests.Session())
s.fetch("http://example.com")        # normal call
s.fetch.map(urls)                    # parallel over urls
s.fetch.stream(urls, batch_size=100) # streaming
```

## API Summary

| Function | Decorator | Returns | Use case |
|---|---|---|---|
| `parallel_map(fn, items)` | `.map(items)` | `ParallelResult` | Results fit in memory |
| `parallel_starmap(fn, items)` | `.starmap(items)` | `ParallelResult` | Multi-arg, fits in memory |
| `parallel_iter(fn, items)` | `.stream(items)` | `Iterator[ItemResult[T]]` | Streaming, constant memory |

Async variants: `async_parallel_map`, `async_parallel_starmap`, `async_parallel_iter`

| Config | Description |
|---|---|
| `RateLimit(count, per)` | Rate limit: `RateLimit(100, "minute")` |
| `Retry(attempts, backoff)` | Per-item retry: `Retry(attempts=3, on=(ConnectionError,))` |

## Documentation

[Full documentation](https://oneryalcin.github.io/pyarallel/) with API reference, advanced features, and best practices.

## License

MIT — see [LICENSE.md](LICENSE.md).
