"""Pyarallel — simple, explicit parallel execution for Python."""

from .aio import (
    async_parallel_iter,
    async_parallel_map,
    async_parallel_starmap,
)
from .checkpoint import CheckpointError
from .core import (
    parallel_iter,
    parallel_map,
    parallel_starmap,
)
from .decorators import async_parallel, parallel
from .limiter import Limiter
from .policies import RateLimit, Retry
from .result import ItemResult, ParallelResult

__version__ = "0.4.0"
__all__ = [
    "ParallelResult",
    "ItemResult",
    "RateLimit",
    "Retry",
    "Limiter",
    "CheckpointError",
    "parallel",
    "parallel_iter",
    "parallel_map",
    "parallel_starmap",
    "async_parallel",
    "async_parallel_iter",
    "async_parallel_map",
    "async_parallel_starmap",
]
