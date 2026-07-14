# Advanced Features

## Progress Tracking

Track progress for long-running operations:

```
from pyarallel import parallel_map

def my_progress(done, total):
    print(f"\r{done}/{total} ({100*done//total}%)", end="", flush=True)

results = parallel_map(process, items, workers=8, on_progress=my_progress)
```

If `items` has a known length, `total` is the final input size. If `items` is an unsized iterable (for example a generator), input is consumed lazily and `total` is the number of items *admitted* so far — one window ahead of completions, growing as the run progresses, so a percentage over it is meaningless. Pass a sized input when you need a real total.

Works with both `parallel_map` and `.map()`:

```
@parallel(workers=4)
def fetch(url): ...

results = fetch.map(urls, on_progress=lambda d, t: print(f"{d}/{t}"))
```

### tqdm Integration

Wire `on_progress` to a tqdm progress bar:

```
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

Note

tqdm works best with known-size inputs (lists, ranges). For generators the total grows as input is consumed, which makes the progress bar jump — use a plain `on_progress` callback instead.

## Timeouts

### Total Timeout (sync)

Set a wall-clock limit for the entire operation:

```
results = parallel_map(fetch, urls, workers=10, timeout=30.0)

# Tasks that didn't complete are marked as failures
if results.timed_out:
    for idx, exc in results.failures():
        if isinstance(exc, TimeoutError):
            print(f"Item {idx} timed out")
```

`results.timed_out` (i.e. `results.status is RunStatus.TIMED_OUT`) is the reliable expiry signal. For sized inputs every unfinished slot is also marked with a `TimeoutError` failure; an unsized input (a generator) instead returns a **shorter** result covering only the items actually pulled — the source is never drained after a stop, so a blocking or infinite generator stays untouched.

A timed-out run is never `ok` (v0.8) — even when every *returned* item succeeded, the run is a truncation, and `.values()`/iteration/indexing raise `TimeoutError` rather than passing a partial list off as the whole. Consume partial results deliberately with `.successes()` or `.ok_values()`.

### Per-Task Timeout (async)

The async API supports per-task timeouts via `asyncio.wait_for`:

```
from pyarallel import async_parallel_map

results = await async_parallel_map(fetch, urls, concurrency=10, task_timeout=5.0)
# Each individual task gets 5 seconds before timing out
```

## Retry

Built-in per-item retry with exponential backoff and jitter:

```
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

Real APIs tell you how long to back off. For the standard HTTP dance, `Retry.for_http()` (v0.9) is prewired — both `Retry-After` dialects (numeric seconds *and* HTTP-date), malformed values falling back to exponential backoff, duck-typed against httpx/requests/aiohttp exception shapes with no client import:

```
results = parallel_map(
    call_api, ids,
    rate_limit=limiter,   # a shared Limiter — see below
    retry=Retry.for_http(on=(httpx.HTTPStatusError,)),   # 429/503 default
)
```

Under the hood it fills two hooks you can also wire yourself when the policy isn't plain HTTP: `retry_if` decides retryability from the exception *instance*; `wait_from` extracts the server-mandated wait, which replaces the backoff delay (no jitter) and pauses the shared limiter so the whole pool slows down:

```
results = parallel_map(
    call_api, ids,
    rate_limit=limiter,
    retry=Retry(
        attempts=5,
        on=(httpx.HTTPStatusError,),
        retry_if=lambda exc: exc.response.status_code in (429, 503),
        wait_from=lambda exc: parse_retry_after(exc.response),
    ),
)
```

Server waits are clamped by `max_server_wait` (default 10 minutes) so a malformed `Retry-After: 86400` can't pin a worker for a day. Write predicates defensively — they receive every exception `on=` lets through.

