#!/usr/bin/env python3
"""The committed benchmark lab for pyarallel.

One runnable harness, stdlib + pyarallel only. Every benchmark is named
after the documented claim it backs, so the numbers in the docs stay
falsifiable on *your* machine instead of resting on a throwaway script that
ran once somewhere.

    uv run --no-project --with . python benchmarks/bench.py           # standard
    uv run --no-project --python 3.14t --with . python benchmarks/bench.py  # FT
    python benchmarks/bench.py --quick   # fast pass, weaker ratios
    python benchmarks/bench.py --json    # machine-readable

Claims mapped 1:1 (docs/user-guide/best-practices.md, roadmap.md):

  cpu-scaling      thread workers=1 vs 4  -> "1.0x under the GIL, 2.4x on
                   free-threaded 3.13t/3.14t"; and thread vs interpreter on
                   a GIL build -> "3.4x, interpreters get true CPU
                   parallelism on standard builds".
  engine-overhead  per-item driver cost of parallel_map / parallel_iter
                   over a plain loop, at two sizes a decade apart —
                   flat per-item overhead is the evidence behind
                   "one windowed engine, O(n) driver".
  worker-startup   time to first result on a cold pool per executor ->
                   interpreter cold-start vs process fork/spawn
                   (measured ~50 ms vs ~72 ms on the reference machine;
                   machine-dependent).

Why the ratios need enough items: parallel_map builds a *fresh* pool per
call, so interpreter/process pay their cold-start on every call. The CPU
parallelism the claims describe only dominates once the compute outweighs
that start-up, which is exactly why worker-startup measures the start-up on
its own. Run with --quick (few items) and the interpreter ratio is
start-up-bound and low; the default item count is chosen to let the
steady-state ratio show.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

# _workloads is the sibling module in this directory; running the file as a
# script puts benchmarks/ on sys.path[0], so it imports cleanly. The lab
# installs pyarallel as a normal dependency; nothing here is imported by the
# package or the test suite.
from _workloads import noop, spin

from pyarallel import parallel_iter, parallel_map

# ~5 ms per item on an Apple M-series core; see _workloads.spin.
SPIN_N = 140_000

InterpreterMaybe = tuple[str, ...]


def _executors() -> InterpreterMaybe:
    base = ("thread", "process")
    if sys.version_info >= (3, 14):
        return (*base, "interpreter")
    return base


def _median_ms(fn: Callable[[], Any], reps: int) -> float:
    """Warm up once, then return the median wall time of ``reps`` runs, in ms."""
    fn()  # warmup: pay import/compile/first-touch costs outside the timing
    samples = []
    for _ in range(reps):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples) * 1000.0


def machine_context() -> dict[str, Any]:
    gil = getattr(sys, "_is_gil_enabled", None)
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "python_build": " ".join(platform.python_build()),
        "python_impl": platform.python_implementation(),
        "gil_enabled": (gil() if gil is not None else None),
        "cpu_count": os.cpu_count(),
    }


def bench_cpu_scaling(reps: int, items: int) -> dict[str, Any]:
    """CPU parallelism: does adding workers actually speed CPU-bound work up?"""
    data = [SPIN_N] * items

    def run(workers: int, executor: str) -> Callable[[], Any]:
        return lambda: parallel_map(spin, data, workers=workers, executor=executor)

    thread_1 = _median_ms(run(1, "thread"), reps)
    thread_4 = _median_ms(run(4, "thread"), reps)
    result: dict[str, Any] = {
        "items": items,
        "spin_n": SPIN_N,
        "thread_w1_ms": round(thread_1, 2),
        "thread_w4_ms": round(thread_4, 2),
        # Backs 1.0x (GIL) / 2.4x (free-threaded) — threads over sequential.
        "thread_scaling_x": round(thread_1 / thread_4, 2),
    }
    for ex in ("process", "interpreter"):
        if ex not in _executors():
            continue
        t = _median_ms(run(4, ex), reps)
        result[f"{ex}_w4_ms"] = round(t, 2)
        # thread_over_<ex>: how much faster the executor is than GIL-bound
        # threads at 4 workers. For interpreter on a GIL build this backs 3.4x.
        result[f"thread_over_{ex}_x"] = round(thread_4 / t, 2)
    return result


def bench_engine_overhead(reps: int, items: int) -> dict[str, Any]:
    """Per-item driver cost at two sizes a decade apart.

    One size cannot demonstrate linearity (v0.10 review) — a flat
    per-item overhead across a 10x size range is the actual evidence
    that the windowed driver is O(n).
    """
    sizes = [max(items // 10, 100), items]
    per_size: list[dict[str, Any]] = []
    for n in sizes:
        data = list(range(n))
        loop_ms = _median_ms(lambda d=data: [noop(x) for x in d], reps)
        map_ms = _median_ms(lambda d=data: parallel_map(noop, d, workers=4), reps)
        iter_ms = _median_ms(
            lambda d=data: [r for r in parallel_iter(noop, d, workers=4)], reps
        )
        per = 1000.0 / n  # ms -> us/item
        per_size.append(
            {
                "items": n,
                "loop_us_per_item": round(loop_ms * per, 3),
                "parallel_map_us_per_item": round(map_ms * per, 3),
                "parallel_iter_us_per_item": round(iter_ms * per, 3),
                "parallel_map_overhead_us": round((map_ms - loop_ms) * per, 3),
                "parallel_iter_overhead_us": round((iter_ms - loop_ms) * per, 3),
            }
        )
    return {"sizes": per_size}


def bench_worker_startup(reps: int) -> dict[str, Any]:
    """Cold-pool time to first result per executor — backs ~30 ms interpreters."""
    out: dict[str, Any] = {}
    for ex in _executors():
        samples = []
        for _ in range(reps):
            start = time.perf_counter()
            # A fresh call each time = a fresh (cold) pool. Pull one result so
            # we time pool spin-up + one worker + one task round-trip.
            gen = parallel_iter(noop, range(64), workers=1, executor=ex)
            next(iter(gen))
            gen.close()
            samples.append((time.perf_counter() - start) * 1000.0)
        out[f"{ex}_first_result_ms"] = round(statistics.median(samples), 2)
    return out


def run_all(quick: bool) -> dict[str, Any]:
    reps = 3 if quick else 5
    # 300 items is where the interpreter-vs-thread ratio on a GIL build
    # amortizes the ~50 ms cold pool and reaches the documented ~3.4x; --quick
    # uses far fewer, so its interpreter ratio is start-up-bound and low.
    cpu_items = 40 if quick else 300
    overhead_items = 5_000 if quick else 20_000
    return {
        "context": machine_context(),
        "config": {
            "quick": quick,
            "reps": reps,
            "executors": list(_executors()),
        },
        "cpu_scaling": bench_cpu_scaling(reps, cpu_items),
        "engine_overhead": bench_engine_overhead(reps, overhead_items),
        "worker_startup": bench_worker_startup(reps),
    }


def print_human(report: dict[str, Any]) -> None:
    ctx = report["context"]
    gil = ctx["gil_enabled"]
    gil_str = "n/a" if gil is None else ("ENABLED" if gil else "DISABLED")
    print("pyarallel benchmark lab")
    print("-" * 60)
    print(f"platform : {ctx['platform']}")
    print(f"python   : {ctx['python_version']}  ({ctx['python_build']})")
    print(f"GIL      : {gil_str}")
    print(f"cpu_count: {ctx['cpu_count']}")
    print(
        f"config   : quick={report['config']['quick']} "
        f"reps={report['config']['reps']} "
        f"executors={','.join(report['config']['executors'])}"
    )
    print()

    cs = report["cpu_scaling"]
    print(f"[cpu-scaling]  {cs['items']} items x spin(n={cs['spin_n']})")
    print(f"  thread w1        {cs['thread_w1_ms']:>9.2f} ms")
    print(
        f"  thread w4        {cs['thread_w4_ms']:>9.2f} ms   "
        f"scaling {cs['thread_scaling_x']}x  (claim: 1.0x GIL / 2.4x free-threaded)"
    )
    if "process_w4_ms" in cs:
        print(
            f"  process w4       {cs['process_w4_ms']:>9.2f} ms   "
            f"thread/process {cs['thread_over_process_x']}x"
        )
    if "interpreter_w4_ms" in cs:
        print(
            f"  interpreter w4   {cs['interpreter_w4_ms']:>9.2f} ms   "
            f"thread/interp {cs['thread_over_interpreter_x']}x  (claim: 3.4x on GIL)"
        )
    print()

    eo = report["engine_overhead"]
    print("[engine-overhead]  no-op items, two sizes (flat per-item = linear)")
    for row in eo["sizes"]:
        print(f"  n={row['items']:>6}")
        print(f"    plain loop       {row['loop_us_per_item']:>9.3f} us/item")
        print(
            f"    parallel_map     {row['parallel_map_us_per_item']:>9.3f} us/item   "
            f"(+{row['parallel_map_overhead_us']} us/item engine)"
        )
        print(
            f"    parallel_iter    {row['parallel_iter_us_per_item']:>9.3f} us/item   "
            f"(+{row['parallel_iter_overhead_us']} us/item engine)"
        )
    print()

    ws = report["worker_startup"]
    print("[worker-startup]  cold pool, workers=1, time to first result")
    for ex in report["config"]["executors"]:
        key = f"{ex}_first_result_ms"
        if key in ws:
            note = "  (claim: beats process fork/spawn)" if ex == "interpreter" else ""
            print(f"  {ex:<12}   {ws[key]:>9.2f} ms{note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="pyarallel benchmark lab")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of a table",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="fast pass, fewer items/reps (ratios are weaker)",
    )
    args = parser.parse_args()

    report = run_all(quick=args.quick)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)


if __name__ == "__main__":
    # Guard is load-bearing: the process executor re-imports this script in a
    # spawned worker, and running main() at import time there would fork-bomb.
    main()
