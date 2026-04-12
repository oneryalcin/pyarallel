# Installation

## Requirements

- Python 3.12 or higher

## Install

```bash
pip install pyarallel
```

## Development Install

```bash
git clone https://github.com/oneryalcin/pyarallel.git
cd pyarallel
pip install -e ".[dev]"
```

## Verify

```python
from pyarallel import parallel_map

results = parallel_map(lambda x: x * 2, [1, 2, 3], workers=2)
print(list(results))  # [2, 4, 6]
```
