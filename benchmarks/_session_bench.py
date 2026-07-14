"""Reusable-session benchmark mechanics and deterministic verdict rules."""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pyarallel import parallel_map

from ._workloads import session_initializer, session_receipt, spin

Strategy = Literal["pyarallel_fresh", "stdlib_fresh", "stdlib_reused"]
STRATEGIES: tuple[Strategy, ...] = (
    "pyarallel_fresh",
    "stdlib_fresh",
    "stdlib_reused",
)
NOISE_RATIO_MAX = 0.10
P1_ABSOLUTE_DIFFERENCE_MS_MAX = 10.0
P1_RELATIVE_DIFFERENCE_MAX = 0.15
T10_SAVED_PER_EXTRA_CALL_MS_MAX = 10.0
_ORDERS: tuple[tuple[Strategy, ...], ...] = (
    ("pyarallel_fresh", "stdlib_fresh", "stdlib_reused"),
    ("stdlib_fresh", "stdlib_reused", "pyarallel_fresh"),
    ("stdlib_reused", "pyarallel_fresh", "stdlib_fresh"),
    ("stdlib_reused", "stdlib_fresh", "pyarallel_fresh"),
    ("pyarallel_fresh", "stdlib_reused", "stdlib_fresh"),
    ("stdlib_fresh", "pyarallel_fresh", "stdlib_reused"),
)


@dataclass(frozen=True, slots=True)
class Cell:
    id: str
    executor: Literal["thread", "process", "interpreter"]
    workers: int
    calls: int
    items: int
    task: Literal["spin", "initialized", "noop"]
    initializer: bool = False
    gate: bool = True


@dataclass(frozen=True, slots=True)
class StrategyStats:
    samples_ns: tuple[int, ...]
    median_ns: float
    mad_ns: float
    noise_ratio: float


@dataclass(frozen=True, slots=True)
class CellMetrics:
    calls: int
    valid: bool
    strategies: Mapping[str, StrategyStats]
    reason: str | None = None
    initializer_durations_ns: Mapping[str, tuple[tuple[int, ...], ...]] = field(
        default_factory=dict
    )


def default_cells(*, include_interpreter: bool, quick: bool) -> tuple[Cell, ...]:
    process = (
        Cell("P1", "process", 4, 1, 8, "spin"),
        Cell("P3", "process", 4, 3, 8, "spin"),
        Cell("P10", "process", 4, 10, 8, "spin"),
        Cell("P64", "process", 4, 10, 64, "spin"),
        Cell("T10", "thread", 4, 10, 8, "spin"),
        Cell("PI3", "process", 4, 3, 8, "initialized", initializer=True),
        Cell("PI10", "process", 4, 10, 8, "initialized", initializer=True),
    )
    if quick:
        return process[:2] + (process[4],)
    if not include_interpreter:
        return process
    mirrors = tuple(
        Cell(
            f"I{cell.id}",
            "interpreter",
            cell.workers,
            cell.calls,
            cell.items,
            cell.task,
            initializer=cell.initializer,
        )
        for cell in process
        if cell.executor == "process"
    )
    return process + mirrors


def diagnostic_cells() -> tuple[Cell, ...]:
    return (
        Cell("D-W1", "process", 1, 3, 8, "spin", gate=False),
        Cell("D-I1", "process", 1, 3, 1, "spin", gate=False),
        Cell("D-NOOP", "process", 4, 3, 8, "noop", gate=False),
    )


def strategy_order(repetition: int) -> tuple[Strategy, ...]:
    return _ORDERS[repetition % len(_ORDERS)]


def aggregate(samples_ns: Sequence[int]) -> StrategyStats:
    if not samples_ns or any(sample <= 0 for sample in samples_ns):
        raise ValueError("timing samples must be positive")
    median_ns = float(statistics.median(samples_ns))
    deviations = [abs(sample - median_ns) for sample in samples_ns]
    mad_ns = float(statistics.median(deviations))
    return StrategyStats(
        samples_ns=tuple(samples_ns),
        median_ns=median_ns,
        mad_ns=mad_ns,
        noise_ratio=mad_ns / median_ns,
    )


