"""Shared run bookkeeping for the sync and async engines.

The pieces of a run that are identical across runtimes and carry no
I/O: the stop state (``_RunStop`` — why a run ended, first writer
wins), input sizing (``len`` without materializing), progress totals,
and the timeout failure markers. Plain synchronous state; both engine
files use it directly.

(Previously ``_plan.py``, named for the batch-planning machinery the
v0.6 engine unification deleted.)
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .result import _PENDING, RunStatus, _Failure


class _RunStop:
    """The stop state of one run — first writer wins.

    Stop reasons speak the public ``RunStatus`` vocabulary directly
    (v0.8 — the internal ``_StopReason`` duplicate is gone).
    ``timed_out``/``aborted`` exclusivity is structural: a failure
    salvaged after the deadline may still call ``stop(ABORTED)`` — it
    no-ops, first writer wins.
    """

    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason: RunStatus | None = None

    def stop(self, reason: RunStatus) -> None:
        assert reason is not RunStatus.COMPLETED  # completion isn't a *stop*
        if self.reason is None:
            self.reason = reason

    @property
    def status(self) -> RunStatus:
        """How the run ended — COMPLETED unless a stop was recorded."""
        return self.reason if self.reason is not None else RunStatus.COMPLETED

    @property
    def stopped(self) -> bool:
        return self.reason is not None

    @property
    def timed_out(self) -> bool:
        return self.reason is RunStatus.TIMED_OUT

    @property
    def aborted(self) -> bool:
        return self.reason is RunStatus.ABORTED


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
