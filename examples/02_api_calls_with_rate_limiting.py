#!/usr/bin/env python3
"""
API Calls with Rate Limiting
============================

This example demonstrates parallel I/O with rate limits that match the
current pyarallel API.

Run with: python examples/02_api_calls_with_rate_limiting.py
"""

import json
import time

from pyarallel import RateLimit, parallel


def mock_api_get(url):
    """Simulate an API call with network delay."""
    time.sleep(0.1)
    return {
        "url": url,
        "status": "success",
        "timestamp": round(time.time(), 3),
        "data": f"Data from {url}",
    }


@parallel(workers=5)
def fetch_data(endpoint_id):
    url = f"https://api.example.com/data/{endpoint_id}"
    return mock_api_get(url)


print("Example 1: Basic parallel API calls")
print("=" * 50)

start = time.time()
endpoint_ids = list(range(10))
results = fetch_data.map(endpoint_ids)
duration = time.time() - start

print(f"Fetched {len(results)} endpoints in {duration:.2f} seconds")
print(f"First result: {json.dumps(list(results)[0], indent=2)}")
print()


@parallel(workers=5, rate_limit=10)
def fetch_with_rate_limit(endpoint_id):
    url = f"https://api.example.com/limited/{endpoint_id}"
    return mock_api_get(url)


print("Example 2: Rate limited API calls (10/second)")
print("=" * 50)

start = time.time()
results = fetch_with_rate_limit.map(range(12))
duration = time.time() - start

print(f"Fetched {len(results)} endpoints in {duration:.2f} seconds")
print("Expected minimum time: about 1.1 seconds")
print()


@parallel(workers=4, rate_limit=RateLimit(120, "minute"))
def fetch_premium_api(resource_id):
    url = f"https://api.premium.com/v1/resource/{resource_id}"
    return mock_api_get(url)


print("Example 3: Per-minute rate limit")
print("=" * 50)

start = time.time()
results = fetch_premium_api.map(range(5))
duration = time.time() - start

print(f"Fetched {len(results)} resources in {duration:.2f} seconds")
print("Rate limit: 120/minute (2 requests/second)")
print()


api_rate = RateLimit(5, "second")


@parallel(workers=3, rate_limit=api_rate)
def fetch_with_rate_object(item_id):
    url = f"https://api.example.com/items/{item_id}"
    return mock_api_get(url)


print("Example 4: Using a RateLimit object")
print("=" * 50)

start = time.time()
results = fetch_with_rate_object.map(range(8))
duration = time.time() - start

print(f"Fetched {len(results)} items in {duration:.2f} seconds")
print(f"Rate limit: {api_rate.count} per {api_rate.per}")
print()


@parallel(workers=4, rate_limit=RateLimit(10, "second"))
def fetch_large_dataset(page_id):
    url = f"https://api.example.com/pages/{page_id}"
    return mock_api_get(url)


print("Example 5: Batching plus rate limiting")
print("=" * 50)

start = time.time()
results = fetch_large_dataset.map(range(20), batch_size=5)
duration = time.time() - start

print(f"Fetched {len(results)} pages in {duration:.2f} seconds")
print("Batch size: 5, Rate limit: 10/second")
print()


print("Summary")
print("=" * 50)
print("1. Use a numeric rate limit for requests/second.")
print("2. Use RateLimit(count, 'minute' | 'hour') for longer windows.")
print("3. Put reusable defaults on the decorator.")
print("4. Pass batch_size on .map(...) when you need memory control.")
