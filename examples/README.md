# Pyarallel Examples

This directory contains practical, runnable examples demonstrating various features of pyarallel.

## Prerequisites

Make sure pyarallel is installed:

```bash
pip install pyarallel
```

Or install from source:

```bash
cd ..
pip install -e .
```

## Examples Overview

### 01_basic_usage.py
**Learn the fundamentals**

- How to write functions for single items
- How to process multiple items in parallel
- Working with instance methods
- Understanding return values (always returns a list)

```bash
python examples/01_basic_usage.py
```

**Key Concepts:**
- Decorator-based API
- Single item → Many items pattern
- Method support (instance/class/static)

---

### 02_api_calls_with_rate_limiting.py
**Master rate limiting for API calls**

- Basic parallel API calls
- Per-second rate limiting
- Per-minute and per-hour limits
- Using the RateLimit object
- Combining batch processing with rate limits

```bash
python examples/02_api_calls_with_rate_limiting.py
```

**Key Concepts:**
- Respecting API quotas
- Three ways to specify rate limits
- Smooth request distribution

---

### 03_cpu_bound_processing.py
**Understand thread vs process parallelism**

- Comparing threads vs processes
- CPU-intensive tasks (image processing, number crunching)
- When to use `executor_type="process"`
- Bypassing Python's GIL for true parallelism

```bash
python examples/03_cpu_bound_processing.py
```

**Key Concepts:**
- Process-based parallelism for CPU tasks
- Thread-based parallelism for I/O tasks
- Performance comparisons

---

### 04_batch_processing.py
**Efficiently handle large datasets**

- Memory-efficient batch processing
- Comparing different batch sizes
- Database-style operations
- Processing datasets larger than RAM

```bash
python examples/04_batch_processing.py
```

**Key Concepts:**
- Controlling memory usage
- Choosing appropriate batch sizes
- Combining batching with other features

---

### 05_configuration.py
**Configure pyarallel globally and per-function**

- Global configuration management
- Category-specific updates
- Configuration hierarchy
- Environment variable support

```bash
python examples/05_configuration.py
```

**Key Concepts:**
- Setting global defaults
- Override at decorator level
- Environment variable configuration

---

## Quick Reference

### Basic Pattern

```python
from pyarallel import parallel

# 1. Write function for ONE item
@parallel(max_workers=4)
def process_item(item):
    return item * 2

# 2. Pass MANY items
items = [1, 2, 3, 4, 5]
results = process_item(items)  # [2, 4, 6, 8, 10]
```

### Common Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `max_workers` | Number of parallel workers | `max_workers=4` |
| `executor_type` | "thread" or "process" | `executor_type="process"` |
| `batch_size` | Items per batch | `batch_size=100` |
| `rate_limit` | Limit operations/time | `rate_limit=10` or `rate_limit=(100, "minute")` |
| `prewarm` | Start workers immediately | `prewarm=True` |

### When to Use What

**Threads (default):**
- ✓ API calls
- ✓ File I/O
- ✓ Database queries
- ✓ Network operations

**Processes:**
- ✓ CPU-intensive calculations
- ✓ Data transformations
- ✓ Image/video processing
- ✓ Scientific computing

**Batching:**
- ✓ Large datasets
- ✓ Memory constraints
- ✓ Database operations
- ✓ Rate limiting

**Rate Limiting:**
- ✓ API quotas
- ✓ Resource protection
- ✓ Smooth load distribution

---

## Running All Examples

To run all examples in sequence:

```bash
for example in examples/*.py; do
    echo "Running $example..."
    python "$example"
    echo "---"
done
```

Or selectively:

```bash
# Just the basics
python examples/01_basic_usage.py

# API examples
python examples/02_api_calls_with_rate_limiting.py

# CPU examples
python examples/03_cpu_bound_processing.py
```

---

## Modifying Examples

Feel free to modify these examples to experiment:

- Change `max_workers` to see performance differences
- Adjust `batch_size` to understand memory tradeoffs
- Compare `executor_type="thread"` vs `executor_type="process"`
- Experiment with different `rate_limit` values

---

## Need Help?

- **Documentation:** https://oneryalcin.github.io/pyarallel
- **Issues:** https://github.com/oneryalcin/pyarallel/issues
- **PyPI:** https://pypi.org/project/pyarallel

---

## Next Steps

After running these examples:

1. Read the [full documentation](https://oneryalcin.github.io/pyarallel)
2. Check out [best practices](../docs/user-guide/best-practices.md)
3. Explore [advanced features](../docs/user-guide/advanced-features.md)
4. Try pyarallel in your own projects!
