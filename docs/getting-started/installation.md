# Installation

## Requirements

- Python 3.9 or higher
- pip package manager

## Basic Installation

Install Pyarallel using pip:

```bash
pip install pyarallel
```

## Development Installation

For development or contributing to Pyarallel:

1. Clone the repository:
```bash
git clone https://github.com/oneryalcin/pyarallel.git
cd pyarallel
```

2. Install development dependencies:
```bash
pip install -e ".[dev]"
```

## Verifying Installation

Verify your installation by running Python and importing Pyarallel:

```python
from pyarallel import parallel

# Test with a simple parallel function
@parallel(max_workers=2)
def test_func(x):
    return x * 2

result = test_func([1, 2, 3])
print(result)  # Should print: [2, 4, 6]
```

## Troubleshooting

If you encounter any issues during installation:

1. Ensure you have the latest pip version:
```bash
pip install --upgrade pip
```

2. Check Python version compatibility:
```bash
python --version
```

3. For platform-specific issues or additional help, please refer to our [GitHub Issues](https://github.com/oneryalcin/pyarallel/issues) page.