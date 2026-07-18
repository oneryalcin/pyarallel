"""CodSpeed memory-scaling probes for streaming and collected results."""

from typing import Any

import pytest

from pyarallel import parallel_iter, parallel_map

SIZES = (1_000, 10_000)


def _identity(item: int) -> int:
    return item


def _drain_stream(size: int) -> int:
    """Consume results without retaining them in the benchmark driver."""
    count = 0
    for _ in parallel_iter(
        _identity,
        range(size),
        workers=4,
        window_size=64,
    ):
        count += 1
    return count


def _collect_map(size: int) -> Any:
    """Collect every result to provide the expected O(n) comparison."""
    return parallel_map(
        _identity,
        range(size),
        workers=4,
        window_size=64,
    )


@pytest.mark.parametrize("size", SIZES, ids=("1k", "10k"))
def test_streaming_memory(benchmark: Any, size: int) -> None:
    """Track whether streaming peak memory stays bounded by the window."""
    assert benchmark(_drain_stream, size) == size


@pytest.mark.parametrize("size", SIZES, ids=("1k", "10k"))
def test_collected_memory(benchmark: Any, size: int) -> None:
    """Track the expected O(n) memory growth when results are retained."""
    result = benchmark(_collect_map, size)
    assert result.values() == list(range(size))
