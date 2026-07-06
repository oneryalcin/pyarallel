"""Shared input bookkeeping for the sync and async engines.

Sizing (``len`` without materializing), progress totals, and the
timeout failure markers — small helpers both runtimes use so sized and
unsized inputs flow through one contract.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from typing import Any

from .result import _PENDING, _Failure


class _StopReason(enum.Enum):
    """Why a run stopped early. A run stops for exactly one reason."""

    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class _RunStop:
    """The stop state of one run — first writer wins.

    ``timed_out``/``aborted`` exclusivity used to be a read-time formula
    (``aborted and not timed_out``) applied at each ``ParallelResult``
    construction; this makes it structural. A failure salvaged after the
    deadline may still call ``stop(ABORTED)`` — it no-ops, exactly the
    masking the formula encoded.
    """

    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason: _StopReason | None = None

    def stop(self, reason: _StopReason) -> None:
        if self.reason is None:
            self.reason = reason

    @property
    def stopped(self) -> bool:
        return self.reason is not None

    @property
    def timed_out(self) -> bool:
        return self.reason is _StopReason.TIMED_OUT

    @property
    def aborted(self) -> bool:
        return self.reason is _StopReason.ABORTED


def _validate_max_errors(max_errors: int | None) -> None:
    """Shared max_errors validation for sync and async entry points."""
    if max_errors is not None and max_errors < 1:
        raise ValueError(f"max_errors must be >= 1, got {max_errors}")


def _total_if_known(items: Iterable[Any]) -> int | None:
    """Return len(items) when available without forcing materialization."""
    try:
        return len(items)  # type: ignore[arg-type]
    except TypeError:
        return None


def _progress_total(total: int | None, results: list[Any]) -> int:
    """Return the best total to report in progress callbacks."""
    return total if total is not None else len(results)


def _timeout_failure(timeout: float, idx: int) -> _Failure:
    """Create a consistent timeout failure wrapper."""
    return _Failure(TimeoutError(f"Task {idx} did not complete within {timeout}s"))


def _mark_timeout_indices(
    results: list[Any], indices: Iterable[int], timeout: float
) -> None:
    """Fill any still-pending result slots at *indices* with timeout failures."""
    for idx in indices:
        if results[idx] is _PENDING:
            results[idx] = _timeout_failure(timeout, idx)
