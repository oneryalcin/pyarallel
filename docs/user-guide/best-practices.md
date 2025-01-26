# Best Practices

## Choosing the Right Executor

### Thread vs Process

- Use **threads** for I/O-bound tasks:
  - Network requests
  - File operations
  - Database queries

----

- Use **processes** for CPU-bound tasks:
  - Data processing
  - Image manipulation
  - Complex calculations

```python
# I/O-bound example
@parallel(executor_type="thread")
def fetch_data(urls: list) -> list:
    return [requests.get(url).json() for url in urls]

# CPU-bound example
@parallel(executor_type="process")
def process_images(images: list) -> list:
    return [heavy_image_processing(img) for img in images]
```

## Worker Pool Management

### Optimal Worker Count

**For CPU-bound tasks**:

  - Use `multiprocessing.cpu_count()` as a baseline
  - Consider leaving 1-2 cores free for system tasks

----

**For I/O-bound tasks**:

  - Can use more workers than CPU cores
  - Monitor system resources to find optimal number

```python
import multiprocessing

# CPU-bound optimization
@parallel(
    max_workers=max(1, multiprocessing.cpu_count() - 1),
    executor_type="process"
)
def cpu_intensive_task(): ...

# I/O-bound optimization
@parallel(
    max_workers=32,  # Higher count for I/O tasks
    executor_type="thread"
)
def io_intensive_task(): ...
```

## Memory Management

### Batch Processing

- Use batching for large datasets
- Adjust batch size based on memory constraints
- Monitor memory usage during processing

```python
# Memory-efficient processing
@parallel(
    max_workers=4,
    batch_size=100,  # Adjust based on item size
    executor_type="process"
)
def process_large_dataset(items: list) -> list:
    return [process_item(item) for item in items]
```

### Resource Cleanup

- Let Pyarallel handle worker lifecycle
- Avoid manual executor management
- Use context managers when needed

## Rate Limiting

### API Considerations

- Respect API rate limits
- Add buffer to prevent limit breaches
- Use appropriate time intervals

```python
# Safe API usage
@parallel(
    max_workers=4,
    rate_limit=(90, "minute")  # 90% of 100/minute limit
)
def api_call(item_id: str) -> dict:
    return api.get_item(item_id)
```

## Error Handling

### Graceful Failure

- Implement proper error handling
- Log errors for debugging
- Consider retry mechanisms

```python
import logging
from tenacity import retry, stop_after_attempt

@retry(stop=stop_after_attempt(3))
@parallel(max_workers=4)
def resilient_process(item):
    try:
        result = process_item(item)
        if not validate_result(result):
            raise ValueError(f"Invalid result for {item}")
        return result
    except Exception as e:
        logging.error(f"Error processing {item}: {e}")
        raise
```

## Performance Optimization

### Prewarming

- Use prewarming for latency-sensitive applications
- Consider startup cost vs benefit

```python
@parallel(
    max_workers=4,
    prewarm=True,  # Prewarm for faster initial response
    executor_type="process"
)
def latency_sensitive_task(): ...
```

### Batch Size Optimization

- Small batches for low latency
- Larger batches for high throughput
- Balance based on use case

```python
# Low latency needs
@parallel(batch_size=5)
def realtime_processing(): ...

# High throughput needs
@parallel(batch_size=100)
def bulk_processing(): ...
```

## Testing and Monitoring

### Unit Testing

- Test with different worker counts
- Verify error handling
- Check resource cleanup

```python
def test_parallel_processing():
    @parallel(max_workers=2)
    def test_func(x):
        return x * 2
    
    # Test with various inputs
    assert test_func([1, 2, 3]) == [2, 4, 6]
    
    # Test error handling
    with pytest.raises(ValueError):
        test_func(['invalid'])
```

### Production Monitoring

- Monitor worker pool health
- Track memory usage
- Log performance metrics

```python
import logging

@parallel(max_workers=4)
def monitored_task(item):
    start_time = time.time()
    try:
        result = process_item(item)
        duration = time.time() - start_time
        logging.info(f"Processed {item} in {duration:.2f}s")
        return result
    except Exception as e:
        logging.error(f"Failed to process {item}: {e}")
        raise
```