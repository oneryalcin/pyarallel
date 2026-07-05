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

### tqdm Integration

Wire `on_progress` to a tqdm progress bar:

```python
from tqdm import tqdm
from pyarallel import parallel_map

def fetch(url):
    return requests.get(url, timeout=10).json()

with tqdm(total=len(urls)) as pbar:
    result = parallel_map(
        fetch, urls, workers=10,
        on_progress=lambda done, total: (setattr(pbar, 'n', done), pbar.refresh()),
    )
```

!!! note
    tqdm works best with known-size inputs (lists, ranges). For generators
    with `batch_size`, the total changes as batches are consumed, which
    makes the progress bar jump — use a plain `on_progress` callback instead.

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

Retries happen *inside the worker* — only the failing item is retried, not the entire batch. This composes cleanly with rate limiting and batching: **every retry attempt draws a fresh rate-limit token**, so a retry storm can never blow through your quota.

### Server-Driven Backoff (429 / Retry-After)

Real APIs tell you how long to back off. `retry_if` decides retryability
from the exception *instance*; `wait_from` extracts the server-mandated
wait, which replaces the backoff delay (no jitter) and pauses the shared
limiter so the whole pool slows down:

```python
def retry_after(exc):
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response else None
    return float(header) if header else None   # None → exponential backoff

results = parallel_map(
    call_api, ids,
    rate_limit=limiter,   # a shared Limiter — see below
    retry=Retry(
        attempts=5,
        on=(httpx.HTTPStatusError,),
        retry_if=lambda exc: exc.response.status_code in (429, 503),
        wait_from=retry_after,
    ),
)
```

Server waits are clamped by `max_server_wait` (default 10 minutes) so a
malformed `Retry-After: 86400` can't pin a worker for a day. Write
predicates defensively — they receive every exception `on=` lets through.

For the full `Retry` API, see [API Reference](../api-reference/core.md#retry).

## Shared Rate Limits and Burst

A `RateLimit` passed directly creates a private limiter per call. When the
quota belongs to an API key — the usual case — share one `Limiter`:

```python
from pyarallel import Limiter, RateLimit

limiter = Limiter(RateLimit(100, "minute", burst=20))

users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)  # same budget
```

`burst` is the token-bucket capacity: how many calls may fire immediately
before the sustained rate applies. The default of 1 gives smooth, evenly
spaced pacing. One instance works concurrently across threads *and* event
loops — sync and async calls can share a budget. Details in
[Rate Limiting](../api-reference/rate-limiting.md).

## Checkpoint / Resume

For long jobs, `checkpoint=` persists every completed item's result to a
SQLite file as it finishes. Rerunning the same call resumes — cached items
load from disk, failed and unseen items execute:

```python
result = parallel_map(embed, chunks, checkpoint="embeddings.ckpt")
# crashed at item 40,000? rerun the same line — only the remainder runs
```

Safety guards, stated honestly:

- The file is bound to the mapped function's identity: name, code digest,
  and visible captured config (default values, closure values,
  `functools.partial` arguments). An edited function or a changed
  `factor=3` raises `CheckpointError` — stale reuse fails closed, never
  silently.
- Live objects in captured state (clients, sessions) count by *type* only —
  config hidden inside them is invisible. Delete the checkpoint when it
  changes. Bound methods and callable objects are rejected outright: their
  entire state is opaque.
- A changed input at the same position is recomputed, never served stale.
- Rows are positional: reordering or inserting inputs forces shifted items
  to recompute. Resume is for *the same call, rerun*.
- Items and results must be picklable; a result that cannot be
  checkpointed aborts the run with `CheckpointError`.

Available on `parallel_map`, `async_parallel_map`, and `.map()`.

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

# Process 10M items — a bounded window in flight, everything else on disk
for item in parallel_iter(process, ten_million_items, workers=8):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)

# Or with the decorator
@parallel(workers=8)
def process(item): ...

for item in process.stream(huge_list):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)
```

The engine keeps a bounded window of items in flight (default
`2 × workers`; set `batch_size` to change it). Input is consumed lazily —
generators are never materialized — and a slow item delays only itself:
there are no batch barriers. Breaking out of the loop stops submission
and cancels not-yet-started tasks; tasks already running in a worker
finish in the background.

Results arrive in **completion order** (fastest tasks first), not input order.
Each `ItemResult` includes the original `.index` so you can match results to inputs.

**When to use which:**

| API | Memory | Use case |
|---|---|---|
| `.map()` / `parallel_map` | All results in memory | Results fit in memory, need `.ok`, `.failures()` |
| `.stream()` / `parallel_iter` | Constant (one window) | ETL, streaming to DB, 10M+ items |

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
