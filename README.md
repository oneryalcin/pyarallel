# Pyarallel

[![Docs](https://img.shields.io/badge/docs-live-brightgreen)](https://oneryalcin.github.io/pyarallel/) [![PyPI version](https://img.shields.io/pypi/v/pyarallel)](https://pypi.org/project/pyarallel/) [![PyPI Downloads](https://static.pepy.tech/badge/pyarallel/month)](https://pepy.tech/project/pyarallel)

A powerful, feature-rich parallel execution library for Python that makes concurrent programming easy and efficient.

## Features

- **Simple Decorator-Based API**: Just add `@parallel` to your functions
- **Flexible Parallelism**: Choose between threads (I/O-bound) and processes (CPU-bound)
- **Smart Rate Limiting**: Control execution rates with per-second, per-minute, or per-hour limits
- **Batch Processing**: Handle large datasets efficiently with automatic batching
- **Method Support**: Works with regular functions, instance methods, class methods, and static methods
- **Performance Optimized**: 
  - Automatic worker pool reuse
  - Optional worker prewarming for latency-critical applications
  - Smart defaults based on your system
- **Production Ready**:
  - Thread-safe implementation
  - Memory-efficient with automatic cleanup
  - Comprehensive error handling

## Documentation

- **[Full Documentation](https://oneryalcin.github.io/pyarallel/)** - Comprehensive guides and API reference
- **[Examples](examples/)** - Runnable code examples for common use cases

## Installation

```bash
pip install pyarallel
```

## Quick Start

```python
from pyarallel import parallel

# Basic parallel processing - decorate a function that processes a single item
@parallel(max_workers=4)
def fetch_url(url):
    return requests.get(url).json()

# Process multiple URLs in parallel by passing a list
urls = ["http://api1.com", "http://api2.com"]
results = fetch_url(urls)  # Returns a list of JSON results

# Rate-limited CPU-intensive task
@parallel(
    max_workers=4,
    executor_type="process",
    rate_limit=(100, "minute")  # 100 ops/minute
)
def process_image(image):
    return heavy_processing(image)

# Memory-efficient batch processing
@parallel(max_workers=4, batch_size=10)
def analyze_text(text):
    return text_analysis(text)
```

## How It Works

Pyarallel uses a simple mental model that makes parallel processing intuitive:

### 1. Write Your Function for ONE Item

```python
def process_number(n):
    """This function handles a SINGLE number."""
    return n * 2
```

### 2. Add the @parallel Decorator

```python
@parallel(max_workers=4)
def process_number(n):
    """Now it can process items in parallel!"""
    return n * 2
```

### 3. Pass MANY Items, Get MANY Results

```python
# Pass a list of items
numbers = [1, 2, 3, 4, 5]
results = process_number(numbers)  # Returns: [2, 4, 6, 8, 10]

# Single items work too (returns a list with one item)
result = process_number(42)  # Returns: [84]
```

**Key Points:**
- Write functions that process a **single item**
- Pass a **list/tuple** to process multiple items in parallel
- Always returns a **list** of results (even for single items)
- Results maintain the **same order** as inputs

## Usage Examples

### Basic Function
```python
from pyarallel import parallel

@parallel
def process_item(x):
    return x * 2

# Single item: Returns [2]
result = process_item(1)  

# Multiple items: Returns [2, 4, 6]
results = process_item([1, 2, 3])  
```

### Instance Methods
```python
class DataProcessor:
    def __init__(self, multiplier):
        self.multiplier = multiplier
    
    @parallel
    def process(self, x):
        # Process a single item
        return x * self.multiplier

processor = DataProcessor(3)
# Single item: Returns [15]
result = processor.process(5)  

# Multiple items: Returns [3, 6, 9]
results = processor.process([1, 2, 3])  
```

### Class Methods
```python
class StringFormatter:
    @classmethod
    @parallel
    def format_item(cls, item):
        # Process a single item
        return f"Formatted-{item}"

# Single item: Returns ["Formatted-x"]
result = StringFormatter.format_item("x")  

# Multiple items: Returns ["Formatted-a", "Formatted-b", "Formatted-c"]
results = StringFormatter.format_item(['a', 'b', 'c'])
```

### Static Methods
```python
class MathUtils:
    @staticmethod
    @parallel
    def square(x):
        # Process a single item
        return x**2

# Single item: Returns [4]
result = MathUtils.square(2)  

# Multiple items: Returns [1, 4, 9]
results = MathUtils.square([1, 2, 3])
```

## Advanced Usage

### Rate Limiting

Control how many operations can be performed in a given time period:

```python
# 10 operations per second
@parallel(rate_limit=10)

# 100 operations per minute
@parallel(rate_limit=(100, "minute"))

# 1000 operations per hour
@parallel(rate_limit=(1000, "hour"))

# Using a RateLimit object for more control
from pyarallel import RateLimit
@parallel(rate_limit=RateLimit(count=5, interval="second"))
```

### Process vs Thread Pools

Choose the right parallelism type based on your workload:

```python
# Thread pool (default) - good for I/O bound tasks
@parallel(executor_type="thread")

# Process pool - good for CPU bound tasks
@parallel(executor_type="process")
```

### Batch Processing

Efficiently handle large datasets by processing in batches:

```python
# Process in batches of 100 items
@parallel(batch_size=100)
def process_large_dataset(item):
    return heavy_computation(item)

# Call with a very large list
results = process_large_dataset(large_list)
```

### Worker Prewarming

Reduce latency for time-critical applications:

```python
@parallel(max_workers=10, prewarm=True)
def latency_critical_function(item):
    return process(item)
```

## Configuration System

Pyarallel features a robust configuration system built on a solid foundation, offering type validation, environment variable support, and thread-safe configuration management.

### Basic Configuration

```python
from pyarallel import config

# Update global default configuration
config.update({
    "max_workers": 8,
    "execution": {
        "default_executor_type": "process",
        "default_batch_size": 50
    },
    "rate_limiting": {
        "rate": 500,
        "interval": "minute"
    },
    "error_handling": {
        "retry_count": 3
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

Override configuration with environment variables:

```bash
export PYARALLEL_MAX_WORKERS=16
export PYARALLEL_EXECUTION__DEFAULT_EXECUTOR_TYPE=process
export PYARALLEL_RATE_LIMITING__RATE=1000
```

## License

This project is licensed under the MIT License - see the LICENSE.md file for details.