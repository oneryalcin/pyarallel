# API Reference: Core

## `parallel_map`

Execute a function over items in parallel, returning ordered results.

```python
from pyarallel import parallel_map

results = parallel_map(
    fn,                              # Function to apply to each item
    items,                           # Any iterable
    *,
    workers=4,                       # Number of parallel workers
    executor="thread",               # "thread" or "process"
    rate_limit=None,                 # RateLimit object or ops/second (float)
    timeout=None,                    # Total timeout in seconds
    on_progress=None,                # callback(completed, total)
)
```

**Returns:** `ParallelResult[R]`

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fn` | `Callable` | required | Function to apply to each item |
| `items` | `Iterable` | required | Any iterable (list, generator, range, set, ...) |
| `workers` | `int` | `4` | Number of parallel workers |
| `executor` | `"thread" \| "process"` | `"thread"` | Thread pool or process pool |
| `rate_limit` | `RateLimit \| float \| None` | `None` | Rate limiting (float = ops/second) |
| `timeout` | `float \| None` | `None` | Total wall-clock timeout in seconds |
| `on_progress` | `Callable[[int, int], None] \| None` | `None` | Progress callback `(completed, total)` |

### Examples

```python
# Basic
results = parallel_map(fetch, urls, workers=10)

# With rate limit object
results = parallel_map(fetch, urls, rate_limit=RateLimit(100, "minute"))

# With rate limit shorthand
results = parallel_map(fetch, urls, rate_limit=10)  # 10 per second

# With timeout and progress
results = parallel_map(fetch, urls, timeout=60.0,
                       on_progress=lambda d, t: print(f"{d}/{t}"))
```

---

## `@parallel`

Decorator that adds `.map()` for parallel execution. The decorated function **keeps its original signature and return type**.

```python
@parallel(workers=4, executor="thread", rate_limit=None)
def fn(item): ...
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `workers` | `int` | `4` | Default worker count for `.map()` |
| `executor` | `"thread" \| "process"` | `"thread"` | Default executor type |
| `rate_limit` | `RateLimit \| float \| None` | `None` | Default rate limiting |

### Usage

```python
@parallel(workers=8)
def fetch(url):
    return requests.get(url).json()

# Normal call — returns dict
data = fetch("http://example.com")

# Parallel — returns ParallelResult
results = fetch.map(urls)
results = fetch.map(urls, workers=16)           # override workers
results = fetch.map(urls, rate_limit=100)       # override rate limit
results = fetch.map(urls, timeout=30.0)         # add timeout
results = fetch.map(urls, on_progress=callback) # add progress

# Bare decorator (uses all defaults)
@parallel
def process(x):
    return x * 2
```

### Method Support

Works with instance methods (via descriptor protocol) and static methods:

```python
class Service:
    @parallel(workers=4)
    def process(self, item):
        return self.transform(item)

    @staticmethod
    @parallel(workers=4)
    def compute(x):
        return x ** 2

svc = Service()
svc.process.map(items)          # instance method
Service.compute.map([1, 2, 3])  # static method
```

---

## `ParallelResult`

Container for parallel execution results. Behaves like a `list` when all tasks succeed. Provides structured access when some fail.

### Properties and Methods

| Member | Returns | Description |
|---|---|---|
| `.ok` | `bool` | `True` if every task succeeded |
| `.values()` | `list[R]` | All results in order. Raises `ExceptionGroup` on failure |
| `.successes()` | `list[tuple[int, R]]` | `(index, value)` for each success |
| `.failures()` | `list[tuple[int, Exception]]` | `(index, exception)` for each failure |
| `.raise_on_failure()` | `None` | Raises `ExceptionGroup` if any task failed |
| `len(result)` | `int` | Total number of tasks |
| `result[i]` | `R` | Index into values (raises on failure) |
| `list(result)` | `list[R]` | Iterate values (raises on failure) |
| `bool(result)` | `bool` | `True` if any tasks were submitted |

### Example

```python
result = parallel_map(process, items, workers=4)

if result.ok:
    for value in result:
        print(value)
else:
    for idx, value in result.successes():
        save(idx, value)
    for idx, error in result.failures():
        log(idx, error)
```

---

## `RateLimit`

Immutable rate limit specification.

```python
from pyarallel import RateLimit

rate = RateLimit(count, per="second")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `count` | `float` | required | Operations allowed per interval |
| `per` | `"second" \| "minute" \| "hour"` | `"second"` | Time interval |

### Property

- `per_second` → `float`: Rate converted to operations per second

### Examples

```python
RateLimit(10)                # 10 per second
RateLimit(100, "minute")     # 100 per minute
RateLimit(1000, "hour")      # 1000 per hour
```

Or use the shorthand — pass a number directly to `rate_limit=`:

```python
parallel_map(fn, items, rate_limit=10)  # same as RateLimit(10)
```