def _saved_per_extra_call_ms(metrics: CellMetrics) -> float:
    if metrics.calls <= 1:
        raise ValueError("per-extra-call savings is undefined for one call")
    fresh = metrics.strategies["stdlib_fresh"].median_ns
    reused = metrics.strategies["stdlib_reused"].median_ns
    return (fresh - reused) / 1_000_000.0 / (metrics.calls - 1)


def _speedup(metrics: CellMetrics) -> float:
    fresh = metrics.strategies["stdlib_fresh"].median_ns
    reused = metrics.strategies["stdlib_reused"].median_ns
    return fresh / reused


def classify(cells: Mapping[str, CellMetrics]) -> str:
    required = ("P1", "P3", "P10", "P64", "T10", "PI3", "PI10")
    for cell_id in required:
        metrics = cells.get(cell_id)
        if metrics is None or not metrics.valid:
            return "invalid-harness"
        if set(metrics.strategies) != set(STRATEGIES):
            return "invalid-harness"
        if any(stats.median_ns <= 0 for stats in metrics.strategies.values()):
            return "invalid-harness"
    p1 = cells["P1"]
    p1_fresh = p1.strategies["stdlib_fresh"].median_ns
    p1_reused = p1.strategies["stdlib_reused"].median_ns
    if abs(p1_fresh - p1_reused) > max(
        P1_ABSOLUTE_DIFFERENCE_MS_MAX * 1_000_000.0,
        p1_fresh * P1_RELATIVE_DIFFERENCE_MAX,
    ):
        return "invalid-harness"
    if _saved_per_extra_call_ms(cells["T10"]) > T10_SAVED_PER_EXTRA_CALL_MS_MAX:
        return "invalid-harness"
    if any(
        cells[cell_id].strategies[strategy].noise_ratio > NOISE_RATIO_MAX
        for cell_id in required
        for strategy in ("stdlib_fresh", "stdlib_reused")
    ):
        return "noisy"

    ordinary = (
        _speedup(cells["P3"]) >= 1.25
        and _saved_per_extra_call_ms(cells["P3"]) >= 10.0
        and _speedup(cells["P10"]) >= 1.50
        and _saved_per_extra_call_ms(cells["P10"]) >= 20.0
        and _speedup(cells["P64"]) >= 1.10
        and _saved_per_extra_call_ms(cells["P64"]) >= 5.0
    )
    if ordinary:
        return "advance-to-design"

    initializer_only = all(
        _speedup(cells[cell_id]) >= 2.0
        and _saved_per_extra_call_ms(cells[cell_id]) >= 50.0
        for cell_id in ("PI3", "PI10")
    )
    return "initializer-specific" if initializer_only else "defer"


def combine_verdicts(first: str, second: str) -> str:
    if "invalid-harness" in (first, second):
        return "invalid-harness"
    if "noisy" in (first, second):
        return "defer-noisy"
    return first if first == second else "defer-unstable"


def _measure_spin_samples(iterations: int) -> tuple[int, ...]:
    samples: list[int] = []
    for _ in range(3):
        start = time.perf_counter_ns()
        spin(iterations)
        samples.append(time.perf_counter_ns() - start)
    return tuple(samples)


def _measure_spin(iterations: int) -> int:
    return int(statistics.median(_measure_spin_samples(iterations)))


def calibrate(
    target_ns: int, minimum_ns: int, maximum_ns: int
) -> tuple[int, int, tuple[int, ...]]:
    high = 1_000
    high_ns = _measure_spin(high)
    while high_ns < target_ns:
        high *= 2
        high_ns = _measure_spin(high)
        if high > 1_000_000_000:
            raise RuntimeError("spin calibration failed to converge")
    low = high // 2
    best = (high, high_ns)
    for _ in range(12):
        middle = (low + high) // 2
        measured = _measure_spin(middle)
        if abs(measured - target_ns) < abs(best[1] - target_ns):
            best = (middle, measured)
        if measured < target_ns:
            low = middle + 1
        else:
            high = max(middle - 1, low)
    final_samples = _measure_spin_samples(best[0])
    final_median = int(statistics.median(final_samples))
    if not minimum_ns <= final_median <= maximum_ns:
        raise RuntimeError(
            f"spin calibration outside accepted band: {final_median / 1e6:.2f} ms"
        )
    return best[0], final_median, final_samples


