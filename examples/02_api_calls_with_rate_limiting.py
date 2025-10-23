#!/usr/bin/env python3
"""
API Calls with Rate Limiting
=============================

This example demonstrates how to use pyarallel for making parallel API calls
with rate limiting to respect API quotas.

Note: This example uses a mock API for demonstration.
Replace with actual API calls for real usage.

Run with: python examples/02_api_calls_with_rate_limiting.py
"""

from pyarallel import parallel, RateLimit
import time
import json


# Mock API function (simulates HTTP request)
def mock_api_get(url):
    """Simulate an API call with network delay."""
    time.sleep(0.1)  # Simulate network latency
    return {
        "url": url,
        "status": "success",
        "timestamp": time.time(),
        "data": f"Data from {url}"
    }


# Example 1: Basic API calls with threads
# ----------------------------------------
@parallel(max_workers=5)
def fetch_data(endpoint_id):
    """Fetch data from a single API endpoint."""
    url = f"https://api.example.com/data/{endpoint_id}"
    return mock_api_get(url)


print("Example 1: Basic parallel API calls")
print("=" * 50)

start = time.time()
endpoint_ids = list(range(20))
results = fetch_data(endpoint_ids)
duration = time.time() - start

print(f"Fetched {len(results)} endpoints in {duration:.2f} seconds")
print(f"First result: {json.dumps(results[0], indent=2)}")
print()


# Example 2: Rate limited API calls (10 requests per second)
# -----------------------------------------------------------
@parallel(
    max_workers=5,
    rate_limit=10  # 10 operations per second
)
def fetch_with_rate_limit(endpoint_id):
    """Fetch with rate limiting - respects API quotas."""
    url = f"https://api.example.com/limited/{endpoint_id}"
    return mock_api_get(url)


print("Example 2: Rate limited API calls (10/second)")
print("=" * 50)

start = time.time()
endpoint_ids = list(range(30))
results = fetch_with_rate_limit(endpoint_ids)
duration = time.time() - start

print(f"Fetched {len(results)} endpoints in {duration:.2f} seconds")
print(f"Expected minimum time: ~3 seconds (30 requests ÷ 10 per second)")
print(f"Actual time: {duration:.2f} seconds")
print()


# Example 3: Per-minute rate limiting
# ------------------------------------
@parallel(
    max_workers=10,
    rate_limit=(100, "minute")  # 100 operations per minute
)
def fetch_premium_api(resource_id):
    """Fetch from API with per-minute quota."""
    url = f"https://api.premium.com/v1/resource/{resource_id}"
    return mock_api_get(url)


print("Example 3: Per-minute rate limiting")
print("=" * 50)

# Fetch 20 items with 100/minute limit
start = time.time()
resource_ids = list(range(20))
results = fetch_premium_api(resource_ids)
duration = time.time() - start

print(f"Fetched {len(results)} resources in {duration:.2f} seconds")
print(f"Rate: ~{len(results) / duration:.1f} requests/second")
print()


# Example 4: Using RateLimit object for clarity
# ----------------------------------------------
api_rate = RateLimit(count=5, interval="second")

@parallel(
    max_workers=3,
    rate_limit=api_rate
)
def fetch_with_rate_object(item_id):
    """Fetch using RateLimit object for better readability."""
    url = f"https://api.example.com/items/{item_id}"
    return mock_api_get(url)


print("Example 4: Using RateLimit object")
print("=" * 50)

start = time.time()
item_ids = list(range(15))
results = fetch_with_rate_object(item_ids)
duration = time.time() - start

print(f"Fetched {len(results)} items in {duration:.2f} seconds")
print(f"Rate limit: {api_rate.count} per {api_rate.interval}")
print(f"Expected minimum time: ~3 seconds (15 items ÷ 5 per second)")
print(f"Actual time: {duration:.2f} seconds")
print()


# Example 5: Batch processing with rate limiting
# -----------------------------------------------
@parallel(
    max_workers=4,
    batch_size=5,  # Process in batches of 5
    rate_limit=(10, "second")
)
def fetch_large_dataset(page_id):
    """Fetch pages from a large dataset."""
    url = f"https://api.example.com/pages/{page_id}"
    return mock_api_get(url)


print("Example 5: Batch processing with rate limiting")
print("=" * 50)

start = time.time()
page_ids = list(range(50))
results = fetch_large_dataset(page_ids)
duration = time.time() - start

print(f"Fetched {len(results)} pages in {duration:.2f} seconds")
print(f"Batch size: 5, Rate limit: 10/second")
print()


print("Summary")
print("=" * 50)
print("Rate limiting options:")
print("  1. @parallel(rate_limit=10)           # 10 per second")
print("  2. @parallel(rate_limit=(100, 'minute'))  # 100 per minute")
print("  3. @parallel(rate_limit=(1000, 'hour'))   # 1000 per hour")
print("  4. @parallel(rate_limit=RateLimit(5, 'second'))  # Using object")
print()
print("Use rate limiting to:")
print("  - Respect API quotas and avoid throttling")
print("  - Control resource consumption")
print("  - Ensure smooth, predictable load on external services")
