# Installation

## Requirements

- Python 3.12 or higher

## Install

```bash
pip install pyarallel
```

## Development Install

The dev toolchain is a [dependency group](https://peps.python.org/pep-0735/)
(not a `[dev]` extra), managed with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/oneryalcin/pyarallel.git
cd pyarallel
uv sync --group dev     # editable install + test/lint/type/docs toolchain
uv run pytest -q        # should be all green
```

## Verify

```python
from pyarallel import parallel_map

results = parallel_map(lambda x: x * 2, [1, 2, 3], workers=2)
print(list(results))  # [2, 4, 6]
```
