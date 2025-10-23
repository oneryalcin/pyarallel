"""Configuration module for pyarallel without external dependencies.

This module provides a lightweight configuration system that mirrors the
behaviour previously powered by Pydantic models. The new implementation relies
on ``dataclasses`` and a small amount of manual validation to keep runtime
requirements minimal while still providing the same API surface that the rest of
``pyarallel`` expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional, Union


def _ensure_min_int(value: Any, minimum: int) -> int:
    """Coerce *value* to ``int`` ensuring it is at least ``minimum``."""

    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected integer value, got {value!r}") from exc
    return max(ivalue, minimum)


def _ensure_non_negative_float(value: Any) -> float:
    """Coerce *value* to ``float`` ensuring it is not negative."""

    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected float value, got {value!r}") from exc
    if fvalue < 0:
        raise ValueError("Float values must be non-negative")
    return fvalue


@dataclass
class ExecutionConfig:
    """Execution configuration settings."""

    max_workers: int = 4
    timeout: float = 30.0
    default_max_workers: int = 4
    default_executor_type: str = "thread"
    default_batch_size: int = 10
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.max_workers = _ensure_min_int(self.max_workers, 1)
        self.timeout = _ensure_non_negative_float(self.timeout)
        self.default_max_workers = _ensure_min_int(self.default_max_workers, 1)
        # ``default_batch_size`` should be at least 1 as well.
        self.default_batch_size = _ensure_min_int(self.default_batch_size, 1)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionConfig":
        known_keys = {
            "max_workers",
            "timeout",
            "default_max_workers",
            "default_executor_type",
            "default_batch_size",
        }
        known = {k: data[k] for k in known_keys if k in data}
        config = cls(**known)
        config.extra = {k: v for k, v in data.items() if k not in known_keys}
        return config

    def model_dump(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.extra:
            data.update(self.extra)
        data.pop("extra", None)
        return data


@dataclass
class RateLimitingConfig:
    """Rate limiting configuration settings."""

    rate: int = 1000
    interval: str = "minute"
    extra: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.rate = _ensure_min_int(self.rate, 0)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RateLimitingConfig":
        known_keys = {"rate", "interval"}
        known = {k: data[k] for k in known_keys if k in data}
        config = cls(**known)
        config.extra = {k: v for k, v in data.items() if k not in known_keys}
        return config

    def model_dump(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.extra:
            data.update(self.extra)
        data.pop("extra", None)
        return data


@dataclass
class PyarallelConfig:
    """Base configuration class for pyarallel."""

    max_workers: int = 4
    timeout: float = 30.0
    execution: Optional[ExecutionConfig] = field(default_factory=ExecutionConfig)
    rate_limiting: Optional[RateLimitingConfig] = None
    error_handling: Dict[str, Any] = field(default_factory=lambda: {"retry_count": 3})
    monitoring: Dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    memory_limit: Optional[int] = None
    cpu_affinity: bool = False
    debug: bool = False
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.max_workers = _ensure_min_int(self.max_workers, 1)
        self.timeout = _ensure_non_negative_float(self.timeout)
        if isinstance(self.execution, dict):
            self.execution = ExecutionConfig.from_dict(self.execution)
        elif self.execution is None:
            self.execution = ExecutionConfig(
                max_workers=self.max_workers, timeout=self.timeout
            )
        if self.execution:
            # Keep top-level values aligned with nested configuration. If only
            # the top-level value was updated we propagate it down; otherwise we
            # reflect the nested configuration back up so both views stay in
            # sync.
            self.execution.max_workers = _ensure_min_int(
                self.execution.max_workers, 1
            )
            self.execution.timeout = _ensure_non_negative_float(
                self.execution.timeout
            )
            if self.max_workers != self.execution.max_workers:
                self.execution.max_workers = self.max_workers
            else:
                self.max_workers = self.execution.max_workers

            if self.timeout != self.execution.timeout:
                self.execution.timeout = self.timeout
            else:
                self.timeout = self.execution.timeout

        if isinstance(self.rate_limiting, dict):
            self.rate_limiting = RateLimitingConfig.from_dict(self.rate_limiting)

        if self.memory_limit is not None:
            self.memory_limit = _ensure_min_int(self.memory_limit, 0)

        # Normalise booleans in case non-bool truthy values are provided.
        self.cpu_affinity = bool(self.cpu_affinity)
        self.debug = bool(self.debug)

        if not isinstance(self.error_handling, dict):
            self.error_handling = {"retry_count": 3}
        if not isinstance(self.monitoring, dict):
            self.monitoring = {"enabled": False}

    def model_dump(self) -> Dict[str, Any]:
        data = {
            "max_workers": self.max_workers,
            "timeout": self.timeout,
            "execution": self.execution.model_dump() if self.execution else None,
            "rate_limiting": self.rate_limiting.model_dump()
            if self.rate_limiting
            else None,
            "error_handling": dict(self.error_handling),
            "monitoring": dict(self.monitoring),
            "memory_limit": self.memory_limit,
            "cpu_affinity": self.cpu_affinity,
            "debug": self.debug,
            "log_level": self.log_level,
        }
        return data

    def to_dict(self) -> Dict[str, Any]:
        """Compatibility helper mirroring the old Pydantic API."""

        return self.model_dump()

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "PyarallelConfig":
        execution = config_dict.get("execution")
        rate_limiting = config_dict.get("rate_limiting")
        return cls(
            max_workers=config_dict.get("max_workers", 4),
            timeout=config_dict.get("timeout", 30.0),
            execution=ExecutionConfig.from_dict(execution)
            if isinstance(execution, dict)
            else execution,
            rate_limiting=RateLimitingConfig.from_dict(rate_limiting)
            if isinstance(rate_limiting, dict)
            else rate_limiting,
            error_handling=config_dict.get("error_handling", {"retry_count": 3}),
            monitoring=config_dict.get("monitoring", {"enabled": False}),
            memory_limit=config_dict.get("memory_limit"),
            cpu_affinity=config_dict.get("cpu_affinity", False),
            debug=config_dict.get("debug", False),
            log_level=config_dict.get("log_level", "INFO"),
        )

    @classmethod
    def from_file(cls, config_path: Union[str, Path]) -> "PyarallelConfig":
        """Load configuration from a JSON, YAML, or TOML file."""

        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        if config_path.suffix == ".json":
            import json

            with config_path.open() as f:
                config_dict = json.load(f)
        elif config_path.suffix in (".yml", ".yaml"):
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ImportError("yaml support requires PyYAML to be installed") from exc
            with config_path.open() as f:
                config_dict = yaml.safe_load(f)
        elif config_path.suffix == ".toml":
            try:
                import toml
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ImportError("toml support requires toml to be installed") from exc
            with config_path.open() as f:
                config_dict = toml.load(f)
        else:
            raise ValueError(
                f"Unsupported configuration file format: {config_path.suffix}"
            )

        return cls.from_dict(config_dict)
