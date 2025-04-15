# Configuration System Implementation Tasks

## 1. Core Configuration System [✅]
- [✅] Create `config.py` with dataclass-based configuration
- [✅] Implement singleton config manager
- [✅] Add type validation for config values
- [✅] Add merge strategy for partial updates

**Test Cases:**
```python
def test_config_defaults():
    """Default values are set correctly"""

def test_config_validation():
    """Invalid values raise proper exceptions"""

def test_partial_update():
    """Partial config updates don't affect other values"""
```

## 2. Environment Variables Support [✅]
- [✅] Add env var parsing in config manager
- [✅] Implement type coercion (str -> proper type)
- [✅] Add prefix support (PYARALLEL_*)
- [✅] Support complex values (lists, dicts via JSON)

**Test Cases:**
```python
def test_env_var_loading():
    """Config loads from environment variables"""

def test_env_var_types():
    """Environment variables are properly typed"""

def test_env_var_prefix():
    """Only PYARALLEL_* vars are loaded"""
```

## 3. Runtime Configuration API [✅]
- [✅] Add global `set()` method
- [✅] Add category-specific setters
- [✅] Add value getters with dot notation
- [✅] Implement config validation hooks

**Test Cases:**
```python
def test_global_set():
    """Global config can be set"""

def test_category_set():
    """Category-specific settings work"""

def test_dot_notation():
    """Dot notation access works for nested config"""
```

## 4. Decorator Integration [✅]
- [✅] Update parallel decorator to use config
- [✅] Add config override in decorator
- [✅] Implement inheritance rules
- [✅] Add runtime config warnings

**Test Cases:**
```python
def test_decorator_defaults():
    """Decorator uses global defaults"""

def test_decorator_override():
    """Decorator args override global config"""

def test_runtime_warnings():
    """Warnings for problematic configs"""
```

## 5. Documentation [✅]
- [✅] Add configuration section to README
- [✅] Document all environment variables
- [✅] Add configuration examples
- [✅] Document best practices

## Configuration Schema
```python
{
    "execution": {
        "default_max_workers": int,
        "default_executor_type": str,
        "default_batch_size": Optional[int],
        "prewarm_pools": bool
    },
    "rate_limiting": {
        "default_rate": Optional[float],
        "default_interval": str,
        "burst_tolerance": float
    },
    "error_handling": {
        "max_retries": int,
        "retry_backoff": float,
        "fail_fast": bool
    },
    "monitoring": {
        "enable_logging": bool,
        "log_level": str,
        "sentry_dsn": Optional[str],
        "metrics_enabled": bool
    }
}
```

## Environment Variables
```
PYARALLEL_MAX_WORKERS=4
PYARALLEL_EXECUTOR_TYPE=thread
PYARALLEL_BATCH_SIZE=10
PYARALLEL_RATE_LIMIT=100/minute
PYARALLEL_FAIL_FAST=true
PYARALLEL_SENTRY_DSN=https://...
```

## Progress Tracking
- ✅ Task completed
- 🚧 In progress
- ❌ Blocked
- [ ] Not started

## Feature: Enhanced Decorator Ergonomics

Implement first-class support for decorating instance, static, and class methods with `@parallel`.

- [✅] **1. Analyze Core Decorator Logic**
    - [✅] Examine `@parallel` implementation in `pyarallel/core.py`.
    - [✅] Understand current argument inspection and `executor.submit` usage.
- [✅] **2. Modify Argument Handling**
    - [✅] Update the `wrapper` function to detect method type (instance, static, class).
    - [✅] Correctly identify the iterable/item argument based on method type (using `inspect` or signature analysis).
- [✅] **3. Adapt Executor Submission**
    - [✅] Ensure `self`/`cls` are passed correctly to `executor.submit` for instance/class methods.
    - [✅] Preserve other arguments (`*args`, `**kwargs`).
- [✅] **4. Implement Comprehensive Tests**
    - [✅] Add tests for instance methods (`test_instance_method_parallel`).
    - [✅] Add tests for static methods (`test_static_method_parallel`).
    - [✅] Add tests for class methods (`test_class_method_parallel`).
    - [✅] Include tests for single item and list inputs.
    - [✅] Test methods with additional arguments.
    - [✅] Ensure no regressions for regular functions.
- [✅] **5. Update Documentation**
    - [✅] Update `README.md` and `docs/index.md` with clear examples for each method type.
- [✅] **6. Code Review and Refinement**
    - [✅] Review implementation, tests, and documentation.
    - [✅] Ensure robustness, adherence to standards, and clarity.
- [✅] **7. Add Type Hints**
    - [✅] Add type hints to the `@parallel` decorator and inner `wrapper` in `pyarallel/core.py` for better IDE support.

## Future Enhancements / Ideas
