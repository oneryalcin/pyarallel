"""Configuration manager module for pyarallel.

This module provides a thread-safe singleton configuration manager that handles
all configuration operations and maintains the global configuration state.
"""

import logging
import time
from threading import RLock
from typing import Any, Dict, Optional, Type, TypeVar

from .config import PyarallelConfig
from .env_config import load_env_vars

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="ConfigManager")


class ConfigManager:
    """Singleton configuration manager for pyarallel.

    This class ensures thread-safe access to configuration settings and provides
    methods for updating and retrieving configuration values.
    """

    _instance: Optional[T] = None
    _lock: RLock = RLock()
    _config: Optional[PyarallelConfig] = None
    _field_versions: Dict[str, int]

    def __new__(cls: Type[T]) -> T:
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    # Initialize with default config
                    config_dict = PyarallelConfig().model_dump()
                    logger.debug(f"Default config: {config_dict}")

                    # Load and apply environment variables
                    env_config = load_env_vars()
                    logger.debug(f"Loaded environment config: {env_config}")

                    # Update config with environment variables
                    if env_config:
                        logger.debug("Updating config with environment variables")
                        config_dict = {
                            **config_dict,
                            **env_config,
                        }  # Use dictionary unpacking for proper update
                        logger.debug(f"Updated config: {config_dict}")

                    cls._instance._config = PyarallelConfig(
                        **config_dict
                    )  # Use direct instantiation
                    logger.debug(f"Final config: {cls._instance._config}")
                    cls._instance._field_versions = {}
        return cls._instance

    @classmethod
    def get_instance(cls: Type[T]) -> T:
        """Get the singleton instance of the configuration manager.

        Returns:
            ConfigManager: The singleton instance
        """
        return cls()

    def get_config(self) -> PyarallelConfig:
        """Get the current configuration.

        Returns:
            PyarallelConfig: The current configuration
        """
        return self._config

    def update_config(self, updates: Dict[str, Any]) -> None:
        """Update the configuration with new values.

        This method implements a merge strategy that allows partial updates
        while preserving existing values.

        Args:
            updates: Dictionary containing the configuration updates
        """
        update_id = time.monotonic_ns()
        with self._lock:
            filtered_updates = self._filter_stale_updates(updates, update_id)
            if not filtered_updates:
                logger.debug("All updates were stale; skipping")
                return
            current_config = self._config.model_dump()
            logger.debug(f"Current config before update: {current_config}")
            logger.debug(f"Incoming updates: {updates}")
            logger.debug(f"Filtered updates: {filtered_updates}")

            # Validate max_workers before merging
            if "max_workers" in filtered_updates and filtered_updates["max_workers"] < 1:
                filtered_updates["max_workers"] = 1

            # Use deep merge for nested updates
            merged_config = self._deep_merge(current_config, filtered_updates)
            logger.debug(f"Merged config: {merged_config}")
            self._config = PyarallelConfig.from_dict(merged_config)
            logger.debug(f"Final config after update: {self._config}")

    def _filter_stale_updates(
        self, updates: Dict[str, Any], update_id: int
    ) -> Dict[str, Any]:
        """Drop updates that are older than the last applied value."""

        if not hasattr(self, "_field_versions"):
            self._field_versions = {}

        filtered: Dict[str, Any] = {}
        for key, value in updates.items():
            if key == "max_workers":
                if self._should_apply("max_workers", update_id):
                    filtered[key] = value
            elif key == "execution" and isinstance(value, dict):
                exec_updates: Dict[str, Any] = {}
                for exec_key, exec_value in value.items():
                    if exec_key == "max_workers":
                        if self._should_apply("max_workers", update_id):
                            exec_updates[exec_key] = exec_value
                    elif exec_key == "timeout":
                        if self._should_apply("timeout", update_id):
                            exec_updates[exec_key] = exec_value
                    else:
                        exec_updates[exec_key] = exec_value
                if exec_updates:
                    filtered[key] = exec_updates
            elif key == "timeout":
                if self._should_apply("timeout", update_id):
                    filtered[key] = value
            else:
                filtered[key] = value
        return filtered

    def _should_apply(self, field: str, update_id: int) -> bool:
        last_update = self._field_versions.get(field, -1)
        if update_id >= last_update:
            self._field_versions[field] = update_id
            return True
        return False

    def _deep_merge(
        self, base: Dict[str, Any], updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Recursively merge two configuration dictionaries with special handling for execution settings.

        This method implements a specialized merge strategy for configuration dictionaries that:
        1. Maintains both top-level and nested execution settings for backward compatibility
        2. Ensures critical nested structures (execution, rate_limiting, error_handling, monitoring) always exist
        3. Performs recursive merging for nested dictionaries
        4. Handles direct value assignments for non-dictionary values

        Special Cases:
        - Execution Settings: When merging execution settings, the method maintains both:
          * Top-level settings (max_workers, timeout) for backward compatibility
          * Nested settings under the 'execution' key for new code
        - Critical Structures: The method ensures critical structures (execution, rate_limiting,
          error_handling, monitoring) always exist with their default values

        Args:
            base: The base configuration dictionary containing current settings
            updates: The dictionary containing configuration updates to apply

        Returns:
            Dict[str, Any]: A new dictionary containing the merged configuration

        Example:
            base = {'max_workers': 4, 'execution': {'timeout': 30}}
            updates = {'execution': {'max_workers': 8}}
            result = _deep_merge(base, updates)
            # Results in:
            # {'max_workers': 8, 'execution': {'max_workers': 8, 'timeout': 30}}
        """
        logger.debug(f"Deep merging - Base: {base}")
        logger.debug(f"Deep merging - Updates: {updates}")
        result = base.copy()

        # Initialize critical nested structures
        self._ensure_critical_structures(result)

        for key, value in updates.items():
            logger.debug(f"Processing key: {key}, value: {value}")

            if self._is_execution_update(key, value):
                self._handle_execution_settings(result, value)
            elif self._is_nested_dict_update(key, value, result):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
                logger.debug(f"Direct value assignment for key: {key}")

            # Re-ensure critical structures after each update
            self._ensure_critical_structures(result)
            logger.debug(f"Current result after processing {key}: {result}")

        return result

    def _ensure_critical_structures(self, config: Dict[str, Any]) -> None:
        """Ensure that critical nested structures exist in the configuration.

        Args:
            config: The configuration dictionary to initialize
        """
        critical_structures = {
            "execution": {},
            "rate_limiting": {},
            "error_handling": {"retry_count": 3},
            "monitoring": {"enabled": False},
        }
        for structure, default_value in critical_structures.items():
            if structure not in config or config[structure] is None:
                config[structure] = default_value.copy()
                logger.debug(
                    f"Initialized {structure} structure with defaults: {default_value}"
                )

    def _is_execution_update(self, key: str, value: Any) -> bool:
        """Check if the update is for execution settings.

        Args:
            key: The configuration key
            value: The value to update

        Returns:
            bool: True if this is an execution settings update
        """
        return key == "execution" and isinstance(value, dict)

    def _is_nested_dict_update(
        self, key: str, value: Any, base: Dict[str, Any]
    ) -> bool:
        """Check if the update is for a nested dictionary.

        Args:
            key: The configuration key
            value: The value to update
            base: The base configuration dictionary

        Returns:
            bool: True if this is a nested dictionary update
        """
        return key in base and isinstance(base[key], dict) and isinstance(value, dict)

    def _handle_execution_settings(
        self, config: Dict[str, Any], execution_settings: Dict[str, Any]
    ) -> None:
        """Handle special case of execution settings update.

        This method maintains both top-level and nested execution settings
        for backward compatibility.

        Args:
            config: The configuration dictionary to update
            execution_settings: The execution settings to apply
        """
        # Update top-level settings for backward compatibility
        if "max_workers" in execution_settings:
            config["max_workers"] = execution_settings["max_workers"]
            logger.debug(
                f"Set top-level max_workers: {execution_settings['max_workers']}"
            )
        if "timeout" in execution_settings:
            config["timeout"] = execution_settings["timeout"]
            logger.debug(f"Set top-level timeout: {execution_settings['timeout']}")

        # Initialize execution settings if None
        if config["execution"] is None:
            config["execution"] = {}
            logger.debug("Initialized empty execution settings")

        # Merge with existing execution settings
        config["execution"] = {**config["execution"], **execution_settings}
        logger.debug(
            f"Merged execution settings in nested structure: {config['execution']}"
        )

    def reset(self) -> None:
        """Reset the configuration to default values.

        This method resets the configuration to its default state by creating
        a new PyarallelConfig instance and clearing the singleton instance.
        """
        with self._lock:
            self._config = PyarallelConfig()
            self._field_versions = {}
            type(self)._instance = None

    def set(self, key: str, value: Any) -> None:
        """Set a global configuration value using dot notation.

        Args:
            key: Configuration key in dot notation (e.g., 'execution.max_workers')
            value: Value to set
        """
        update_id = time.monotonic_ns()
        with self._lock:
            parts = key.split(".")
            current = self._config.model_dump()
            target = current
            logger.debug(f"Setting config value - Key: {key}, Value: {value}")
            logger.debug(f"Current config state: {current}")

            # Handle execution settings at top level
            if parts[0] == "execution" and len(parts) == 2:
                if parts[1] in ["max_workers", "timeout"]:
                    target[parts[1]] = value
                    logger.debug(
                        f"Setting top-level execution parameter: {parts[1]} = {value}"
                    )
                else:
                    # Create execution dictionary if needed
                    if "execution" not in target:
                        target["execution"] = {}
                    target["execution"][parts[1]] = value
                    logger.debug(
                        f"Setting nested execution parameter: {parts[1]} = {value}"
                    )
                if parts[1] in ["max_workers", "timeout"]:
                    self._field_versions[parts[1]] = update_id
            else:
                # Initialize nested configuration objects if needed
                if parts[0] == "rate_limiting" and target["rate_limiting"] is None:
                    target["rate_limiting"] = {"rate": 1000, "interval": "minute"}
                    logger.debug(
                        f"Initialized rate_limiting config: {target['rate_limiting']}"
                    )

                # Navigate to the nested location
                for part in parts[:-1]:
                    if part not in target:
                        target[part] = {}
                    target = target[part]
                    logger.debug(
                        f"Navigating to nested key: {part}, Current target: {target}"
                    )

                # Set the value
                target[parts[-1]] = value
                logger.debug(f"Set final value at {parts[-1]}: {value}")
                if parts[-1] in ["max_workers", "timeout"]:
                    self._field_versions[parts[-1]] = update_id

            logger.debug(f"Updated config state: {current}")
            self._config = PyarallelConfig.from_dict(current)
            logger.debug(f"Final config after update: {self._config}")

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        current = self._config.model_dump()
        logger.debug(f"Getting config value - Key: {key}")
        logger.debug(f"Current config state: {current}")

        # Handle execution settings specially
        if parts[0] == "execution":
            if current["execution"] is None:
                # Check if the value exists at top level
                if len(parts) == 2 and parts[1] in ["max_workers", "timeout"]:
                    logger.debug(
                        f"Retrieving top-level {parts[1]} value: {current[parts[1]]}"
                    )
                    return current[parts[1]]
                logger.debug("Execution config is None, returning default")
                return default

        # Navigate through nested structure
        for part in parts:
            if not isinstance(current, dict):
                logger.debug(f"Current value is not a dictionary: {current}")
                return default
            if part not in current:
                logger.debug(f"Key not found: {part} in {current}")
                return default
            current = current[part]
            logger.debug(f"Navigating to nested key: {part}, Current value: {current}")

        logger.debug(f"Final retrieved value: {current}")
        return current

    def set_execution(self, **kwargs) -> None:
        """Update execution-specific settings.

        Args:
            **kwargs: Execution configuration parameters
        """
        with self._lock:
            updates = {"execution": kwargs}
            self.update_config(updates)

    def set_rate_limiting(self, **kwargs) -> None:
        """Update rate limiting settings.

        Args:
            **kwargs: Rate limiting configuration parameters
        """
        with self._lock:
            updates = {"rate_limiting": kwargs}
            self.update_config(updates)

    def set_error_handling(self, **kwargs) -> None:
        """Update error handling settings.

        Args:
            **kwargs: Error handling configuration parameters
        """
        with self._lock:
            updates = {"error_handling": kwargs}
            self.update_config(updates)

    def set_monitoring(self, **kwargs) -> None:
        """Update monitoring settings.

        Args:
            **kwargs: Monitoring configuration parameters
        """
        with self._lock:
            updates = {"monitoring": kwargs}
            self.update_config(updates)

    # Convenience aliases for better DevX
    def update(self, updates: Dict[str, Any]) -> None:
        """Alias for update_config() for cleaner API.

        Args:
            updates: Dictionary containing the configuration updates
        """
        self.update_config(updates)

    def update_execution(self, **kwargs) -> None:
        """Alias for set_execution() for consistent naming.

        Args:
            **kwargs: Execution configuration parameters
        """
        self.set_execution(**kwargs)

    def update_rate_limiting(self, **kwargs) -> None:
        """Alias for set_rate_limiting() for consistent naming.

        Args:
            **kwargs: Rate limiting configuration parameters
        """
        self.set_rate_limiting(**kwargs)

    def update_error_handling(self, **kwargs) -> None:
        """Alias for set_error_handling() for consistent naming.

        Args:
            **kwargs: Error handling configuration parameters
        """
        self.set_error_handling(**kwargs)

    def update_monitoring(self, **kwargs) -> None:
        """Alias for set_monitoring() for consistent naming.

        Args:
            **kwargs: Monitoring configuration parameters
        """
        self.set_monitoring(**kwargs)

    # Property accessors for cleaner attribute access
    @property
    def execution(self):
        """Get execution configuration.

        Returns:
            ExecutionConfig: The execution configuration object
        """
        return self.get_config().execution

    @property
    def rate_limiting(self):
        """Get rate limiting configuration.

        Returns:
            RateLimitingConfig: The rate limiting configuration object
        """
        return self.get_config().rate_limiting

    @property
    def error_handling(self):
        """Get error handling configuration.

        Returns:
            ErrorHandlingConfig: The error handling configuration object
        """
        return self.get_config().error_handling

    @property
    def monitoring(self):
        """Get monitoring configuration.

        Returns:
            MonitoringConfig: The monitoring configuration object
        """
        return self.get_config().monitoring
