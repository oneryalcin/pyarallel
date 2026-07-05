# API Reference: Core

## `parallel_map`

Execute a function over items in parallel, returning ordered results.

```python
from pyarallel import parallel_map

results = parallel_map(
    fn,                              # Function to apply to each item
    items,                           # Any iterable
    *,
    workers=None,                    # Number of parallel workers
    executor="thread",               # "thread" or "process"
    rate_limit=None,                 # RateLimit spec, shared Limiter, or ops/second
    timeout=None,                    # Total timeout in seconds
    on_progress=None,                # callback(completed, total)
    batch_size=None,                 # Lazy batch consumption for unsized iterables
    retry=None,                      # Retry(attempts=3, backoff=1.0)
    checkpoint=None,                 # Path to a resume file (SQLite)
)
```

**Returns:** `ParallelResult[R]`

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | `Callable` | required | Function to apply to each item |
| `items` | `Iterable` | required | Any iterable (list, generator, range, set, ...) |
| `workers` | `int \| None` | `None` | Number of parallel workers. `None` lets the executor choose |
| `executor` | `"thread" \| "process"` | `"thread"` | Thread pool or process pool |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Rate limiting (float = ops/second). Pass a shared `Limiter` to draw from one budget across calls |
| `timeout` | `float \| None` | `None` | Total wall-clock timeout in seconds |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback `(completed, total)`. For unsized iterables with batching, `total` is items seen so far |
| `batch_size` | `int \| None` | `None` | Process items in chunks of this size. With unsized iterables, input is consumed lazily one batch at a time |
| `retry` | `Retry \| None` | `None` | Per-item retry with backoff |
| `checkpoint` | `str \| Path \| None` | `None` | Checkpoint file for resumable runs — completed items load from disk on rerun |

### Examples

```python
# Basic
results = parallel_map(fetch, urls, workers=10)

# With rate limit object
results = parallel_map(fetch, urls, rate_limit=RateLimit(100, "minute"))

# With rate limit shorthand
results = parallel_map(fetch, urls, rate_limit=10)  # 10 per second

# With timeout and progress
results = parallel_map(fetch, urls, timeout=60.0,
                       on_progress=lambda d, t: print(f"{d}/{t}"))

# Batched — keeps unsized iterables lazy one batch at a time
results = parallel_map(process, million_items, workers=8, batch_size=500)

# With retry — flaky network calls
results = parallel_map(fetch, urls, workers=10, retry=Retry(attempts=3, backoff=1.0))

# Resumable — crash at item 40k does not restart from zero
results = parallel_map(embed, chunks, checkpoint="embeddings.ckpt")
```

### Checkpoint / Resume

With `checkpoint=` set, every completed item's result is written to a SQLite
file as it finishes. Rerunning the same call resumes: completed items load
from disk, failed and unseen items execute. Rows are keyed by item index plus
a fingerprint of the item, so a changed input at the same position is
recomputed, never served stale.

Constraints: items and results must be picklable — a result that cannot be
pickled is reported as that item's failure. Failures are never checkpointed;
a resumed run retries them. Available on `parallel_map`,
`async_parallel_map`, and `.map()`.

### Notes on Progress and Unsized Iterables

When `items` has a known length, `on_progress(done, total)` reports the final
total.

When `items` is unsized (for example a generator) and `batch_size` is set,
Pyarallel keeps input consumption lazy instead of materializing the full input
up front. In that mode, `total` is the number of items discovered so far, not a
guaranteed final total.

---

## `@parallel`

Decorator that adds `.map()` for parallel execution. The decorated function **keeps its original signature and return type**.

```python
@parallel(workers=4, executor="thread", rate_limit=None)
def fn(item): ...
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `workers` | `int \| None` | `None` | Default worker count for `.map()` |
| `executor` | `"thread" \| "process"` | `"thread"` | Default executor type |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Default rate limiting |

### Usage

```python
@parallel(workers=8)
def fetch(url):
    return requests.get(url).json()

# Normal call — returns dict
data = fetch("http://example.com")

# Parallel — returns ParallelResult
results = fetch.map(urls)
results = fetch.map(urls, workers=16)           # override workers
results = fetch.map(urls, rate_limit=100)       # override rate limit
results = fetch.map(urls, timeout=30.0)         # add timeout
results = fetch.map(urls, on_progress=callback) # add progress

