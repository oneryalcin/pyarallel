"""Test cases for the configuration manager module.

This module contains tests for the singleton pattern, thread safety,
and merge strategy of the configuration manager.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pyarallel.config_manager import ConfigManager


def test_singleton_pattern(config_manager):
    """Test that ConfigManager maintains singleton pattern."""
    config1 = ConfigManager()
    config2 = ConfigManager()
    assert config1 is config2


def test_thread_safety(config_manager):
    """Test thread-safe access to configuration."""
    def update_config(i):
        manager = config_manager
        manager.update_config({"max_workers": i})
        return manager.get_config().max_workers

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(update_config, range(10)))
    
    # Verify the final state is consistent
    assert config_manager.get_config().max_workers == 9


def test_partial_update(config_manager):
    """Test that partial updates don't affect other values."""
    # Initial state
    initial_config = config_manager.get_config()
    initial_timeout = initial_config.timeout
    
    # Update only max_workers
    config_manager.update_config({"max_workers": 8})
    
    # Verify timeout remains unchanged
    updated_config = config_manager.get_config()
    assert updated_config.timeout == initial_timeout
    assert updated_config.max_workers == 8


def test_nested_merge(config_manager):
    """Test deep merging of nested configuration values."""
    # Update with nested structure
    update = {
        "execution": {
            "max_workers": 8,
            "timeout": 60.0
        }
    }
    
    config_manager.update_config(update)
    config = config_manager.get_config()
    
    assert config.max_workers == 8
    assert config.timeout == 60.0