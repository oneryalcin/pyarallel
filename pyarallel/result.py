"""Result containers: how pyarallel reports success and failure.

Failures are first-class data, never silently swallowed. ``ParallelResult``
behaves like a list until something failed; then it forces you to look.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, overload

_MISSING = object()

# Sentinel for "slot not yet filled" — distinct from a legitimate None return.
# Lives here so ParallelResult can refuse to be built around a leaked one.
_PENDING = object()


class RunStatus(enum.Enum):
    """How a run ended. One vocabulary for every engine, sync and async.

    ``COMPLETED`` means the source was exhausted and every admitted item
    resolved — items may still have *failed*; that is the meaningful
    "completed but not ok" state. ``TIMED_OUT`` and ``ABORTED`` are
    truncations: the source was not exhausted, and for unsized inputs
    the missing items leave no per-item trace — the status is the one
    reliable signal.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"
    CANCELLED = "cancelled"  # cooperative stop (v0.9): StopToken.stop()


class Cancelled(RuntimeError):
    """The run stopped because its ``StopToken`` was stopped.

    Items that never ran (or, async, were cancelled in flight) are
    marked with this — distinguishable from genuine failures:
    ``isinstance(exc, Cancelled)``.
    """


class Aborted(RuntimeError):
    """The run stopped early because ``max_errors`` failures accumulated.

    Items that never ran (or never finished) are marked with this — they
    are distinguishable from items that genuinely failed:
    ``isinstance(exc, Aborted)``.
    """


class _Failure:
    """Sentinel wrapping a failed task result."""

    __slots__ = ("exception",)

    def __init__(self, exception: Exception) -> None:
        self.exception = exception


@dataclass(slots=True)
class _Outcome:
    """Internal task outcome with execution metadata.

    Returned by the worker-side execution wrapper; module-level and
    plain-field so it pickles across process boundaries. Exactly one of
    ``value`` / ``error`` is meaningful, discriminated by ``error is None``.
    """

    value: Any
    error: Exception | None
    attempts: int
    duration: float


def _item_result(idx: int, outcome: _Outcome) -> ItemResult[Any]:
    """Build the streaming result item from a task outcome."""
    if outcome.error is not None:
        return ItemResult(
            idx,
            error=outcome.error,
            attempts=outcome.attempts,
            duration=outcome.duration,
        )
    return ItemResult(
        idx,
        value=outcome.value,
        attempts=outcome.attempts,
        duration=outcome.duration,
    )


@dataclass(init=False, frozen=True, slots=True)
class ItemResult[R]:
    """Single streaming result item.

    Exactly one of ``value`` or ``error`` is set.

    ``attempts`` is the number of attempts actually made (1 = no retry).
    ``duration`` is wall-clock seconds from the start of the first
    attempt to the final outcome — *including* retry backoff sleeps,
    *excluding* time spent queued before a worker picked the item up.
    """

    index: int
    value: R | None
    error: Exception | None
    attempts: int
    duration: float

    def __init__(
        self,
        index: int,
        value: R | None | object = _MISSING,
        error: Exception | None | object = _MISSING,
        attempts: int = 1,
        duration: float = 0.0,
    ) -> None:
        has_value = value is not _MISSING
        has_error = error is not _MISSING
        if has_value == has_error:
            raise ValueError("Exactly one of value or error must be set")
        # ItemResult(error=None) used to pass this check (the parameter
        # *was* supplied) and build a fake success — .ok True, .value None.
        if has_error and not isinstance(error, Exception):
            raise ValueError(f"error must be an Exception instance, got {error!r}")
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "value", value if has_value else None)
        object.__setattr__(self, "error", error if has_error else None)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(self, "duration", duration)

    @property
    def ok(self) -> bool:
        """True when this item succeeded."""
        return self.error is None


