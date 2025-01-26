# Pyarallel

A powerful, feature-rich parallel execution library for Python that makes concurrent programming easy and efficient.

## Overview

Pyarallel simplifies parallel processing in Python by providing a decorator-based API that handles both I/O-bound and CPU-bound tasks efficiently. With features like automatic worker pool management, rate limiting, and batch processing, it's designed to make concurrent programming accessible while maintaining high performance.

## Key Features

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

## Quick Example

```python
from pyarallel import parallel

# Basic parallel processing
@parallel(max_workers=4)
def fetch_url(url: str) -> dict:
    return requests.get(url).json()

# Process multiple URLs in parallel
urls = ["http://api1.com", "http://api2.com"]
results = fetch_url(urls)
```

## Installation

```bash
pip install pyarallel
```

## Documentation

Explore our comprehensive documentation to learn more:

- [Quick Start Guide](getting-started/quickstart.md)
- [Basic Usage](user-guide/basic-usage.md)
- [Advanced Features](user-guide/advanced-features.md)
- [Configuration](user-guide/configuration.md)
- [Best Practices](user-guide/best-practices.md)

## Contributing

We welcome contributions! Check out our [Contributing Guide](development/contributing.md) to get started.

## License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/oneryalcin/pyarallel/blob/main/LICENSE.md) file for details.