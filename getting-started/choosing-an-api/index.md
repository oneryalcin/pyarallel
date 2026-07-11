# Which API Should I Use?

Pyarallel's surface is small — one decision tree covers all of it.

```
Is your function async (async def)?
│
├─ NO (sync) ──► Do you need results incrementally / constant memory?
│               ├─ NO  ──► parallel_map(fn, items)          ← start here
│               ├─ YES ──► parallel_iter(fn, items)
│               └─ items are argument TUPLES?
│                          parallel_starmap(fn, pairs)
│
└─ YES ───────► Do you need results incrementally / constant memory?
                ├─ NO  ──► async_parallel_map(fn, items)
                ├─ YES ──► async_parallel_iter(fn, items)
                └─ items are argument TUPLES?
                           async_parallel_starmap(fn, pairs)
```

One caveat on the starmap branch: the starmaps cover the common "unpack these tuples" case but don't take `checkpoint=`, `max_errors=`, or `stop=`. If you need those, wrap the call into a single-argument function and use `parallel_map`:

```
def process(pair: tuple[str, int]) -> dict:
    name, count = pair
    return upload(name, count)

result = parallel_map(process, pairs, checkpoint="run.ckpt")
```

## Collected vs streaming

|                       | Collected                   | Streaming                         |
| --------------------- | --------------------------- | --------------------------------- |
| Sync function         | `parallel_map`              | `parallel_iter`                   |
| Async function        | `async_parallel_map`        | `async_parallel_iter`             |
| Returns               | `ParallelResult` at the end | `ItemResult`s as they finish      |
| Memory                | holds all results           | constant (windowed)               |
| Checkpoint/resume     | yes                         | no                                |
| Wall-clock `timeout=` | yes                         | no (use `task_timeout=` in async) |

Rule of thumb: **collect** when the job fits in memory and you want `RunStatus`, checkpointing, and one honest verdict at the end. **Stream** when results should be written out as they arrive, inputs are huge or unsized, or you want to react to failures mid-run.

```
# Collected: one verdict at the end
result = parallel_map(fetch, urls, workers=8)
if result.ok:
    save_all(result.values())

# Streaming: constant memory, act per item
for item in parallel_iter(fetch, urls, workers=8):
    if item.ok:
        sink.write(item.value)
```

## Functions vs the decorator

Same engine, different ergonomics. Use the **functions** when you're parallelizing someone else's callable at a call site. Use the **decorator** when you own the function and want its parallel defaults declared once, next to its definition:

```
@parallel(workers=8, rate_limit=10, retry=Retry(attempts=3))
def fetch(url: str) -> dict:
    ...

fetch("https://api.example.com/1")   # normal call — unchanged
fetch.map(urls)                      # parallel, with the defaults above
fetch.stream(urls)                   # streaming, same defaults
fetch.map(urls, workers=2)           # any call can override
```

`@async_parallel` is the same idea for `async def` functions.

## Which executor?

Only sync calls take `executor=`; async concurrency is tasks on your event loop, no pool involved.

|                      | Use for                 | Notes                                                                                                                                                      |
| -------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"thread"` (default) | I/O: HTTP, DBs, files   | Right for ~90% of uses. On free-threaded Python (3.13t/3.14t) threads also parallelize CPU work                                                            |
| `"process"`          | CPU-bound work          | Function + items must pickle, module-scope functions only; ~70ms+ startup per worker                                                                       |
| `"interpreter"`      | CPU-bound, Python 3.14+ | One OS process, GIL-free parallelism; ~50ms startup per worker on a standard build (~100ms free-threaded; machine-dependent); same module-scope constraint |

Numbers and methodology: [`benchmarks/`](https://github.com/oneryalcin/pyarallel/tree/main/benchmarks).

## When you don't need pyarallel at all

No rate limit, no retry, no resume, no partial-failure story — just "run this 20 times concurrently"? The standard library is enough:

```
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(fetch, urls))
```

Pyarallel earns its place when the service pushes back — quotas, 429s, flaky calls, jobs long enough to crash. The [comparison page](https://oneryalcin.github.io/pyarallel/getting-started/comparison/index.md) draws the full map, including when you want Prefect, Ray, or Celery instead.
