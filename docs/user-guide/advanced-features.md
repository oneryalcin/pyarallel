# Advanced Features

## Configuration Options

### Executor Types

Pyarallel supports both thread and process-based executors:

```python
# Thread-based for I/O-bound tasks (default)
@parallel(executor_type="thread")
def io_task(): ...

# Process-based for CPU-bound tasks
@parallel(executor_type="process")
def cpu_task(): ...
```

### Worker Management

#### Worker Prewarming

Reduce initial latency by prewarming workers:

```python
@parallel(
    max_workers=4,
    prewarm=True  # Start workers immediately
)
def latency_sensitive_task(): ...
```

#### Dynamic Worker Pools

Pyarallel automatically manages worker lifecycles:

```python
# Workers are reused across calls
@parallel(max_workers=4)
def reused_pool_task(): ...

# Workers are cleaned up when no longer needed
```

## Rate Limiting

### Time-Based Rate Limits

```python
# Operations per second
@parallel(rate_limit=10)  # 10 ops/second

# Operations per minute
@parallel(rate_limit=(100, "minute"))  # 100 ops/minute

# Operations per hour
@parallel(rate_limit=(1000, "hour"))  # 1000 ops/hour
```

### Custom Rate Limiting

```python
from pyarallel import RateLimit

# Create custom rate limit
rate = RateLimit(count=50, interval="minute")

@parallel(rate_limit=rate)
def rate_limited_task(): ...
```

## Batch Processing

### Automatic Batching

```python
@parallel(
    max_workers=4,
    batch_size=10,  # Process 10 items at a time
    executor_type="process"
)
def process_batch(items: list) -> list:
    return [process_item(item) for item in items]

# Process large dataset
items = list(range(1000))
results = process_batch(items)  # Processed in batches of 10
```

### Memory-Efficient Processing

```python
@parallel(
    max_workers=4,
    batch_size=100  # Larger batches for better throughput
)
def process_large_dataset(data: list) -> list:
    return heavy_processing(data)

# Process millions of items efficiently
results = process_large_dataset(large_dataset)
```

## Advanced Error Handling

### Batch Error Handling

```python
from typing import List, Optional

@parallel(batch_size=10)
def process_with_errors(items: List[str]) -> List[Optional[dict]]:
    results = []
    for item in items:
        try:
            results.append(process_item(item))
        except Exception as e:
            results.append(None)  # Continue on error
            print(f"Error processing {item}: {e}")
    return results
```

### Custom Exception Handling

```python
class ProcessingError(Exception):
    pass

@parallel(max_workers=4)
def safe_process(item):
    try:
        result = process_item(item)
        if not validate_result(result):
            raise ProcessingError(f"Invalid result for {item}")
        return result
    except ProcessingError as e:
        # Handle specific errors
        handle_processing_error(e)
    except Exception as e:
        # Handle unexpected errors
        handle_unexpected_error(e)
```

## Performance Optimization

### Worker Pool Optimization

```python
# Optimize for CPU-bound tasks
@parallel(
    max_workers=multiprocessing.cpu_count(),
    executor_type="process"
)
def cpu_optimized_task(): ...

# Optimize for I/O-bound tasks
@parallel(
    max_workers=32,  # Higher worker count for I/O
    executor_type="thread"
)
def io_optimized_task(): ...
```

### Batch Size Optimization

```python
# Small batches for low latency
@parallel(batch_size=5)
def low_latency_task(): ...

# Large batches for high throughput
@parallel(batch_size=100)
def high_throughput_task(): ...
```