def _executor(cell: Cell) -> Executor:
    initializer = session_initializer if cell.initializer else None
    if cell.executor == "thread":
        return ThreadPoolExecutor(max_workers=cell.workers, initializer=initializer)
    if cell.executor == "interpreter":
        if sys.version_info >= (3, 14):
            from concurrent.futures import InterpreterPoolExecutor

            return InterpreterPoolExecutor(
                max_workers=cell.workers, initializer=initializer
            )
        raise RuntimeError("interpreter benchmark requires Python 3.14+")
    return ProcessPoolExecutor(max_workers=cell.workers, initializer=initializer)


Receipt = tuple[int, int, int, bool, int]


def _inputs(
    cell: Cell, spin_n: int, call_dir: Path
) -> list[tuple[int, int, str, bool, int]]:
    task_spin = spin_n if cell.task == "spin" else 0
    return [
        (value, task_spin, str(call_dir), cell.initializer, cell.workers)
        for value in range(cell.items)
    ]


def _validate_call(cell: Cell, call_dir: Path, receipts: Sequence[Receipt]) -> None:
    values = [receipt[0] for receipt in receipts]
    if values != list(range(cell.items)):
        raise RuntimeError(f"{cell.id}: wrong result values")
    identities = {(receipt[1], receipt[2]) for receipt in receipts}
    markers = {entry.name for entry in call_dir.iterdir()}
    expected_markers = {f"{pid}-{thread_id}" for pid, thread_id in identities}
    if len(identities) != cell.workers or markers != expected_markers:
        raise RuntimeError(
            f"{cell.id}: expected {cell.workers} worker identities, "
            f"got {len(identities)}"
        )
    if cell.initializer:
        for receipt in receipts:
            duration_ns = receipt[4]
            if not receipt[3] or duration_ns <= 0:
                raise RuntimeError(
                    f"{cell.id}: invalid initializer receipt {duration_ns / 1e6:.2f} ms"
                )


def _run_strategy(
    strategy: Strategy,
    cell: Cell,
    spin_n: int,
    sample_root: Path,
) -> tuple[int, tuple[int, ...]]:
    calls: list[tuple[Path, list[tuple[int, int, str, bool, int]]]] = []
    for call in range(cell.calls):
        call_dir = sample_root / f"call-{call}"
        call_dir.mkdir(parents=True)
        calls.append((call_dir, _inputs(cell, spin_n, call_dir)))

    init_dirs: list[Path | None] = [None] * cell.calls
    if cell.initializer:
        if strategy == "stdlib_reused":
            created_init_dir = sample_root / "initializers"
            created_init_dir.mkdir()
            init_dirs = [created_init_dir] * cell.calls
        else:
            init_dirs = []
            for call_dir, _ in calls:
                created_init_dir = sample_root / f"initializers-{call_dir.name}"
                created_init_dir.mkdir()
                init_dirs.append(created_init_dir)

    start = time.perf_counter_ns()
    if strategy == "pyarallel_fresh":
        all_receipts = []
        for (_, inputs), configured_init_dir in zip(calls, init_dirs, strict=True):
            if configured_init_dir is not None:
                os.environ["PYARALLEL_BENCH_INIT_DIR"] = str(configured_init_dir)
            result = parallel_map(
                session_receipt,
                inputs,
                workers=cell.workers,
                executor=cast(Any, cell.executor),
                worker_init=session_initializer if cell.initializer else None,
            )
            all_receipts.append(cast(list[Receipt], result.values()))
    elif strategy == "stdlib_fresh":
        all_receipts = []
        for (_, inputs), configured_init_dir in zip(calls, init_dirs, strict=True):
            if configured_init_dir is not None:
                os.environ["PYARALLEL_BENCH_INIT_DIR"] = str(configured_init_dir)
            with _executor(cell) as pool:
                all_receipts.append(list(pool.map(session_receipt, inputs)))
    else:
        all_receipts = []
        configured_init_dir = init_dirs[0] if init_dirs else None
        if configured_init_dir is not None:
            os.environ["PYARALLEL_BENCH_INIT_DIR"] = str(configured_init_dir)
        with _executor(cell) as pool:
            for _, inputs in calls:
                all_receipts.append(list(pool.map(session_receipt, inputs)))
    elapsed = time.perf_counter_ns() - start
    initializer_durations: dict[tuple[int, int, int], int] = {}
    for call_index, ((call_dir, _), receipts) in enumerate(
        zip(calls, all_receipts, strict=True)
    ):
        _validate_call(cell, call_dir, receipts)
        if cell.initializer:
            pool_key = 0 if strategy == "stdlib_reused" else call_index
            for receipt in receipts:
                initializer_durations[(pool_key, receipt[1], receipt[2])] = receipt[4]
    return elapsed, tuple(
        duration for _, duration in sorted(initializer_durations.items())
    )


