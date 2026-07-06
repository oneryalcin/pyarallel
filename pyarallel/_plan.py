"""Input planning: batching, lazy consumption, and timeout bookkeeping.

Shared by the sync and async engines so both consume inputs — sized or
unsized, batched or not — through one code path.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Any

from .result import _PENDING, _Failure


@dataclass(slots=True)
class _CollectedMapPlan:
    """Shared input plan for collected sync/async map execution."""

    results: list[Any]
    total: int | None
    batches: Iterable[list[tuple[int, Any]]]
    remaining: Iterator[Any] | None = None


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


def _make_chunks(n: int, batch_size: int | None) -> list[range]:
    """Split range(n) into chunks for batched processing."""
    if batch_size is not None and batch_size < n:
        return [
            range(start, min(start + batch_size, n))
            for start in range(0, n, batch_size)
        ]
    return [range(n)]


def _iter_batches_from_iterator(
    it: Iterator[Any], batch_size: int
) -> Iterator[list[tuple[int, Any]]]:
    """Yield indexed item batches lazily from an existing iterator."""
    index = 0
    while True:
        batch = list(islice(it, batch_size))
        if not batch:
            break
        start = index
        index += len(batch)
        yield list(enumerate(batch, start))


def _plan_collected_map(
    items: Iterable[Any], batch_size: int | None
) -> _CollectedMapPlan:
    """Build the shared input plan for collected map-style execution."""
    if batch_size is None:
        items_list = list(items)
        total = len(items_list)
        batches = (
            [*enumerate(items_list[start:stop], start)]
            for start, stop in (
                (chunk.start, chunk.stop) for chunk in _make_chunks(total, batch_size)
            )
        )
        return _CollectedMapPlan(
            results=[_PENDING] * total,
            total=total,
            batches=batches,
        )

    return _CollectedMapPlan(
        results=[],
        total=_total_if_known(items),
        batches=_iter_batches_from_iterator(source := iter(items), batch_size),
        remaining=source,
    )


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


def _append_timeout_failures(
    results: list[Any], remaining: Iterator[Any] | None, timeout: float
) -> None:
    """Drain remaining unseen input items and append timeout failures."""
    if remaining is None:
        return
    for _item in remaining:
        results.append(_timeout_failure(timeout, len(results)))
