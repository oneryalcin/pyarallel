# API Reference

## Rate Limiting API

### RateLimit

Configuration class for rate limiting parallel operations.

```python
from pyarallel import RateLimit

rate = RateLimit(
    count: float,
    interval: Literal["second", "minute", "hour"] = "second"
)
```

#### Parameters

- `count` (float): Number of operations allowed per interval
- `interval` (str): Time interval for rate limiting ("second", "minute", "hour")

#### Properties

- `per_second` (float): Converts the rate to operations per second

#### Examples

```python
# Define rate limits
per_minute = RateLimit(100, "minute")  # 100 ops/minute
per_hour = RateLimit(1000, "hour")    # 1000 ops/hour

# Use with parallel decorator
@parallel(rate_limit=per_minute)
def rate_limited_function(item): ...
```

### TokenBucket

Thread-safe implementation of the token bucket algorithm for rate limiting.

```python
from pyarallel import TokenBucket

bucket = TokenBucket(
    rate_limit: RateLimit,
    capacity: int = None
)
```

#### Parameters

- `rate_limit` (RateLimit): Rate limiting configuration
- `capacity` (int, optional): Maximum number of tokens the bucket can hold

#### Methods

- `get_token() -> bool`: Try to get a token from the bucket
- `wait_for_token()`: Block until a token is available
