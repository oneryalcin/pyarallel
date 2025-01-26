# API Reference

## Configuration API

### ConfigManager

Singleton class for managing global configuration.

```python
from pyarallel import ConfigManager

config_manager = ConfigManager.get_instance()
```

#### Methods

- `get_config()`: Get current configuration
- `update_config(config: dict)`: Update configuration with new values
- `reset_config()`: Reset to default configuration

#### Configuration Options

```python
{
    "execution": {
        "default_max_workers": 4,
        "default_executor_type": "thread",
        "default_batch_size": 10
    }
}
```

#### Examples

```python
# Get config manager instance
config_manager = ConfigManager.get_instance()

# Update global configuration
config_manager.update_config({
    "execution": {
        "default_max_workers": 8,
        "default_executor_type": "process"
    }
})

# Get current configuration
config = config_manager.get_config()
```