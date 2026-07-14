"""Deterministic contracts for the reusable-session benchmark harness."""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import pytest

from benchmarks._session_bench import (
    STRATEGIES,
    Cell,
    CellMetrics,
    StrategyStats,
    _metrics_json,
    _run_strategy,
    _thresholds_json,
    _validate_call,
    aggregate,
    classify,
    combine_verdicts,
    default_cells,
    strategy_order,
    with_run_temp,
)
from benchmarks._workloads import session_initializer, session_receipt


def _stats(milliseconds: float, *, noise: float = 0.0) -> StrategyStats:
    nanoseconds = milliseconds * 1_000_000.0
    return StrategyStats(
        samples_ns=(int(nanoseconds),) * 7,
        median_ns=nanoseconds,
        mad_ns=nanoseconds * noise,
        noise_ratio=noise,
    )


def _metrics(
    calls: int,
    fresh_ms: float,
    reused_ms: float,
    *,
    pyarallel_ms: float | None = None,
    noise: float = 0.0,
    valid: bool = True,
) -> CellMetrics:
    return CellMetrics(
        calls=calls,
        valid=valid,
        strategies={
            "pyarallel_fresh": _stats(
                fresh_ms if pyarallel_ms is None else pyarallel_ms
            ),
            "stdlib_fresh": _stats(fresh_ms, noise=noise),
            "stdlib_reused": _stats(reused_ms, noise=noise),
        },
    )


def _passing_cells() -> dict[str, CellMetrics]:
    return {
        "P1": _metrics(1, 100, 100),
        "P3": _metrics(3, 300, 200),
        "P10": _metrics(10, 1_000, 500),
        "P64": _metrics(10, 1_000, 800),
        "T10": _metrics(10, 100, 100),
        "PI3": _metrics(3, 900, 300),
        "PI10": _metrics(10, 3_000, 600),
    }


def test_json_metrics_separate_reuse_and_integration_costs() -> None:
    output = _metrics_json(_metrics(3, 300, 200, pyarallel_ms=330))

    assert output["reuse_speedup_x"] == 1.5
    assert output["fresh_minus_reused_ms"] == 100.0
    assert output["saved_per_extra_call_ms"] == 50.0
    assert output["integration_delta_ms"] == 30.0
    assert output["noisy"] is False


def test_exact_default_and_quick_cells() -> None:
    assert [
        cell.id for cell in default_cells(include_interpreter=False, quick=False)
    ] == [
        "P1",
        "P3",
        "P10",
        "P64",
        "T10",
        "PI3",
        "PI10",
    ]
    assert [
        cell.id for cell in default_cells(include_interpreter=False, quick=True)
    ] == [
        "P1",
        "P3",
        "T10",
    ]
    assert [
        cell.id for cell in default_cells(include_interpreter=True, quick=False)[7:]
    ] == ["IP1", "IP3", "IP10", "IP64", "IPI3", "IPI10"]


def test_balanced_strategy_order_is_pinned() -> None:
    assert [strategy_order(index) for index in range(7)] == [
        ("pyarallel_fresh", "stdlib_fresh", "stdlib_reused"),
        ("stdlib_fresh", "stdlib_reused", "pyarallel_fresh"),
        ("stdlib_reused", "pyarallel_fresh", "stdlib_fresh"),
        ("stdlib_reused", "stdlib_fresh", "pyarallel_fresh"),
        ("pyarallel_fresh", "stdlib_reused", "stdlib_fresh"),
        ("stdlib_fresh", "pyarallel_fresh", "stdlib_reused"),
        ("pyarallel_fresh", "stdlib_fresh", "stdlib_reused"),
    ]


def test_aggregate_reports_median_mad_and_raw_samples() -> None:
    result = aggregate([100, 110, 120, 130, 1_000])

    assert result.samples_ns == (100, 110, 120, 130, 1_000)
    assert result.median_ns == 120
    assert result.mad_ns == 10
    assert result.noise_ratio == pytest.approx(10 / 120)


