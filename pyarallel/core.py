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

from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from functools import wraps
from typing import Any, Callable, TypeVar, Literal
from itertools import islice
import time
import threading
from dataclasses import dataclass
import multiprocessing
import weakref

T = TypeVar('T')

TimeUnit = Literal["second", "minute", "hour"]
ExecutorType = Literal["thread", "process"]

# Global executor cache using weak references
_EXECUTOR_CACHE = weakref.WeakValueDictionary()

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
        multiplier = {
            "second": 1,
            "minute": 60,
            "hour": 3600
        }
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
            
            self.next_allowed = max(self.next_allowed + (1 / self.rate), now + (1 / self.rate))
            return True
    
    def wait_for_token(self):
        """Block until a token is available"""
        while True:
            with self.lock:
                now = time.time()
                if now >= self.next_allowed:
                    self.next_allowed = max(self.next_allowed + (1 / self.rate), now + (1 / self.rate))
                    return
                wait_time = self.next_allowed - now
            
            time.sleep(max(0.001, wait_time))  # Min sleep 1ms for CPU

def get_executor_class(executor_type: ExecutorType):
    """Get the appropriate executor class based on type"""
    return {
        "thread": ThreadPoolExecutor,
        "process": ProcessPoolExecutor
    }[executor_type]

def get_or_create_executor(executor_type: ExecutorType, max_workers: int, prewarm: bool = False):
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
    max_workers: int = None, 
    batch_size: int = None, 
    rate_limit: float | tuple[float, TimeUnit] | RateLimit = None,
    executor_type: ExecutorType = "thread",
    prewarm: bool = False
):
    """
    Decorator for parallel execution of functions over iterables.
    
    This decorator transforms a function that processes a single item into one
    that can process multiple items in parallel. It supports both thread and
    process-based parallelism, rate limiting, batch processing, and worker
    prewarming.
    
    The decorated function should take a single item as its first argument.
    When called with a list/tuple, it will process all items in parallel.
    When called with a single item, it will process it normally and return
    a single-item list.
    
    Args:
        max_workers: Maximum number of parallel workers. Defaults to:
                    - Processes: CPU count
                    - Threads: CPU count * 5
        
        batch_size: Number of items to process in each batch. Useful for
                   controlling memory usage with large iterables. None means
                   process all items at once.
        
        rate_limit: Rate limiting configuration, specified as:
                   - float: Operations per second
                   - tuple[float, TimeUnit]: (count, interval) e.g. (100, "minute")
                   - RateLimit: RateLimit instance
        
        executor_type: Type of parallelism to use:
                      - "thread": For I/O-bound tasks (default)
                      - "process": For CPU-bound tasks
        
        prewarm: If True, starts all workers immediately. Useful for
                latency-critical applications where cold start time matters.
    
    Returns:
        Callable: Wrapped function that processes items in parallel
    
    Examples:
        ```python
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
        
        # Batch processing
        @parallel(max_workers=4, batch_size=10)
        def analyze_text(text: str) -> dict:
            return text_analysis(text)
        
        # Usage
        urls = ["http://example1.com", "http://example2.com"]
        results = fetch_url(urls)  # Parallel processing
        
        single_result = fetch_url("http://example.com")  # Returns [result]
        ```
    
    Notes:
        - The function preserves the original function's docstring and signature
        - Rate limiting is thread-safe
        - Executors are reused and automatically cleaned up
        - Process pools handle rate limiting in the main process
        - Batch processing helps with memory usage
        - Prewarming is useful for latency-critical applications
    """
    # Calculate workers here so it's consistent
    workers = max_workers or (
        multiprocessing.cpu_count() 
        if executor_type == "process" 
        else (multiprocessing.cpu_count() * 5)
    )
    
    # Prewarm if requested
    if prewarm:
        get_or_create_executor(executor_type, workers, prewarm=True)
    
    def decorator(func: Callable[..., T]) -> Callable[..., list[T]]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> list[T]:
            if not args or not isinstance(args[0], (list, tuple)):
                return [func(*args, **kwargs)]
            
            items = args[0]
            other_args = args[1:]
            results = []
            
            # Convert rate_limit to RateLimit object
            rate = None
            if rate_limit is not None:
                if isinstance(rate_limit, (int, float)):
                    rate = RateLimit(float(rate_limit))
                elif isinstance(rate_limit, tuple):
                    rate = RateLimit(*rate_limit)
                else:
                    rate = rate_limit
            
            # For process pool, rate limiting happens in the main process
            bucket = TokenBucket(rate) if rate and executor_type == "thread" else None
            
            def rate_limited_func(item):
                if bucket:
                    bucket.wait_for_token()
                return func(item, *other_args, **kwargs)
            
            # Get or create cached executor
            executor = get_or_create_executor(executor_type, workers)
            
            try:
                if batch_size is None:
                    # Rate limit in main process for ProcessPoolExecutor
                    if rate and executor_type == "process":
                        bucket = TokenBucket(rate)
                        for item in items:
                            bucket.wait_for_token()
                    
                    futures = [
                        executor.submit(rate_limited_func if executor_type == "thread" else func, item, *other_args, **kwargs)
                        for item in items
                    ]
                    results.extend(f.result() for f in as_completed(futures))
                else:
                    # Process in batches
                    it = iter(items)
                    while batch := list(islice(it, batch_size)):
                        # Rate limit in main process for ProcessPoolExecutor
                        if rate and executor_type == "process":
                            bucket = TokenBucket(rate)
                            for _ in batch:
                                bucket.wait_for_token()
                                
                        futures = [
                            executor.submit(rate_limited_func if executor_type == "thread" else func, item, *other_args, **kwargs)
                            for item in batch
                        ]
                        results.extend(f.result() for f in as_completed(futures))
            except:
                # Don't shutdown cached executor on error
                raise
                
            return results
                
        return wrapper
    return decorator