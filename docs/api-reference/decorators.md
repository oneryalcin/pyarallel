# API Reference

## Decorator API

### @parallel

The main decorator for enabling parallel execution of functions.

```python
from pyarallel import parallel

@parallel(
    max_workers: int = None,
    batch_size: int = None,
    rate_limit: float | tuple[float, TimeUnit] | RateLimit = None,
    executor_type: Literal["thread", "process"] = None,
    prewarm: bool = False
)
def function(item, *args, **kwargs) -> Any: ...
```

#### Parameters

- `max_workers` (int, optional): Maximum number of parallel workers. Defaults to global configuration.
- `batch_size` (int, optional): Number of items to process in each batch. Defaults to global configuration.
- `rate_limit`: Rate limiting configuration. Can be:
  - A float (operations per second)
  - A tuple of (count, unit) where unit is "second", "minute", or "hour"
  - A RateLimit instance
- `executor_type` (str, optional): Type of parallelism to use ("thread" or "process"). Defaults to global configuration.
- `prewarm` (bool): If True, starts all workers immediately.

#### Returns

A wrapped function that accepts either:
- A single item (returns a single-item list)
- A list/tuple of items (processes in parallel and returns a list of results)

#### Examples

```python
# Basic usage with threads
@parallel(max_workers=4)
def fetch_url(url: str) -> dict:
    return requests.get(url).json()

# Process-based execution with rate limiting
@parallel(
    max_workers=4,
    executor_type="process",
    rate_limit=(100, "minute")
)
def process_image(image: bytes) -> bytes:
    return heavy_processing(image)

# Batch processing with prewarming
@parallel(
    max_workers=4,
    batch_size=10,
    prewarm=True
)
def analyze_text(text: str) -> dict:
    return text_analysis(text)
```