For the full `Retry` API, see [API Reference](https://oneryalcin.github.io/pyarallel/api-reference/core/#retry).

## Shared Rate Limits and Burst

A `RateLimit` passed directly creates a private limiter per call. When the quota belongs to an API key — the usual case — share one `Limiter`:

```
from pyarallel import Limiter, RateLimit

limiter = Limiter(RateLimit(100, "minute", burst=20))

users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)  # same budget
```

`burst` is the token-bucket capacity: how many calls may fire immediately before the sustained rate applies. The default of 1 gives smooth, evenly spaced pacing. One instance works concurrently across threads *and* event loops — sync and async calls can share a budget. Details in [Rate Limiting](https://oneryalcin.github.io/pyarallel/api-reference/rate-limiting/index.md).

## Checkpoint / Resume

For long jobs, `checkpoint=` persists every completed item's result to a SQLite file as it finishes. Rerunning the same call resumes — cached items load from disk, failed and unseen items execute:

```
result = parallel_map(embed, chunks, checkpoint="embeddings.ckpt")
# crashed at item 40,000? rerun the same line — only the remainder runs
```

Safety guards, stated honestly:

- The file is bound to the mapped function's identity: name, code digest, and visible captured config (default values, closure values, `functools.partial` arguments). An edited function or a changed `factor=3` raises `CheckpointError` — stale reuse fails closed, never silently.
- Live objects in captured state (clients, sessions) count by *type* only — config hidden inside them is invisible to inspection. For exactly that config — a prompt, a model name, an environment — declare it (v0.9):

```
result = parallel_map(classify, tickets, checkpoint="classify.ckpt",
                      checkpoint_version=("classify-v3", MODEL, PROMPT_SHA))
```

Change the prompt → the token changes → the rerun fails closed with both tokens in the error, instead of silently stitching 40k old-prompt answers to 10k new-prompt ones. Tokens are `str`/`int`/`bytes` or tuples of those (stable reprs only — a dict would invalidate on every run). Bound methods and callable objects are rejected outright: their entire state is opaque.

- A changed input at the same position is recomputed, never served stale.
- Rows are positional by default: reordering or inserting inputs forces shifted items to recompute. For evolving inputs, key rows by identity:

```
result = parallel_map(fetch, users, checkpoint="run.ckpt",
                      checkpoint_key=lambda u: u.id)
# next week: three new users prepended — only they run
```

Duplicate keys raise `CheckpointError`; keys are type-tagged (`1` vs `"1"` vs `b"1"` never collide); a changed payload under the same key still recomputes. The same identity is also exposed as `ItemResult.key` on live callbacks and `result.item_results()`. You do not need to repeat the function as `item_key=`: when `item_key` is omitted, `checkpoint_key` supplies the result key automatically and is evaluated only once per item. If you explicitly pass the same callable for both options, Pyarallel still evaluates it only once. Distinct callables each run once and may return different checkpoint and application identities.

- Checkpoint files from pyarallel < 0.5 fail closed — delete and rerun.
- Items and results must be picklable; a result that cannot be checkpointed aborts the run with `CheckpointError`.
- A corrupted row raises `CheckpointError` with delete-to-start-fresh instructions, never a raw unpickling error.

Checkpoint files are code, not data

Rows are stored as **pickle** — resuming from a checkpoint executes whatever its pickle streams contain. Anyone who can *write* the file can run code in your process on the next resume. Never resume from a file you didn't create; never accept one from an untrusted source (a bug report, a shared bucket). Pyarallel creates new checkpoint files owner-only (`0o600`, POSIX) and leaves existing files' permissions alone — but a directory writable by others (`/tmp`-like locations) is not a safe home for one regardless.

Available on `parallel_map`, `async_parallel_map`, and `.map()`.

### Inspect Before You Resume

Use `checkpoint_info()` when an operator needs to identify a checkpoint before deciding whether to resume it or remove it. Inspection counts rows without loading their pickled values:

```
from pathlib import Path

from pyarallel import checkpoint_info


checkpoint = Path("classify.ckpt")
expected = ("classify-v3", "gpt-5")
info = checkpoint_info(checkpoint)

if info.checkpoint_version == expected:
    print(f"resume candidate: {info.completed} rows already persisted")
else:
    checkpoint.unlink()  # deliberate policy choice: start this campaign fresh
```

`CheckpointInfo` also exposes `schema_version`, `task_signature`, and the primary database's pre-open `size_bytes`. The value is frozen, and all database metadata plus `completed` come from one coherent read snapshot. A concurrent writer can commit immediately afterward, so this is an inspection receipt, not a live monitor.

Know what inspection cannot prove

Inspection does not compare the stored identity with your current function, does not know how many input items exist, and does not report a run status or external-delivery state. `completed` means persisted rows only. The reader does not unpickle result values, but the file must still be treated as untrusted input: maliciously huge metadata can consume parser resources. SQLite may also create or update `-wal`/`-shm` sidecars while opening a WAL-mode checkpoint logically read-only.

Outside checkpointing, `item_key=` is available on map, starmap, and streaming APIs, sync and async. Its values may repeat because they are descriptive application identities; only checkpoint row keys must be unique.

## Cooperative Stop — `StopToken`

SIGTERM during a deploy, the notebook stop button, a spend-limit watchdog: long runs need a way to say *land the plane* (v0.9):

```
import signal
from pyarallel import StopToken, RunStatus, parallel_map

token = StopToken()
signal.signal(signal.SIGTERM, lambda *_: token.stop())

result = parallel_map(call_api, items, stop=token, checkpoint="run.ckpt")
if result.status is RunStatus.CANCELLED:
    ...  # completed rows are already in the checkpoint; rerun resumes
```

`stop()` is thread-safe, idempotent, and signal-handler-safe. On stop: admission ceases, cancellable work is cancelled, completed checkpoint rows are kept, and the result reports `RunStatus.CANCELLED` with unresolved slots marked `Cancelled`. Cancel latency is ~100ms even while rate-limit-paced (waits are sliced when a token is present).

Stated honestly: the **async** engine cancels in-flight tasks; the **sync** engine cannot kill running threads — in-flight items finish in the background (their slots still read `Cancelled`: the result was not delivered to this run). A token is a latch, not a pulse — once stopped, every run given it cancels immediately; use a fresh token per campaign.

Collected APIs only (`parallel_map` / `async_parallel_map` and decorator `.map()`): streaming already has cooperative stop by construction — `break` out of the loop (sync) or close the stream (async, `aclosing`).

## Early Abort — `max_errors`

Stop paying for a dead API. With `max_errors=N` the run aborts once N items have failed (counted after retries are exhausted) and returns partial results:

```
from pyarallel import Aborted

result = parallel_map(fetch, urls, workers=10, max_errors=10)

if result.aborted:
    real  = [(i, e) for i, e in result.failures() if not isinstance(e, Aborted)]
    unrun = [i for i, e in result.failures() if isinstance(e, Aborted)]
```

Because admission is windowed, the abort is genuinely cheap — a 10k-item job against a dead API costs tens of calls, not thousands. Finished items keep their real results; never-run items are marked `Aborted`, and `result.aborted` reports the early stop.

The overnight-job combo is `max_errors` + `checkpoint`: the job aborts cheaply when the API dies, successes are already persisted, and the morning rerun resumes exactly where it stopped:

```
result = parallel_map(fetch, users, workers=10,
                      max_errors=10, checkpoint="nightly.ckpt")
```

Streaming APIs accept `max_errors` too — the stream ends after the Nth failure is yielded, with no placeholder items for unseen input.

## The Admission Window

Every API — collected and streaming — admits work through a bounded window: at most `window_size` items (default `2 × workers`) are submitted but unresolved at any moment. Input is consumed lazily, one window ahead, so generators are never materialized:

```
# At most 500 items in flight instead of the default 2 x workers
results = parallel_map(process, huge_list, workers=8, window_size=500)
```

There are no chunks and no barriers — a slow item never stalls the items behind it, and an error anywhere never prevents the rest from running.

## Starmap — Multi-Argument Functions

For functions that take multiple arguments, use `parallel_starmap` or `.starmap()`:

```
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

```
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

The engine keeps a bounded window of items in flight (default `2 × workers`; set `window_size` to change it). Input is consumed lazily — generators are never materialized — and a slow item delays only itself: there are no batch barriers. Breaking out of the loop stops submission and cancels not-yet-started tasks; tasks already running in a worker finish in the background.

Results arrive in **completion order** (fastest tasks first), not input order. Each `ItemResult` includes the original `.index` so you can match results to inputs. Pass `ordered=True` to yield in input order instead — early results wait in a reorder buffer that is counted inside the window, so memory stays bounded even behind a straggler. Streaming also takes `on_progress=` with the same contract as `parallel_map`.

**When to use which:**

| API                           | Memory                | Use case                                         |
| ----------------------------- | --------------------- | ------------------------------------------------ |
| `.map()` / `parallel_map`     | All results in memory | Results fit in memory, need `.ok`, `.failures()` |
| `.stream()` / `parallel_iter` | Constant (one window) | ETL, streaming to DB, 10M+ items                 |

## Structured Error Handling

`ParallelResult` never silently drops errors:

```
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

When you iterate or call `.values()`, failures raise an `ExceptionGroup` — and each sub-exception carries its item index as a PEP 678 note, so provenance survives without changing exception types (`except* ConnectionError` still matches):

```
try:
    values = list(result)
except ExceptionGroup as eg:
    print(f"{len(eg.exceptions)} tasks failed")
    for exc in eg.exceptions:
        print(f"  {type(exc).__name__}: {exc}")   # notes show "item index N"
```

A *truncated* run (`timeout=` hit, or `max_errors` abort) raises from those same accessors even when every returned item succeeded — a partial list must never read as the whole. `result.status` says how the run ended; `.successes()` / `.ok_values()` are the deliberate partial-result paths.
