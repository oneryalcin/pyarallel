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
    executor="thread",               # "thread", "process", or "interpreter" (3.14+)
    rate_limit=None,                 # RateLimit spec, shared Limiter, or ops/second
    timeout=None,                    # Total timeout in seconds
    on_progress=None,                # callback(completed, total)
    window_size=None,                # Admission window: max unresolved items
    retry=None,                      # Retry(attempts=3, backoff=1.0)
    checkpoint=None,                 # Path to a resume file (SQLite)
    checkpoint_key=None,             # Stable per-item identity for resume
    max_errors=None,                 # Abort after N failures
    sequential=False,                # Debug mode: run inline, no pool
    worker_init=None,                # Run once per worker before tasks
    max_tasks_per_worker=None,       # Recycle process workers (process only)
)
```

**Returns:** `ParallelResult[R]`

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | `Callable` | required | Function to apply to each item |
| `items` | `Iterable` | required | Any iterable (list, generator, range, set, ...) |
| `workers` | `int \| None` | `None` | Number of parallel workers. `None` lets the executor choose |
| `executor` | `"thread" \| "process" \| "interpreter"` | `"thread"` | Thread pool, process pool, or (Python 3.14+) sub-interpreter pool. Interpreter follows the process rules — importable module-level functions, no shared limiter, no contextvars — and C extensions without subinterpreter support (numpy among them) fail with `ImportError` inside workers |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Rate limiting (float = ops/second). Pass a shared `Limiter` to draw from one budget across calls |
| `timeout` | `float \| None` | `None` | Total wall-clock timeout in seconds. Sets `result.timed_out` on expiry; the source is never drained after a stop |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback `(completed, total)`. For unsized iterables, `total` is items seen so far |
| `window_size` | `int \| None` | `None` | Admission window: max items submitted but unresolved (default `2 × workers`). A lookahead/memory bound, not a chunk size — no barriers, input consumed lazily |
| `retry` | `Retry \| None` | `None` | Per-item retry with backoff |
| `checkpoint` | `str \| Path \| None` | `None` | Checkpoint file for resumable runs — completed items load from disk on rerun. **Contains pickle: treat the file like code** — never resume from a file you didn't create ([details](../user-guide/advanced-features.md#checkpoint-resume)) |
| `checkpoint_key` | `Callable[[T], str \| int \| bytes] \| None` | `None` | Stable per-item identity — rows keyed by identity instead of position, so evolving inputs keep their completed work. Requires `checkpoint=` |
| `checkpoint_version` | `str \| int \| bytes \| tuple \| None` | `None` | Semantic token joining checkpoint identity — the config function inspection can't see (prompt, model). Changed token fails closed with both versions in the error. Requires `checkpoint=` |
| `max_errors` | `int \| None` | `None` | Abort after this many failures (counted after retries). Unrun items are marked `Aborted` |
| `stop` | `StopToken \| None` | `None` | Cooperative cancel: `token.stop()` (thread/signal-safe) ceases admission, keeps completed checkpoint rows, reports `RunStatus.CANCELLED`, marks unresolved items `Cancelled`. See [Cooperative Stop](../user-guide/advanced-features.md#cooperative-stop-stoptoken) |
| `sequential` | `bool` | `False` | Run every item inline in the calling thread — no pool, real stack traces, working breakpoints. `workers` is ignored |
| `worker_init` | `Callable[[], None] \| None` | `None` | Run once per worker before it takes tasks (one DB connection / model per worker). Must be picklable for `executor="process"`; for `executor="interpreter"` it must be a module-level function in an importable module |
| `max_tasks_per_worker` | `int \| None` | `None` | Recycle each process worker after N tasks (native-leak guard). Requires `executor="process"` |

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

# Wider admission window — more lookahead for uneven task durations
results = parallel_map(process, million_items, workers=8, window_size=500)

# With retry — flaky network calls
results = parallel_map(fetch, urls, workers=10, retry=Retry(attempts=3, backoff=1.0))

# Resumable — crash at item 40k does not restart from zero
results = parallel_map(embed, chunks, checkpoint="embeddings.ckpt")

# Abort early — a dead API costs tens of calls, not thousands
results = parallel_map(fetch, urls, workers=10, max_errors=10)
```