# Bare decorator (uses all defaults)
@parallel
def process(x):
    return x * 2
```

### Method Support

Works with instance methods (via descriptor protocol) and static methods:

```python
class Service:
    @parallel(workers=4)
    def process(self, item):
        return self.transform(item)

    @staticmethod
    @parallel(workers=4)
    def compute(x):
        return x ** 2

svc = Service()
svc.process.map(items)          # instance method
Service.compute.map([1, 2, 3])  # static method
```

---

## `parallel_starmap`

Like `parallel_map` but unpacks each item as `fn(*args)` — for functions that take multiple arguments.

```python
from pyarallel import parallel_starmap

results = parallel_starmap(fn, [(arg1, arg2), (arg3, arg4), ...])
```

Takes the same options as `parallel_map` (workers, executor, rate_limit, timeout, batch_size, retry).

### Examples

```python
def add(a, b):
    return a + b

results = parallel_starmap(add, [(1, 2), (3, 4), (5, 6)])  # [3, 7, 11]

# With retry
results = parallel_starmap(fetch_with_auth, [(url, token) for url in urls],
                           workers=10, retry=Retry(attempts=3))
```

Also available as `.starmap()` on `@parallel` decorated functions:

```python
@parallel(workers=4)
def add(a, b): return a + b

add.starmap([(1, 2), (3, 4)])  # ParallelResult([3, 7])
```

---

## `parallel_iter`

Streaming version of `parallel_map` — yields `ItemResult` in completion order.
Results are **not accumulated in memory**.

```python
from pyarallel import parallel_iter

for item in parallel_iter(fn, items, workers=4, batch_size=1000):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)
```

### When to Use

- **`parallel_map`** — results fit in memory, you need `ParallelResult` features (.ok, .successes(), .failures())
- **`parallel_iter`** — large-scale processing where results should be consumed and discarded (10M+ items, ETL pipelines, streaming to DB)

### Parameters

Same as `parallel_map` except no `timeout` or `on_progress` (results stream as they complete).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | `Callable` | required | Function to apply to each item |
| `items` | `Iterable` | required | Any iterable |
| `workers` | `int \| None` | `None` | Number of parallel workers (stdlib default when `None`) |
| `executor` | `"thread" \| "process"` | `"thread"` | Thread pool or process pool |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Rate limiting |
| `batch_size` | `int \| None` | `None` | Process in chunks (controls memory) |
| `retry` | `Retry \| None` | `None` | Per-item retry |

### Yields

`ItemResult[T]` — each item includes `.index`, `.ok`, `.value`, and `.error`.
Results arrive in **completion order** (not input order).

Also available as `.stream()` on `@parallel` decorated functions:

```python
@parallel(workers=8)
def process(item): ...

for item in process.stream(huge_list, batch_size=1000):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)
```

---

## `ItemResult`

Single streaming result item returned by `parallel_iter`, `async_parallel_iter`,
and `.stream()`.

### Properties

| Member | Returns | Description |
|---|---|---|
| `.index` | `int` | Original input index |
| `.ok` | `bool` | `True` when this item succeeded |
| `.value` | `T \| None` | Result value for successful items. May legitimately be `None` |
| `.error` | `Exception \| None` | Exception for failed items |

### Invariant

Exactly one of `.value` or `.error` is set. Success and failure are explicit.

### Example

```python
for item in parallel_iter(process, items, workers=4):
    if item.ok:
        handle(item.index, item.value)
    else:
        log(item.index, item.error)
```

---

## `ParallelResult`

Container for parallel execution results. Behaves like a `list` when all tasks succeed. Provides structured access when some fail.

### Properties and Methods

| Member | Returns | Description |
|---|---|---|
| `.ok` | `bool` | `True` if every task succeeded |
| `.values()` | `list[R]` | All results in order. Raises `ExceptionGroup` on failure |
| `.successes()` | `list[tuple[int, R]]` | `(index, value)` for each success |
| `.failures()` | `list[tuple[int, Exception]]` | `(index, exception)` for each failure |
| `.raise_on_failure()` | `None` | Raises `ExceptionGroup` if any task failed |
| `len(result)` | `int` | Total number of tasks |
| `result[i]` | `R` | Index into values (raises on failure) |
| `list(result)` | `list[R]` | Iterate values (raises on failure) |
| `bool(result)` | `bool` | `True` if any tasks were submitted |

### Example

```python
result = parallel_map(process, items, workers=4)

