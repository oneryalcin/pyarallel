# Pyarallel

Simple, explicit parallel execution for Python.

## Overview

Pyarallel wraps Python's `concurrent.futures` and `asyncio.TaskGroup` with better ergonomics: structured error handling, rate limiting, progress callbacks, and async support.

**Design principles:**

- **Explicit over implicit** — `.map()` for parallel, normal calls stay normal
- **No magic** — no type-sniffing, no global config, no singletons
- **Structured errors** — all failures collected via `ExceptionGroup`, never silently lost
- **Sync + async** — mirror APIs, you choose explicitly

## Quick Example

```python
from pyarallel import parallel_map, RateLimit

# Fan out 200 API calls, rate-limited to 100/min, with progress
results = parallel_map(
    fetch_url,
    urls,
    workers=10,
    rate_limit=RateLimit(100, "minute"),
    on_progress=lambda done, total: print(f"{done}/{total}"),
)

for item in results:        # raises ExceptionGroup if any failed
    process(item)
```

Or with the decorator:

```python
from pyarallel import parallel

@parallel(workers=4)
def fetch(url):
    return requests.get(url).json()

data = fetch("http://example.com")        # normal call, returns dict
results = fetch.map(["u1", "u2", "u3"])   # parallel, returns ParallelResult
```

## Documentation

- [Installation](getting-started/installation.md)
- [Quick Start](getting-started/quickstart.md)
- [Advanced Features](user-guide/advanced-features.md)
- [Best Practices](user-guide/best-practices.md)
- [API Reference](api-reference/core.md)

## License

MIT — see [LICENSE](https://github.com/oneryalcin/pyarallel/blob/main/LICENSE.md).
