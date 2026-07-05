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


class Limiter:
    """Shareable token-bucket rate limiter.

    Up to ``rate_limit.burst`` calls may proceed immediately; sustained
    throughput is capped at the spec's rate. One instance may be used from
    many threads and event loops at once — the internal lock is held only
    for bookkeeping, never while sleeping.

    ``pause(seconds)`` holds *all* future slots until the deadline passes.
    This is the hook for server-mandated backoff (HTTP 429 ``Retry-After``):
    one task's throttle signal slows the whole pool, not just itself.

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

    def _reserve(self) -> float:
        """Claim one slot; return how long the caller must wait for it.

        Tokens may go negative — a queue of debt. Each caller's wait is the
        time until its claimed token exists. Refill is capped at burst
        capacity, so idle time never accumulates more than one burst of
        credit.
        """
        with self._lock:
            now = self._clock()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._updated) * self._rate
            )
            self._updated = now
            self._tokens -= 1.0
            wait = max(0.0, self._not_before - now)
            if self._tokens < 0.0:
                wait = max(wait, -self._tokens / self._rate)
            return wait

    def wait(self) -> None:
        """Block the calling thread until its slot arrives."""
        delay = self._reserve()
        if delay > 0:
            time.sleep(delay)

    async def wait_async(self) -> None:
        """Await until the caller's slot arrives."""
        delay = self._reserve()
        if delay > 0:
            await asyncio.sleep(delay)

    def pause(self, seconds: float) -> None:
        """Hold all future slots for *seconds* (server-mandated backoff).

        Concurrent pauses don't stack — the furthest deadline wins.
        """
        if seconds <= 0:
            return
        with self._lock:
            self._not_before = max(self._not_before, self._clock() + seconds)


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