def _serialize_stats(stats: StrategyStats) -> dict[str, Any]:
    return {
        "samples_ns": list(stats.samples_ns),
        "median_ms": round(stats.median_ns / 1_000_000.0, 3),
        "mad_ms": round(stats.mad_ns / 1_000_000.0, 3),
        "noise_ratio": round(stats.noise_ratio, 4),
    }


def _metrics_json(metrics: CellMetrics) -> dict[str, Any]:
    output: dict[str, Any] = {
        "valid": metrics.valid,
        "reason": metrics.reason,
        "strategies": {
            strategy: _serialize_stats(stats)
            for strategy, stats in metrics.strategies.items()
        },
        "reuse_speedup_x": None,
        "fresh_minus_reused_ms": None,
        "saved_per_extra_call_ms": None,
        "integration_delta_ms": None,
        "noisy": None,
        "initializer_durations_ns": {
            strategy: [list(sample) for sample in samples]
            for strategy, samples in metrics.initializer_durations_ns.items()
        },
    }
    if set(metrics.strategies) == set(STRATEGIES):
        fresh = metrics.strategies["stdlib_fresh"].median_ns
        reused = metrics.strategies["stdlib_reused"].median_ns
        pyarallel = metrics.strategies["pyarallel_fresh"].median_ns
        output.update(
            {
                "reuse_speedup_x": round(fresh / reused, 3),
                "fresh_minus_reused_ms": round((fresh - reused) / 1_000_000.0, 3),
                "saved_per_extra_call_ms": (
                    None
                    if metrics.calls <= 1
                    else round(_saved_per_extra_call_ms(metrics), 3)
                ),
                "integration_delta_ms": round((pyarallel - fresh) / 1_000_000.0, 3),
                "noisy": any(
                    metrics.strategies[strategy].noise_ratio > NOISE_RATIO_MAX
                    for strategy in ("stdlib_fresh", "stdlib_reused")
                ),
            }
        )
    return output


def _thresholds_json() -> dict[str, Any]:
    return {
        "noise_mad_ratio_max": NOISE_RATIO_MAX,
        "p1_absolute_difference_ms_max": P1_ABSOLUTE_DIFFERENCE_MS_MAX,
        "p1_relative_difference_max": P1_RELATIVE_DIFFERENCE_MAX,
        "t10_saved_per_extra_call_ms_max": T10_SAVED_PER_EXTRA_CALL_MS_MAX,
        "ordinary": {
            "P3": {"speedup_min": 1.25, "saved_per_extra_call_ms_min": 10.0},
            "P10": {"speedup_min": 1.50, "saved_per_extra_call_ms_min": 20.0},
            "P64": {"speedup_min": 1.10, "saved_per_extra_call_ms_min": 5.0},
        },
        "initializer": {
            "PI3": {"speedup_min": 2.0, "saved_per_extra_call_ms_min": 50.0},
            "PI10": {
                "speedup_min": 2.0,
                "saved_per_extra_call_ms_min": 50.0,
            },
        },
        "calibration_ms": {
            "spin": {"target": 5.0, "minimum": 3.0, "maximum": 8.0},
            "initializer": {
                "target": 100.0,
                "minimum": 75.0,
                "maximum": 150.0,
            },
        },
    }


def with_run_temp[T](callback: Callable[[Path], T]) -> T:
    with tempfile.TemporaryDirectory(prefix="pyarallel-session-bench-") as raw:
        return callback(Path(raw))


