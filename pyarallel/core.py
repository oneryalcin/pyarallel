"""
Pyarallel: A Powerful Parallel Execution Library for Python

This module provides a decorator-based approach to parallel execution, supporting both
thread and process-based parallelism with advanced features like rate limiting and batch processing.

Key Features:
- Simple decorator-based API
- Support for both I/O-bound (threading) and CPU-bound (multiprocessing) tasks
- Configurable rate limiting with support for per-second, per-minute, and per-hour rates
- Batch processing for memory efficiency
- Worker prewarming for latency-critical applications
- Automatic executor reuse and cleanup
- Thread-safe implementation

Example Usage:
    ```python
    from pyarallel import parallel
    
    # Basic I/O-bound task
    @parallel(max_workers=4)
    def fetch_url(url: str) -> dict:
        return requests.get(url).json()
    
    # CPU-bound task with rate limiting
    @parallel(
        max_workers=4,
        executor_type="process",
        rate_limit=(100, "minute")
    )
    def process_image(image: bytes) -> bytes:
        return heavy_processing(image)
    
    # Batch processing with prewarming
    @parallel(
        max_workers=4,
        batch_size=10,
        prewarm=True
    )
    def analyze_text(text: str) -> dict:
        return text_analysis(text)
    
    # Use with lists for parallel execution
    urls = ["http://example1.com", "http://example2.com"]
    results = fetch_url(urls)  # Processes URLs in parallel
    
    # Single items work too
    result = fetch_url("http://example.com")  # Returns [result]
    ```

For detailed documentation and examples, see:
https://github.com/oneryalcin/pyarallel
"""

import inspect
import logging
import multiprocessing
import threading
import time
import weakref
from concurrent.futures import (ProcessPoolExecutor, ThreadPoolExecutor,
                                as_completed)
from dataclasses import dataclass
from functools import partial, wraps
from itertools import islice
from typing import (Any, Callable, List, Literal, Optional, Tuple, TypeVar,
                    Union)

from .config_manager import ConfigManager

T = TypeVar("T")
U = TypeVar("U")  # Type variable for the items in the list

TimeUnit = Literal["second", "minute", "hour"]
ExecutorType = Literal["thread", "process"]

# Global executor cache using weak references
_EXECUTOR_CACHE = weakref.WeakValueDictionary()

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """
    Configuration for rate limiting parallel operations.

    Args:
        count: Number of operations allowed per interval
        interval: Time interval for rate limiting ("second", "minute", "hour")

    Example:
        ```python
        # 100 operations per minute
        rate = RateLimit(100, "minute")

        @parallel(rate_limit=rate)
        def my_func(): ...
        ```
    """

    count: float
    interval: TimeUnit = "second"

    @property
    def per_second(self) -> float:
        """Convert rate to operations per second"""
        multiplier = {"second": 1, "minute": 60, "hour": 3600}
        return self.count / multiplier[self.interval]


class TokenBucket:
    """
    Thread-safe token bucket algorithm implementation for rate limiting.

    The token bucket algorithm provides smooth rate limiting with the ability
    to handle bursts up to the bucket capacity. Tokens are added to the bucket
    at a fixed rate, and each operation consumes one token.

    Args:
        rate_limit: RateLimit configuration
        capacity: Maximum number of tokens the bucket can hold. Defaults to
                 the number of operations allowed per interval.
    """

    def __init__(self, rate_limit: RateLimit, capacity: int = None):
        self.rate = rate_limit.per_second
        self.capacity = capacity or rate_limit.count
        self.tokens = self.capacity
        self.last_update = time.time()
        self.lock = threading.Lock()
        self.next_allowed = self.last_update  # Track next allowed operation time

    def get_token(self) -> bool:
        """
        Try to get a token from the bucket.

        Returns:
            bool: True if a token was acquired, False otherwise
        """
        with self.lock:
            now = time.time()
            if now < self.next_allowed:
                return False

            self.next_allowed = max(
                self.next_allowed + (1 / self.rate), now + (1 / self.rate)
            )
            return True

    def wait_for_token(self):
        """Block until a token is available"""
        while True:
            with self.lock:
                now = time.time()
                if now >= self.next_allowed:
                    self.next_allowed = max(
                        self.next_allowed + (1 / self.rate), now + (1 / self.rate)
                    )
                    return
                wait_time = self.next_allowed - now

            time.sleep(max(0.001, wait_time))  # Min sleep 1ms for CPU


