# Pyarallel: API Vision & Redesign Proposal

> **Date**: 2026-04-12
> **Status**: Proposal
> **Companion**: See `DEVX_REVIEW.md` for the full audit of current issues

---

## The Core Question

**Is a parallelization decorator abstraction useful for developers?**

Yes — but the current one isn't. Here's why, and what would be.

The pain that developers actually feel is not "I need a decorator." It's:

1. **"I have 200 URLs to fetch. I don't want to write the ThreadPoolExecutor boilerplate again."**
2. **"Some of these API calls fail. I want to know which ones, get the rest, and maybe retry."**
3. **"There's a rate limit. I need to throttle to 100 requests/minute."**
4. **"This is taking forever. I want a progress bar."**
5. **"I need this to work in my async FastAPI app, not just sync scripts."**

The current pyarallel addresses only #1 (partially — with magic list detection that creates new problems) and #3 (rate limiting — the one genuinely useful feature). A redesigned library should address all five.

---

## Lessons from Great Python Libraries

Seven principles emerge from studying how FastAPI, SQLAlchemy, Celery, httpx, Typer, Tenacity, and trio/anyio handle abstraction:

### 1. Explicit Over Implicit

Every great library avoids runtime type-sniffing of argument *values*. FastAPI inspects the function *definition* at decoration time (sync vs async — an intrinsic property). Typer reads *type hints* at decoration time. Celery offers `.delay()` vs `.apply_async()` — an explicit user choice.

Pyarallel's `isinstance(args[0], (list, tuple))` check is the exact opposite. It sniffs the runtime *value* to decide behavior. This is the cardinal sin.

**Principle**: A function's behavior should be knowable from its signature, not from the runtime type of its arguments.

### 2. Progressive Disclosure of Complexity

Celery is the gold standard: `task.delay(arg)` for simple cases → `task.apply_async(args, countdown=10)` for control → `chain(a.s() | b.s())` for workflows. Each level adds power without changing the simpler levels.

**Principle**: The 80% use case should be one line. The 95% case should be three lines. The 100% case should be possible.

### 3. Separate Sync and Async Explicitly

httpx provides `Client` (sync) and `AsyncClient` (async) — same API shape, different execution model. SQLAlchemy has `Session` and `AsyncSession`. Neither auto-detects whether you're in an async context.

**Principle**: Don't auto-detect. Let the developer choose. Share internals, diverge at the API surface.

### 4. Don't Change the Function's Signature

FastAPI, Typer, and Tenacity all preserve the decorated function's original signature. The decorator *adds capabilities* (routes, CLI parsing, retry logic) without *changing what the function looks like* to callers, IDEs, and type checkers.

**Principle**: A decorated function should remain callable with its original signature. Add new methods (`.map()`, `.retry()`), don't change the return type.

### 5. Composable Configuration via Strategy Objects

Tenacity's `stop=stop_after_attempt(3) | stop_after_delay(30)` and `wait=wait_exponential() + wait_random(0, 2)` show that configuration should be built from composable pieces, not a monolithic decorator with 10 keyword arguments.

**Principle**: Each concern (rate limiting, batching, timeout, retry) should be a separate composable object, not a flat parameter.

### 6. Structured Resource Management

trio/anyio's TaskGroup and httpx's context managers show that parallel resources should have explicit lifetimes tied to lexical scopes — not global caches with weak references.

**Principle**: Use context managers. No global mutable state. No singletons.

### 7. Convenience Shortcuts Backed by Explicit Primitives

Celery's `.delay()` is documented as a shortcut for `.apply_async()`. httpx's `httpx.get(url)` is a shortcut for `with Client() as c: c.get(url)`. The shortcut is transparent.

**Principle**: Offer a simple function for common cases, backed by an explicit class for advanced cases.

---

## Proposed API Design

### Level 1: The One-Liner (80% of use cases)

```python
from pyarallel import parallel_map

# Fan-out/collect — the most common pattern
results = parallel_map(fetch_url, urls, workers=10)

# With options
results = parallel_map(
    process_item,
    items,
    workers=4,
    executor="process",          # or "thread" (default)
    rate_limit=RateLimit(100, "minute"),
    timeout=5.0,                 # per-item timeout
    on_progress=lambda done, total: print(f"{done}/{total}"),
)
```

