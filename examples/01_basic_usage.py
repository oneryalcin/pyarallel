#!/usr/bin/env python3
"""
Basic Usage Example
===================

This example demonstrates the current pyarallel API:
1. Write a function that handles ONE item
2. Decorate it with @parallel for reusable defaults
3. Use .map(...) for parallel execution over MANY items

Run with: python examples/01_basic_usage.py
"""

import time

from pyarallel import parallel


@parallel(workers=4)
def multiply_by_two(number):
    """Process a single number."""
    return number * 2


print("Example 1: Direct call vs .map()")
print("=" * 50)

single = multiply_by_two(5)
print(f"multiply_by_two(5) = {single}")  # Scalar return value

numbers = [1, 2, 3, 4, 5]
parallel_result = multiply_by_two.map(numbers)
print(f"multiply_by_two.map({numbers}) = {list(parallel_result)}")
print()


@parallel(workers=4)
def simulate_io_task(task_id):
    """Simulate an I/O-bound task such as an HTTP request."""
    time.sleep(0.2)
    return f"Task {task_id} completed"


print("Example 2: Parallel I/O")
print("=" * 50)

start = time.time()
tasks = list(range(8))
results = simulate_io_task.map(tasks)
duration = time.time() - start

print(f"Processed {len(results)} tasks in {duration:.2f} seconds")
print(f"First 3 results: {list(results)[:3]}")
print()


@parallel(workers=4)
def format_name(name):
    """Format a single name to title case."""
    return name.strip().title()


print("Example 3: Data transformation")
print("=" * 50)

names = ["john doe", "JANE SMITH", "  bob jones  ", "alice wonder"]
formatted = format_name.map(names)
print(f"Original: {names}")
print(f"Formatted: {list(formatted)}")
print()


class TextProcessor:
    def __init__(self, prefix):
        self.prefix = prefix

    @parallel(workers=4)
    def add_prefix(self, text):
        return f"{self.prefix}: {text}"


print("Example 4: Instance methods")
print("=" * 50)

processor = TextProcessor("LOG")
messages = ["Starting", "Processing", "Finished"]
prefixed = processor.add_prefix.map(messages)
print(f"Original: {messages}")
print(f"Prefixed: {list(prefixed)}")
print()


print("Summary")
print("=" * 50)
print("1. Decorated functions keep normal single-item behavior.")
print("2. Use .map(...) to parallelize over many items.")
print("3. .map(...) returns ParallelResult, which behaves like a list on success.")
print("4. Methods work the same way as regular functions.")
