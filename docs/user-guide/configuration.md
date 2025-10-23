# Configuration

Pyarallel features a robust configuration system built on lightweight dataclasses, offering type validation, environment variable support, and thread-safe configuration management.

## Configuration Schema

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

## Basic Configuration

```python
from pyarallel import config

# The config object is a thread-safe singleton configuration manager

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

## Environment Variables

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

## Best Practices

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

## Runtime Configuration Warnings

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

## Configuration Inheritance

Pyarallel uses a hierarchical configuration system:

1. **Default Values**: Built-in defaults (4 workers, thread executor, batch size 10)
2. **Global Configuration**: Set via config object
3. **Environment Variables**: Override global config
4. **Decorator Arguments**: Highest precedence, override all other settings

```python
from pyarallel import config

# Global configuration (lowest precedence)
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