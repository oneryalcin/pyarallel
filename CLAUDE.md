# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pyarallel is a Python library for parallel execution, designed to make concurrent programming easy and efficient. It uses a decorator-based API and supports both thread-based (for I/O-bound tasks) and process-based (for CPU-bound tasks) parallelism. Key features include rate limiting, batch processing, and a flexible configuration system.

## Code Architecture

The core of Pyarallel revolves around the `@parallel` decorator (`pyarallel/core.py`) which wraps functions to enable parallel execution.

Configuration is managed by the `ConfigManager` (`pyarallel/config_manager.py`), a singleton class that handles global settings. It supports updates via Python code and environment variables. Configuration settings are structured into categories like `execution`, `rate_limiting`, `error_handling`, and `monitoring`. Environment variables use the `PYARALLEL_` prefix (e.g., `PYARALLEL_MAX_WORKERS`).

Rate limiting logic is implemented in `pyarallel/core.py` and utilizes a `TokenBucket` class.

The library supports various callable types, including regular functions, instance methods, class methods, and static methods.

## Common Commands

### Setup Development Environment
```bash
# Clone the repository
git clone https://github.com/oneryalcin/pyarallel.git
cd pyarallel

# Install development dependencies
pip install -e ".[dev]"
```

### Running Tests
```bash
# Run the full test suite
make test
# or
pytest tests/ -v
```
To run a single test file:
```bash
pytest tests/test_pyarallel.py -v
```
To run a specific test case:
```bash
pytest tests/test_pyarallel.py::test_basic_parallel_processing -v
```

### Formatting Code
```bash
make format
# This runs:
# ruff format .
# ruff check --fix .
```

### Linting (Type Checking)
```bash
make lint
# This runs:
# ruff check .
# mypy pyarallel/
```

### Building the Package
```bash
make build
# This cleans previous build artifacts and then runs:
# python -m build
```

### Publishing the Package (to PyPI)
```bash
make publish
# This first runs 'make build', then:
# twine upload dist/*
```

### Building and Serving Documentation Locally
```bash
make docs-serve
# This runs:
# mkdocs serve
```

### Deploying Documentation to GitHub Pages
```bash
make docs-deploy
# This updates the version in mkdocs.yml and then runs:
# mkdocs gh-deploy
```

## Development Guidelines

- Follow the coding style enforced by Black (line length 88 characters).
- Use type hints for function arguments and return values.
- Write docstrings for all public functions and classes.
- Add tests for new code and ensure the test suite passes (`make test`).
- Update documentation if APIs are changed.
- Ensure code lints successfully (`make lint`).
