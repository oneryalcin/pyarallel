# Advanced Features

## Progress Tracking

Track progress for long-running operations:

```python
from pyarallel import parallel_map

def my_progress(done, total):
    print(f"\r{done}/{total} ({100*done//total}%)", end="", flush=True)

results = parallel_map(process, items, workers=8, on_progress=my_progress)
```

Works with both `parallel_map` and `.map()`:

```python
@parallel(workers=4)
def fetch(url): ...

results = fetch.map(urls, on_progress=lambda d, t: print(f"{d}/{t}"))
```

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

results = await async_parallel_map(fetch, urls, concurrency=10, timeout=5.0)
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
results = await fetch.map(urls, timeout=5.0)    # with per-task timeout
```

## Structured Error Handling

`ParallelResult` never silently drops errors:

```python
result = parallel_map(process, items, workers=4)

# Inspect successes and failures separately
for idx, value in result.successes():
    save(idx, value)

for idx, error in result.failures():
    log_error(idx, error)

# Retry failed items
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
