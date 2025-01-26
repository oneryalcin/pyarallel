"""Configuration module for pyarallel.

This module provides a configuration system using Pydantic for schema validation,
with support for loading configurations from different sources and thread-safe operations.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field
from pydantic_core import ValidationError


class PyarallelConfig(BaseModel):
    """Base configuration class for pyarallel.

    This class defines the configuration schema and provides validation
    using Pydantic. It includes default values and type hints for all settings.
    """

    # Execution settings
    max_workers: int = Field(
        default=4,
        description="Maximum number of worker processes/threads",
        ge=1
    )
    timeout: float = Field(
        default=30.0,
        description="Default timeout for parallel operations in seconds",
        ge=0
    )

    # Resource management
    memory_limit: Optional[int] = Field(
        default=None,
        description="Memory limit per worker in bytes, None for no limit"
    )
    cpu_affinity: bool = Field(
        default=False,
        description="Enable CPU affinity for workers"
    )

    # Logging and debugging
    debug: bool = Field(
        default=False,
        description="Enable debug mode"
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "PyarallelConfig":
        """Create a configuration instance from a dictionary.

        Args:
            config_dict: Dictionary containing configuration values

        Returns:
            PyarallelConfig instance

        Raises:
            ValidationError: If the configuration is invalid
        """
        return cls(**config_dict)

    @classmethod
    def from_file(cls, config_path: Union[str, Path]) -> "PyarallelConfig":
        """Load configuration from a file (JSON, YAML, or TOML).

        Args:
            config_path: Path to the configuration file

        Returns:
            PyarallelConfig instance

        Raises:
            FileNotFoundError: If the configuration file doesn't exist
            ValidationError: If the configuration is invalid
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load based on file extension
        if config_path.suffix == ".json":
            import json
            with open(config_path) as f:
                config_dict = json.load(f)
        elif config_path.suffix in (".yml", ".yaml"):
            import yaml
            with open(config_path) as f:
                config_dict = yaml.safe_load(f)
        elif config_path.suffix == ".toml":
            import toml
            with open(config_path) as f:
                config_dict = toml.load(f)
        else:
            raise ValueError(f"Unsupported configuration file format: {config_path.suffix}")

        return cls.from_dict(config_dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration
        """
        return self.model_dump()