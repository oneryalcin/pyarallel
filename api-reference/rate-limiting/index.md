# Rate Limiting

Pyarallel separates rate limiting into a **spec** and a **runtime**:

- `RateLimit(count, per, burst)` — the immutable spec. Passing it (or a plain number) to an entry point creates a private limiter for that call.
- `Limiter(rate_limit)` — the shareable runtime. Pass the same instance to multiple calls and functions to draw from **one budget** — the right model when a quota belongs to an API key rather than to a single operation.

## Basic Usage

```
from pyarallel import parallel_map, RateLimit

results = parallel_map(call_api, ids, rate_limit=RateLimit(100, "minute"))
results = parallel_map(fn, items, rate_limit=10)  # shorthand: 10/second

# burst: how many calls may fire immediately before the sustained rate
# applies. Default 1 = smooth, even pacing (safest against secondary
# per-second limits); raise it when the quota genuinely allows bursts.
results = parallel_map(call_api, ids,
                       rate_limit=RateLimit(100, "minute", burst=20))
```

The `RateLimit` parameter table lives in the [core reference](https://oneryalcin.github.io/pyarallel/api-reference/core/#ratelimit).

## Sharing One Budget: `Limiter`

Real quotas are per API key, not per call. A `RateLimit` passed directly creates a fresh limiter each call — two concurrent maps would each think they own the full quota. Share a `Limiter` instead:

```
from pyarallel import Limiter, RateLimit, parallel_map

limiter = Limiter(RateLimit(100, "minute"))

users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)  # same quota
```

One `Limiter` instance may be used from many threads **and** event loops at once — the same object works across `parallel_map` and `async_parallel_map` concurrently.

### Server-Mandated Holds: `pause()`

`Limiter.pause(seconds)` holds all slots until the deadline passes — including callers already sleeping toward a slot, who re-check on wake and honor the hold. You rarely call it yourself — `Retry(wait_from=...)` calls it automatically when a task hits a 429 (see [Retry](https://oneryalcin.github.io/pyarallel/api-reference/core/index.md)), so one task's throttle signal slows the whole pool. Concurrent pauses don't stack; the furthest deadline wins. The pause also empties the bucket: traffic resumes with one call at the deadline and the rest at the refill pace, never a burst into a server that just said stop.

## How It Works

Rate limiting uses a **token bucket** with commit-at-grant acquisition:

1. The bucket holds up to `burst` tokens and refills at the spec's rate
1. A caller consumes a token only at the moment it is granted; a caller that gives up while waiting (timeout, cancellation) consumes nothing — abandoned waits never leak capacity
1. Sleeping callers re-check shared state when they wake, so a `pause()` issued mid-sleep is honored, never bypassed
1. Refill is capped at `burst` — idle time never accumulates more than one burst of credit
1. Thread-safe and event-loop-safe — the lock is held only for bookkeeping, never while sleeping. Waiters are not queued FIFO; under heavy contention grant order is not guaranteed, only the aggregate rate
1. With `retry=`, every retry attempt draws a fresh token — retries are API calls too and never bypass the limiter (exception: `executor="process"` workers can't share the parent's limiter, so their retries pace only by backoff)

Two implementation notes worth knowing: rate limiting applies at **submission time** for sync (items enter the pool at the controlled rate) and at **execution time** for async (tasks acquire the token inside the semaphore). Decorated functions take `rate_limit=` as a default and per-call override like every other option.

For guidance — headroom below the documented limit, when to share a `Limiter`, burst discipline — see [Best Practices](https://oneryalcin.github.io/pyarallel/user-guide/best-practices/#rate-limiting).
