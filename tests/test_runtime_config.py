"""Test cases for runtime configuration API features.

This module contains tests for dot notation access, category-specific setters,
and configuration validation hooks.
"""

import logging

logger = logging.getLogger("pyarallel")
logger.setLevel(logging.DEBUG)


def test_dot_notation_get(nested_config):
    """Test that dot notation access works for getting nested config values."""
    assert nested_config.get("execution.max_workers") == 8
    assert nested_config.get("rate_limiting.rate") == 1000
    assert nested_config.get("nonexistent.key", "default") == "default"


def test_dot_notation_set(config_manager):
    """Test that dot notation access works for setting nested config values."""
    config_manager.set("execution.max_workers", 16)
    config_manager.set("rate_limiting.rate", 2000)

    assert config_manager.get("execution.max_workers") == 16
    assert config_manager.get("rate_limiting.rate") == 2000


def test_nested_config_merge(nested_config):
    """Test that nested configurations are merged correctly."""
    nested_config.update_config(
        {"execution": {"max_workers": 12, "new_setting": "value"}}
    )

    assert nested_config.get("execution.max_workers") == 12
    assert nested_config.get("execution.timeout") == 60.0  # Original value preserved
    assert nested_config.get("execution.new_setting") == "value"  # New value added


def test_config_validation(config_manager):
    """Test that configuration validation works correctly."""
    # Test max_workers validation (should not be less than 1)
    config_manager.update_config({"max_workers": 0})
    assert config_manager.get_config().max_workers == 1

    config_manager.update_config({"max_workers": -5})
    assert config_manager.get_config().max_workers == 1


def test_thread_safe_config_access(config_manager):
    """Test that configuration access is thread-safe."""
    import queue
    import threading

    results = queue.Queue()

    def update_and_read():
        config_manager.set("execution.max_workers", 8)
        results.put(config_manager.get("execution.max_workers"))

    threads = [threading.Thread(target=update_and_read) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Verify all threads saw consistent values
    values = set()
    while not results.empty():
        values.add(results.get())

    assert len(values) == 1  # All threads should see the same value
    assert 8 in values  # The value should be what we set


def test_category_specific_setters(category_config):
    """Test that category-specific setters work correctly."""
    logger.debug("Starting test_category_specific_setters")

    logger.debug("Getting initial config...")
    config = category_config.get_config()
    logger.debug(
        f"Initial config retrieved: max_workers={config.max_workers}, timeout={config.timeout}"
    )

    logger.debug("Testing max_workers assertion...")
    assert config.max_workers == 4
    logger.debug("max_workers assertion passed")

    logger.debug("Testing timeout assertion...")
    assert config.timeout == 30.0
    logger.debug("timeout assertion passed")

    # Test rate limiting settings
    logger.debug("Setting rate limiting configuration...")
    category_config.set_rate_limiting(rate=1000)
    logger.debug("Rate limiting configuration set")

    logger.debug("Testing rate limiting assertion...")
    rate_value = category_config.get("rate_limiting.rate")
    logger.debug(f"Retrieved rate limiting value: {rate_value}")
    assert rate_value == 1000
    logger.debug("Rate limiting assertion passed")

    # Test error handling settings
    logger.debug("Testing error handling settings...")
    retry_count = category_config.get("error_handling.retry_count")
    logger.debug(f"Retrieved retry count: {retry_count}")
    assert retry_count == 3
    logger.debug("Error handling assertion passed")

    # Test monitoring settings
    logger.debug("Testing monitoring settings...")
    monitoring_enabled = category_config.get("monitoring.enabled")
    logger.debug(f"Retrieved monitoring enabled status: {monitoring_enabled}")
    assert monitoring_enabled is True
    logger.debug("Monitoring assertion passed")

    logger.debug("All tests completed successfully")
