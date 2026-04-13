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
    task_timeout=None,                    # Per-task timeout in seconds
    on_progress=None,                # callback(completed, total)
    batch_size=None,                 # Lazy batch consumption for unsized iterables
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
| `task_timeout` | `float \| None` | `None` | **Per-task** timeout in seconds |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback. For unsized iterables with batching, `total` is items seen so far |
| `batch_size` | `int \| None` | `None` | Process items in chunks. With unsized iterables, input is consumed lazily one batch at a time |
| `retry` | `Retry \| None` | `None` | Per-item retry with backoff |

### Why `concurrency` instead of `workers`?

The sync API uses `workers` because it sizes a thread/process **pool** — you're creating real OS threads or processes. The async API uses `concurrency` because there's no pool — everything runs on one event loop, and `concurrency` controls how many tasks are allowed to run at the same time via a semaphore.

Calling both `workers` would be misleading (async has no workers). Calling both `concurrency` would be wrong for sync (you're literally setting `ThreadPoolExecutor(max_workers=N)`). The names match what each actually controls.

### Other Differences from Sync

- `task_timeout` is **per-task** (via `asyncio.wait_for`), not total wall-clock like sync `timeout`
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
    task_timeout=5.0,
)
```

### Notes on Progress and Unsized Iterables

When `items` has a known length, `on_progress(done, total)` reports the final
total.

When `items` is unsized (for example a generator) and `batch_size` is set,
Pyarallel keeps input consumption lazy instead of materializing the full input
up front. In that mode, `total` is the number of items discovered so far, not a
guaranteed final total.

---

## `async_parallel_starmap`

Like `async_parallel_map` but unpacks each item as `fn(*args)`.

```python
from pyarallel import async_parallel_starmap

async def add(a, b): return a + b

results = await async_parallel_starmap(add, [(1, 2), (3, 4)], concurrency=4)
# ParallelResult([3, 7])
```

Takes the same options as `async_parallel_map`. Also available as `.starmap()` on `@async_parallel` decorated functions.

---

## `async_parallel_iter`

Async streaming — yields `(index, result_or_exception)` in completion order. Constant memory.

```python
from pyarallel import async_parallel_iter

async for index, value in async_parallel_iter(fetch, urls, concurrency=10):
    await db.save(value)
```

Also available as `.stream()` on `@async_parallel` decorated functions:

```python
@async_parallel(concurrency=10)
async def fetch(url): ...

async for index, value in fetch.stream(urls, batch_size=1000):
    await db.save(value)
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
results = await fetch.map(urls, concurrency=20, task_timeout=5.0)
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
