"""Result containers: how pyarallel reports success and failure.

Failures are first-class data, never silently swallowed. ``ParallelResult``
behaves like a list until something failed; then it forces you to look.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_MISSING = object()


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

    Iterating or calling ``.values()`` raises ``ExceptionGroup``
    if any task failed — you always see errors, never silently.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: list[Any]) -> None:
        self._entries = entries

    # --- Introspection ---

    @property
    def ok(self) -> bool:
        """True when every task succeeded."""
        return not any(isinstance(e, _Failure) for e in self._entries)

    def values(self) -> list[R]:
        """All results in input order. Raises if any task failed."""
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

    def raise_on_failure(self) -> None:
        """Raise ``ExceptionGroup`` containing all task failures."""
        fails = self.failures()
        if fails:
            n = len(self._entries)
            raise ExceptionGroup(
                f"{len(fails)} of {n} tasks failed",
                [e for _, e in fails],
            )

    # --- list-like interface (raises on failure) ---

    def __iter__(self) -> Iterator[R]:
        return iter(self.values())

    def __getitem__(self, index: int | slice) -> Any:
        self.raise_on_failure()
        return self._entries[index]

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return len(self._entries) > 0

    def __repr__(self) -> str:
        if self.ok:
            return f"ParallelResult({list(self._entries)})"
        s, f = len(self.successes()), len(self.failures())
        return f"ParallelResult({s} ok, {f} failed)"
