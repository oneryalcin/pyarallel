# API Reference: Async

Pyarallel provides async-native parallel execution using `asyncio.TaskGroup` for structured concurrency and `asyncio.Semaphore` for concurrency control.

## `async_parallel_map`

Execute an async function over items concurrently.

```python
from pyarallel import async_parallel_map

results = await async_parallel_map(
    fn,                              # Async function
    items,                           # Any iterable
    *,
    concurrency=4,                   # Max concurrent tasks
    rate_limit=None,                 # RateLimit or ops/second
    timeout=None,                    # Per-task timeout in seconds
    on_progress=None,                # callback(completed, total)
    batch_size=None,                 # Process in chunks to control memory
    retry=None,                      # Retry(attempts=3, backoff=1.0)
)
```

**Returns:** `ParallelResult[R]`

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | async `Callable` | required | Async function to apply to each item |
| `items` | `Iterable` | required | Any iterable |
| `concurrency` | `int` | `4` | Maximum concurrent tasks |
| `rate_limit` | `RateLimit \| float \| None` | `None` | Rate limiting |
| `timeout` | `float \| None` | `None` | **Per-task** timeout in seconds |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback |
| `batch_size` | `int \| None` | `None` | Process items in chunks (controls memory) |
| `retry` | `Retry \| None` | `None` | Per-item retry with backoff |

### Key Difference from Sync

- Uses `concurrency` (not `workers`) — controls task concurrency, not pool size
- `timeout` is **per-task** (via `asyncio.wait_for`), not total
- Uses `asyncio.TaskGroup` for structured concurrency — proper cleanup on errors

### Examples

```python
import httpx

async def fetch(url):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()

# Basic
results = await async_parallel_map(fetch, urls, concurrency=20)

# With rate limit and per-task timeout
results = await async_parallel_map(
    fetch, urls,
    concurrency=10,
    rate_limit=RateLimit(100, "minute"),
    timeout=5.0,
)
```

---

## `@async_parallel`

Decorator that adds `.map()` for async parallel execution.

```python
@async_parallel(concurrency=4, rate_limit=None)
async def fn(item): ...
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `concurrency` | `int` | `4` | Default concurrency for `.map()` |
| `rate_limit` | `RateLimit \| float \| None` | `None` | Default rate limiting |

### Usage

```python
from pyarallel import async_parallel

@async_parallel(concurrency=10)
async def fetch(url):
    async with httpx.AsyncClient() as c:
        return (await c.get(url)).json()

# Normal call
data = await fetch("http://example.com")

# Parallel
results = await fetch.map(urls)
results = await fetch.map(urls, concurrency=20, timeout=5.0)
```

### Method Support

Works with async instance methods:

```python
class AsyncScraper:
    def __init__(self, base_url):
        self.base_url = base_url

    @async_parallel(concurrency=5)
    async def fetch(self, path):
        async with httpx.AsyncClient() as c:
            return (await c.get(f"{self.base_url}{path}")).json()

scraper = AsyncScraper("https://api.example.com")
data = await scraper.fetch("/users/1")
results = await scraper.fetch.map(["/users/1", "/users/2", "/users/3"])
```
