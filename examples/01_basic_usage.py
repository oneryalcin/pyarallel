#!/usr/bin/env python3
"""
Basic Usage Example
===================

This example demonstrates the fundamental concepts of pyarallel:
1. Write a function that processes ONE item
2. Decorate it with @parallel
3. Pass MANY items to process them in parallel

Run with: python examples/01_basic_usage.py
"""

from pyarallel import parallel
import time


# Example 1: Simple multiplication
# ---------------------------------
# Notice: The function takes a SINGLE number, not a list
@parallel(max_workers=4)
def multiply_by_two(number):
    """Process a single number."""
    return number * 2


print("Example 1: Simple multiplication")
print("=" * 50)

# Single item - returns a list with one result
result = multiply_by_two(5)
print(f"multiply_by_two(5) = {result}")  # Output: [10]

# Multiple items - processes in parallel
numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
results = multiply_by_two(numbers)
print(f"multiply_by_two({numbers}) = {results}")
print()


# Example 2: I/O simulation with timing
# --------------------------------------
@parallel(max_workers=4)
def simulate_io_task(task_id):
    """Simulate an I/O-bound task (like API call or file read)."""
    time.sleep(0.5)  # Simulate network delay
    return f"Task {task_id} completed"


print("Example 2: I/O simulation")
print("=" * 50)

# Sequential execution would take 5 seconds (10 tasks × 0.5s)
# Parallel execution with 4 workers takes ~1.5 seconds
start = time.time()
tasks = list(range(10))
results = simulate_io_task(tasks)
duration = time.time() - start

print(f"Processed {len(results)} tasks in {duration:.2f} seconds")
print(f"Results: {results[:3]}...")  # Show first 3 results
print(f"Expected time (sequential): ~5 seconds")
print(f"Actual time (parallel): {duration:.2f} seconds")
print()


# Example 3: Data transformation
# -------------------------------
@parallel(max_workers=4)
def format_name(name):
    """Format a single name to title case."""
    return name.strip().title()


print("Example 3: Data transformation")
print("=" * 50)

names = ["john doe", "JANE SMITH", "  bob jones  ", "alice wonder"]
formatted = format_name(names)
print(f"Original: {names}")
print(f"Formatted: {formatted}")
print()


# Example 4: Working with instance methods
# -----------------------------------------
class TextProcessor:
    def __init__(self, prefix):
        self.prefix = prefix

    @parallel(max_workers=4)
    def add_prefix(self, text):
        """Process a single text item."""
        return f"{self.prefix}: {text}"


print("Example 4: Instance methods")
print("=" * 50)

processor = TextProcessor("LOG")
messages = ["Starting", "Processing", "Finished"]
prefixed = processor.add_prefix(messages)
print(f"Original: {messages}")
print(f"Prefixed: {prefixed}")
print()


print("Summary")
print("=" * 50)
print("Key takeaways:")
print("1. Write functions for ONE item (singular)")
print("2. Pass MANY items (list/tuple) to process in parallel")
print("3. Always get a list back, even for single items")
print("4. Works with regular functions, instance methods, class methods")
