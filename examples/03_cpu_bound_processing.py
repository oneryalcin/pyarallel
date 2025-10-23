#!/usr/bin/env python3
"""
CPU-Bound Processing
====================

This example demonstrates using pyarallel for CPU-intensive tasks
using process-based parallelism to bypass Python's Global Interpreter Lock (GIL).

Run with: python examples/03_cpu_bound_processing.py
"""

from pyarallel import parallel
import time
import hashlib


# Example 1: Comparison - Threads vs Processes
# ---------------------------------------------
def compute_intensive_task(n):
    """CPU-intensive task: calculate hash of concatenated numbers."""
    result = ""
    for i in range(n):
        result += str(i)

    # Hash it multiple times to make it CPU-intensive
    for _ in range(1000):
        result = hashlib.sha256(result.encode()).hexdigest()

    return result[:16]  # Return first 16 chars


# Using THREADS (not ideal for CPU-bound tasks due to GIL)
@parallel(max_workers=4, executor_type="thread")
def compute_with_threads(n):
    """CPU task with threads - limited by GIL."""
    return compute_intensive_task(n)


# Using PROCESSES (ideal for CPU-bound tasks)
@parallel(max_workers=4, executor_type="process")
def compute_with_processes(n):
    """CPU task with processes - bypasses GIL."""
    return compute_intensive_task(n)


print("Example 1: Threads vs Processes for CPU-bound tasks")
print("=" * 50)

numbers = [5000, 5000, 5000, 5000]

# Test with threads
start = time.time()
results_threads = compute_with_threads(numbers)
thread_time = time.time() - start

# Test with processes
start = time.time()
results_processes = compute_with_processes(numbers)
process_time = time.time() - start

print(f"Thread-based execution: {thread_time:.2f} seconds")
print(f"Process-based execution: {process_time:.2f} seconds")
print(f"Speedup with processes: {thread_time / process_time:.2f}x faster")
print()


# Example 2: Image processing simulation
# ---------------------------------------
def process_image_data(image_id):
    """Simulate CPU-intensive image processing."""
    # Simulate image transformations (resize, filter, etc.)
    data = bytearray(range(256)) * 100  # Simulate image data

    # Simulate complex transformations
    for _ in range(10000):
        # Simulate filters/transformations
        data = bytearray([b ^ 0xFF for b in data])
        data = bytearray([b >> 1 for b in data])

    return f"image_{image_id}_processed.jpg"


@parallel(max_workers=4, executor_type="process")
def process_images(image_id):
    """Process image using multiple CPU cores."""
    return process_image_data(image_id)


print("Example 2: Batch image processing")
print("=" * 50)

start = time.time()
image_ids = list(range(12))
processed_images = process_images(image_ids)
duration = time.time() - start

print(f"Processed {len(processed_images)} images in {duration:.2f} seconds")
print(f"Throughput: {len(processed_images) / duration:.1f} images/second")
print(f"Results: {processed_images[:3]}...")
print()


# Example 3: Data analysis / number crunching
# --------------------------------------------
def analyze_dataset(dataset_id):
    """Simulate data analysis on a dataset."""
    # Generate some "data"
    data = [i * 1.5 for i in range(50000)]

    # Perform calculations
    total = sum(data)
    mean = total / len(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    std_dev = variance ** 0.5

    return {
        "dataset_id": dataset_id,
        "count": len(data),
        "mean": round(mean, 2),
        "std_dev": round(std_dev, 2)
    }


@parallel(max_workers=4, executor_type="process")
def analyze_datasets(dataset_id):
    """Analyze multiple datasets in parallel."""
    return analyze_dataset(dataset_id)


print("Example 3: Parallel data analysis")
print("=" * 50)

start = time.time()
dataset_ids = list(range(8))
analyses = analyze_datasets(dataset_ids)
duration = time.time() - start

print(f"Analyzed {len(analyses)} datasets in {duration:.2f} seconds")
print(f"Sample result: {analyses[0]}")
print()


# Example 4: Prime number calculation
# ------------------------------------
def is_prime(n):
    """Check if a number is prime (CPU-intensive for large numbers)."""
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False

    # Check odd divisors up to sqrt(n)
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


@parallel(max_workers=4, executor_type="process")
def check_prime(n):
    """Check if a number is prime using parallel processing."""
    return (n, is_prime(n))


print("Example 4: Prime number checking")
print("=" * 50)

# Check primality of large numbers
start = time.time()
large_numbers = [1000003, 1000033, 1000037, 1000039, 1000081, 1000099, 1000117, 1000121]
results = check_prime(large_numbers)
duration = time.time() - start

print(f"Checked {len(results)} numbers in {duration:.2f} seconds")
for number, prime in results:
    print(f"  {number}: {'PRIME' if prime else 'NOT PRIME'}")
print()


print("Summary")
print("=" * 50)
print("When to use executor_type='process':")
print("  ✓ CPU-intensive calculations")
print("  ✓ Data processing and transformations")
print("  ✓ Image/video processing")
print("  ✓ Scientific computing")
print("  ✓ Cryptographic operations")
print()
print("When to use executor_type='thread' (default):")
print("  ✓ I/O-bound tasks (network, disk)")
print("  ✓ API calls")
print("  ✓ Database queries")
print("  ✓ File operations")
print()
print("Key insight:")
print("  Process-based parallelism bypasses Python's GIL,")
print("  enabling true parallel CPU utilization across cores.")
