#!/usr/bin/env python3
"""
Configuration Management
========================

This example demonstrates how to configure pyarallel globally
and override settings at the decorator level.

Run with: python examples/05_configuration.py
"""

from pyarallel import parallel, config
import time


print("Example 1: Default configuration")
print("=" * 50)

# Check default configuration
current_config = config.get_config()
print("Current configuration:")
print(f"  Max workers: {current_config.max_workers}")
print(f"  Timeout: {current_config.timeout}")
print()


# Example 2: Update global configuration
# ---------------------------------------
print("Example 2: Updating global configuration")
print("=" * 50)

# Update global defaults
config.update_config({
    "max_workers": 8,
    "execution": {
        "default_executor_type": "thread",
        "default_batch_size": 50
    }
})

print("Updated configuration:")
print(f"  Max workers: {config.get_config().max_workers}")
print(f"  Default executor: {config.execution.default_executor_type}")
print(f"  Default batch size: {config.execution.default_batch_size}")
print()


# Example 3: Functions using global config
# -----------------------------------------
@parallel  # Uses global configuration (8 workers)
def process_with_defaults(item):
    """Process using global configuration."""
    time.sleep(0.01)
    return item * 2


print("Example 3: Using global configuration")
print("=" * 50)

items = list(range(20))
results = process_with_defaults(items)
print(f"Processed {len(results)} items using global config (8 workers)")
print()


# Example 4: Override configuration at decorator level
# -----------------------------------------------------
@parallel(max_workers=4, batch_size=10)  # Override global settings
def process_with_override(item):
    """Process with decorator-level configuration."""
    time.sleep(0.01)
    return item * 3


print("Example 4: Overriding global configuration")
print("=" * 50)

items = list(range(20))
results = process_with_override(items)
print(f"Processed {len(results)} items using decorator config (4 workers, batch 10)")
print()


# Example 5: Category-specific updates
# -------------------------------------
print("Example 5: Category-specific configuration")
print("=" * 50)

# Update execution settings
config.update_execution(default_max_workers=16)

# Update rate limiting defaults
config.update_rate_limiting(rate=100, interval="minute")

print("Updated execution config:")
print(f"  Default max workers: {config.execution.default_max_workers}")
print()
print("Updated rate limiting config:")
print(f"  Rate: {config.rate_limiting.rate}")
print(f"  Interval: {config.rate_limiting.interval}")
print()


# Example 6: Configuration hierarchy
# -----------------------------------
print("Example 6: Configuration hierarchy")
print("=" * 50)

print("Configuration precedence (highest to lowest):")
print("  1. Decorator arguments")
print("  2. Environment variables (PYARALLEL_*)")
print("  3. Global configuration (config.update_config())")
print("  4. Built-in defaults")
print()

# Demonstrate hierarchy
config.update_config({"max_workers": 8})  # Global config

@parallel(max_workers=4)  # Decorator override
def demo_hierarchy(item):
    return item

print("Global config sets max_workers=8")
print("Decorator sets max_workers=4")
print("Result: Decorator wins (max_workers=4)")
print()


# Example 7: Environment variable configuration
# ----------------------------------------------
print("Example 7: Environment variable support")
print("=" * 50)

print("Set environment variables to configure pyarallel:")
print()
print("  export PYARALLEL_MAX_WORKERS=16")
print("  export PYARALLEL_TIMEOUT=60.0")
print("  export PYARALLEL_DEBUG=true")
print("  export PYARALLEL_LOG_LEVEL=INFO")
print()
print("Environment variables are loaded at import time")
print("and override global configuration.")
print()


# Example 8: Accessing configuration values
# ------------------------------------------
print("Example 8: Accessing configuration")
print("=" * 50)

# Get full configuration
full_config = config.get_config()

print("Full configuration structure:")
print(f"  Max workers: {full_config.max_workers}")
print(f"  Timeout: {full_config.timeout}")
print(f"  Debug: {full_config.debug}")
print(f"  Log level: {full_config.log_level}")
print()

# Access nested configuration
print("Execution settings:")
print(f"  Default max workers: {full_config.execution.default_max_workers}")
print(f"  Default executor type: {full_config.execution.default_executor_type}")
print(f"  Default batch size: {full_config.execution.default_batch_size}")
print()


print("Summary")
print("=" * 50)
print("Configuration best practices:")
print()
print("1. Set global defaults at application startup:")
print("   config.update_config({...})")
print()
print("2. Use environment variables for deployment:")
print("   PYARALLEL_MAX_WORKERS=8 python app.py")
print()
print("3. Override at decorator level for specific needs:")
print("   @parallel(max_workers=4, batch_size=10)")
print()
print("4. Use category-specific updates for clarity:")
print("   config.update_execution(max_workers=16)")
print("   config.update_rate_limiting(rate=100)")