def test_classifier_advances_only_when_every_ordinary_gate_passes() -> None:
    assert classify(_passing_cells()) == "advance-to-design"

    cells = _passing_cells()
    cells["P10"] = _metrics(10, 749, 500)
    assert classify(cells) == "initializer-specific"

    cells["PI3"] = _metrics(3, 599, 300)
    assert classify(cells) == "defer"


def test_classifier_accepts_exact_threshold_boundaries() -> None:
    cells = _passing_cells()
    cells["P3"] = _metrics(3, 250, 200)  # 1.25x, 25 ms/extra call
    cells["P10"] = _metrics(10, 750, 500)  # 1.50x, >20 ms/extra call
    cells["P64"] = _metrics(10, 550, 500)  # 1.10x, >5 ms/extra call

    assert classify(cells) == "advance-to-design"


def test_classifier_fails_closed_on_missing_invalid_or_noisy_data() -> None:
    cells = _passing_cells()
    del cells["P10"]
    assert classify(cells) == "invalid-harness"

    cells = _passing_cells()
    cells["P3"] = _metrics(3, 300, 200, valid=False)
    assert classify(cells) == "invalid-harness"

    cells = _passing_cells()
    cells["P3"] = _metrics(3, 300, 200, noise=0.1001)
    assert classify(cells) == "noisy"


def test_classifier_rejects_biased_controls() -> None:
    cells = _passing_cells()
    cells["P1"] = _metrics(1, 100, 80)
    assert classify(cells) == "invalid-harness"

    cells = _passing_cells()
    cells["T10"] = _metrics(10, 200, 100)
    assert classify(cells) == "invalid-harness"

    cells = _passing_cells()
    cells["P1"] = _metrics(1, 100, 80, noise=0.20)
    assert classify(cells) == "invalid-harness"


def test_strategy_set_is_exact() -> None:
    cells = _passing_cells()
    cells["P3"] = CellMetrics(
        calls=3,
        valid=True,
        strategies={strategy: _stats(100) for strategy in STRATEGIES[:-1]},
    )
    assert classify(cells) == "invalid-harness"


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("advance-to-design", "advance-to-design", "advance-to-design"),
        ("initializer-specific", "defer", "defer-unstable"),
        ("noisy", "advance-to-design", "defer-noisy"),
        ("noisy", "noisy", "defer-noisy"),
        ("invalid-harness", "advance-to-design", "invalid-harness"),
    ],
)
def test_two_run_verdict_is_deterministic(
    first: str, second: str, expected: str
) -> None:
    assert combine_verdicts(first, second) == expected


def test_run_temp_cleans_up_after_validation_failure() -> None:
    captured: Path | None = None

    def fail(root: Path) -> None:
        nonlocal captured
        captured = root
        (root / "marker").write_text("stale")
        raise RuntimeError("injected validation failure")

    with pytest.raises(RuntimeError, match="injected"):
        with_run_temp(fail)

    assert captured is not None
    assert not captured.exists()


def test_initializer_receipts_accept_scheduler_stretched_duration(
    tmp_path: Path,
) -> None:
    cell = Cell("PI3", "process", 4, 3, 4, "initialized", True)
    receipts = []
    for value in range(4):
        pid = value + 1
        thread_id = 10
        (tmp_path / f"{pid}-{thread_id}").touch()
        receipts.append((value, pid, thread_id, True, 470_000_000))

    _validate_call(cell, tmp_path, receipts)


def test_initializer_receipts_require_positive_duration(tmp_path: Path) -> None:
    cell = Cell("PI3", "process", 4, 3, 4, "initialized", True)
    receipts = []
    for value in range(4):
        pid = value + 1
        thread_id = 10
        (tmp_path / f"{pid}-{thread_id}").touch()
        receipts.append((value, pid, thread_id, True, 0))

    with pytest.raises(RuntimeError, match="invalid initializer receipt"):
        _validate_call(cell, tmp_path, receipts)


