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
an unsized iterable (for example a generator), input is consumed lazily and
`total` is the number of items *admitted* so far — one window ahead of
completions, growing as the run progresses, so a percentage over it is
meaningless. Pass a sized input when you need a real total.

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
    the total grows as input is consumed, which makes the progress bar
    jump — use a plain `on_progress` callback instead.

## Timeouts

### Total Timeout (sync)

Set a wall-clock limit for the entire operation:

```python
results = parallel_map(fetch, urls, workers=10, timeout=30.0)

# Tasks that didn't complete are marked as failures
if results.timed_out:
    for idx, exc in results.failures():
        if isinstance(exc, TimeoutError):
            print(f"Item {idx} timed out")
```

`results.timed_out` is the reliable expiry signal. For sized inputs
every unfinished slot is also marked with a `TimeoutError` failure; an
unsized input (a generator) instead returns a **shorter** result
covering only the items actually pulled — the source is never drained
after a stop, so a blocking or infinite generator stays untouched.

### Per-Task Timeout (async)

The async API supports per-task timeouts via `asyncio.wait_for`:

```python
from pyarallel import async_parallel_map

results = await async_parallel_map(fetch, urls, concurrency=10, task_timeout=5.0)
# Each individual task gets 5 seconds before timing out
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

Retries happen *inside the worker* — only the failing item is retried, never its neighbors. This composes cleanly with rate limiting: **every retry attempt draws a fresh rate-limit token**, so a retry storm can never blow through your quota.

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
- Rows are positional by default: reordering or inserting inputs forces
  shifted items to recompute. For evolving inputs, key rows by identity:

  ```python
  result = parallel_map(fetch, users, checkpoint="run.ckpt",
                        checkpoint_key=lambda u: u.id)
  # next week: three new users prepended — only they run
  ```

  Duplicate keys raise `CheckpointError`; keys are type-tagged (`1` vs
  `"1"` vs `b"1"` never collide); a changed payload under the same key
  still recomputes.
- Checkpoint files from pyarallel < 0.5 fail closed — delete and rerun.
- Items and results must be picklable; a result that cannot be
  checkpointed aborts the run with `CheckpointError`.
- A corrupted row raises `CheckpointError` with delete-to-start-fresh
  instructions, never a raw unpickling error.

!!! danger "Checkpoint files are code, not data"
    Rows are stored as **pickle** — resuming from a checkpoint executes
    whatever its pickle streams contain. Anyone who can *write* the file
    can run code in your process on the next resume. Never resume from a
    file you didn't create; never accept one from an untrusted source
    (a bug report, a shared bucket). Pyarallel creates new checkpoint
    files owner-only (`0o600`, POSIX) and leaves existing files'
    permissions alone — but a directory writable by others (`/tmp`-like
    locations) is not a safe home for one regardless.

Available on `parallel_map`, `async_parallel_map`, and `.map()`.

## Early Abort — `max_errors`

Stop paying for a dead API. With `max_errors=N` the run aborts once N
items have failed (counted after retries are exhausted) and returns
partial results:

```python
from pyarallel import Aborted

result = parallel_map(fetch, urls, workers=10, max_errors=10)

if result.aborted:
    real  = [(i, e) for i, e in result.failures() if not isinstance(e, Aborted)]
    unrun = [i for i, e in result.failures() if isinstance(e, Aborted)]
```

Because admission is windowed, the abort is genuinely cheap — a
10k-item job against a dead API costs tens of calls, not thousands.
Finished items keep their real results; never-run items are marked
`Aborted`, and `result.aborted` reports the early stop.

The overnight-job combo is `max_errors` + `checkpoint`: the job aborts
cheaply when the API dies, successes are already persisted, and the
morning rerun resumes exactly where it stopped:

```python
result = parallel_map(fetch, users, workers=10,
                      max_errors=10, checkpoint="nightly.ckpt")
```

Streaming APIs accept `max_errors` too — the stream ends after the Nth
failure is yielded, with no placeholder items for unseen input.

## The Admission Window

Every API — collected and streaming — admits work through a bounded
window: at most `window_size` items (default `2 × workers`) are
submitted but unresolved at any moment. Input is consumed lazily, one
window ahead, so generators are never materialized:

```python
# At most 500 items in flight instead of the default 2 x workers
results = parallel_map(process, huge_list, workers=8, window_size=500)
```

There are no chunks and no barriers — a slow item never stalls the
items behind it, and an error anywhere never prevents the rest from
running.

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
`2 × workers`; set `window_size` to change it). Input is consumed lazily —
generators are never materialized — and a slow item delays only itself:
there are no batch barriers. Breaking out of the loop stops submission
and cancels not-yet-started tasks; tasks already running in a worker
finish in the background.

Results arrive in **completion order** (fastest tasks first), not input order.
Each `ItemResult` includes the original `.index` so you can match results to inputs.
Pass `ordered=True` to yield in input order instead — early results wait in a
reorder buffer that is counted inside the window, so memory stays bounded even
behind a straggler. Streaming also takes `on_progress=` with the same contract
as `parallel_map`.

**When to use which:**

| API | Memory | Use case |
|---|---|---|
| `.map()` / `parallel_map` | All results in memory | Results fit in memory, need `.ok`, `.failures()` |
| `.stream()` / `parallel_iter` | Constant (one window) | ETL, streaming to DB, 10M+ items |

## Structured Error Handling

`ParallelResult` never silently drops errors:

```python
result = parallel_map(process, items, workers=4)

# The common partial-failure path: keep what worked
good = result.ok_values()          # values only, input order, never raises

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
