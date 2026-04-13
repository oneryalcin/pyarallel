# Pyarallel

[![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)

Apply one function to many inputs — with rate limiting, retry, batching, and structured errors. Sync and async.

Pyarallel is for "fan out one function over N items" workloads: API calls, file processing, data crunching. Not DAGs, not queues, not distributed systems. Just `concurrent.futures` and `asyncio` with the common policies and result handling already built in.

**Zero dependencies. Python 3.12+.**

## Before / After

Fetch 10,000 URLs with rate limiting and error handling.

**concurrent.futures:**

```python
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch(url):
    return requests.get(url, timeout=10).json()

urls = ["https://api.example.com/users/1", "https://api.example.com/users/2", ...]

results = [None] * len(urls)
errors = []

with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch, url): i for i, url in enumerate(urls)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            results[i] = f.result()
        except Exception as e:
            errors.append((i, e))

# No rate limiting. No retry. No batching. And you still
# need to wire those yourself every time.
```

**pyarallel:**

```python
from pyarallel import parallel_map, RateLimit, Retry

result = parallel_map(
    fetch, urls,
    workers=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, on=(ConnectionError, TimeoutError)),
)

for idx, val in result.successes():
    save(val)
for idx, exc in result.failures():
    log_error(idx, exc)
```

Same thing, async:

```python
import httpx
from pyarallel import async_parallel_map, RateLimit, Retry

async def fetch_async(url):
    async with httpx.AsyncClient() as client:
        return (await client.get(url, timeout=10)).json()

result = await async_parallel_map(
    fetch_async, urls,
    concurrency=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, on=(ConnectionError, TimeoutError)),
)
# Same result model — result.ok, result.successes(), result.failures()
```

## Install

```bash
pip install pyarallel
```

## What You Get

- **Rate limiting** — token bucket, per-second/minute/hour: `rate_limit=RateLimit(100, "minute")`
- **Retry with backoff** — per-item, exponential, jitter, exception filtering: `retry=Retry(attempts=3, on=(ConnectionError,))`
- **Batched execution** — lazy input consumption for generators, memory control: `batch_size=500`
- **Streaming** — constant-memory processing via `parallel_iter` / `async_parallel_iter`
- **Structured errors** — `ParallelResult` with `.ok`, `.successes()`, `.failures()`, `.raise_on_failure()`
- **Timeouts** — wall-clock for the whole operation (`timeout=30.0`) or per-task in async (`task_timeout=5.0`)
- **Progress callbacks** — `on_progress=lambda done, total: print(f"{done}/{total}")`
- **Process executor** — CPU-bound work: `executor="process"`
- **Decorator API** — `@parallel` / `@async_parallel` with `.map()`, `.starmap()`, `.stream()`

## Quick Start

### Sync

```python
import requests
from pyarallel import parallel_map, RateLimit, Retry

def fetch(url):
    return requests.get(url, timeout=10).json()

# Fan out over a list, get ordered results
result = parallel_map(fetch, urls, workers=10)

# Rate-limited API calls with retry
def call_api(user_id):
    return requests.get(f"https://api.example.com/users/{user_id}").json()

result = parallel_map(
    call_api, user_ids,
    workers=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, backoff=1.0, on=(ConnectionError, TimeoutError)),
)

# CPU-bound with processes
from PIL import Image

def resize_image(path):
    img = Image.open(path)
    img.thumbnail((800, 600))
    img.save(path.replace(".png", "_thumb.png"))

result = parallel_map(resize_image, paths, executor="process")
```

### Async

```python
import httpx
from pyarallel import async_parallel_map

async def fetch_async(url):
    async with httpx.AsyncClient() as client:
        return (await client.get(url, timeout=10)).json()

result = await async_parallel_map(
    fetch_async, urls, concurrency=20, task_timeout=5.0,
)
```

### Decorator

Adds `.map()`, `.starmap()`, `.stream()` without changing the function:

```python
from pyarallel import parallel, async_parallel, RateLimit

@parallel(workers=8, rate_limit=RateLimit(100, "minute"))
def fetch(url):
    return requests.get(url).json()

fetch("http://example.com")          # normal call — returns dict
fetch.map(urls)                      # parallel — returns ParallelResult
fetch.stream(urls, batch_size=500)   # streaming — yields ItemResult

@async_parallel(concurrency=10)
async def fetch_async(url):
    async with httpx.AsyncClient() as c:
        return (await c.get(url)).json()

await fetch_async.map(urls)          # async parallel
```

### Streaming — Constant Memory

For ETL, pipelines, or datasets too large to hold in memory:

```python
from pyarallel import parallel_iter

def transform(row):
    return {"id": row["id"], "name": row["name"].strip().title()}

for item in parallel_iter(transform, ten_million_rows, batch_size=1000):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)
```

### Error Handling

All errors collected, never silently swallowed:

```python
def send_email(msg):
    return smtp.send(msg["to"], msg["subject"], msg["body"])

result = parallel_map(send_email, messages)

if result.ok:
    values = result.values()           # list of all results, in order
else:
    for idx, exc in result.failures():
        log_error(idx, exc)
    result.raise_on_failure()          # or raise ExceptionGroup with all errors
```

## API Summary

| Function | Decorator | Returns | Use case |
|---|---|---|---|
| `parallel_map(fn, items)` | `.map(items)` | `ParallelResult` | Results fit in memory |
| `parallel_starmap(fn, items)` | `.starmap(items)` | `ParallelResult` | Multi-arg, fits in memory |
| `parallel_iter(fn, items)` | `.stream(items)` | `Iterator[ItemResult]` | Streaming, constant memory |

Async mirrors: `async_parallel_map`, `async_parallel_starmap`, `async_parallel_iter`

| Config | Example |
|---|---|
| `RateLimit(count, per)` | `RateLimit(100, "minute")` |
| `Retry(attempts, backoff, on)` | `Retry(attempts=3, on=(ConnectionError,))` |

Works with instance methods and static methods via `@parallel` decorator — see [full docs](https://oneryalcin.github.io/pyarallel/).

## Documentation

[Full docs](https://oneryalcin.github.io/pyarallel/) — API reference, advanced features, best practices.

## License

MIT — see [LICENSE.md](LICENSE.md).
