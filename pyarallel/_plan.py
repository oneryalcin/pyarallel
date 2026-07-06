"""Shared input bookkeeping for the sync and async engines.

Sizing (``len`` without materializing), progress totals, and the
timeout failure markers — small helpers both runtimes use so sized and
unsized inputs flow through one contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .result import _PENDING, _Failure


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
