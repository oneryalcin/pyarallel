# Pyarallel

[![Docs](https://img.shields.io/badge/docs-live-brightgreen)](https://oneryalcin.github.io/pyarallel/) [![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)


A powerful,feature-rich parallel execution library for Python that makes concurrent programming easy and efficient.

## Features

- **Simple Decorator-Based API**: Just add `@parallel` to your functions
- **Flexible Parallelism**: Choose between threads (I/O-bound) and processes (CPU-bound)
- **Smart Rate Limiting**: Control execution rates with per-second, per-minute, or per-hour limits
- **Batch Processing**: Handle large datasets efficiently with automatic batching
- **Performance Optimized**: 
  - Automatic worker pool reuse
  - Optional worker prewarming for latency-critical applications
  - Smart defaults based on your system
- **Production Ready**:
  - Thread-safe implementation
  - Memory-efficient with automatic cleanup
  - Comprehensive error handling

## Documentation

Check out the [documentation](https://oneryalcin.github.io/pyarallel/) for detailed usage instructions and examples.

## Installation

```bash
pip install pyarallel
```

## Quick Start

```python
from pyarallel import parallel

# Basic parallel processing
@parallel(max_workers=4)
def fetch_url(url: str) -> dict:
    return requests.get(url).json()

# Process multiple URLs in parallel
urls = ["http://api1.com", "http://api2.com"]
results = fetch_url(urls)

# Rate-limited CPU-intensive task
@parallel(
    max_workers=4,
    executor_type="process",
    rate_limit=(100, "minute")  # 100 ops/minute
)
def process_image(image: bytes) -> bytes:
    return heavy_processing(image)

# Memory-efficient batch processing
@parallel(max_workers=4, batch_size=10)
def analyze_text(text: str) -> dict:
    return text_analysis(text)
```

## Usage Examples

### Basic Function
```python
from pyarallel import parallel

@parallel
def process_item(x):
    return x * 2

results = process_item([1, 2, 3])  # [2, 4, 6]
```

### Instance Methods
```python
class DataProcessor:
    def __init__(self, multiplier):
        self.multiplier = multiplier
    
    @parallel
    def process(self, x):
        return x * self.multiplier

processor = DataProcessor(3)
results = processor.process([1, 2, 3])  # [3, 6, 9]
```

### Class Methods
```python
class StringFormatter:
    @classmethod
    @parallel
    def format_all(cls, items):
        return [f"Formatted-{item}" for item in items]

results = StringFormatter.format_all(['a', 'b', 'c'])
# ['Formatted-a', 'Formatted-b', 'Formatted-c']
```

### Static Methods
```python
class MathUtils:
    @staticmethod
    @parallel
    def square_all(numbers):
        return [n**2 for n in numbers]

results = MathUtils.square_all([1, 2, 3])  # [1, 4, 9]
```

## Advanced Usage

### Rate Limiting

Control execution rates using various formats:

```python
# Operations per second
@parallel(rate_limit=2.0)
def func1(): ...

# Operations per minute
@parallel(rate_limit=(100, "minute"))
def func2(): ...

# Custom rate limit object
from pyarallel import RateLimit
rate = RateLimit(1000, "hour")
@parallel(rate_limit=rate)
def func3(): ...
```

### CPU-Bound Tasks

Use process-based parallelism for CPU-intensive operations:

```python
@parallel(
    max_workers=4,
    executor_type="process",  # Use processes instead of threads
    batch_size=10            # Process in batches of 10
)
def cpu_intensive(data: bytes) -> bytes:
    return heavy_computation(data)
```

### Latency-Critical Applications

Prewarm workers to minimize cold start latency:

```python
@parallel(
    max_workers=4,
    prewarm=True  # Start workers immediately
)
def latency_critical(item): ...
```

### Memory-Efficient Processing

Handle large datasets with batch processing:

```python
@parallel(
    max_workers=4,
    batch_size=100  # Process items in batches of 100
)
def process_large_dataset(item): ...

# Process millions of items without memory issues
items = range(1_000_000)
results = process_large_dataset(items)
```

## Best Practices

1. **Choose the Right Executor**:
   - Use `executor_type="thread"` (default) for I/O-bound tasks (network, disk)
   - Use `executor_type="process"` for CPU-bound tasks (computation)

2. **Optimize Worker Count**:
   - For I/O-bound: `max_workers = cpu_count * 5` (default)
   - For CPU-bound: `max_workers = cpu_count` (default)

3. **Control Resource Usage**:
   - Use `batch_size` for large datasets
   - Use `rate_limit` to prevent overwhelming resources
   - Only use `prewarm=True` when cold start latency is critical

4. **Handle Errors Properly**:
   ```python
   @parallel()
   def my_func(item):
       try:
           return process(item)
       except Exception as e:
           return {"error": str(e), "item": item}
   ```

## Configuration

Pyarallel features a robust configuration system built on Pydantic, offering type validation, environment variable support, and thread-safe configuration management.

### Basic Configuration

```python
from pyarallel import ConfigManager

# Get the thread-safe singleton configuration manager
config = ConfigManager.get_instance()

# Update configuration with type validation
config.update_config({
    "execution": {
        "default_max_workers": 8,
        "default_executor_type": "thread",
        "default_batch_size": 100,
        "prewarm_pools": True
    },
    "rate_limiting": {
        "default_rate": 1000,
        "default_interval": "minute",
        "burst_tolerance": 1.5
    }
})

# Access configuration using dot notation
workers = config.execution.default_max_workers
rate = config.rate_limiting.default_rate

# Category-specific updates
config.update_execution(max_workers=16)
config.update_rate_limiting(rate=2000)
```

### Environment Variables

Configure Pyarallel using environment variables with the `PYARALLEL_` prefix. The system automatically handles type coercion and validation:

```bash
# Execution settings
export PYARALLEL_MAX_WORKERS=4
export PYARALLEL_EXECUTOR_TYPE=thread
export PYARALLEL_BATCH_SIZE=100

# Rate limiting
export PYARALLEL_RATE_LIMIT=100/minute
export PYARALLEL_FAIL_FAST=true

# Complex values (using JSON)
export PYARALLEL_RETRY_CONFIG='{"max_attempts": 3, "backoff": 1.5}'
```

### Configuration Schema

The configuration system uses a structured schema with the following categories:

```python
{
    "execution": {
        "default_max_workers": int,        # Default worker count
        "default_executor_type": str,      # "thread" or "process"
        "default_batch_size": Optional[int], # Default batch size
        "prewarm_pools": bool             # Enable worker prewarming
    },
    "rate_limiting": {
        "default_rate": Optional[float],   # Default operations per interval
        "default_interval": str,          # "second", "minute", "hour"
        "burst_tolerance": float          # Burst allowance factor
    },
    "error_handling": {
        "max_retries": int,               # Maximum retry attempts
        "retry_backoff": float,           # Backoff multiplier
        "fail_fast": bool                 # Stop on first error
    },
    "monitoring": {
        "enable_logging": bool,           # Enable detailed logging
        "log_level": str,                # Logging level
        "sentry_dsn": Optional[str],     # Sentry integration
        "metrics_enabled": bool          # Enable metrics collection
    }
}
```

### Best Practices

1. **Use Environment Variables for Deployment**:
   - Keep configuration in environment variables for different environments
   - Use the `PYARALLEL_` prefix to avoid conflicts
   - Complex values can be passed as JSON strings

2. **Validate Configuration Early**:
   - Set up configuration at application startup
   - Use type validation to catch issues early
   - Test configuration with sample data

3. **Thread-Safe Updates**:
   - Always use `ConfigManager.get_instance()` for thread-safe access
   - Make configuration changes before starting parallel operations
   - Use category-specific update methods for better type safety

4. **Configuration Inheritance**:
   - Global settings serve as defaults
   - Decorator arguments override global configuration
   - Environment variables take precedence over code-based configuration

### Runtime Configuration Warnings

Pyarallel includes built-in warnings to help identify potential performance issues:

```python
# Warning for high worker count
@parallel(max_workers=150)  # Triggers warning about system impact
def high_worker_task(): ...

# Warning for inefficient process pool configuration
@parallel(
    executor_type="process",
    batch_size=1  # Triggers warning about inefficient batch size
)
def inefficient_task(): ...
```

### Configuration Inheritance

Pyarallel uses a hierarchical configuration system:

1. **Default Values**: Built-in defaults (4 workers, thread executor, batch size 10)
2. **Global Configuration**: Set via ConfigManager
3. **Environment Variables**: Override global config
4. **Decorator Arguments**: Highest precedence, override all other settings

```python
# Global configuration (lowest precedence)
config = ConfigManager.get_instance()
config.update_config({
    "execution": {
        "default_max_workers": 8,
        "default_executor_type": "thread"
    }
})

# Environment variables (middle precedence)
# export PYARALLEL_MAX_WORKERS=16

# Decorator arguments (highest precedence)
@parallel(max_workers=4)  # This value wins
def my_func(): ...
```

## Roadmap

### Observability & Debugging
- **Advanced Telemetry System**
  - Task execution metrics (duration, wait times, queue times)
  - Worker utilization tracking
  - Error frequency analysis
  - SQLite persistence for historical data
  - Interactive visualizations with Plotly
  - Performance bottleneck identification

- **Rich Logging System**
  - Configurable log levels per component
  - Structured logging for machine parsing
  - Contextual information for debugging
  - Log rotation and management
  - Integration with popular logging frameworks

### Advanced Features
- **Callback System**
  - Pre/post execution hooks
  - Error handling callbacks
  - Progress tracking
  - Custom metrics collection
  - State management hooks

- **Smart Scheduling**
  - Priority queues for tasks
  - Deadline-aware scheduling
  - Resource-aware task distribution
  - Adaptive batch sizing
  - Dynamic worker scaling

- **Fault Tolerance**
  - Automatic retries with backoff
  - Circuit breaker pattern
  - Fallback strategies
  - Dead letter queues
  - Task timeout handling

- **Resource Management**
  - Memory usage monitoring
  - CPU utilization tracking
  - Network bandwidth control
  - Disk I/O rate limiting
  - Resource quotas per task

### Developer Experience
- **CLI Tools**
  - Task monitoring dashboard
  - Performance profiling
  - Configuration management
  - Log analysis utilities
  - Telemetry visualization

### Enterprise Features
- **Integration**
  - Distributed tracing (OpenTelemetry)
  - Metrics export (Prometheus)
  - Log aggregation (ELK Stack)

Want to contribute? Check out our [CONTRIBUTING.md](CONTRIBUTING.md) guide!

## API Reference

### @parallel Decorator

```python
@parallel(
    max_workers: int = None,          # Maximum workers (default: based on CPU)
    batch_size: int = None,           # Items per batch (default: all at once)
    rate_limit: Union[                # Rate limiting configuration
        float,                        # - Operations per second
        Tuple[float, str],           # - (count, interval)
        RateLimit                     # - RateLimit object
    ] = None,
    executor_type: str = "thread",    # "thread" or "process"
    prewarm: bool = False            # Prewarm workers
)
```

### RateLimit Class

```python
class RateLimit:
    def __init__(self, count: float, interval: str = "second"):
        """
        Args:
            count: Operations allowed per interval
            interval: "second", "minute", or "hour"
        """
```

## Contributing

Contributions are welcome! Please check out our [Contributing Guide](CONTRIBUTING.md).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.