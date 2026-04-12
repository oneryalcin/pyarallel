#!/usr/bin/env python3
"""Smoke test — validates pyarallel with real I/O and real concurrency.

Run with: uv run smoke_test.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time

# Ensure we're importing the local version
sys.path.insert(0, os.path.dirname(__file__))

from pyarallel import (
    ParallelResult,
    RateLimit,
    Retry,
    async_parallel,
    async_parallel_map,
    parallel,
    parallel_map,
)

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")


# ---------------------------------------------------------------------------
# 1. Real file I/O — parallel reads/writes
# ---------------------------------------------------------------------------
print("\n--- Sync: File I/O ---")

with tempfile.TemporaryDirectory() as tmpdir:
    # Write files
    paths = []
    for i in range(20):
        p = os.path.join(tmpdir, f"file_{i}.txt")
        with open(p, "w") as f:
            f.write(f"content-{i}")
        paths.append(p)

    # Parallel read
    def read_file(path):
        with open(path) as f:
            return f.read()

    result = parallel_map(read_file, paths, workers=4)
    check("file read results", result.ok)
    check("file read order", list(result) == [f"content-{i}" for i in range(20)])

# ---------------------------------------------------------------------------
# 2. Batch processing — verify memory control
# ---------------------------------------------------------------------------
print("\n--- Sync: Batch processing ---")

import threading

peak_concurrent = 0
current_concurrent = 0
lock = threading.Lock()


def tracked_work(x):
    global peak_concurrent, current_concurrent
    with lock:
        current_concurrent += 1
        peak_concurrent = max(peak_concurrent, current_concurrent)
    time.sleep(0.02)
    with lock:
        current_concurrent -= 1
    return x * 2


result = parallel_map(tracked_work, range(50), workers=4, batch_size=10)
check("batch correctness", list(result) == [x * 2 for x in range(50)])
check(f"batch memory control (peak={peak_concurrent})", peak_concurrent <= 10,
      f"expected <=10, got {peak_concurrent}")

# ---------------------------------------------------------------------------
# 3. Retry with flaky function
# ---------------------------------------------------------------------------
print("\n--- Sync: Retry ---")

attempt_counts = {}


def flaky_compute(x):
    attempt_counts[x] = attempt_counts.get(x, 0) + 1
    if attempt_counts[x] < 3:
        raise ValueError(f"not ready yet: {x}")
    return x * 100


result = parallel_map(flaky_compute, [1, 2, 3, 4, 5], workers=3, retry=Retry(attempts=3))
check("retry all succeed", result.ok)
check("retry correct values", list(result) == [100, 200, 300, 400, 500])
check("retry attempt counts", all(v == 3 for v in attempt_counts.values()),
      f"counts: {attempt_counts}")

# ---------------------------------------------------------------------------
# 4. Rate limiting — timing check
# ---------------------------------------------------------------------------
print("\n--- Sync: Rate limiting ---")

start = time.monotonic()
parallel_map(lambda x: x, range(10), workers=10, rate_limit=RateLimit(20, "second"))
elapsed = time.monotonic() - start
check(f"rate limit timing ({elapsed:.2f}s)", elapsed >= 0.35,
      f"expected >= 0.35s for 10 items at 20/sec")

# ---------------------------------------------------------------------------
# 5. Error handling — partial results
# ---------------------------------------------------------------------------
print("\n--- Sync: Error handling ---")


def maybe_fail(x):
    if x % 3 == 0:
        raise RuntimeError(f"divisible by 3: {x}")
    return x


result = parallel_map(maybe_fail, range(12), workers=4)
check("partial: not ok", not result.ok)
check("partial: 8 successes", len(result.successes()) == 8)
check("partial: 4 failures", len(result.failures()) == 4,
      f"got {len(result.failures())} failures")

try:
    result.raise_on_failure()
    check("raise_on_failure raises", False, "should have raised")
except ExceptionGroup as eg:
    check("ExceptionGroup has 4 errors", len(eg.exceptions) == 4)

# ---------------------------------------------------------------------------
# 6. Decorator — normal call + .map()
# ---------------------------------------------------------------------------
print("\n--- Sync: Decorator ---")


@parallel(workers=3)
def square(x):
    return x ** 2


check("decorator single call", square(7) == 49)
result = square.map(range(5))
check("decorator .map()", list(result) == [0, 1, 4, 9, 16])

# ---------------------------------------------------------------------------
# 7. Instance method
# ---------------------------------------------------------------------------
print("\n--- Sync: Instance method ---")


class Multiplier:
    def __init__(self, factor):
        self.factor = factor

    @parallel(workers=2)
    def multiply(self, x):
        return x * self.factor


m = Multiplier(7)
check("method single call", m.multiply(3) == 21)
check("method .map()", list(m.multiply.map([1, 2, 3])) == [7, 14, 21])

# ---------------------------------------------------------------------------
# 8. Progress callback
# ---------------------------------------------------------------------------
print("\n--- Sync: Progress ---")

progress_log = []
parallel_map(lambda x: x, range(10), workers=3,
             on_progress=lambda d, t: progress_log.append((d, t)))
check("progress called 10 times", len(progress_log) == 10)
check("progress total correct", all(t == 10 for _, t in progress_log))

# ---------------------------------------------------------------------------
# 9. Async — basic + retry + batch
# ---------------------------------------------------------------------------
print("\n--- Async ---")


async def run_async_tests():
    # Basic
    async def async_double(x):
        await asyncio.sleep(0.01)
        return x * 2

    result = await async_parallel_map(async_double, range(10), concurrency=5)
    check("async basic", list(result) == [x * 2 for x in range(10)])

    # Retry
    async_attempts = {}

    async def async_flaky(x):
        async_attempts[x] = async_attempts.get(x, 0) + 1
        if async_attempts[x] < 2:
            raise ValueError("not yet")
        return x

    result = await async_parallel_map(
        async_flaky, [10, 20, 30], concurrency=3, retry=Retry(attempts=3)
    )
    check("async retry", result.ok and list(result) == [10, 20, 30])

    # Batch
    result = await async_parallel_map(
        async_double, range(20), concurrency=3, batch_size=5
    )
    check("async batch", list(result) == [x * 2 for x in range(20)])

    # Decorator
    @async_parallel(concurrency=3)
    async def afetch(x):
        return f"data-{x}"

    check("async decorator single", await afetch("a") == "data-a")
    result = await afetch.map(["a", "b", "c"])
    check("async decorator .map()", list(result) == ["data-a", "data-b", "data-c"])


asyncio.run(run_async_tests())

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed")
print(f"{'='*50}")
sys.exit(1 if FAIL > 0 else 0)
