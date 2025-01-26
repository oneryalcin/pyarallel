# Pyarallel

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

## Configuration

Pyarallel provides a flexible configuration system that allows you to customize its behavior globally or per-function:

### Basic Configuration

```python
from pyarallel import ConfigManager

# Get the global configuration manager
config = ConfigManager.get_instance()

# Update configuration
config.update_config({
    "max_workers": 8,
    "timeout": 60.0,
    "debug": True
})
```

### Configuration Options

- **Execution Settings**
  - `max_workers`: Maximum number of worker processes/threads (default: 4)
  - `timeout`: Default timeout for parallel operations in seconds (default: 30.0)

- **Resource Management**
  - `memory_limit`: Memory limit per worker in bytes (default: None)
  - `cpu_affinity`: Enable CPU affinity for workers (default: False)

- **Logging and Debugging**
  - `debug`: Enable debug mode (default: False)
  - `log_level`: Logging level (default: "INFO")

### Environment Variables

You can configure Pyarallel using environment variables with the `PYARALLEL_` prefix:

```bash
PYARALLEL_MAX_WORKERS=4
PYARALLEL_TIMEOUT=60.0
PYARALLEL_DEBUG=true
```

### Configuration Files

Load configuration from JSON, YAML, or TOML files:

```python
from pyarallel import PyarallelConfig

# Load from file
config = PyarallelConfig.from_file("pyarallel.yaml")

# Convert to dictionary
config_dict = config.to_dict()