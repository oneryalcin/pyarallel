"""Environment variable configuration module for pyarallel.

This module provides functionality for loading and parsing environment
variables with the PYARALLEL_ prefix into configuration values.
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

def load_env_vars() -> Dict[str, Any]:
    """Load configuration from environment variables.

    Returns:
        Dict[str, Any]: Configuration dictionary from environment variables
    """
    config = {}
    logger.debug("Starting to load environment variables")
    
    # Map of environment variable names to config keys
    env_map = {
        "PYARALLEL_MAX_WORKERS": "max_workers",
        "PYARALLEL_TIMEOUT": "timeout",
        "PYARALLEL_DEBUG": "debug",
        "PYARALLEL_LOG_LEVEL": "log_level",
        "PYARALLEL_MEMORY_LIMIT": "memory_limit",
        "PYARALLEL_CPU_AFFINITY": "cpu_affinity"
    }

    for env_var, config_key in env_map.items():
        if env_var in os.environ:
            value = os.environ[env_var]
            logger.debug(f"Found environment variable {env_var} = {value}")
            
            # Type conversion based on config key
            if config_key in ("max_workers", "memory_limit"):
                try:
                    config[config_key] = int(value)
                except ValueError:
                    logger.warning(f"Invalid integer value for {env_var}: {value}")
            elif config_key == "timeout":
                try:
                    config[config_key] = float(value)
                except ValueError:
                    logger.warning(f"Invalid float value for {env_var}: {value}")
            elif config_key == "debug":
                config[config_key] = value.lower() in ("true", "1", "yes")
            elif config_key == "cpu_affinity":
                config[config_key] = value.lower() in ("true", "1", "yes")
            else:
                config[config_key] = value

    logger.debug(f"Final environment config: {config}")
    return config