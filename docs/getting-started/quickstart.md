# Quick Start Guide

## Basic Usage

Pyarallel makes parallel processing in Python simple and efficient. Here's how to get started:

```python
from pyarallel import parallel

# Basic parallel processing with threads
@parallel(max_workers=4)
def fetch_url(url: str) -> dict:
    import requests
    return requests.get(url).json()

# Process multiple URLs in parallel
urls = [
    "https://api.example.com/data/1",
    "https://api.example.com/data/2"
]
results = fetch_url(urls)  # Returns list of results
```

## Common Patterns

### CPU-Bound Tasks

For CPU-intensive operations, use process-based parallelism:

```python
@parallel(
    max_workers=4,
    executor_type="process"  # Use processes instead of threads
)
def process_data(data: bytes) -> bytes:
    # CPU-intensive computation
    return heavy_computation(data)
```

### Rate-Limited Operations

Control execution rates for API calls or resource-intensive operations:

```python
@parallel(
    max_workers=4,
    rate_limit=(100, "minute")  # 100 operations per minute
)
def api_call(item_id: str) -> dict:
    return api.get_item(item_id)
```

### Batch Processing

Handle large datasets efficiently with automatic batching:

```python
@parallel(
    max_workers=4,
    batch_size=10  # Process items in batches of 10
)
def analyze_text(text: str) -> dict:
    return text_analysis(text)

# Process a large list of texts
texts = ["text1", "text2", ..., "text1000"]
results = analyze_text(texts)  # Processed in batches
```

## Error Handling

Pyarallel provides comprehensive error handling:

```python
@parallel(max_workers=4)
def process_item(item):
    try:
        return do_work(item)
    except Exception as e:
        # Errors are propagated to the caller
        raise RuntimeError(f"Failed to process {item}: {e}")

# Handle errors in the caller
try:
    results = process_item(items)
except Exception as e:
    print(f"Processing failed: {e}")
```

## Next Steps

- Explore [Advanced Features](../user-guide/advanced-features.md) for more capabilities
- Check [Configuration](../user-guide/configuration.md) for customization options
- Review [Best Practices](../user-guide/best-practices.md) for optimization tips