if result.ok:
    for value in result:
        print(value)
else:
    for idx, value in result.successes():
        save(idx, value)
    for idx, error in result.failures():
        log(idx, error)
```

---

## `RateLimit`

Immutable rate limit specification.

```python
from pyarallel import RateLimit

rate = RateLimit(count, per="second", burst=1)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `count` | `float` | required | Operations allowed per interval |
| `per` | `"second" \| "minute" \| "hour"` | `"second"` | Time interval |
| `burst` | `int` | `1` | Token-bucket capacity: calls that may fire immediately before the sustained rate applies |

### Property

- `per_second` → `float`: Rate converted to operations per second

### Examples

```python
RateLimit(10)                        # 10 per second, evenly spaced
RateLimit(100, "minute")             # 100 per minute, evenly spaced
RateLimit(100, "minute", burst=20)   # up to 20 at once, then refill pace
```

To share one budget across several calls or functions, wrap the spec in a
[`Limiter`](rate-limiting.md#sharing-one-budget-limiter).

Or use the shorthand — pass a number directly to `rate_limit=`:

```python
parallel_map(fn, items, rate_limit=10)  # same as RateLimit(10)
```

---

## `Retry`

Immutable per-item retry configuration. Retries happen inside each worker — only the failing item is retried, not the whole batch.

```python
from pyarallel import Retry

retry = Retry(attempts=3, backoff=1.0)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `attempts` | `int` | `3` | Total attempts (1 = no retry, 3 = up to 2 retries) |
| `backoff` | `float` | `1.0` | Base delay for exponential backoff |
| `max_delay` | `float` | `60.0` | Maximum sleep between retries |
| `jitter` | `bool` | `True` | Randomize delays to avoid retry bursts |
| `on` | `tuple[type[Exception], ...] \| None` | `None` | Retry only these exception types. `None` means retry all exceptions |
| `retry_if` | `Callable[[Exception], bool] \| None` | `None` | Predicate on the exception instance. Both `on` and `retry_if` must pass |
| `wait_from` | `Callable[[Exception], float \| None] \| None` | `None` | Extract a server-mandated wait in seconds (e.g. `Retry-After`). `None` falls back to backoff |

### Backoff Behavior

With `backoff=1.0`, `attempts=3`, and the default exponential logic:

- Attempt 1: immediate
- Attempt 2: sleep 1.0s, then retry
- Attempt 3: sleep 2.0s, then retry

The actual delay formula is `min(backoff * 2^attempt, max_delay)`, optionally
multiplied by jitter.

### Examples

```python
# Basic retry — 3 attempts, exponential backoff starting at 1s
results = parallel_map(fetch, urls, retry=Retry())

# Retry only transient network failures
results = parallel_map(
    fetch,
    urls,
    retry=Retry(on=(ConnectionError, TimeoutError)),
)

# Retry with custom caps and deterministic delays
results = parallel_map(
    fetch,
    urls,
    retry=Retry(attempts=5, backoff=0.5, max_delay=5.0, jitter=False),
)

# Retry + rate limit + batch — the full production stack
results = parallel_map(
    call_api, ids,
    workers=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, backoff=1.0),
    batch_size=500,
)
```

### Server-Driven Backoff

Real APIs speak 429 + `Retry-After`. `retry_if` decides retryability from the
exception *instance*, and `wait_from` extracts the server-mandated wait — it
replaces the backoff delay with no jitter and no `max_delay` cap, because the
server knows better than we do:

```python
def parse_retry_after(exc):
    header = getattr(exc.response, "headers", {}).get("Retry-After")
    return float(header) if header else None

results = parallel_map(
    call_api, ids,
    rate_limit=limiter,   # a shared Limiter
    retry=Retry(
        attempts=5,
        on=(httpx.HTTPStatusError,),
        retry_if=lambda exc: exc.response.status_code in (429, 503),
        wait_from=parse_retry_after,
    ),
)
```

When a shared `Limiter` is in play, a server-mandated wait also pauses the
limiter — even if that task is on its final attempt — so one task's 429 slows
the whole pool instead of every worker discovering the throttle separately.
(With `executor="process"` the pause stays local to each worker: a limiter
cannot cross process boundaries.)
