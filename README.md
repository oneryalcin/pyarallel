# Pyarallel

[![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)

The fan-out layer for rate-limited APIs — one function over many inputs, with rate limiting, retry, resume, and structured errors. Sync and async.

Fanning out over a service that throttles you — LLM calls, embeddings, scraping, any SaaS API — means the same hand-rolled stack every time: a semaphore, tenacity, a token bucket, ad-hoc 429 handling, and a "TODO: resume" you never get to. Pyarallel is that stack, already built and tested. Not DAGs, not queues, not a distributed system — just `concurrent.futures` and `asyncio` with the policies and result handling already built in.

**Zero dependencies. Python 3.12+** — free-threaded 3.13t/3.14t tested, sub-interpreter executor on 3.14.

```bash
pip install pyarallel
```

## Before / After

Fetch 10,000 URLs with rate limiting and error handling.

**concurrent.futures:**

```python
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

urls = ["https://api.example.com/users/1", "https://api.example.com/users/2", ...]

results = [None] * len(urls)
errors = []

with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch, url): i for i, url in enumerate(urls)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            results[i] = f.result()
        except Exception as e:
            errors.append((i, e))

# No rate limiting. No retry. No resume. And you still
# need to wire those yourself every time.
```

**pyarallel:**

```python
from pyarallel import Limiter, RateLimit, Retry, parallel_map

limiter = Limiter(RateLimit(100, "minute"))

result = parallel_map(
    fetch, urls,
    workers=10,
    rate_limit=limiter,
    retry=Retry.for_http(on=(requests.HTTPError,), attempts=4),
    checkpoint="fetch.ckpt",
    checkpoint_version="fetch-v1",
    max_errors=20,
)

for idx, val in result.successes():
    save(val)
for idx, exc in result.failures():
    log_error(idx, exc)
```

Same thing, async:

```python
import httpx
from pyarallel import Limiter, RateLimit, Retry, async_parallel_map

limiter = Limiter(RateLimit(100, "minute"))

async with httpx.AsyncClient(timeout=10) as client:
    async def fetch_async(url):
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    result = await async_parallel_map(
        fetch_async, urls,
        concurrency=10,
        rate_limit=limiter,
        retry=Retry.for_http(on=(httpx.HTTPStatusError,)),
        checkpoint="fetch.ckpt",
        checkpoint_version="fetch-v1",
        max_errors=20,
    )
# Same result model — result.ok, result.successes(), result.failures()
```

## What You Get

**Respect the service**

- **Rate limiting** — token bucket with burst, per-second/minute/hour: `rate_limit=RateLimit(100, "minute", burst=20)`
- **Shared quota** — one `Limiter` instance across calls and functions when the budget belongs to an API key: `rate_limit=Limiter(RateLimit(100, "minute"))`
- **Retry with backoff** — per-item, exponential, jitter, exception filtering: `retry=Retry(attempts=3, on=(ConnectionError,))`
- **Server-driven backoff** — `retry=Retry.for_http(on=(httpx.HTTPStatusError,))`: 429/503 + `Retry-After` (numeric *and* HTTP-date form) prewired, no client import; the wait also pauses the shared limiter so one throttled task slows the whole pool. Custom policies via `retry_if=`/`wait_from=`

**Finish expensive jobs**

- **Checkpoint/resume** — `checkpoint="run.ckpt"`: a crash at item 40,000 resumes instead of restarting from zero; `checkpoint_key=lambda u: u.id` keys rows by identity so evolving inputs keep completed work
- **Early abort** — `max_errors=10`: a dead API costs tens of calls, not thousands; unrun items are marked `Aborted`, partial results returned
- **Cooperative stop** — `stop=StopToken()`: SIGTERM/notebook-stop lands the plane — admission ceases, checkpoint rows kept, `RunStatus.CANCELLED` reported

**Control execution and outcomes**

- **One windowed engine** — every API (collected and streaming, sync and async) admits work through a bounded in-flight window: lazy input, generators never materialized, no batch barriers, a straggler never stalls the items behind it
- **Streaming** — `parallel_iter` / `async_parallel_iter`: `ordered=True` for input-order yields, per-item `attempts`/`duration`
- **Async sources** — async cursors and paginated generators feed `async_parallel_*` directly, with backpressure to the producer — no draining into a list first
- **Structured errors** — `ParallelResult` with `.ok`, `.ok_values()`, `.successes()`, `.failures()`, `.raise_on_failure()`, plus `.status` (`RunStatus`) for how the run ended; a truncated run is never `.ok` and won't hand out a partial list as if it were complete
- **Timeouts** — total wall-clock on sync *and* async (`timeout=30.0`), per-task in async (`task_timeout=5.0`)
- **Debug mode** — `sequential=True` runs inline: no pool, real stack traces, working breakpoints
- **Progress callbacks** — `on_progress=lambda done, total: print(f"{done}/{total}")` on collected and streaming APIs

**Choose the runtime and interface**

- **Process executor** — CPU-bound work: `executor="process"`, with `worker_init=` and `max_tasks_per_worker=`
- **Interpreter executor** (Python 3.14+) — `executor="interpreter"`: true CPU parallelism for pure-Python work without process overhead (PEP 734)
- **Contextvars propagation** — correlation IDs survive into thread workers
- **Decorator API** — `@parallel` / `@async_parallel` with `.map()`, `.starmap()`, `.stream()` — typed options via `Unpack[TypedDict]`, and single-arg named functions bind their item type (`fetch.map([1])` is a type error when `fetch` takes `str` — in mypy *and* pyright)

## Other API Shapes

### Decorator

Adds `.map()`, `.starmap()`, `.stream()` without changing the function:

```python
from pyarallel import RateLimit, parallel

@parallel(workers=8, rate_limit=RateLimit(100, "minute"))
def fetch(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

fetch("http://example.com")          # normal call — returns dict
fetch.map(urls)                      # parallel — returns ParallelResult
fetch.stream(urls, window_size=500)   # streaming — yields ItemResult
```

`@async_parallel` mirrors the same interface for `async def` functions.

### Streaming — Constant Memory

For ETL, pipelines, or datasets too large to hold in memory:

```python
from pyarallel import parallel_iter

def transform(row):
    return {"id": row["id"], "name": row["name"].strip().title()}

for item in parallel_iter(transform, ten_million_rows, window_size=1000):
    if item.ok:
        db.save(item.value)
    else:
        log_error(item.index, item.error)
```

### Error Handling

All errors collected, never silently swallowed:

```python
def send_email(msg):
    return smtp.send(msg["to"], msg["subject"], msg["body"])

result = parallel_map(send_email, messages)

if result.ok:
    values = result.values()           # list of all results, in order
else:
    for idx, exc in result.failures():
        log_error(idx, exc)
    result.raise_on_failure()          # or raise ExceptionGroup with all errors
```

## API Summary

| Function | Decorator | Returns | Use case |
|---|---|---|---|
| `parallel_map(fn, items)` | `.map(items)` | `ParallelResult` | Results fit in memory |
| `parallel_starmap(fn, items)` | `.starmap(items)` | `ParallelResult` | Multi-arg, fits in memory |
| `parallel_iter(fn, items)` | `.stream(items)` | `Iterator[ItemResult]` | Streaming, constant memory |

Async mirrors: `async_parallel_map`, `async_parallel_starmap`, `async_parallel_iter`

| Config | Example |
|---|---|
| `RateLimit(count, per, burst)` | `RateLimit(100, "minute", burst=20)` |
| `Limiter(rate_limit)` | shared budget: `Limiter(RateLimit(100, "minute"))` |
| `Retry(attempts, backoff, on, retry_if, wait_from)` | `Retry(attempts=3, on=(ConnectionError,))` |
| `Retry.for_http(on, statuses)` | HTTP prewired: `Retry.for_http(on=(httpx.HTTPStatusError,))` |
| `checkpoint=` | resumable runs: `checkpoint="run.ckpt"` |

Works with instance methods and static methods via `@parallel` decorator — see [full docs](https://oneryalcin.github.io/pyarallel/).

## See It Prove Itself

Every *resilience* claim — 429 handling, the pool-wide pause, rate-limit
pacing, kill-and-resume — verified locally in ~10 seconds, no
credentials, no dependencies, one file:

```bash
python examples/resilience_demo.py
```

A fake quota-enforcing API comes up on localhost; a full-speed pool
draws a 429 and you watch **one** `Retry-After` pause the whole pool
(the server measures the gap); the same job with a client-side
`RateLimit` never gets throttled at all; then a checkpointed run is
**SIGKILLed mid-flight** and rerun — it resumes from SQLite, and the
server's request counter proves the paid-for calls were not repeated.
The demo asserts its own claims and exits non-zero if any fail.

## Documentation

- [Quick Start](https://oneryalcin.github.io/pyarallel/getting-started/quickstart/) — one safe API job, then the policies you are likely to change
- [Which API Should I Use?](https://oneryalcin.github.io/pyarallel/getting-started/choosing-an-api/) — collected vs streaming, sync vs async, functions vs decorators
- [Production API Job](examples/07_production_api_job.py) — runnable template with shared client, retry, resume, graceful stop, and explicit status handling
- [Cookbook](https://oneryalcin.github.io/pyarallel/cookbook/) — LLM calls, embeddings, scraping, bulk operations, ETL, and CPU fan-out
- [Testing](https://oneryalcin.github.io/pyarallel/user-guide/testing/) and [Troubleshooting](https://oneryalcin.github.io/pyarallel/user-guide/troubleshooting/) — deterministic tests and symptom-first diagnosis
- [Comparison](https://oneryalcin.github.io/pyarallel/getting-started/comparison/) — alternatives and when *not* to use pyarallel

## License

MIT — see [LICENSE.md](LICENSE.md).
