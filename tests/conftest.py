import os
import logging
import pytest
from pyarallel import parallel, RateLimit
from pyarallel.config_manager import ConfigManager

logger = logging.getLogger("pyarallel")
logger.setLevel(logging.DEBUG)

@pytest.fixture
def config_manager():
    """Fixture providing a fresh ConfigManager instance for each test."""
    manager = ConfigManager()
    # Reset to default state
    yield manager
    manager.reset()

@pytest.fixture
def rate_limit():
    """Fixture providing a RateLimit instance for testing rate-limited operations."""
    return RateLimit(2, "second")

@pytest.fixture
def parallel_function():
    """Fixture providing a basic parallel function for testing."""
    @parallel(max_workers=2)
    def func(x):
        return x * x
    return func

def _process_pool_func(x):
    return x * x

@pytest.fixture
def process_pool_function():
    """Fixture providing a process pool function for testing CPU-bound operations."""
    return parallel(executor_type="process", max_workers=2)(_process_pool_func)

@pytest.fixture
def nested_config():
    """Fixture providing a ConfigManager instance with nested configuration."""
    
    manager = ConfigManager()
    logger.debug("ConfigManager initialized with nested configuration: %s", manager._config)

    manager.update_config({
        "execution": {
            "max_workers": 8,
            "timeout": 60.0
        },
        "rate_limiting": {
            "rate": 1000,
            "interval": "minute"
        }
    })
    logger.debug("ConfigManager updated with nested configuration: %s", manager._config)
    yield manager
    manager.reset()
    logger.debug("ConfigManager reset after nested configuration test: %s", manager._config)

@pytest.fixture
def category_config():
    """Fixture providing a ConfigManager instance with category-specific settings."""
    manager = ConfigManager()
    manager.set_execution(max_workers=4, timeout=30.0)
    manager.set_rate_limiting(rate=500, interval="second")
    manager.set_error_handling(retry_count=3)
    manager.set_monitoring(enabled=True)
    yield manager
    manager.reset()

@pytest.fixture
def clean_env():
    """Fixture to provide a clean environment for testing.
    
    This fixture cleans up environment variables and resets the ConfigManager
    before each test to ensure a fresh state.
    """
    # Store original environment
    original_env = dict(os.environ)
    
    # Reset ConfigManager to ensure fresh state
    ConfigManager.get_instance().reset()
    
    # Clean environment variables
    for key in list(os.environ.keys()):
        if key.startswith('PYARALLEL_'):
            del os.environ[key]
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def batch_processor():
    """Fixture providing a function with batch processing capabilities."""
    processed = []
    
    @parallel(batch_size=2)
    def func(x):
        processed.append(x)
        return x
    
    func.processed = processed
    return func