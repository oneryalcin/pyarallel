# API Reference: Async

Pyarallel provides async-native parallel execution: a windowed engine driven by `asyncio.wait` with `asyncio.Semaphore` for concurrency control — in-flight tasks are cancelled and awaited on errors and timeouts, so nothing is left running on the loop.

## `async_parallel_map`

Execute an async function over items concurrently.

<!-- docs-check: skip -->
```python
from pyarallel import async_parallel_map

results = await async_parallel_map(
    fn,                              # Async function
    items,                           # Any iterable — sync OR async (v0.9)
    *,
    concurrency=4,                   # Max concurrent tasks
    rate_limit=None,                 # RateLimit spec, shared Limiter, or ops/second
    timeout=None,                    # Total wall-clock timeout (mirror of sync)
    task_timeout=None,               # Per-task timeout in seconds
    on_progress=None,                # callback(completed, total)
    on_result=None,                  # sync callback(ItemResult) in completion order
    window_size=None,                # Admission window: max unresolved items
    retry=None,                      # Retry(attempts=3, backoff=1.0)
    checkpoint=None,                 # Path to a resume file (SQLite)
    checkpoint_key=None,             # Stable per-item identity for resume
    max_errors=None,                 # Abort after N failures
)
```

**Returns:** `ParallelResult[R]`

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | async `Callable` | required | Async function to apply to each item |
| `items` | `Iterable \| AsyncIterable` | required | Sync or async source (v0.9). Async cursors and paginated generators are consumed directly with end-to-end backpressure — one item pulled as a window slot frees, never materialized. An idle source is never touched; a pull in progress at stop/close is cancelled (its `finally` runs) — final closing is the caller's job |
| `concurrency` | `int` | `4` | Maximum concurrent tasks |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Rate limiting. Pass a shared `Limiter` to draw from one budget across calls |
| `timeout` | `float \| None` | `None` | **Total** wall-clock timeout — mirror of the sync `timeout`. Unfinished tasks are cancelled, `result.timed_out` is set; sized slots are marked `TimeoutError`, unsized inputs return a shorter result (the source is never drained) |
| `task_timeout` | `float \| None` | `None` | **Per-task** timeout in seconds |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback. For unsized iterables, `total` is items seen so far |
| `on_result` | `Callable[[ItemResult[R]], None] \| None` | `None` | Synchronous per-item callback on the event-loop thread, in completion order. Receives successes and failures with retry metadata; checkpoint hits have `attempts=0`. Exceptions propagate like `on_progress`; use `async_parallel_iter` when handling must be awaited |
| `window_size` | `int \| None` | `None` | Admission window: max tasks created but unresolved (default `2 × concurrency`). A lookahead/memory bound, not a chunk size — no barriers, input consumed lazily |
| `retry` | `Retry \| None` | `None` | Per-item retry with backoff |
| `checkpoint` | `str \| Path \| None` | `None` | Checkpoint file for resumable runs — completed items load from disk on rerun |
| `checkpoint_key` | `Callable[[T], str \| int \| bytes] \| None` | `None` | Stable per-item identity — see the [sync docs](core.md#checkpoint-resume) |
| `max_errors` | `int \| None` | `None` | Abort after this many failures (counted after retries). Windowed admission makes the abort cheap; unrun items are marked `Aborted`, `result.aborted` is set — see the [sync docs](core.md#early-abort-with-max_errors) |

!!! warning "Keep result callbacks fast"
    `on_result` is synchronous and runs inline on the event-loop thread. A
    slow callback delays completion processing; blocking I/O blocks the whole
    event loop. Keep it brief, hand work to a queue, or use
    `async_parallel_iter` when result handling must be awaited. See the
    [complete callback example](core.md#handle-results-as-they-complete).

### Why `concurrency` instead of `workers`?

The sync API uses `workers` because it sizes a thread/process **pool** — you're creating real OS threads or processes. The async API uses `concurrency` because there's no pool — everything runs on one event loop, and `concurrency` controls how many tasks are allowed to run at the same time via a semaphore.

Calling both `workers` would be misleading because async execution has no worker pool. Calling both `concurrency` would be wrong for sync because the implementation really is sizing `ThreadPoolExecutor(max_workers=N)` or `ProcessPoolExecutor(max_workers=N)`. The names match what each API actually controls.

Pre-v1, Pyarallel keeps these names intentionally different. If you're moving from sync to async, translate `workers=` to `concurrency=` rather than expecting them to be interchangeable.

### Other Differences from Sync

- Both timeouts exist here: `timeout` is total wall-clock (same contract
  as sync), `task_timeout` is per-task via `asyncio.wait_for` — the sync
  API deliberately has no per-task timeout (threads can't be cancelled)
- On errors and timeouts, in-flight tasks are cancelled and awaited —
  proper cleanup, nothing left running on the loop
- No `sequential=` — `concurrency=1` already serializes

### Examples

```python
import httpx

client = httpx.AsyncClient()  # ONE client — connections pooled across calls

async def fetch(url):
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

When `items` is unsized (for example a generator), input consumption is
lazy — one window ahead, never materialized — and `total` is the number
of items *admitted* so far, growing as the run progresses: a percentage
over it is meaningless. Pass a sized input for a real total.

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

Async streaming — yields `ItemResult` in completion order. A bounded
window of tasks is in flight at any moment (default `2 × concurrency`,
override with `window_size`): memory stays constant, input is consumed
lazily, and a slow item delays only itself.

```python
from pyarallel import async_parallel_iter

async for item in async_parallel_iter(fetch, urls, concurrency=10):
    if item.ok:
        await db.save(item.value)
    else:
        log_error(item.index, item.error)
```

!!! note "Changed in v0.5 (streaming) and v0.6 (everywhere)"
    `window_size` is an **in-flight bound**, not a chunk size — one
    meaning across every API since v0.6: there are no barriers, and
    input is never materialized.

Takes the same `ordered=` and `on_progress=` options as `parallel_iter`:
`ordered=True` yields in input order with a reorder buffer counted
inside the window; `on_progress(done, total)` fires per completed item.

To stop early, close the generator — unlike sync generators, a bare
`break` does **not** finalize an async generator promptly (Python defers
it to event-loop shutdown, so tasks keep running). Wrap the stream in
`contextlib.aclosing`:

```python
from contextlib import aclosing

async with aclosing(async_parallel_iter(fetch, urls)) as stream:
    async for item in stream:
        if enough(item):
            break   # aclosing cancels in-flight tasks right here
```

Async tasks, unlike threads, are genuinely cancellable — once the
generator is closed, in-flight work stops.

Also available as `.stream()` on `@async_parallel` decorated functions:

```python
@async_parallel(concurrency=10)
async def fetch(url): ...

async for item in fetch.stream(urls):
    if item.ok:
        await db.save(item.value)
    else:
        log_error(item.index, item.error)
```

---

## `@async_parallel`

Decorator that adds `.map()` for async parallel execution.

```python
@async_parallel(concurrency=4, rate_limit=None, on_result=None)
async def fn(item): ...
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `concurrency` | `int` | `4` | Default concurrency for `.map()` |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Default rate limiting |
| `on_result` | `Callable[[ItemResult], None] \| None` | `None` | Default synchronous live-result callback for `.map()`/`.starmap()` — ignored by `.stream()`, which already yields each result |

### Usage

```python
from pyarallel import async_parallel

client = httpx.AsyncClient()  # ONE client, reused by every call

@async_parallel(concurrency=10)
async def fetch(url):
    return (await client.get(url)).json()

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
        self.client = httpx.AsyncClient()  # one client per scraper

    @async_parallel(concurrency=5)
    async def fetch(self, path):
        return (await self.client.get(f"{self.base_url}{path}")).json()

scraper = AsyncScraper("https://api.example.com")
data = await scraper.fetch("/users/1")
results = await scraper.fetch.map(["/users/1", "/users/2", "/users/3"])
```
