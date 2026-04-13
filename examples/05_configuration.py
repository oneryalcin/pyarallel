#!/usr/bin/env python3
"""
Execution Configuration
=======================

This example demonstrates the configuration knobs that exist today:
- decorator defaults
- per-call overrides
- retry and rate limiting
- timeout behavior

Run with: python examples/05_configuration.py
"""

import time

from pyarallel import RateLimit, Retry, parallel, parallel_map


@parallel(workers=4, rate_limit=RateLimit(20, "second"))
def process_item(item):
    time.sleep(0.05)
    return item * 2


def flaky(item):
    if item == 2:
        raise ValueError("temporary failure")
    return item * 10


def slow(item):
    time.sleep(0.3)
    return item


print("Example 1: Decorator defaults")
print("=" * 50)

results = process_item.map(range(6))
print(f"Results with decorator defaults: {list(results)}")
print()


print("Example 2: Per-call overrides")
print("=" * 50)

start = time.time()
results = process_item.map(range(8), workers=2, batch_size=4)
duration = time.time() - start

print(f"Results with workers=2 and batch_size=4: {list(results)}")
print(f"Elapsed: {duration:.2f} seconds")
print()


print("Example 3: Direct function API")
print("=" * 50)

results = parallel_map(
    lambda x: x + 1,
    [10, 20, 30],
    workers=3,
    rate_limit=RateLimit(30, "second"),
)
print(f"parallel_map results: {list(results)}")
print()


print("Example 4: Retry")
print("=" * 50)

retry_result = parallel_map(
    flaky,
    [1, 2, 3],
    workers=3,
    retry=Retry(attempts=2, backoff=0, jitter=False),
)

print(f"Successes: {retry_result.successes()}")
print(
    "Failures:",
    [(index, type(exc).__name__, str(exc)) for index, exc in retry_result.failures()],
)
print()


print("Example 5: Total timeout")
print("=" * 50)

timeout_result = parallel_map(slow, [1, 2, 3], workers=3, timeout=0.1)
print(
    "Timeout failures:",
    [(index, type(exc).__name__) for index, exc in timeout_result.failures()],
)
print()


print("Summary")
print("=" * 50)
print("1. Put reusable defaults on the decorator.")
print("2. Override workers, batch_size, and timeout on each .map(...) call.")
print("3. Use Retry(...) for transient failures.")
print("4. Use RateLimit(...) to control request rate.")
