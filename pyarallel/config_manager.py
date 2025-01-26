"""Configuration manager module for pyarallel.

This module provides a thread-safe singleton configuration manager that handles
all configuration operations and maintains the global configuration state.
"""

from threading import Lock
from typing import Any, Dict, Optional, Type, TypeVar

from .config import PyarallelConfig

T = TypeVar('T', bound='ConfigManager')


class ConfigManager:
    """Singleton configuration manager for pyarallel.

    This class ensures thread-safe access to configuration settings and provides
    methods for updating and retrieving configuration values.
    """
    _instance: Optional[T] = None
    _lock: Lock = Lock()
    _config: Optional[PyarallelConfig] = None

    def __new__(cls: Type[T]) -> T:
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._config = PyarallelConfig()
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
        with self._lock:
            current_config = self._config.model_dump()
            # Handle nested execution structure
            if "execution" in updates:
                for key, value in updates["execution"].items():
                    updates[key] = value
                del updates["execution"]
            
            # Validate max_workers before merging
            if "max_workers" in updates and updates["max_workers"] < 1:
                updates["max_workers"] = 1
                
            merged_config = self._deep_merge(current_config, updates)
            self._config = PyarallelConfig.from_dict(merged_config)

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge two dictionaries.

        Args:
            base: The base dictionary
            updates: The dictionary with updates

        Returns:
            Dict[str, Any]: The merged dictionary
        """
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def reset(self) -> None:
        """Reset the configuration to default values.

        This method resets the configuration to its default state by creating
        a new PyarallelConfig instance.
        """
        with self._lock:
            self._config = PyarallelConfig()