### Debug Mode with `sequential=True`

`sequential=True` runs every item inline in the calling thread: no pool,
no futures — real stack traces, working breakpoints, deterministic input
order. It honors `rate_limit`, `retry`, `checkpoint`, `on_progress`, and
`max_errors`; `timeout` is checked between items only (an in-flight item
cannot be interrupted). `workers` is ignored rather than rejected, so a
single flag can flip production code into debug mode:

```python
results = parallel_map(fetch, urls, workers=10,
                       sequential=os.environ.get("DEBUG") == "1")
```

The async API doesn't need it — `concurrency=1` already serializes.

### Context Variables

`contextvars` set by the caller are visible inside thread-executor tasks:
each item runs under a fresh copy of the submitting thread's context, so
correlation IDs and request-scoped state survive into workers (they used
to silently vanish). Writes inside tasks land in the copy and never leak
back to the caller. Process workers are skipped — contexts don't pickle.

### Worker Lifecycle

`worker_init=` runs once in each worker before it takes tasks — the
place for one DB connection or one loaded model per worker instead of
per item. With `executor="process"` it must be a module-level function
(fail-fast `ValueError` otherwise). Note: the initializer's contextvars
are invisible to tasks (tasks run under the caller's context) — use
globals or thread-locals for per-worker state.

`max_tasks_per_worker=` recycles each **process** worker after N tasks —
the standard guard against native-library memory leaks. With
`executor="thread"` or `executor="interpreter"` it raises `ValueError`
(explicit beats silent ignore — only process pools recycle workers).

### Early Abort with `max_errors`

With `max_errors=N`, the run stops once N items have failed (counted
**after** retries are exhausted — an item that fails then succeeds on
retry is a success), and `result.aborted` is set. Because all admission
is windowed (`window_size` if set, else `2 × workers`), the abort is
cheap by construction: total submissions stay within the abort point
plus one window.

On abort you still get a complete `ParallelResult` — one entry per input
item. Finished work keeps its real result; items that never ran are
marked with an `Aborted` exception, distinguishable from real failures:

```python
from pyarallel import Aborted

result = parallel_map(fetch, urls, workers=10, max_errors=10)
real = [(i, e) for i, e in result.failures() if not isinstance(e, Aborted)]
unrun = [i for i, e in result.failures() if isinstance(e, Aborted)]
```

Composes with `timeout=` (whichever fires first wins, each marking its
own failure type) and with `checkpoint=` — completed successes are
already persisted, so the aborted job resumes exactly where it stopped.
Streaming APIs take `max_errors` too: the stream simply ends after the
Nth failure is yielded, with no placeholder items. In `ordered=True`
mode the ending failure is still delivered in input order — admission
has stopped, so the wait is bounded to the window in *items*, but not
in time: a task that never completes blocks the ordered stream, exactly
as it blocks every other sync call (threads cannot be cancelled). Put
timeouts inside your function, or use the default unordered mode for
the promptest abort on a dead API. Completed-but-unyielded successes
behind the ending failure are discarded.

The input source is **never consumed after the stop** — a blocking or
infinite generator stays untouched. Sized inputs get one `Aborted`
entry per unseen item (by count); unsized inputs return a result
covering only the items actually pulled from the source.

### Checkpoint / Resume

With `checkpoint=` set, every completed item's result is written to a SQLite
file as it finishes. Rerunning the same call resumes: completed items load
from disk, failed and unseen items execute. Rows are keyed by item index plus
a fingerprint of the item, so a changed input at the same position is
recomputed, never served stale. The file is also bound to the mapped
function's identity — name, code digest, and visible captured config
(defaults, closure values, `functools.partial` arguments): resuming with a
different, edited, or reconfigured function raises `CheckpointError` —
stale reuse fails closed, never silently. Live objects in captured state
count by type only (config inside them is invisible — delete the file when
it changes); bound methods and callable objects are rejected because their
entire state is opaque. `CheckpointError` is raised plainly by both the
sync and async APIs — never wrapped in an `ExceptionGroup`.

Constraints, stated honestly:

- **Items and results must be picklable.** A result that cannot be
  checkpointed aborts the run with `CheckpointError` — the alternative
  (mislabeling a successful item as failed, or continuing with a checkpoint
  that cannot resume) would lie to you.
- **Rows are positional by default.** Reordering, prepending, or removing
  input items shifts indices; the fingerprint check then forces every
  shifted item to recompute. Pass `checkpoint_key=` when inputs evolve:

  ```python
  parallel_map(fetch, users, checkpoint="run.ckpt",
               checkpoint_key=lambda u: u.id)
  ```

  Rows are then keyed by identity — prepending an item runs only the new
  item, reordering recomputes nothing. Keys must be unique per run
  (duplicates raise `CheckpointError`); keys are type-tagged, so `1`,
  `"1"`, and `b"1"` are three distinct rows; the fingerprint check still
  applies — a changed payload under the same key recomputes. Changing the
  key function just changes the keys (a cache miss, never a wrong hit).
- **Checkpoint files are schema v2.** Files written by earlier versions
  fail closed with a `CheckpointError` telling you to delete them — no
  silent migration.
- **One run per file at a time.** Failures are never checkpointed; a
  resumed run retries them. Sharing one file between concurrently running
  jobs is not supported.

Available on `parallel_map`, `async_parallel_map`, and `.map()` — not on
starmap or the streaming APIs.

### Notes on Progress and Unsized Iterables

When `items` has a known length, `on_progress(done, total)` reports the final
total.

When `items` is unsized (for example a generator) and `window_size` is set,
Pyarallel keeps input consumption lazy instead of materializing the full input
up front. In that mode, `total` is the number of items discovered so far, not a
guaranteed final total.

---

## `@parallel`

Decorator that adds `.map()` for parallel execution. The decorated function **keeps its original signature and return type**.

```python
@parallel(workers=4, executor="thread", rate_limit=None,
          retry=None, timeout=None, window_size=None,
          max_errors=None, on_progress=None)
def fn(item): ...
```

### Parameters

Decorator defaults are *properties of the function's behavior* — "this
fetcher retries 429s" belongs at the decorator. Any per-call keyword
overrides them (presence is the sentinel: unpassed inherits, explicit —
even `None` — overrides).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `workers` | `int \| None` | `None` | Default worker count for `.map()` |
| `executor` | `"thread" \| "process" \| "interpreter"` | `"thread"` | Default executor type |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Default rate limiting |
| `retry` | `Retry \| None` | `None` | Default retry policy (v0.10) |
| `timeout` | `float \| None` | `None` | Default total timeout for `.map()`/`.starmap()` — ignored by `.stream()` (no total deadline in streaming) |
| `window_size` | `int \| None` | `None` | Default admission window |
| `max_errors` | `int \| None` | `None` | Default early-abort threshold |
| `on_progress` | `Callable \| None` | `None` | Default progress callback |

**Deliberately not accepted** (run-scoped, not function-scoped):
`checkpoint`/`checkpoint_key`/`checkpoint_version` — a checkpoint file
names a *run*; two `.map()` calls sharing a default file would collide
keys and serve wrong resumes. `stop` — a token is a campaign latch.
Pass these per call.

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

Takes the same options as `parallel_map` (workers, executor, rate_limit, timeout, window_size, retry).

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

A bounded window of items is in flight at any moment: memory stays
constant, input is consumed lazily (generators are never materialized),
and a slow item delays only itself — items behind it keep streaming.

```python
from pyarallel import parallel_iter

for item in parallel_iter(fn, items, workers=4):
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
| `executor` | `"thread" \| "process" \| "interpreter"` | `"thread"` | Thread pool, process pool, or (3.14+) sub-interpreter pool — see [parallel_map](#parallel_map) |
| `rate_limit` | `Limiter \| RateLimit \| float \| None` | `None` | Rate limiting |
| `window_size` | `int \| None` | `None` | Maximum items in flight (default `2 × workers`) |
| `retry` | `Retry \| None` | `None` | Per-item retry |
| `ordered` | `bool` | `False` | Yield in input order instead of completion order |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | `callback(done, total)` per completed item |

!!! note "Changed in v0.5 (streaming) and v0.6 (everywhere)"
    `window_size` is an **in-flight bound**, not a chunk size — one
    meaning across every API since v0.6. Earlier versions processed
    chunks with a barrier between them (one slow item stalled the next
    chunk) and materialized the whole input when `window_size` was
    unset. Neither is true anymore — the default window of `2 × workers`
    already gives constant memory; raise `window_size` only to increase
    lookahead.

### Yields

`ItemResult[T]` — each item includes `.index`, `.ok`, `.value`, and `.error`.
Results arrive in **completion order** by default; pass `ordered=True`
for input order.

### Ordered streaming

With `ordered=True`, completed items that arrive early wait in a reorder
buffer. The window bounds in-flight **plus** buffered items, so memory
stays constant even when one slow item holds back the stream — admission
stalls until it completes (that's backpressure, not a hang).

```python
for item in parallel_iter(fetch, urls, workers=8, ordered=True):
    print(item.index)  # 0, 1, 2, ... regardless of completion order
```

### Progress

`on_progress` fires per **completed** item, before its yield — same
contract as `parallel_map`. For unsized inputs `total` is the number of
items consumed from the source so far.

### Stopping early

Breaking out of the loop stops the engine: nothing new is submitted and
not-yet-started tasks are cancelled. Tasks already running in a worker
thread or process cannot be interrupted and finish in the background
(at most one window's worth).

Also available as `.stream()` on `@parallel` decorated functions:

```python
@parallel(workers=8)
def process(item): ...

for item in process.stream(huge_list):
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
| `.attempts` | `int` | Attempts actually made (`1` = no retry needed) |
| `.duration` | `float` | Seconds from the start of the first attempt to the final outcome — **including** retry backoff sleeps, **excluding** queue wait |

### Invariant

Exactly one of `.value` or `.error` is set. Success and failure are explicit.

### Accounting example

```python
for item in parallel_iter(fetch, urls, workers=8, retry=Retry(attempts=3)):
    metrics.observe(latency=item.duration, retries=item.attempts - 1)
```

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

Container for parallel execution results. Behaves like a `list` when the
run completed and all tasks succeeded. Provides structured access
otherwise.

`.status` (a `RunStatus`: `COMPLETED` / `TIMED_OUT` / `ABORTED`) reports
how the run *ended* — the reliable signal when per-item markers can't
carry it: an unsized input that hits `timeout=` returns only the items
actually pulled (possibly all successes), and the status is what
distinguishes that truncation from a completed run. Since v0.8, **a
truncated run is never `ok`** and its "whole result" accessors —
`.values()`, iteration, indexing — raise (`TimeoutError` / `Aborted`)
instead of quietly returning a partial list. Use `.successes()` /
`.ok_values()` to consume partial results deliberately.

### Properties and Methods

| Member | Returns | Description |
|---|---|---|
| `.ok` | `bool` | `True` when the run **completed** and every task succeeded |
| `.status` | `RunStatus` | How the run ended: `COMPLETED`, `TIMED_OUT`, or `ABORTED` |
| `.complete` | `bool` | Source exhausted, every item resolved — independent of failures: a run can be complete and not ok |
| `.timed_out` | `bool` | Derived: `status is RunStatus.TIMED_OUT` |
| `.aborted` | `bool` | Derived: `status is RunStatus.ABORTED` |
| `.values()` | `list[R]` | All results in order. Raises `TimeoutError`/`Aborted` on truncation (checked first), `ExceptionGroup` on failure |
| `.ok_values()` | `list[R]` | Values of successful tasks only, in input order. Never raises |
| `.successes()` | `list[tuple[int, R]]` | `(index, value)` for each success |
| `.failures()` | `list[tuple[int, Exception]]` | `(index, exception)` for each failure |
| `.item_results()` | `list[ItemResult[R]]` | Every item as an `ItemResult` (index/value/error/**attempts/duration**), input order. Never raises. The collected mirror of streaming `ItemResult`. Checkpoint hits and truncation placeholders carry `attempts=0, duration=0.0` (nothing ran this run) |
| `.raise_on_failure()` | `None` | Raises `ExceptionGroup` if any task failed; each sub-exception carries its item index as a PEP 678 note (`except*` matching untouched) |
| `len(result)` | `int` | Number of tasks with a result entry |
| `result[i]` | `R` | Index into values (raises on failure/truncation) |
| `list(result)` | `list[R]` | Iterate values (raises on failure/truncation) |
| `bool(result)` | `bool` | `True` if any tasks were submitted |

### Example

```python
result = parallel_map(process, items, workers=4)

if result.ok:
    for value in result:
        print(value)
else:
    good = result.ok_values()          # just the values that succeeded
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
| `max_server_wait` | `float \| None` | `600.0` | Ceiling on honored server waits — a malformed `Retry-After: 86400` must not pin a worker for a day. `None` trusts the server unconditionally |

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
    window_size=500,
)
```

### `Retry.for_http()` — the prewired HTTP policy

The 429/`Retry-After` dance is one call (v0.9):

```python
results = parallel_map(
    call_api, ids,
    rate_limit=limiter,   # a shared Limiter
    retry=Retry.for_http(on=(httpx.HTTPStatusError,)),
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `on` | `tuple[type[Exception], ...]` | required | Exception types to consider |
| `statuses` | `set[int]` | `{429, 503}` | Retry only these HTTP statuses. Members of `on` carrying no status (connection errors) are always retried |
| `response` | `Callable[[Exception], Any] \| None` | `None` | How to find the response. Default duck-types: `exc.response` when present (httpx, requests), else the exception itself (aiohttp) |
| `attempts` / `backoff` / `max_delay` / `jitter` / `max_server_wait` | — | as `Retry` | Passed through |

What it handles that hand-rolls routinely get wrong:

- **Both `Retry-After` dialects** — numeric seconds *and* HTTP-date
  (`Retry-After: Fri, 10 Jul 2026 08:00:00 GMT`). Homemade parsers hit
  the date form and either crash or retry instantly — a stampede.
- **Malformed values fall back** to exponential backoff, never raise.
- **No HTTP client import** — pure duck typing; works with httpx,
  requests, and aiohttp exception shapes out of the box.
- Returns a plain frozen `Retry`, so everything composes — including
  the shared-limiter pause below, and process-executor pickling.

### Server-Driven Backoff (under the hood)

Real APIs speak 429 + `Retry-After`. `retry_if` decides retryability from the
exception *instance*, and `wait_from` extracts the server-mandated wait — it
replaces the backoff delay with no jitter and no `max_delay` cap, because the
server knows better than we do. `Retry.for_http()` is exactly this, prewired:

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

Server waits are honored up to `max_server_wait` (default 10 minutes) — a
guard against a hostile or misconfigured server pinning worker threads for
hours. Set it to `None` to trust the server unconditionally.

When a shared `Limiter` is in play, a server-mandated wait also pauses the
limiter — even if that task is on its final attempt — so one task's 429 slows
the whole pool instead of every worker discovering the throttle separately.
Every retry attempt also draws a fresh rate-limit token: a retry is a real
API call and never bypasses the limiter. (With `executor="process"` neither
applies inside workers: a limiter cannot cross process boundaries, so process
retries pace only by backoff. A `Retry` carrying lambda predicates is
rejected at submit time for process executors — its callables must be
module-level functions to be picklable.)

One footgun to avoid: `retry_if` and `wait_from` receive *every* exception
that `on=` lets through. A predicate like `lambda exc: exc.response.status_code`
raises `AttributeError` on a `ConnectionError`, and that error replaces the
original in the failure report. Narrow with `on=` first, or write predicates
defensively (`getattr(exc, "response", None)`).
