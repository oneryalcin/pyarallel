# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pyarallel is a Python library for explicit parallel execution. It provides
sync and async map/stream helpers plus decorator-based APIs. It supports
thread-based execution for I/O-bound work and process-based execution for
CPU-bound work. Core features include rate limiting, batch processing, retry,
and structured result handling.

## Code Architecture

The core sync API lives in `pyarallel/core.py` and the async API lives in
`pyarallel/aio.py`.

Important public types and functions:
- `parallel_map`, `parallel_starmap`, `parallel_iter`
- `async_parallel_map`, `async_parallel_starmap`, `async_parallel_iter`
- `@parallel` and `@async_parallel`
- `ParallelResult`, `ItemResult`, `RateLimit`, and `Retry`

Rate limiting is implemented with local token-bucket helpers in the sync and
async modules. The library supports regular functions plus instance methods via
the descriptor protocol used by the decorator wrappers.

## Common Commands

### Setup Development Environment
```bash
# Clone the repository
git clone https://github.com/oneryalcin/pyarallel.git
cd pyarallel

# Install development dependencies
uv sync --group dev
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
pytest tests/test_parallel_map.py -v
```
To run a specific test case:
```bash
pytest tests/test_parallel_map.py::TestParallelMapBasic::test_basic_parallel_map -v
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

- Follow the coding style enforced by Ruff formatting and linting (line length 88 characters).
- Use type hints for function arguments and return values.
- Write docstrings for all public functions and classes.
- Add tests for new code and ensure the test suite passes (`make test`).
- Update documentation if APIs are changed.
- Ensure code lints successfully (`make lint`).
