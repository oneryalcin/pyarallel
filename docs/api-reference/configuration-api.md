# API Reference

## Configuration API

### Simple Access (Recommended)

The easiest way to access and update configuration:

```python
from pyarallel import config

# Update global configuration
config.update_config({
    "execution": {
        "default_max_workers": 8,
        "default_executor_type": "process"
    }
})

# Access configuration using dot notation
workers = config.execution.default_max_workers

# Category-specific updates
config.update_execution(max_workers=16)
config.update_rate_limiting(rate=2000)
```

### Advanced: ConfigManager

For advanced use cases, you can import the ConfigManager class directly:

```python
from pyarallel import ConfigManager

config_manager = ConfigManager.get_instance()
```

#### Methods

- `get_config()`: Get current configuration
- `update_config(config: dict)`: Update configuration with new values
- `update_execution(**kwargs)`: Update execution settings
- `update_rate_limiting(**kwargs)`: Update rate limiting settings
- `reset_config()`: Reset to default configuration

#### Configuration Options

```python
{
    "execution": {
        "default_max_workers": 4,
        "default_executor_type": "thread",
        "default_batch_size": 10
    },
    "rate_limiting": {
        "rate": 1000,
        "interval": "minute"
    }
}
```

#### Examples

```python
from pyarallel import config

# Update global configuration
config.update_config({
    "execution": {
        "default_max_workers": 8,
        "default_executor_type": "process"
    }
})

# Get current configuration
current_config = config.get_config()
```