**Why this works**:
- No decorator. No magic. No type ambiguity.
- `fn` always takes a single item. `items` is always an iterable.
- Returns `list[T]` — the intent is obvious from the call site.
- Every option is explicit and per-call. No global config.

### Level 2: The Decorator (for repeated use)

```python
from pyarallel import parallel

@parallel(workers=4, rate_limit=RateLimit(100, "minute"))
def fetch_url(url: str) -> dict:
    return requests.get(url).json()

# CRITICAL DIFFERENCE from current design:
# The decorated function KEEPS its original signature.
# Single-item calls work normally:
result = fetch_url("http://example.com")  # Returns dict, NOT [dict]

# Parallel execution uses an explicit .map() method:
results = fetch_url.map(urls)             # Returns list[dict]
results = fetch_url.map(urls, workers=8)  # Override workers for this call

# Starmap for multi-argument functions:
results = fetch_url.starmap([(url, header) for url, header in tasks])
```

**Why this works**:
- The function signature is **preserved**. `fetch_url("http://example.com")` returns `dict`, not `list[dict]`. Type checkers, IDEs, and developers all see the correct type.
- Parallel execution is **explicit** via `.map()` — no magic list detection.
- `.map()` is a clear, well-understood metaphor from `map()`, `Pool.map()`, `executor.map()`.
- Per-call overrides are possible without global config.

### Level 3: The Executor (full control)

```python
from pyarallel import ParallelExecutor, RateLimit

with ParallelExecutor(workers=10, executor="thread") as executor:
    # Submit individual tasks
    future = executor.submit(fetch_url, url)

    # Map over iterables
    results = executor.map(fetch_url, urls)

    # Map with rate limiting and progress
    results = executor.map(
        fetch_url, urls,
        rate_limit=RateLimit(100, "minute"),
        timeout=5.0,
        on_progress=my_progress_callback,
    )

# Executor is cleaned up here — no global cache, no weak references
```

**Why this works**:
- Explicit resource lifetime via context manager.
- Familiar API — mirrors `concurrent.futures.Executor` but adds rate limiting, progress, timeout.
- No global state. Testable. Composable.

### Level 4: Async Support (mirror API)

```python
from pyarallel import async_parallel_map, async_parallel, AsyncParallelExecutor

# One-liner (async)
results = await async_parallel_map(fetch_url_async, urls, concurrency=10)

# Decorator (async)
@async_parallel(concurrency=10, rate_limit=RateLimit(100, "minute"))
async def fetch_url(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()

result = await fetch_url("http://example.com")   # Returns dict
results = await fetch_url.map(urls)               # Returns list[dict]

# Full control (async) — uses TaskGroup for structured concurrency
async with AsyncParallelExecutor(concurrency=10) as executor:
    results = await executor.map(fetch_url, urls)
```

**Why this works**:
- Follows the httpx pattern: `ParallelExecutor` / `AsyncParallelExecutor`.
- Developer explicitly chooses sync or async. No auto-detection.
- Async version uses `asyncio.TaskGroup` internally for structured concurrency.
- Uses `asyncio.Semaphore` for rate limiting instead of `time.sleep()`.

---

## Structured Error Handling

The current library raises the first exception and loses everything else. The redesigned API should return structured results:

```python
from pyarallel import parallel_map, ParallelResult

results: ParallelResult = parallel_map(fetch_url, urls, workers=10)

# Iterate successes
for item, value in results.successes():
    print(f"{item} -> {value}")

# Inspect failures
for item, error in results.failures():
    print(f"{item} failed: {error}")

# Get all results in order (None or exception for failures)
all_results = results.all()

# Raise if any failures (uses ExceptionGroup on Python 3.11+)
results.raise_on_failure()

# Boolean check
if results.ok:
    process(results.values())  # list of successful values, in order
```

**Why this works**:
- No information is lost. Successes and failures are both accessible.
- The developer chooses how to handle errors: inspect, raise, or ignore.
- `ExceptionGroup` (Python 3.11+) aggregates all exceptions properly.
- Works naturally with retry: `retry_results = parallel_map(fn, results.failed_items())`.

---

## Composable Configuration

Instead of a monolithic config system, each concern is a composable object:

```python
from pyarallel import parallel_map, RateLimit, Retry, Timeout, BatchSize

# Compose behaviors
results = parallel_map(
    fetch_url,
    urls,
    workers=10,
    rate_limit=RateLimit(100, per="minute"),
    timeout=Timeout(per_item=5.0, total=300.0),
    retry=Retry(attempts=3, backoff="exponential"),
    batch=BatchSize(50),
)
```

