"""Pyarallel — simple, explicit parallel execution for Python."""

from .core import ParallelResult, RateLimit, Retry, parallel, parallel_map
from .aio import async_parallel, async_parallel_map

__version__ = "0.2.0"
__all__ = [
    "ParallelResult",
    "RateLimit",
    "Retry",
    "parallel",
    "parallel_map",
    "async_parallel",
    "async_parallel_map",
]
