"""Importable workloads for the benchmark lab.

These live in a real module (not ``__main__``) on purpose: the ``process``
and ``interpreter`` executors re-import the target function by
``(module, qualname)`` in a fresh worker. A function defined in the
``bench.py`` script's ``__main__`` is unreachable to an interpreter worker
(it gets a fresh, empty ``__main__``) and would fail per item. Keeping the
workloads here is what lets one harness exercise all three executors.

Deliberately dependency-free: the whole point of the lab is that anyone can
re-run it with stdlib + pyarallel only.
"""


def spin(n: int) -> int:
    """CPU-bound: sum of squares in a pure-Python loop.

    Pure Python (no C extension) so the GIL actually serializes it on a
    standard build and free-threading / subinterpreters can actually
    parallelize it. Tune ``n`` for the target per-item wall time
    (~140_000 is ~5 ms on an Apple M-series core).
    """
    total = 0
    for i in range(n):
        total += i * i
    return total


def noop(x: int) -> int:
    """Near-zero-work item: isolates the engine's per-item driver cost."""
    return x
