"""Test cases for environment variable configuration.

This module contains tests for loading and parsing environment variables
with the PYARALLEL_ prefix into configuration values.
"""

import os
import logging
from pyarallel.config_manager import ConfigManager
from pyarallel.env_config import load_env_vars

logger = logging.getLogger("pyarallel")
logger.setLevel(logging.DEBUG)

def test_env_var_loading(clean_env):
    """Test that configuration loads correctly from environment variables."""
    os.environ["PYARALLEL_MAX_WORKERS"] = "6"
    os.environ["PYARALLEL_TIMEOUT"] = "45.0"
    os.environ["PYARALLEL_DEBUG"] = "true"
    
    config = load_env_vars()
    
    assert config["max_workers"] == 6
    assert config["timeout"] == 45.0
    assert config["debug"] is True

def test_env_var_types(clean_env):
    """Test that environment variables are properly typed."""
    os.environ["PYARALLEL_MAX_WORKERS"] = "invalid"
    os.environ["PYARALLEL_TIMEOUT"] = "not_a_float"
    os.environ["PYARALLEL_DEBUG"] = "0"
    
    config = load_env_vars()
    
    assert "max_workers" not in config  # Invalid int should be skipped
    assert "timeout" not in config  # Invalid float should be skipped
    assert config["debug"] is False

def test_env_var_prefix(clean_env):
    """Test that only PYARALLEL_* variables are loaded."""
    os.environ["PYARALLEL_MAX_WORKERS"] = "6"
    os.environ["OTHER_VAR"] = "value"
    
    config = load_env_vars()
    
    assert "max_workers" in config
    assert len(config) == 1  # Only PYARALLEL_* var should be loaded

def test_config_manager_env_integration(clean_env):
    """Test ConfigManager integration with environment variables."""
    logger.setLevel(logging.DEBUG)
    logger.debug("Setting environment variables for test")
    os.environ["PYARALLEL_MAX_WORKERS"] = "6"
    os.environ["PYARALLEL_TIMEOUT"] = "45.0"
    logger.debug(f"Environment variables set: MAX_WORKERS=6, TIMEOUT=45.0")
    
    logger.debug("Creating ConfigManager instance")
    manager = ConfigManager()
    config = manager.get_config()
    logger.debug(f"Retrieved config: {config}")
    
    assert config.max_workers == 6
    assert config.timeout == 45.0