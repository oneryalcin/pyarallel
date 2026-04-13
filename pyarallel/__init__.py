"""Pyarallel — simple, explicit parallel execution for Python."""

from .aio import (async_parallel, async_parallel_iter, async_parallel_map,
                  async_parallel_starmap)
from .core import (ParallelResult, RateLimit, Retry, parallel, parallel_iter,
                   parallel_map, parallel_starmap)

__version__ = "0.3.0"
__all__ = [
    "ParallelResult",
    "RateLimit",
    "Retry",
    "parallel",
    "parallel_iter",
    "parallel_map",
    "parallel_starmap",
    "async_parallel",
    "async_parallel_iter",
    "async_parallel_map",
    "async_parallel_starmap",
]
