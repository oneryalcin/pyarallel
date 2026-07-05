"""Policy specifications: what to allow (``RateLimit``) and how to recover
(``Retry``).

These are small, frozen, composable value objects. Execution machinery lives
elsewhere — a ``RateLimit`` is a spec; the runtime state that enforces it is
``pyarallel.limiter.Limiter``.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Rate limit specification.

    ``burst`` is the token-bucket capacity: how many calls may fire
    immediately before the sustained rate applies. The default of 1 gives
    smooth, evenly-spaced pacing — the safest choice against secondary
    per-second limits. Raise it when the quota genuinely allows bursts.

    Examples::

        RateLimit(10)                        # 10 per second, evenly spaced
        RateLimit(100, "minute")             # 100 per minute, evenly spaced
        RateLimit(100, "minute", burst=20)   # up to 20 at once, then refill pace
    """

    count: float
    per: Literal["second", "minute", "hour"] = "second"
    burst: int = 1

    _VALID_INTERVALS = {"second": 1, "minute": 60, "hour": 3600}

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError(f"RateLimit count must be positive, got {self.count}")
        if self.per not in self._VALID_INTERVALS:
            raise ValueError(
                f'RateLimit per must be "second", "minute", or "hour", got {self.per!r}'
            )
        if self.burst < 1:
            raise ValueError(f"RateLimit burst must be >= 1, got {self.burst}")

    @property
    def per_second(self) -> float:
        return self.count / self._VALID_INTERVALS[self.per]


@dataclass(frozen=True, slots=True)
class Retry:
    """Per-item retry with exponential backoff, jitter, and server-driven waits.

    ``on`` filters by exception type; ``retry_if`` is a predicate on the
    exception *instance* — both must pass for a retry to happen.
    ``wait_from`` extracts a server-mandated wait in seconds from the
    exception (e.g. an HTTP 429 ``Retry-After`` header); when it returns a
    number, that wait replaces the backoff delay — no jitter, no
    ``max_delay`` cap, because the server knows better than we do. When a
    shared ``Limiter`` is in play, a server-mandated wait also pauses the
    limiter, so one task's throttle signal slows the whole pool.

    Examples::

        Retry()                          # 3 attempts, 1s base, exponential + jitter
        Retry(attempts=5, backoff=2.0)   # 5 attempts, 2s base
        Retry(on=(ConnectionError, TimeoutError))  # only retry these
        Retry(jitter=False)              # deterministic delays (testing)

        Retry(                           # honor HTTP 429 + Retry-After
            attempts=5,
            on=(httpx.HTTPStatusError,),
            retry_if=lambda exc: exc.response.status_code in (429, 503),
            wait_from=lambda exc: parse_retry_after(exc.response),  # float | None
        )

    Backoff formula: ``min(backoff * 2^attempt, max_delay) * jitter``
    """

    attempts: int = 3
    backoff: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    on: tuple[type[Exception], ...] | None = None
    retry_if: Callable[[Exception], bool] | None = None
    wait_from: Callable[[Exception], float | None] | None = None

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"Retry attempts must be >= 1, got {self.attempts}")

    def _delay(self, attempt: int) -> float:
        """Compute backoff delay before retry *attempt* (0-indexed)."""
        delay: float = min(self.backoff * (2**attempt), self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        return delay

    def _should_retry(self, exc: Exception) -> bool:
        """True if this exception is retryable (type filter and predicate)."""
        if self.on is not None and not isinstance(exc, self.on):
            return False
        return self.retry_if is None or self.retry_if(exc)

    def _server_wait(self, exc: Exception) -> float | None:
        """Server-mandated wait extracted from *exc*, or None."""
        if self.wait_from is None:
            return None
        wait = self.wait_from(exc)
        return None if wait is None else max(0.0, float(wait))
