#!/usr/bin/env python3
"""
Batch Processing
================

This example demonstrates memory control with batch_size on .map(...).

Run with: python examples/04_batch_processing.py
"""

import time

from pyarallel import parallel


@parallel(workers=4)
def process_record(record_id):
    time.sleep(0.01)
    return f"Record {record_id} processed"


@parallel(workers=4)
def process_large_chunk(chunk_id):
    chunk = {
        "chunk_id": chunk_id,
        "data": [i for i in range(1000)],
        "size_kb": 100,
    }
    return {
        "chunk_id": chunk_id,
        "result": sum(chunk["data"]),
        "size_kb": chunk["size_kb"],
    }


@parallel(workers=10)
def fetch_and_enrich_user(user_id):
    time.sleep(0.01)
    return {
        "id": user_id,
        "name": f"User {user_id}",
        "email": f"user{user_id}@example.com",
        "status": "active",
        "processed_at": round(time.time(), 3),
    }


@parallel(workers=4, executor="process")
def process_file(file_id):
    lines = [f"line {i}" for i in range(100)]
    processed_lines = [line.upper() for line in lines]
    return {"file_id": file_id, "lines_processed": len(processed_lines)}


def benchmark_batch_size(batch_size, total_items=80):
    @parallel(workers=4)
    def process_item(item_id):
        time.sleep(0.005)
        return item_id * 2

    start = time.time()
    results = process_item.map(range(total_items), batch_size=batch_size)
    assert len(results) == total_items
    return time.time() - start


def main():
    print("Example 1: Memory-efficient batch processing")
    print("=" * 50)

    start = time.time()
    results = process_record.map(range(40), batch_size=10)
    duration = time.time() - start

    print(f"Processed {len(results)} records in {duration:.2f} seconds")
    print("Batch size: 10")
    print()

    print("Example 2: Batch size comparison")
    print("=" * 50)

    for batch_size in [1, 5, 10, 20, 40]:
        duration = benchmark_batch_size(batch_size)
        print(f"Batch size {batch_size:2d}: {duration:.2f} seconds")
    print()

    print("Example 3: Large dataset processing")
    print("=" * 50)

    start = time.time()
    chunk_results = process_large_chunk.map(range(20), batch_size=5)
    duration = time.time() - start
    total_size = sum(r["size_kb"] for r in chunk_results)

    print(f"Processed {len(chunk_results)} chunks ({total_size} KB total)")
    print(f"Time: {duration:.2f} seconds")
    print("Batch size: 5")
    print()

    print("Example 4: Database-style operations")
    print("=" * 50)

    start = time.time()
    users = fetch_and_enrich_user.map(range(100), batch_size=20)
    duration = time.time() - start

    print(f"Fetched and enriched {len(users)} users")
    print(f"Time: {duration:.2f} seconds")
    print(f"Sample: {list(users)[0]}")
    print()

    print("Example 5: Process-based batch work")
    print("=" * 50)

    start = time.time()
    file_results = process_file.map(range(40), batch_size=10)
    duration = time.time() - start
    total_lines = sum(r["lines_processed"] for r in file_results)

    print(f"Processed {len(file_results)} files ({total_lines:,} lines total)")
    print(f"Time: {duration:.2f} seconds")
    print("Batch size: 10, executor='process'")
    print()

    print("Summary")
    print("=" * 50)
    print("1. Pass batch_size to .map(...) to limit in-flight work.")
    print("2. Smaller batches reduce peak memory.")
    print("3. Larger batches can improve throughput for tiny tasks.")
    print("4. Batching works with both thread and process executors.")


if __name__ == "__main__":
    main()
