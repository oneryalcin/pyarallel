import pytest
from pyarallel import parallel, RateLimit
from pyarallel.config_manager import ConfigManager

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
def batch_processor():
    """Fixture providing a function with batch processing capabilities."""
    processed = []
    
    @parallel(batch_size=2)
    def func(x):
        processed.append(x)
        return x
    
    func.processed = processed
    return func