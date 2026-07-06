# Pyarallel

[![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)

The fan-out layer for rate-limited APIs. Apply one function to many inputs — with rate limiting, retry, batching, and structured errors. Sync and async.

Pyarallel is for "fan out one function over N items" workloads against services that throttle you: LLM calls, embeddings, scraping, SaaS APIs — plus general file processing and data crunching. Everyone hand-rolls the same stack for this: a semaphore, tenacity, a rate limiter, ad-hoc 429 handling. Pyarallel is that stack as one coherent tool. Not DAGs, not queues, not distributed systems. Just `concurrent.futures` and `asyncio` with the policies and result handling already built in.

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

- **Rate limiting** — token bucket with burst, per-second/minute/hour: `rate_limit=RateLimit(100, "minute", burst=20)`
- **Shared quota** — one `Limiter` instance across calls and functions when the budget belongs to an API key: `rate_limit=Limiter(RateLimit(100, "minute"))`
- **Retry with backoff** — per-item, exponential, jitter, exception filtering: `retry=Retry(attempts=3, on=(ConnectionError,))`
- **Server-driven backoff** — honor 429 + `Retry-After`: `retry=Retry(retry_if=..., wait_from=...)`; the wait also pauses the shared limiter so one throttled task slows the whole pool
- **Checkpoint/resume** — `checkpoint="run.ckpt"`: a crash at item 40,000 resumes instead of restarting from zero; `checkpoint_key=lambda u: u.id` keys rows by identity so evolving inputs keep completed work
- **Early abort** — `max_errors=10`: a dead API costs tens of calls, not thousands; unrun items are marked `Aborted`, partial results returned
- **Streaming** — sliding-window `parallel_iter` / `async_parallel_iter`: bounded in-flight window, lazy input, no batch barriers, `ordered=True` for input-order yields, per-item `attempts`/`duration`
- **Structured errors** — `ParallelResult` with `.ok`, `.ok_values()`, `.successes()`, `.failures()`, `.raise_on_failure()`
- **Timeouts** — total wall-clock on sync *and* async (`timeout=30.0`), per-task in async (`task_timeout=5.0`)
- **Debug mode** — `sequential=True` runs inline: no pool, real stack traces, working breakpoints
- **Progress callbacks** — `on_progress=lambda done, total: print(f"{done}/{total}")` on collected and streaming APIs
- **Process executor** — CPU-bound work: `executor="process"`, with `worker_init=` and `max_tasks_per_worker=`
- **Contextvars propagation** — correlation IDs survive into thread workers
- **Decorator API** — `@parallel` / `@async_parallel` with `.map()`, `.starmap()`, `.stream()` — fully typed via `Unpack[TypedDict]`

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
| `RateLimit(count, per, burst)` | `RateLimit(100, "minute", burst=20)` |
| `Limiter(rate_limit)` | shared budget: `Limiter(RateLimit(100, "minute"))` |
| `Retry(attempts, backoff, on, retry_if, wait_from)` | `Retry(attempts=3, on=(ConnectionError,))` |
| `checkpoint=` | resumable runs: `checkpoint="run.ckpt"` |

Works with instance methods and static methods via `@parallel` decorator — see [full docs](https://oneryalcin.github.io/pyarallel/).

## Documentation

[Full docs](https://oneryalcin.github.io/pyarallel/) — API reference, advanced features, best practices.

## License

MIT — see [LICENSE.md](LICENSE.md).