def run_execution_session(*, quick: bool, diagnostic: bool = False) -> dict[str, Any]:
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    gil_enabled = gil_probe() if gil_probe is not None else True
    include_interpreter = sys.version_info >= (3, 14) and gil_enabled
    cells = list(default_cells(include_interpreter=include_interpreter, quick=quick))
    if diagnostic and not quick:
        cells.extend(diagnostic_cells())
    expected_ids = [cell.id for cell in cells]
    if len(expected_ids) != len(set(expected_ids)):
        raise AssertionError("session benchmark cell ids must be unique")

    spin_n, spin_ns, spin_samples = calibrate(5_000_000, 3_000_000, 8_000_000)
    init_n, init_ns, init_samples = calibrate(100_000_000, 75_000_000, 150_000_000)
    previous_init = os.environ.get("PYARALLEL_BENCH_INIT_N")
    previous_init_dir = os.environ.get("PYARALLEL_BENCH_INIT_DIR")
    os.environ["PYARALLEL_BENCH_INIT_N"] = str(init_n)
    repetitions = 3 if quick else 7

    def execute(root: Path) -> tuple[dict[str, CellMetrics], dict[str, Any]]:
        metrics_by_id: dict[str, CellMetrics] = {}
        cell_json: dict[str, Any] = {}
        for cell in cells:
            samples: dict[str, list[int]] = {strategy: [] for strategy in STRATEGIES}
            initializer_durations: dict[str, list[tuple[int, ...]]] = {
                strategy: [] for strategy in STRATEGIES
            }
            reason: str | None = None
            sample_number = 0
            try:
                for strategy in STRATEGIES:
                    warmup_root = root / cell.id / f"warmup-{strategy}"
                    _run_strategy(strategy, cell, spin_n, warmup_root)
                for repetition in range(repetitions):
                    for strategy in strategy_order(repetition):
                        sample_root = (
                            root / cell.id / f"sample-{sample_number}-{strategy}"
                        )
                        elapsed, durations = _run_strategy(
                            strategy, cell, spin_n, sample_root
                        )
                        samples[strategy].append(elapsed)
                        initializer_durations[strategy].append(durations)
                        sample_number += 1
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"

            stats = {
                strategy: aggregate(values)
                for strategy, values in samples.items()
                if values
            }
            metrics = CellMetrics(
                calls=cell.calls,
                valid=reason is None and set(stats) == set(STRATEGIES),
                strategies=stats,
                reason=reason,
                initializer_durations_ns={
                    strategy: tuple(values)
                    for strategy, values in initializer_durations.items()
                },
            )
            metrics_by_id[cell.id] = metrics
            cell_json[cell.id] = {"cell": asdict(cell), **_metrics_json(metrics)}
        return metrics_by_id, cell_json

    try:
        metrics_by_id, cell_json = with_run_temp(execute)
    finally:
        if previous_init is None:
            os.environ.pop("PYARALLEL_BENCH_INIT_N", None)
        else:
            os.environ["PYARALLEL_BENCH_INIT_N"] = previous_init
        if previous_init_dir is None:
            os.environ.pop("PYARALLEL_BENCH_INIT_DIR", None)
        else:
            os.environ["PYARALLEL_BENCH_INIT_DIR"] = previous_init_dir

    verdict = "smoke-only" if quick else classify(metrics_by_id)
    return {
        "config": {
            "quick": quick,
            "diagnostic": diagnostic,
            "repetitions": repetitions,
            "cell_ids": expected_ids,
            "strategies": list(STRATEGIES),
            "timed_strategy_samples": len(cells) * len(STRATEGIES) * repetitions,
            "warmup_strategy_samples": len(cells) * len(STRATEGIES),
        },
        "calibration": {
            "spin_n": spin_n,
            "spin_ms": round(spin_ns / 1_000_000.0, 3),
            "spin_samples_ns": list(spin_samples),
            "initializer_n": init_n,
            "initializer_ms": round(init_ns / 1_000_000.0, 3),
            "initializer_samples_ns": list(init_samples),
        },
        "thresholds": _thresholds_json(),
        "cells": cell_json,
        "verdict": verdict,
    }
