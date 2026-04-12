from .config_manager import ConfigManager
from .core import RateLimit, parallel

__version__ = "0.1.3"

# Singleton config instance for easy access
config = ConfigManager.get_instance()

__all__ = ["parallel", "RateLimit", "config", "ConfigManager"]

__doc__ = f"""
pyarallel v{__version__}

Simple parallel processing framework for Python.
"""
