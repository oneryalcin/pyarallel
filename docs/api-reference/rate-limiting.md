# Rate Limiting

Pyarallel includes a token-bucket rate limiter for controlling execution rates.

## Basic Usage

### With `RateLimit` Object

```python
from pyarallel import parallel_map, RateLimit

# 100 operations per minute
results = parallel_map(call_api, ids, workers=4,
                       rate_limit=RateLimit(100, "minute"))

# 1000 per hour
results = parallel_map(process, items,
                       rate_limit=RateLimit(1000, "hour"))
```

### Shorthand (ops per second)

Pass a number for simple per-second limits:

```python
results = parallel_map(fn, items, rate_limit=10)  # 10 per second
```

## How It Works

Rate limiting uses a **token bucket** algorithm:

1. Each operation claims a time-slot
2. Slots are spaced at `1 / rate_per_second` intervals
3. If a slot is in the future, the caller sleeps until that slot
4. Thread-safe — the lock is held only for bookkeeping, never during sleep

For the async API, an equivalent `asyncio.Lock`-based bucket is used with `asyncio.sleep`.

## With the Decorator

Set a default rate limit, override per-call:

```python
@parallel(workers=4, rate_limit=RateLimit(100, "minute"))
def call_api(item_id):
    return api.get(item_id)

# Uses default rate limit
results = call_api.map(ids)

# Override for this call
results = call_api.map(ids, rate_limit=RateLimit(500, "minute"))
```

## Tips

- **Leave buffer** below actual API limits (use 90% of the limit)
- **Rate limiting is at submission time** (sync) — items are submitted to the pool at the controlled rate
- **Rate limiting is at execution time** (async) — tasks acquire the rate token inside the semaphore
