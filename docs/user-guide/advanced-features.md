# Advanced Features

## Progress Tracking

Track progress for long-running operations:

```python
from pyarallel import parallel_map

def my_progress(done, total):
    print(f"\r{done}/{total} ({100*done//total}%)", end="", flush=True)

results = parallel_map(process, items, workers=8, on_progress=my_progress)
```

If `items` has a known length, `total` is the final input size. If `items` is
an unsized iterable (for example a generator) and you also set `batch_size`,
Pyarallel keeps input consumption lazy; in that mode `total` is the number of
items discovered so far.

Works with both `parallel_map` and `.map()`:

```python
@parallel(workers=4)
def fetch(url): ...

results = fetch.map(urls, on_progress=lambda d, t: print(f"{d}/{t}"))
```

## Timeouts

### Total Timeout (sync)

Set a wall-clock limit for the entire operation:

```python
results = parallel_map(fetch, urls, workers=10, timeout=30.0)

# Tasks that didn't complete are marked as failures
if not results.ok:
    for idx, exc in results.failures():
        if isinstance(exc, TimeoutError):
            print(f"Item {idx} timed out")
```

### Per-Task Timeout (async)

The async API supports per-task timeouts via `asyncio.wait_for`:

```python
from pyarallel import async_parallel_map

results = await async_parallel_map(fetch, urls, concurrency=10, task_timeout=5.0)
# Each individual task gets 5 seconds before timing out
```

## Method Support

The `@parallel` decorator works with instance methods via the descriptor protocol:

### Instance Methods

```python
class Scraper:
    def __init__(self, session):
        self.session = session

    @parallel(workers=4)
    def fetch(self, url):
        return self.session.get(url).text

s = Scraper(requests.Session())
s.fetch("http://example.com")       # normal — returns str
s.fetch.map(urls)                   # parallel — returns ParallelResult
```

### Static Methods

```python
class MathUtils:
    @staticmethod
    @parallel(workers=4)
    def square(x):
        return x ** 2

MathUtils.square(5)                 # 25
MathUtils.square.map([1, 2, 3])    # ParallelResult([1, 4, 9])
```

### Using parallel_map with Methods

You can always use `parallel_map` directly with bound methods:

```python
scraper = Scraper(session)
results = parallel_map(scraper.fetch, urls, workers=8)
```

## Overriding Decorator Defaults

Per-call overrides on `.map()`:

```python
@parallel(workers=2, rate_limit=RateLimit(10, "second"))
def process(item): ...

# Override workers and rate limit for this specific call
results = process.map(big_list, workers=16, rate_limit=RateLimit(1000, "minute"))
```

## Async Decorator

Same pattern as sync, with `async`/`await`:

```python
from pyarallel import async_parallel

@async_parallel(concurrency=10)
async def fetch(url):
    async with httpx.AsyncClient() as c:
        return (await c.get(url)).json()

data = await fetch("http://example.com")       # single call
results = await fetch.map(urls)                 # parallel
results = await fetch.map(urls, task_timeout=5.0)  # with per-task timeout
```

## Retry

Built-in per-item retry with exponential backoff and jitter:

```python
from pyarallel import parallel_map, Retry

# Retry up to 3 times with 1s base exponential backoff
# Jitter is ON by default — randomizes delay ±50% to prevent thundering herd
results = parallel_map(fetch, urls, workers=10, retry=Retry(attempts=3, backoff=1.0))

# Only retry transient network errors — fail immediately on bad input
results = parallel_map(fetch, urls, workers=10,
                       retry=Retry(on=(ConnectionError, TimeoutError)))

# Cap max delay and disable jitter (useful for testing)
results = parallel_map(fetch, urls, workers=10,
                       retry=Retry(attempts=5, backoff=2.0, max_delay=30.0, jitter=False))
```

**How backoff works:** delay = `backoff * 2^attempt`, capped at `max_delay`. With `jitter=True` (default), the delay is multiplied by a random factor between 0.5 and 1.5 — this prevents all workers from retrying at the exact same moment when a service recovers.

Retries happen *inside the worker* — only the failing item is retried, not the entire batch. This composes cleanly with rate limiting and batching.

For the full `Retry` API, see [API Reference](../api-reference/core.md#retry).

## Batching

Control memory for large datasets by processing in chunks:

```python
# Submit 500 items at a time instead of 500,000 at once
results = parallel_map(process, huge_list, workers=8, batch_size=500)
```

With `batch_size` set, unsized iterables are consumed lazily one batch at a
time instead of being materialized up front.

Errors in one batch don't prevent subsequent batches from running.

## Starmap — Multi-Argument Functions

For functions that take multiple arguments, use `parallel_starmap` or `.starmap()`:

```python
from pyarallel import parallel_starmap

def fetch_with_auth(url, token):
    return requests.get(url, headers={"Authorization": token}).json()

results = parallel_starmap(fetch_with_auth,
                           [(url, token) for url in urls],
                           workers=10)

# Or with the decorator
@parallel(workers=10)
def fetch_with_auth(url, token): ...

results = fetch_with_auth.starmap([(url1, token), (url2, token)])
```

## Streaming — Constant Memory

For large-scale processing where results shouldn't accumulate in memory, use `parallel_iter` or `.stream()`:

```python
from pyarallel import parallel_iter

# Process 10M items — only one batch of results in memory at a time
for index, value in parallel_iter(process, ten_million_items,
                                  workers=8, batch_size=1000):
    if isinstance(value, Exception):
        log_error(index, value)
    else:
        db.save(value)

# Or with the decorator
@parallel(workers=8)
def process(item): ...

for index, value in process.stream(huge_list, batch_size=1000):
    db.save(value)
```

Results arrive in **completion order** (fastest tasks first), not input order. Each `(index, value)` tuple includes the original index so you can match results to inputs.

**When to use which:**

| API | Memory | Use case |
|---|---|---|
| `.map()` / `parallel_map` | All results in memory | Results fit in memory, need `.ok`, `.failures()` |
| `.stream()` / `parallel_iter` | Constant (one batch) | ETL, streaming to DB, 10M+ items |

## Structured Error Handling

`ParallelResult` never silently drops errors:

```python
result = parallel_map(process, items, workers=4)

# Inspect successes and failures separately
for idx, value in result.successes():
    save(idx, value)

for idx, error in result.failures():
    log_error(idx, error)

# Retry just the failed items
failed_items = [items[idx] for idx, _ in result.failures()]
retry_result = parallel_map(process, failed_items, workers=2)
```

When you iterate or call `.values()`, failures raise an `ExceptionGroup`:

```python
try:
    values = list(result)
except ExceptionGroup as eg:
    print(f"{len(eg.exceptions)} tasks failed")
    for exc in eg.exceptions:
        print(f"  {type(exc).__name__}: {exc}")
```
