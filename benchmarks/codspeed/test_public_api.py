"""CodSpeed pilot benchmarks for deterministic in-process API paths.

These intentionally cover only the sequential and thread executors. The
existing benchmark lab remains authoritative for process, interpreter,
free-threaded scaling, and cold-worker startup measurements.
"""

from collections.abc import Callable
from typing import Any

import pytest

from pyarallel import parallel_iter, parallel_map

ITEMS = tuple(range(1_000))


def _identity(item: int) -> int:
    return item


def _consume_stream(*, ordered: bool) -> list[Any]:
    return list(
        parallel_iter(
            _identity,
            ITEMS,
            workers=4,
            ordered=ordered,
            window_size=64,
        )
    )


@pytest.mark.parametrize(
    ("name", "operation"),
    [
        pytest.param(
            "parallel-map-sequential",
            lambda: parallel_map(_identity, ITEMS, sequential=True),
            id="parallel-map-sequential",
        ),
        pytest.param(
            "parallel-map-thread",
            lambda: parallel_map(_identity, ITEMS, workers=4, window_size=64),
            id="parallel-map-thread",
        ),
        pytest.param(
            "parallel-iter-unordered",
            lambda: _consume_stream(ordered=False),
            id="parallel-iter-unordered",
        ),
        pytest.param(
            "parallel-iter-ordered",
            lambda: _consume_stream(ordered=True),
            id="parallel-iter-ordered",
        ),
    ],
)
def test_public_api_overhead(
    benchmark: Any,
    name: str,
    operation: Callable[[], Any],
) -> None:
    """Track public driver overhead without process or network variability."""
    result = benchmark(operation)

    if name.startswith("parallel-map"):
        assert result.values() == list(ITEMS)
    else:
        assert len(result) == len(ITEMS)
        assert sorted(item.value for item in result) == list(ITEMS)