Each object is:
- **Independently testable**: `RateLimit(100, "minute").tokens_per_second == 1.666...`
- **Composable**: Can be combined, reused, shared across calls
- **Self-documenting**: The class name describes what it does
- **Not a flat kwarg**: Richer than `rate_limit=(100, "minute")` — can have methods, validation, etc.

---

## Testing Support

A `sequential` mode makes parallel code deterministic for testing:

```python
# In tests — same API, sequential execution, deterministic behavior
results = parallel_map(process, items, workers=1)  # Sequential

# Or via the decorator
@parallel(workers=4)
def process(x): ...

# In tests, override:
results = process.map(items, sequential=True)  # No threads, no processes
```

---

## What Gets Deleted

| Current Feature | Lines | Action |
|---|---|---|
| Magic list detection | ~40 | **Delete** — replaced by explicit `.map()` |
| Always-returns-list for single items | ~10 | **Delete** — single calls return `T` |
| `ConfigManager` singleton | 423 | **Delete** — per-call config, no global state |
| `PyarallelConfig` dataclasses | 241 | **Delete** — replaced by composable objects |
| `env_config.py` | 58 | **Delete** — env vars are over-engineering |
| `_EXECUTOR_CACHE` WeakValueDictionary | ~20 | **Delete** — context managers manage lifetime |
| `batch_size` (unimplemented) | ~10 | **Implement** properly or remove |
| Qualname-based method detection | ~30 | **Delete** — `.map()` is always a regular call |

**Result**: ~830 lines of current code deleted. Replaced with ~400-500 lines of focused, tested, well-designed code.

---

## Architecture Overview

```
pyarallel/
  __init__.py          # Public API: parallel_map, parallel, ParallelExecutor, etc.
  _sync.py             # Sync execution engine (ThreadPool/ProcessPool)
  _async.py            # Async execution engine (TaskGroup/Semaphore)
  _result.py           # ParallelResult container
  _rate_limit.py       # RateLimit, TokenBucket (composable)
  _retry.py            # Retry strategies (composable, or integrate with tenacity)
  _progress.py         # Progress callback protocol
  _decorator.py        # @parallel decorator that adds .map() without changing signature
```

Each module is small (~50-100 lines), focused, independently testable.

---

## Migration Path

For existing users of the current API:

```python
# BEFORE (current, broken)
@parallel(max_workers=4)
def process(x):
    return x * 2

results = process([1, 2, 3])  # Magic list detection, returns [2, 4, 6]
result = process(1)            # Returns [2], not 2

# AFTER (redesigned)
@parallel(workers=4)
def process(x):
    return x * 2

results = process.map([1, 2, 3])  # Explicit, returns [2, 4, 6]
result = process(1)                # Returns 2, as expected
```

The migration is mechanical: change `fn(list)` to `fn.map(list)`. Single-item calls just work.

---

## Summary: Why This Design Wins

| Concern | Current Pyarallel | Proposed Design | Inspiration |
|---|---|---|---|
| Single vs parallel | Magic list detection | Explicit `.map()` method | Celery `.delay()` |
| Return type | Always `List[T]` | `T` for single, `list[T]` for `.map()` | Typer/FastAPI |
| Sync vs async | Sync only | Separate `parallel` / `async_parallel` | httpx Client/AsyncClient |
| Error handling | First exception, rest lost | `ParallelResult` with successes + failures | — |
| Configuration | 722-line singleton system | Composable per-call objects | Tenacity strategies |
| Resource lifetime | Global WeakValueDict cache | Context managers | trio TaskGroup |
| Type safety | Broken (no overloads) | Preserved (decorator keeps signature) | FastAPI/Typer |
| Testing | Requires singleton reset | `sequential=True` flag | — |
| Progress | None | `on_progress` callback | joblib `verbose` |

The redesigned pyarallel would be:
- **~400-500 lines** (down from 1,136)
- **Zero global state**
- **Fully typed** (mypy/pyright clean)
- **Sync + async** as first-class citizens
- **Composable** (rate limiting, retry, timeout, batching as separate objects)
- **Honest** (no unimplemented features in docs)

The value proposition becomes: **"`concurrent.futures.map()` but with structured error handling, rate limiting, progress tracking, and an async variant — in one line."** That's a library developers would actually reach for.
