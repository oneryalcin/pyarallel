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

from __future__ import annotations

import os
import threading
import time
from contextlib import suppress
from pathlib import Path


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


def session_initializer() -> None:
    """Run calibrated setup and persist proof before the first task."""
    iterations = int(os.environ["PYARALLEL_BENCH_INIT_N"])
    marker_root = Path(os.environ["PYARALLEL_BENCH_INIT_DIR"])
    start = time.perf_counter_ns()
    spin(iterations)
    duration_ns = time.perf_counter_ns() - start
    identity = (os.getpid(), threading.get_ident())
    (marker_root / f"{identity[0]}-{identity[1]}").write_text(str(duration_ns))


def session_receipt(
    payload: tuple[int, int, str, bool, int],
) -> tuple[int, int, int, bool, int]:
    """Return a value plus worker/initializer evidence for one timed task.

    ``payload`` is ``(value, spin_n, rendezvous_dir, require_init, workers)``.
    The rendezvous forces lazy executors to materialize the same worker count
    inside the timed map instead of assuming scheduler distribution.
    """
    value, spin_n, rendezvous_dir, require_init, workers = payload
    identity = (os.getpid(), threading.get_ident())
    marker = Path(rendezvous_dir) / f"{identity[0]}-{identity[1]}"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)

    deadline = time.monotonic() + 5.0
    while len(tuple(Path(rendezvous_dir).iterdir())) < workers:
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"worker rendezvous timed out before {workers} identities arrived"
            )
        time.sleep(0.001)

    init_duration_ns = 0
    if require_init:
        init_root = Path(os.environ["PYARALLEL_BENCH_INIT_DIR"])
        init_marker = init_root / f"{identity[0]}-{identity[1]}"
        with suppress(FileNotFoundError, ValueError):
            init_duration_ns = int(init_marker.read_text())
    initialized = init_duration_ns > 0
    if require_init and (not initialized or init_duration_ns <= 0):
        raise RuntimeError("worker initializer evidence is missing")
    if spin_n:
        spin(spin_n)
    return value, identity[0], identity[1], initialized, init_duration_ns
