"""Test cases for decorator configuration integration.

This module contains tests for how the parallel decorator interacts with
the configuration system, particularly focusing on configuration inheritance
and overrides.
"""

import pytest

from pyarallel import parallel
from pyarallel.config_manager import ConfigManager


@pytest.fixture
def config_manager():
    """Fixture providing a clean ConfigManager instance with test defaults."""
    manager = ConfigManager()

    # Set up test defaults
    manager.update_config(
        {
            "execution": {
                "default_max_workers": 4,
                "default_executor_type": "thread",
                "default_batch_size": 10,
            }
        }
    )

    yield manager
    manager.reset()


def test_decorator_uses_global_defaults(config_manager):
    """Test that parallel decorator uses global configuration defaults."""

    @parallel()
    def sample_function(x):
        return x * 2

    # The decorator should have picked up the global defaults
    assert sample_function.max_workers == 4
    assert sample_function.executor_type == "thread"
    assert sample_function.batch_size == 10

    # Test actual parallel execution
    result = sample_function([1, 2, 3, 4, 5])
    assert result == [2, 4, 6, 8, 10]


class TestDecoratorOverrides:
    @staticmethod
    def sample_function(x):
        return x * 2

    def test_decorator_overrides_global_defaults(self, config_manager):
        """Test that decorator arguments override global configuration defaults."""

        decorated_func = parallel(
            max_workers=6, executor_type="process", batch_size=20
        )(self.sample_function)

        # Verify decorator args override global defaults
        assert decorated_func.max_workers == 6  # Override from default 4
        assert (
            decorated_func.executor_type == "process"
        )  # Override from default "thread"
        assert decorated_func.batch_size == 20  # Override from default 10

        # Test actual parallel execution
        result = decorated_func([1, 2, 3, 4, 5])
        assert result == [2, 4, 6, 8, 10]


def test_inheritance_rules(config_manager):
    """Test that nested configuration values are properly inherited."""
    # Set up nested configuration
    config_manager.update_config(
        {"execution": {"default_max_workers": 8, "nested_setting": {"value": "test"}}}
    )

    @parallel()
    def sample_function(x):
        return x * 2

    # Verify nested settings are inherited
    assert sample_function.max_workers == 8

    # Update nested config and verify changes propagate
    config_manager.update_config({"execution": {"default_max_workers": 12}})

    @parallel()
    def another_function(x):
        return x * 3

    assert another_function.max_workers == 12


def test_runtime_config_warnings():
    """Test that appropriate warnings are raised for problematic configurations."""
    import warnings

    # Test warning for excessive workers
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        @parallel(max_workers=1000)  # Unreasonably high number of workers
        def excessive_workers(x):
            return x

        assert len(w) > 0
        assert issubclass(w[-1].category, RuntimeWarning)
        assert "high number of workers" in str(w[-1].message)

    # Test warning for incompatible settings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        @parallel(
            executor_type="process", batch_size=1
        )  # Process pool with small batch size
        def inefficient_config(x):
            return x

        assert len(w) > 0
        assert issubclass(w[-1].category, RuntimeWarning)
        assert "inefficient configuration" in str(w[-1].message)
