---
title: CPU-Bound Fan-Out (Processes and Interpreters)
description: Parallelize CPU-heavy work — image resizing, PDF parsing, hashing — across process or interpreter workers.
---

# CPU-Bound Fan-Out

Same API, different executor. Resize a directory of images, parse a
pile of PDFs, hash a million records — anything where the bottleneck is
your CPU, not a remote service:

```python
from pathlib import Path
from PIL import Image
from pyarallel import parallel_map

def make_thumbnail(path):
    """Must be a module-level function — process workers re-import it."""
    img = Image.open(path)
    img.thumbnail((800, 600))
    out = path.with_stem(path.stem + "_thumb")
    img.save(out)
    return str(out)

paths = list(Path("photos").glob("*.jpg"))

result = parallel_map(
    make_thumbnail, paths,
    executor="process",        # true parallelism — one Python per core
    max_tasks_per_worker=100,  # recycle workers (native-leak guard)
)

for idx, exc in result.failures():
    print(f"failed: {paths[idx]} — {exc}")
```

Everything else composes unchanged: `retry=` for flaky items,
`on_progress=` for a progress bar, `parallel_iter` when results
shouldn't accumulate in memory, `sequential=True` to debug with real
stack traces. `workers=None` defaults to one worker per core.

Two variants worth knowing:

- **Pure-Python CPU work on Python 3.14+**: `executor="interpreter"`
  gives the same true parallelism with faster worker startup than
  process fork/spawn (~50 ms vs ~72 ms measured; machine-dependent) and
  no OS processes — but C extensions like Pillow/numpy don't support
  subinterpreters yet, so *this* recipe stays on `"process"`. See
  [choosing the right executor](../user-guide/best-practices.md#interpreters-executorinterpreter-python-314).
- **Heavy multiprocessing** (shared memory, per-worker state, CPU
  pinning) is [mpire](https://pypi.org/project/mpire/)'s specialty —
  see the [comparison](../getting-started/comparison.md#vs-mpire) for
  where each tool wins.
