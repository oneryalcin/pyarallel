"""Configuration module for pyarallel.

This module provides a configuration system using Pydantic for schema validation,
with support for loading configurations from different sources and thread-safe operations.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field, ConfigDict


class ExecutionConfig(BaseModel):
    """Execution configuration settings."""
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
    max_workers: Optional[int] = 4
    timeout: Optional[float] = 30.0
    
    model_config = ConfigDict(extra="allow")  # Allow dynamic fields

class RateLimitingConfig(BaseModel):
    """Rate limiting configuration settings."""
    rate: int = Field(
        default=1000,
        description="Rate limit per interval",
        ge=0
    )
    interval: str = Field(
        default="minute",
        description="Rate limit interval"
    )

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
    execution: Optional[ExecutionConfig] = Field(
        default=None,
        description="Nested execution settings"
    )

    # Rate limiting settings
    rate_limiting: Optional[RateLimitingConfig] = Field(
        default=None,
        description="Rate limiting settings"
    )

    # Error handling settings
    error_handling: Dict[str, Any] = Field(
        default_factory=lambda: {"retry_count": 3},
        description="Error handling settings"
    )

    # Monitoring settings
    monitoring: Dict[str, Any] = Field(
        default_factory=lambda: {"enabled": False},
        description="Monitoring settings"
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