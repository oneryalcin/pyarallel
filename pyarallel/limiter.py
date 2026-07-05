"""Rate limiter runtime: turns a ``RateLimit`` spec into enforced pacing.

``RateLimit`` is the spec; ``Limiter`` is the runtime state that enforces it.
Passing a ``RateLimit`` (or a plain number) to an entry point creates a
private ``Limiter`` for that call. Passing a ``Limiter`` instance shares one
budget across calls and functions — the right model when a quota belongs to
an API key rather than to a single operation.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from .policies import RateLimit

# Grant when within this fraction of a full token — avoids a float-precision
# spin where a caller is repeatedly told to wait ~1e-17s for the remainder.
_GRANT_EPSILON = 1e-9
# Never tell a caller to sleep less than this; guarantees the clock advances
# between acquire attempts.
_MIN_WAIT = 1e-6


class Limiter:
    """Shareable token-bucket rate limiter.

    Up to ``rate_limit.burst`` calls may proceed immediately; sustained
    throughput is capped at the spec's rate. One instance may be used from
    many threads and event loops at once — the internal lock is held only
    for bookkeeping, never while sleeping.

    A token is consumed only at the moment a caller is *granted* its slot.
    A caller that gives up while waiting (timeout, cancellation) consumes
    nothing, and sleeping callers re-check shared state when they wake — a
    ``pause()`` issued while they slept is honored, never bypassed. Waiters
    are not queued FIFO; under heavy contention grant order is not
    guaranteed, only the aggregate rate.

    ``pause(seconds)`` holds *all* slots until the deadline passes and
    empties the bucket, so traffic resumes at the refill rate — one call at
    the deadline, then paced — rather than bursting into a server that just
    said stop. This is the hook for server-mandated backoff (HTTP 429
    ``Retry-After``): one task's throttle signal slows the whole pool.

    Example::

        limiter = Limiter(RateLimit(100, "minute"))
        users  = parallel_map(fetch_user,  user_ids,  rate_limit=limiter)
        orders = parallel_map(fetch_order, order_ids, rate_limit=limiter)
    """

    __slots__ = (
        "_rate",
        "_capacity",
        "_tokens",
        "_updated",
        "_not_before",
        "_lock",
        "_clock",
    )

    def __init__(self, rate_limit: RateLimit) -> None:
        self._rate = rate_limit.per_second
        self._capacity = float(rate_limit.burst)
        self._tokens = self._capacity
        self._lock = threading.Lock()
        self._clock: Callable[[], float] = time.monotonic
        self._updated = self._clock()
        self._not_before = 0.0

    def _try_acquire(self) -> float:
        """Take one token if available now.

        Returns 0.0 on success (the token is consumed) or the seconds to
        sleep before trying again (nothing is consumed — no debt, so a
        caller that abandons the wait leaks no capacity).
        """
        with self._lock:
            now = self._clock()
            if self._not_before > now:
                return self._not_before - now
            elapsed = max(0.0, now - self._updated)
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = max(self._updated, now)
            if self._tokens >= 1.0 - _GRANT_EPSILON:
                self._tokens -= 1.0
                return 0.0
            return max((1.0 - self._tokens) / self._rate, _MIN_WAIT)

    def wait(self) -> None:
        """Block the calling thread until a token is granted."""
        while True:
            delay = self._try_acquire()
            if delay <= 0:
                return
            time.sleep(delay)

    async def wait_async(self) -> None:
        """Await until a token is granted."""
        while True:
            delay = self._try_acquire()
            if delay <= 0:
                return
            await asyncio.sleep(delay)

    def pause(self, seconds: float) -> None:
        """Hold all slots for *seconds* (server-mandated backoff).

        Concurrent pauses don't stack — the furthest deadline wins. The
        bucket is left with exactly one token at the deadline: the retrying
        task proceeds the moment the server allows, everyone else resumes
        at the refill pace.
        """
        if seconds <= 0:
            return
        with self._lock:
            deadline = self._clock() + seconds
            if deadline > self._not_before:
                self._not_before = deadline
                self._tokens = 1.0
                self._updated = deadline

    def _probe(self) -> float:
        """Current wait a new caller would face, without consuming anything.

        Test seam — like ``_try_acquire`` but side-effect-free on grant.
        """
        with self._lock:
            now = self._clock()
            if self._not_before > now:
                return self._not_before - now
            elapsed = max(0.0, now - self._updated)
            tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if tokens >= 1.0 - _GRANT_EPSILON:
                return 0.0
            return (1.0 - tokens) / self._rate


def _as_limiter(
    rate_limit: Limiter | RateLimit | float | None,
) -> Limiter | None:
    """Normalize the ``rate_limit`` argument into a ``Limiter`` (or None)."""
    if rate_limit is None:
        return None
    if isinstance(rate_limit, Limiter):
        return rate_limit
    if isinstance(rate_limit, (int, float)):
        rate_limit = RateLimit(rate_limit)
    return Limiter(rate_limit)
