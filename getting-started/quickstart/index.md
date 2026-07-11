# Quick Start

This page builds one safe API job, then shows the few choices you are most likely to change. For the complete surface, use [Which API Should I Use?](https://oneryalcin.github.io/pyarallel/getting-started/choosing-an-api/index.md).

## The Function: `parallel_map`

The simplest way to parallelize work:

```
from pyarallel import parallel_map

def fetch_url(url):
    import requests
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()

urls = ["https://api.example.com/1", "https://api.example.com/2"]
results = parallel_map(fetch_url, urls, workers=4)

for item in results:
    print(item)
```

`parallel_map` accepts **any iterable** — lists, generators, ranges, sets:

```
# All of these work
parallel_map(process, [1, 2, 3], workers=4)
parallel_map(process, range(100), workers=4)
parallel_map(process, (x for x in data), workers=4)
```

## The Decorator: `@parallel`

For functions you use repeatedly. The function keeps its normal behavior — `.map()` is explicit:

```
from pyarallel import parallel

@parallel(workers=4)
def fetch(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()

# Normal call — returns dict
data = fetch("http://example.com")

# Parallel call — returns ParallelResult
results = fetch.map(["http://a.com", "http://b.com"])

# Decorator args are defaults — any call can override them
results = fetch.map(urls, workers=16, rate_limit=100)
```

It works on instance and static methods too (descriptor protocol), and the async twin `@async_parallel` follows the same pattern — see the [API reference](https://oneryalcin.github.io/pyarallel/api-reference/core/#parallel) for both. Plain `parallel_map(scraper.fetch, urls)` with a bound method also just works.

## Add the production policies

When several calls spend one API key's budget, share a `Limiter`. `Retry.for_http()` understands both forms of `Retry-After`; a throttled response pauses that shared limiter, so the whole pool slows down together:

```
from pyarallel import Limiter, RateLimit, Retry

limiter = Limiter(RateLimit(100, "minute"))

result = parallel_map(
    call_api, ids,
    workers=10,
    rate_limit=limiter,
    retry=Retry.for_http(on=(HttpError,), attempts=4),
    checkpoint="api-job.ckpt",
    checkpoint_version="request-shape-v1",
    max_errors=20,
)
```

Rerun the same call after a crash: completed items load from the checkpoint. Bump `checkpoint_version` when the request, model, prompt, or output contract changes. See [Shared Rate Limits](https://oneryalcin.github.io/pyarallel/user-guide/advanced-features/#shared-rate-limits-and-burst) and [Checkpoint / Resume](https://oneryalcin.github.io/pyarallel/user-guide/advanced-features/#checkpoint-resume) for the precise contracts.

## Error Handling

Errors are never silently lost. `ParallelResult` gives you structured access:

```
result = parallel_map(process, items, workers=4)

if result.ok:
    # All succeeded
    for value in result:
        print(value)
else:
    # Some failed — inspect both
    print(f"{len(result.successes())} succeeded")
    print(f"{len(result.failures())} failed")

    for idx, exc in result.failures():
        print(f"  Item {idx}: {exc}")

    # Or raise all errors at once
    result.raise_on_failure()  # ExceptionGroup
```

## Async uses the same policies

Mirror API for async functions:

```
from pyarallel import Retry, async_parallel_map

async with httpx.AsyncClient(timeout=30) as client:
    async def fetch(url):
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    results = await async_parallel_map(
        fetch,
        urls,
        concurrency=10,
        retry=Retry.for_http(on=(httpx.HTTPStatusError,)),
    )
```

Create one client outside the item function so calls reuse its connection pool. The context manager closes it even if the run raises.

## Next Steps

- [Which API Should I Use?](https://oneryalcin.github.io/pyarallel/getting-started/choosing-an-api/index.md) — one decision tree for the whole surface
- [Production API Job](https://github.com/oneryalcin/pyarallel/blob/main/examples/07_production_api_job.py) — runnable end-to-end template
- [Advanced Features](https://oneryalcin.github.io/pyarallel/user-guide/advanced-features/index.md) — resume, stop, timeouts, admission, methods
- [Best Practices](https://oneryalcin.github.io/pyarallel/user-guide/best-practices/index.md) — executors and operational patterns
- [Testing](https://oneryalcin.github.io/pyarallel/user-guide/testing/index.md) — deterministic tests without sleeps or real HTTP
- [API Reference](https://oneryalcin.github.io/pyarallel/api-reference/core/index.md) — full parameter docs