def get_executor_class(executor_type: ExecutorType):
    """Get the appropriate executor class based on type"""
    return {"thread": ThreadPoolExecutor, "process": ProcessPoolExecutor}[executor_type]


def get_or_create_executor(
    executor_type: ExecutorType, max_workers: int, prewarm: bool = False
):
    """
    Get a cached executor or create a new one.

    This function manages a global cache of executors, allowing them to be
    reused across multiple calls. The cache uses weak references, so executors
    are automatically cleaned up when no longer needed.

    Args:
        executor_type: Type of executor ("thread" or "process")
        max_workers: Maximum number of workers
        prewarm: If True, starts all workers immediately

    Returns:
        ThreadPoolExecutor or ProcessPoolExecutor
    """
    key = (executor_type, max_workers)
    executor = _EXECUTOR_CACHE.get(key)

    if executor is None or executor._shutdown:
        executor_class = get_executor_class(executor_type)
        executor = executor_class(max_workers=max_workers)
        _EXECUTOR_CACHE[key] = executor

        # Prewarm workers by submitting no-op tasks
        if prewarm:
            futures = [executor.submit(lambda: None) for _ in range(max_workers)]
            for f in futures:
                f.result()  # Wait for workers to start

    return executor


def parallel(
    func: Optional[Callable[..., T]] = None,
    *,
    max_workers: Optional[int] = None,
    executor_type: Optional[str] = None,
    batch_size: Optional[int] = None,
    rate_limit: Optional[float | tuple[float, TimeUnit] | RateLimit] = None,
    prewarm: bool = False,
) -> Callable[..., List[T]]:
    """Decorator for parallel execution of functions over iterables.

    This decorator transforms a function that processes a single item into one
    that can process multiple items in parallel. It supports both thread and
    process-based parallelism, rate limiting, batch processing, and worker
    prewarming.

    The decorated function should take a single item as its first argument.
    When called with a list/tuple, it will process all items in parallel.
    When called with a single item, it will process it normally and return
    a single-item list.

    Args:
        func: The function to be decorated.
        max_workers: Maximum number of parallel workers. Defaults to global config.
        executor_type: Type of parallelism to use. Defaults to global config.
        batch_size: Number of items to process in each batch. Defaults to global config.
        rate_limit: Rate limiting configuration. Defaults to global config.
        prewarm: If True, starts all workers immediately.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., List[T]]:
        # Get global configuration
        config_manager = ConfigManager.get_instance()
        config = config_manager.get_config()

        # Initialize execution config if it's None
        if config.execution is None:
            config_manager.update_config(
                {
                    "execution": {
                        "default_max_workers": 4,
                        "default_executor_type": "thread",
                        "default_batch_size": 10,
                    }
                }
            )
            config = config_manager.get_config()

        # Use global defaults if not explicitly provided
        workers = (
            max_workers
            if max_workers is not None
            else config.execution.default_max_workers
        )
        batch = (
            batch_size
            if batch_size is not None
            else config.execution.default_batch_size
        )
        exec_type = (
            executor_type
            if executor_type is not None
            else config.execution.default_executor_type
        )

        # Runtime configuration warnings
        if workers and workers > 100:  # Arbitrary threshold for demonstration
            import warnings

            warnings.warn(
                f"high number of workers ({workers}) specified - this may impact system performance",
                RuntimeWarning,
            )

        if exec_type == "process" and (batch_size is not None and batch_size < 2):
            import warnings

            warnings.warn(
                "inefficient configuration: Using process pool with very small batch size. Consider increasing batch size or using thread pool.",
                RuntimeWarning,
            )

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> List[T]:
            logger.debug(f"---> Entering @parallel wrapper for {fn.__name__}")
            logger.debug(f"Wrapper called with args: {args!r}, kwargs: {kwargs!r}")

            # --- Robust detection of instance/class/static method ---
            items_arg_index = 0
            if args:
                qualname = fn.__qualname__
                if "." in qualname:
                    cls_name = qualname.split(".")[0]
                    # Instance method: first argument is an instance of the class
                    if (
                        hasattr(args[0], "__class__")
                        and args[0].__class__.__name__ == cls_name
                    ):
                        items_arg_index = 1
                        logger.debug(
                            f"Detected instance method. fn={fn!r}, self={args[0]!r}"
                        )
                    # Class method: first argument is the class itself
                    elif isinstance(args[0], type) and args[0].__name__ == cls_name:
                        items_arg_index = 1
                        logger.debug(
                            f"Detected class method. fn={fn!r}, cls={args[0]!r}"
                        )
                    else:
                        logger.debug(f"Detected static method or function. fn={fn!r}")
                else:
                    logger.debug(f"Detected function (no class context). fn={fn!r}")
            else:
                logger.debug(f"No args provided to wrapper.")

            # --- Check if the relevant argument is an iterable ---
            if len(args) <= items_arg_index or not isinstance(
                args[items_arg_index], (list, tuple)
            ):
                if items_arg_index:
                    # For bound methods, include self/cls in the call
                    single_item_args: Tuple[Any, ...] = (args[0],) + args[
                        items_arg_index:
                    ]
                else:
                    single_item_args = args[items_arg_index:]
                logger.debug(
                    f"Single item path. Calling fn({single_item_args=}, {kwargs=})"
                )
                # Ensure return is always a list, even for single item path
                result: T = fn(*single_item_args, **kwargs)
                return [result]

            # --- List Processing ---
            items: Union[List[U], Tuple[U, ...]] = args[items_arg_index]
            other_args: Tuple[Any, ...] = args[items_arg_index + 1 :]
            bound_arg: Optional[Any] = args[0] if items_arg_index else None
            logger.debug(f"List processing path. {items=}, {other_args=}")

            results: List[T] = [None] * len(items)  # Preallocate results list
            futures_map = {}

            # Get or create cached executor
            executor = get_or_create_executor(exec_type, workers, prewarm)

            # Initialize rate limiter if specified
            rate_limiter = None
            if rate_limit is not None:
                if isinstance(rate_limit, (tuple, list)):
                    rate_limiter = TokenBucket(RateLimit(rate_limit[0], rate_limit[1]))
                elif isinstance(rate_limit, (int, float)):
                    rate_limiter = TokenBucket(RateLimit(rate_limit))
                elif isinstance(rate_limit, RateLimit):
                    rate_limiter = TokenBucket(rate_limit)

            # Create futures with their original indices
            for i, item in enumerate(items):
                if rate_limiter:
                    rate_limiter.wait_for_token()

                # Create task with correct binding
                if items_arg_index == 1:  # Instance or class method
                    # args[0] is self/cls
                    task = partial(fn, bound_arg, item, *other_args, **kwargs)
                else:  # Static method or function
                    task = partial(fn, item, *other_args, **kwargs)

                future = executor.submit(task)
                futures_map[future] = i
                logger.debug(f"LOOP {i}: Submitted task to executor.")

            # Wait for completion and maintain order
            logger.debug("Waiting for tasks to complete...")
            first_exception = None
            for future in as_completed(futures_map):
                original_index = futures_map[future]
                try:
                    result: T = future.result()
                    results[original_index] = result
                    logger.debug(
                        f"Task {original_index} completed with result: {result!r}"
                    )
                except Exception as e:
                    logger.error(f"Task {original_index} failed: {e}", exc_info=True)
                    if first_exception is None:
                        first_exception = e
                    results[original_index] = e  # Or handle error differently

            if first_exception is not None:
                raise first_exception

            logger.debug(
                f"<--- Exiting @parallel wrapper for {fn.__name__}. Results: {results!r}"
            )
            return results

        # Store configuration as attributes on the wrapper function
        wrapper.max_workers = workers
        wrapper.executor_type = exec_type
        wrapper.batch_size = batch

        return wrapper

    if func is None:
        # Called as @parallel(...)
        return decorator
    else:
        # Called as @parallel directly on the function
        return decorator(func)
