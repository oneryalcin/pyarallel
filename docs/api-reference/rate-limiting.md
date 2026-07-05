# Rate Limiting

Pyarallel separates rate limiting into a **spec** and a **runtime**:

- `RateLimit(count, per, burst)` — the immutable spec. Passing it (or a plain
  number) to an entry point creates a private limiter for that call.
- `Limiter(rate_limit)` — the shareable runtime. Pass the same instance to
  multiple calls and functions to draw from **one budget** — the right model
  when a quota belongs to an API key rather than to a single operation.

## Basic Usage

### With `RateLimit` Object

```python
from pyarallel import parallel_map, RateLimit

# 100 operations per minute, evenly spaced
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

### Burst Capacity

`burst` is the token-bucket capacity: how many calls may fire immediately
before the sustained rate applies. The default of 1 gives smooth, evenly
spaced pacing — the safest choice against secondary per-second limits.
Raise it when the quota genuinely allows bursts:

```python
# Up to 20 requests immediately, then refill at 100/minute
results = parallel_map(call_api, ids,
                       rate_limit=RateLimit(100, "minute", burst=20))
```

## Sharing One Budget: `Limiter`

Real quotas are per API key, not per call. A `RateLimit` passed directly
creates a fresh limiter each call — two concurrent maps would each think
they own the full quota. Share a `Limiter` instead:

```python
from pyarallel import Limiter, RateLimit, parallel_map

limiter = Limiter(RateLimit(100, "minute"))

users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)  # same quota
```

One `Limiter` instance may be used from many threads **and** event loops at
once — the same object works across `parallel_map` and `async_parallel_map`
concurrently.

### Server-Mandated Holds: `pause()`

`Limiter.pause(seconds)` holds all future slots until the deadline passes.
You rarely call it yourself — `Retry(wait_from=...)` calls it automatically
when a task hits a 429 (see [Retry](core.md)), so one task's throttle signal
slows the whole pool. Concurrent pauses don't stack; the furthest deadline
wins.

## How It Works

Rate limiting uses a **token bucket** with a debt model:

1. The bucket holds up to `burst` tokens and refills at the spec's rate
2. Each operation takes a token; when the bucket is empty, tokens go
   negative and the caller waits until its claimed token exists
3. Refill is capped at `burst` — idle time never accumulates more than one
   burst of credit
4. Thread-safe and event-loop-safe — the lock is held only for bookkeeping,
   never while sleeping

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

A shared `Limiter` works here too — pass the same instance as the default
for several decorated functions hitting the same API.

## Tips

- **Leave buffer** below actual API limits (use 90% of the limit)
- **Share a `Limiter`** whenever two calls hit the same quota — separate
  `RateLimit` specs on each call means each call assumes the full budget
- **Rate limiting is at submission time** (sync) — items are submitted to the pool at the controlled rate
- **Rate limiting is at execution time** (async) — tasks acquire the rate token inside the semaphore