def test_initializer_persists_receipt_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_dir = tmp_path / "initializers"
    rendezvous_dir = tmp_path / "rendezvous"
    init_dir.mkdir()
    rendezvous_dir.mkdir()
    monkeypatch.setenv("PYARALLEL_BENCH_INIT_N", "10")
    monkeypatch.setenv("PYARALLEL_BENCH_INIT_DIR", str(init_dir))

    session_initializer()
    receipt = session_receipt((0, 0, str(rendezvous_dir), True, 1))

    assert receipt[3] is True
    assert receipt[4] > 0
    assert len(tuple(init_dir.iterdir())) == 1


def test_initializer_receipt_fails_without_persisted_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_dir = tmp_path / "initializers"
    rendezvous_dir = tmp_path / "rendezvous"
    init_dir.mkdir()
    rendezvous_dir.mkdir()
    monkeypatch.setenv("PYARALLEL_BENCH_INIT_DIR", str(init_dir))

    with pytest.raises(RuntimeError, match="initializer evidence is missing"):
        session_receipt((0, 0, str(rendezvous_dir), True, 1))


def test_initializer_evidence_is_outside_worker_rendezvous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYARALLEL_BENCH_INIT_N", "10")
    cell = Cell("TI1", "thread", 4, 1, 4, "initialized", True)

    _run_strategy("stdlib_fresh", cell, 0, tmp_path)

    assert len(tuple((tmp_path / "call-0").iterdir())) == 4
    assert len(tuple((tmp_path / "initializers-call-0").iterdir())) == 4


@pytest.mark.parametrize(
    "filename",
    [
        "2026-07-14-reusable-session-cpython312-run1.json",
        "2026-07-14-reusable-session-cpython312-run2.json",
        "2026-07-14-reusable-session-cpython314.json",
    ],
)
def test_recorded_artifact_matches_current_json_contract(filename: str) -> None:
    path = Path(__file__).parents[1] / "benchmarks" / "results" / filename
    session = json.loads(path.read_text())["execution_session"]

    assert session["thresholds"] == _thresholds_json()
    for prefix in ("spin", "initializer"):
        raw = session["calibration"][f"{prefix}_samples_ns"]
        assert len(raw) == 3
        assert (
            round(statistics.median(raw) / 1_000_000.0, 3)
            == session["calibration"][f"{prefix}_ms"]
        )
    metrics_by_id: dict[str, CellMetrics] = {}
    for cell_id, cell in session["cells"].items():
        metrics = CellMetrics(
            calls=cell["cell"]["calls"],
            valid=cell["valid"],
            reason=cell["reason"],
            strategies={
                strategy: aggregate(cell["strategies"][strategy]["samples_ns"])
                for strategy in STRATEGIES
            },
            initializer_durations_ns={
                strategy: tuple(tuple(sample) for sample in samples)
                for strategy, samples in cell["initializer_durations_ns"].items()
            },
        )
        metrics_by_id[cell_id] = metrics
        recorded_metrics = {key: value for key, value in cell.items() if key != "cell"}
        assert recorded_metrics == _metrics_json(metrics)
        if cell["cell"]["initializer"]:
            for strategy, samples in cell["initializer_durations_ns"].items():
                assert len(samples) == 7
                expected_workers = cell["cell"]["workers"] * (
                    1 if strategy == "stdlib_reused" else cell["cell"]["calls"]
                )
                assert all(len(sample) == expected_workers for sample in samples)
                assert all(duration > 0 for sample in samples for duration in sample)
    assert session["verdict"] == classify(metrics_by_id)


def test_benchmark_module_is_importable_without_env_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    from benchmarks import bench

    assert callable(bench.run_all)
    assert os.path.isabs(bench._BENCH_ROOT)
    assert "PYTHONPATH" not in os.environ
    if sys.version_info >= (3, 14):
        with pytest.raises(RuntimeError, match="process startup"):
            bench._require_interpreter_import_path()
    monkeypatch.setenv("PYTHONPATH", bench._BENCH_ROOT)
    bench._require_interpreter_import_path()
