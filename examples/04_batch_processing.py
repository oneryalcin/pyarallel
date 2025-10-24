#!/usr/bin/env python3
"""
Batch Processing
================

This example demonstrates how to efficiently process large datasets
using batch processing to control memory usage.

Run with: python examples/04_batch_processing.py

Note: Example 5 uses process-based parallelism, so all execution code
must be wrapped in if __name__ == "__main__" for proper pickling.
"""

from pyarallel import parallel
import time


# Example 1: Memory-efficient batch processing
# ---------------------------------------------
@parallel(max_workers=4, batch_size=10)
def process_record(record_id):
    """Process a single record from a large dataset."""
    # Simulate processing
    time.sleep(0.01)
    return f"Record {record_id} processed"

# Process 100 records in batches of 10
start = time.time()
record_ids = list(range(100))
results = process_record(record_ids)
duration = time.time() - start

print(f"Processed {len(results)} records in {duration:.2f} seconds")
print(f"Batch size: 10 (processes 10 items at a time)")
print(f"Total batches: {len(results) // 10}")
print()


# Example 2: Comparing batch sizes
# ---------------------------------
def benchmark_batch_size(batch_size, total_items=200):
    """Benchmark different batch sizes."""

    @parallel(max_workers=4, batch_size=batch_size)
    def process_item(item_id):
        time.sleep(0.005)  # Simulate processing
        return item_id * 2

    start = time.time()
    items = list(range(total_items))
    results = process_item(items)
    duration = time.time() - start

    return duration


print("Example 2: Batch size comparison")
print("=" * 50)

batch_sizes = [1, 10, 25, 50, 100]
for batch_size in batch_sizes:
    duration = benchmark_batch_size(batch_size)
    print(f"Batch size {batch_size:3d}: {duration:.2f} seconds")

print()


# Example 3: Large dataset processing with memory constraints
# ------------------------------------------------------------
class DataLoader:
    """Simulate loading large data objects."""

    @staticmethod
    def load_chunk(chunk_id):
        """Simulate loading a large data chunk (e.g., from database)."""
        # In real scenario, this might load from database/file
        return {
            "chunk_id": chunk_id,
            "data": [i for i in range(1000)],  # Simulate large data
            "size_kb": 100
        }


@parallel(max_workers=4, batch_size=5)  # Small batches for memory control
def process_large_chunk(chunk_id):
    """Process large data chunks in controlled batches."""
    chunk = DataLoader.load_chunk(chunk_id)

    # Process the data
    processed_data = sum(chunk["data"])

    return {
        "chunk_id": chunk_id,
        "result": processed_data,
        "size_kb": chunk["size_kb"]
    }


print("Example 3: Memory-constrained processing")
print("=" * 50)

start = time.time()
chunk_ids = list(range(50))  # 50 large chunks
results = process_large_chunk(chunk_ids)
duration = time.time() - start

total_size = sum(r["size_kb"] for r in results)
print(f"Processed {len(results)} chunks ({total_size} KB total)")
print(f"Time: {duration:.2f} seconds")
print(f"Batch size: 5 (max 5 chunks in memory at once)")
print(f"Memory efficiency: Only 5 chunks loaded simultaneously")
print()


# Example 4: Database-style batch processing
# -------------------------------------------
class DatabaseSimulator:
    """Simulate batch database operations."""

    @staticmethod
    def fetch_user(user_id):
        """Simulate fetching user from database."""
        time.sleep(0.01)  # Simulate DB latency
        return {
            "id": user_id,
            "name": f"User {user_id}",
            "email": f"user{user_id}@example.com"
        }


@parallel(
    max_workers=10,
    batch_size=20,  # Process 20 users at a time
    prewarm=True     # Start workers immediately
)
def fetch_and_enrich_user(user_id):
    """Fetch user and add enrichment data."""
    user = DatabaseSimulator.fetch_user(user_id)

    # Add enrichment
    user["status"] = "active"
    user["processed_at"] = time.time()

    return user


print("Example 4: Batch database operations")
print("=" * 50)

start = time.time()
user_ids = list(range(1000))
users = fetch_and_enrich_user(user_ids)
duration = time.time() - start

print(f"Fetched and enriched {len(users)} users")
print(f"Time: {duration:.2f} seconds")
print(f"Throughput: {len(users) / duration:.0f} users/second")
print(f"Batch size: 20, Workers: 10 (prewarmed)")
print(f"Sample: {users[0]}")
print()


# Example 5: File processing in batches
# --------------------------------------
@parallel(
    max_workers=4,
    batch_size=50,  # Process 50 files per batch
    executor_type="process"
)
def process_file(file_id):
    """Simulate processing a file (e.g., CSV, log file)."""
    # Simulate file reading and processing
    lines = [f"line {i}" for i in range(100)]

    # Process lines
    processed_lines = [line.upper() for line in lines]

    return {
        "file_id": file_id,
        "lines_processed": len(processed_lines)
    }


print("Example 5: Batch file processing")
print("=" * 50)

start = time.time()
file_ids = list(range(500))  # 500 files
results = process_file(file_ids)
duration = time.time() - start

total_lines = sum(r["lines_processed"] for r in results)
print(f"Processed {len(results)} files ({total_lines:,} lines total)")
print(f"Time: {duration:.2f} seconds")
print(f"Files/second: {len(results) / duration:.0f}")
print(f"Batch size: 50 (processes 50 files at a time)")
print()


print("Summary")
print("=" * 50)
print("Batch processing benefits:")
print("  ✓ Controls memory usage (limits concurrent operations)")
print("  ✓ Prevents resource exhaustion")
print("  ✓ Improves predictability")
print("  ✓ Enables processing of datasets larger than RAM")
print()
print("Choosing batch size:")
print("  • Small batches (5-20): Large items, limited memory")
print("  • Medium batches (20-100): Balanced approach")
print("  • Large batches (100+): Small items, optimize throughput")
print()
print("Combine with:")
print("  • max_workers: Control concurrency")
print("  • executor_type: Choose thread/process based on task")
print("  • rate_limit: Control external API/resource load")