class ParallelResult[R]:
    """Results from parallel execution.

    Behaves like a ``list[R]`` when every task succeeded.
    When some tasks failed, use ``.successes()``, ``.failures()``,
    or ``.raise_on_failure()`` for structured access.

    Iterating or calling ``.values()`` raises if any task failed
    (``ExceptionGroup``) *or* the run was truncated (``TimeoutError`` /
    ``Aborted``) — you always see problems, never silently.

    ``status`` reports how the run *ended* (``RunStatus``); ``timed_out``
    / ``aborted`` are derived reads. The status exists because per-item
    failure markers cannot always carry that fact: an unsized input
    that hits the total ``timeout=`` returns only the items actually
    pulled from the source — possibly all successes — and the status
    is the one reliable signal that the result is a truncation,
    not a completion.
    """

    __slots__ = ("_entries", "_meta", "_status")

    def __init__(
        self,
        entries: list[Any],
        *,
        status: RunStatus = RunStatus.COMPLETED,
        meta: list[tuple[int, float]] | None = None,
    ) -> None:
        # A leaked unfilled slot must fail loudly here, not surface later
        # as a silent "success" value from .values()/.ok.
        if any(e is _PENDING for e in entries):
            raise RuntimeError(
                "internal error: unfilled result slot leaked into ParallelResult"
            )
        # The meta list is hand-aligned with entries at every site that
        # grows results, across three engines — misalignment is a bug in
        # an engine, and it must fail here, not as a wrong receipt later.
        if meta is not None and len(meta) != len(entries):
            raise RuntimeError(
                "internal error: metadata misaligned with results "
                f"({len(meta)} != {len(entries)})"
            )
        self._entries = entries
        # Per-index (attempts, duration), index-aligned with entries. The
        # engines fill it; hand-constructed results leave it None and
        # item_results() synthesizes honest defaults.
        self._meta = meta
        self._status = status

    # --- Introspection ---

    @property
    def ok(self) -> bool:
        """True when the run completed AND every task succeeded.

        A truncated run (``TIMED_OUT`` / ``ABORTED``) is never ok, even
        when every *returned* item succeeded — the items never pulled
        from the source did not.
        """
        return self._status is RunStatus.COMPLETED and not any(
            isinstance(e, _Failure) for e in self._entries
        )

    @property
    def status(self) -> RunStatus:
        """How the run ended — the source of truth."""
        return self._status

    @property
    def complete(self) -> bool:
        """True when the source was exhausted and every item resolved.

        Independent of item failures: a run can be complete and not ok.
        """
        return self._status is RunStatus.COMPLETED

    @property
    def timed_out(self) -> bool:
        """True when the run stopped on the total ``timeout=`` deadline."""
        return self._status is RunStatus.TIMED_OUT

    @property
    def aborted(self) -> bool:
        """True when the run stopped early via ``max_errors``."""
        return self._status is RunStatus.ABORTED

    def _raise_if_truncated(self) -> None:
        """Truncated runs have no 'all results' to hand out.

        Checked BEFORE per-item failures (v0.8 review): sized truncations
        carry placeholder failure markers, and failures-first would raise
        an ``ExceptionGroup`` for those while unsized truncations raised
        ``TimeoutError`` — two surfaces for the same event. Truncation is
        the louder fact; the exception message routes to the partial
        accessors, and ``.failures()`` still has the per-item detail.
        """
        if self._status is RunStatus.TIMED_OUT:
            raise TimeoutError(
                f"run timed out after {len(self._entries)} items — the "
                "source was not exhausted, so these are partial results. "
                "Use .successes() / .ok_values() to consume them."
            )
        if self._status is RunStatus.ABORTED:
            raise Aborted(
                f"run aborted (max_errors) after {len(self._entries)} "
                "items — partial results. Use .successes() / .ok_values() "
                "to consume them."
            )
        if self._status is RunStatus.CANCELLED:
            raise Cancelled(
                f"run cancelled (StopToken) after {len(self._entries)} "
                "items — partial results. Use .successes() / .ok_values() "
                "to consume them."
            )

    def values(self) -> list[R]:
        """All results in input order.

        Raises if the run was truncated (``TimeoutError`` / ``Aborted``)
        or any task failed (``ExceptionGroup``) — a partial list must
        never read as the whole. Truncation is checked first.
        """
        self._raise_if_truncated()
        self.raise_on_failure()
        return list(self._entries)

    def successes(self) -> list[tuple[int, R]]:
        """``(index, value)`` for each task that succeeded."""
        return [
            (i, v) for i, v in enumerate(self._entries) if not isinstance(v, _Failure)
        ]

    def ok_values(self) -> list[R]:
        """Values of successful tasks only, in input order. Never raises."""
        return [v for _, v in self.successes()]

    def failures(self) -> list[tuple[int, Exception]]:
        """``(index, exception)`` for each task that failed."""
        return [
            (i, f.exception)
            for i, f in enumerate(self._entries)
            if isinstance(f, _Failure)
        ]

    def item_results(self) -> list[ItemResult[R]]:
        """Every item as an ``ItemResult``, in input order. Never raises.

        The collected mirror of the streaming ``ItemResult`` — same
        vocabulary (index/value/error/attempts/duration) so a collected
        run and a streamed run read identically. A partial-results
        accessor, like ``.successes()``: it does not raise on failures
        or truncation.

        ``attempts``/``duration`` are the receipts the workers already
        computed. Two honest special cases carry ``attempts=0,
        duration=0.0`` — nothing ran *this* run: a checkpoint cache hit
        and a truncation placeholder (timeout/abort). The ``ItemResult``
        docstring's "1 = no retry" applies to items that actually ran.

        Hand-constructed results have no metadata; each item then
        synthesizes ``attempts=1, duration=0.0`` (metadata unavailable).
        """
        out: list[ItemResult[R]] = []
        for i, e in enumerate(self._entries):
            attempts, duration = self._meta[i] if self._meta is not None else (1, 0.0)
            if isinstance(e, _Failure):
                out.append(
                    ItemResult(
                        i, error=e.exception, attempts=attempts, duration=duration
                    )
                )
            else:
                out.append(ItemResult(i, value=e, attempts=attempts, duration=duration))
        return out

    def raise_on_failure(self) -> None:
        """Raise ``ExceptionGroup`` containing all task failures.

        Each sub-exception carries its item index as a PEP 678 note —
        provenance without changing exception types, so
        ``except* ConnectionError`` matching is untouched. Notes mutate
        the stored exception objects: repeated calls don't duplicate a
        note, but the same exception *instance* raised for items in two
        different runs accumulates a note per run — raise fresh
        exceptions per item if that matters.
        """
        fails = self.failures()
        if fails:
            n = len(self._entries)
            for i, e in fails:
                note = f"pyarallel: item index {i}"
                if note not in getattr(e, "__notes__", ()):
                    e.add_note(note)
            raise ExceptionGroup(
                f"{len(fails)} of {n} tasks failed",
                [e for _, e in fails],
            )

    # --- list-like interface (raises on failure/truncation) ---

    def __iter__(self) -> Iterator[R]:
        return iter(self.values())

    @overload
    def __getitem__(self, index: int) -> R: ...
    @overload
    def __getitem__(self, index: slice) -> list[R]: ...
    def __getitem__(self, index: int | slice) -> R | list[R]:
        self._raise_if_truncated()
        self.raise_on_failure()
        return self._entries[index]

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return len(self._entries) > 0

    def __repr__(self) -> str:
        # A truncated run must not print like a complete one.
        status = "" if self.complete else f", {self._status.value}"
        if self.ok and not status:
            return f"ParallelResult({list(self._entries)})"
        s, f = len(self.successes()), len(self.failures())
        return f"ParallelResult({s} ok, {f} failed{status})"
