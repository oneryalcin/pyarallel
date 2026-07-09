"""Policy specifications: what to allow (``RateLimit``) and how to recover
(``Retry``).

These are small, frozen, composable value objects. Execution machinery lives
elsewhere — a ``RateLimit`` is a spec; the runtime state that enforces it is
``pyarallel.limiter.Limiter``.
"""

from __future__ import annotations

import email.utils
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal


def _default_response(exc: Exception) -> Any:
    """Duck-typed response extraction covering the big three clients:
    httpx and requests hang the response on ``exc.response``; aiohttp's
    ``ClientResponseError`` carries ``status``/``headers`` on the
    exception itself — so the exception *is* the response there."""
    return getattr(exc, "response", exc)


def _http_status(resp: Any) -> int | None:
    """``status_code`` (httpx/requests) or ``status`` (aiohttp), else None."""
    for attr in ("status_code", "status"):
        value = getattr(resp, attr, None)
        if isinstance(value, int):
            return value
    return None


def _retry_after_seconds(resp: Any) -> float | None:
    """Parse ``Retry-After`` from *resp*'s headers — both dialects.

    Numeric (``Retry-After: 30``) and HTTP-date (``Retry-After: Fri, 10
    Jul 2026 08:00:00 GMT``) forms are handled; homemade parsers
    routinely choke on the date form and either crash or retry
    instantly — a stampede, the exact thing the server asked to avoid.
    Malformed or missing values return None (= exponential backoff),
    never an exception. Plain-dict headers (aiohttp) are
    case-sensitive, so both spellings are tried.
    """
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        value = headers.get("retry-after")
    if value is None:
        return None
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)  # RFC 9110: dates are GMT
    return max(0.0, (dt - datetime.now(UTC)).total_seconds())


class _HttpRetryIf:
    """``retry_if`` for ``Retry.for_http`` — a module-level callable
    class (not a closure) so the policy pickles into process workers."""

    __slots__ = ("statuses", "extract")

    def __init__(
        self, statuses: frozenset[int], extract: Callable[[Exception], Any]
    ) -> None:
        self.statuses = statuses
        self.extract = extract

    def __call__(self, exc: Exception) -> bool:
        status = _http_status(self.extract(exc))
        if status is None:
            # No status to judge (e.g. ConnectionError included in on=):
            # the type filter already admitted it — retry.
            return True
        return status in self.statuses


class _HttpWaitFrom:
    """``wait_from`` for ``Retry.for_http`` — picklable, see above."""

    __slots__ = ("extract",)

    def __init__(self, extract: Callable[[Exception], Any]) -> None:
        self.extract = extract

    def __call__(self, exc: Exception) -> float | None:
        return _retry_after_seconds(self.extract(exc))


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
        # isfinite first: NaN compares False to everything, so `<= 0`
        # alone waves it through and it poisons the bucket math silently.
        if not math.isfinite(self.count) or self.count <= 0:
            raise ValueError(
                f"RateLimit count must be positive and finite, got {self.count}"
            )
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
    ``max_delay`` cap. It is clamped only by ``max_server_wait`` (default
    10 minutes), a guard against a hostile or misconfigured server pinning
    worker threads for hours; set it to ``None`` to trust the server
    unconditionally. When a shared ``Limiter`` is in play, a server-mandated
    wait also pauses the limiter, so one task's throttle signal slows the
    whole pool.

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
    max_server_wait: float | None = 600.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"Retry attempts must be >= 1, got {self.attempts}")
        # NaN/inf/negative all poison the delay math downstream (NaN wins
        # every min(), a negative backoff yields negative sleeps) — reject
        # at construction, where the mistake is visible.
        if not math.isfinite(self.backoff) or self.backoff < 0:
            raise ValueError(
                f"Retry backoff must be >= 0 and finite, got {self.backoff}"
            )
        if not math.isfinite(self.max_delay) or self.max_delay < 0:
            raise ValueError(
                f"Retry max_delay must be >= 0 and finite, got {self.max_delay}"
            )
        if self.max_server_wait is not None and (
            not math.isfinite(self.max_server_wait) or self.max_server_wait < 0
        ):
            raise ValueError(
                f"Retry max_server_wait must be >= 0 and finite (or None), got "
                f"{self.max_server_wait}"
            )

    @classmethod
    def for_http(
        cls,
        *,
        on: tuple[type[Exception], ...],
        statuses: set[int] | frozenset[int] = frozenset({429, 503}),
        response: Callable[[Exception], Any] | None = None,
        attempts: int = 3,
        backoff: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
        max_server_wait: float | None = 600.0,
    ) -> Retry:
        """A ``Retry`` prewired for the HTTP throttling dance.

        Retries the exception types in *on* when their response status
        is in *statuses* (default 429/503), honoring ``Retry-After`` in
        both its dialects — numeric seconds and HTTP-date — with
        malformed values falling back to exponential backoff. When a
        shared ``Limiter`` is in play, the server-mandated wait pauses
        the whole pool, as with any ``wait_from``.

        No HTTP client is imported. The response is found by duck
        typing: ``exc.response`` when present (httpx, requests),
        otherwise the exception itself (aiohttp puts ``status`` and
        ``headers`` right on it). Pass ``response=`` to override.
        Exceptions in *on* that carry no status at all (connection
        errors) are retried — the type filter is the whole decision
        for them.

        Examples::

            Retry.for_http(on=(httpx.HTTPStatusError,))
            Retry.for_http(on=(requests.HTTPError,), statuses={429})
            Retry.for_http(on=(aiohttp.ClientResponseError, ConnectionError))

        Returns a plain frozen ``Retry`` — everything composes.
        """
        extract = response if response is not None else _default_response
        return cls(
            attempts=attempts,
            backoff=backoff,
            max_delay=max_delay,
            jitter=jitter,
            max_server_wait=max_server_wait,
            on=on,
            retry_if=_HttpRetryIf(frozenset(statuses), extract),
            wait_from=_HttpWaitFrom(extract),
        )

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
        """Server-mandated wait extracted from *exc*, or None.

        Clamped to ``[0, max_server_wait]`` — a malformed ``Retry-After``
        must not pin a worker thread (and, via the executor's atexit join,
        the whole process) for an unbounded time.
        """
        if self.wait_from is None:
            return None
        wait = self.wait_from(exc)
        if wait is None:
            return None
        wait = max(0.0, float(wait))
        if self.max_server_wait is not None:
            wait = min(wait, self.max_server_wait)
        return wait
