#!/usr/bin/env python3
"""
CPU-Bound Processing
====================

This example compares thread and process execution for CPU-heavy work.

Run with: python examples/03_cpu_bound_processing.py
"""

import hashlib
import time

from pyarallel import parallel


def compute_intensive_task(n):
    """Small but real CPU-heavy task."""
    result = ""
    for i in range(n):
        result += str(i)

    for _ in range(250):
        result = hashlib.sha256(result.encode()).hexdigest()

    return result[:16]


def process_image_data(image_id):
    data = bytearray(range(128)) * 40
    for _ in range(1000):
        data = bytearray([b ^ 0xFF for b in data])
        data = bytearray([b >> 1 for b in data])
    return f"image_{image_id}_processed.jpg"


def analyze_dataset(dataset_id):
    data = [i * 1.5 for i in range(15000)]
    total = sum(data)
    mean = total / len(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    return {
        "dataset_id": dataset_id,
        "count": len(data),
        "mean": round(mean, 2),
        "std_dev": round(variance**0.5, 2),
    }


def is_prime(n):
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False

    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


@parallel(workers=4, executor="thread")
def compute_with_threads(n):
    return compute_intensive_task(n)


@parallel(workers=4, executor="process")
def compute_with_processes(n):
    return compute_intensive_task(n)


@parallel(workers=4, executor="process")
def process_images(image_id):
    return process_image_data(image_id)


@parallel(workers=4, executor="process")
def analyze_datasets(dataset_id):
    return analyze_dataset(dataset_id)


@parallel(workers=4, executor="process")
def check_prime(n):
    return (n, is_prime(n))


if __name__ == "__main__":
    print("Example 1: Threads vs Processes for CPU-bound tasks")
    print("=" * 50)

    numbers = [3000, 3000, 3000, 3000]

    start = time.time()
    thread_results = compute_with_threads.map(numbers)
    thread_time = time.time() - start

    start = time.time()
    process_results = compute_with_processes.map(numbers)
    process_time = time.time() - start

    print(f"Thread-based execution: {thread_time:.2f} seconds")
    print(f"Process-based execution: {process_time:.2f} seconds")
    print(f"Thread sample: {list(thread_results)[0]}")
    print(f"Process sample: {list(process_results)[0]}")
    print()

    print("Example 2: Batch image processing")
    print("=" * 50)

    start = time.time()
    image_results = process_images.map(range(6))
    duration = time.time() - start

    print(f"Processed {len(image_results)} images in {duration:.2f} seconds")
    print(f"Results: {list(image_results)[:3]}...")
    print()

    print("Example 3: Parallel data analysis")
    print("=" * 50)

    start = time.time()
    analyses = analyze_datasets.map(range(4))
    duration = time.time() - start

    print(f"Analyzed {len(analyses)} datasets in {duration:.2f} seconds")
    print(f"Sample result: {list(analyses)[0]}")
    print()

    print("Example 4: Prime number checking")
    print("=" * 50)

    large_numbers = [1000003, 1000033, 1000037, 1000039]
    start = time.time()
    results = check_prime.map(large_numbers)
    duration = time.time() - start

    print(f"Checked {len(results)} numbers in {duration:.2f} seconds")
    for number, prime in results:
        print(f"  {number}: {'PRIME' if prime else 'NOT PRIME'}")
    print()

    print("Summary")
    print("=" * 50)
    print("1. Use executor='process' for CPU-heavy work.")
    print("2. Keep process functions at module scope so they stay picklable.")
    print("3. Decorated .map(...) now works for both thread and process executors.")
