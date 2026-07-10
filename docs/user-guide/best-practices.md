# Best Practices

## Choosing the Right Executor

### Threads (`executor="thread"`, default)

Best for **I/O-bound** work where tasks spend most time waiting:

- HTTP requests
- Database queries
- File I/O
- API calls

```python
results = parallel_map(fetch_url, urls, workers=20, executor="thread")
```

!!! note "Free-threaded Python"
    On free-threaded builds (3.13t/3.14t) threads parallelize CPU-bound
    work too — a 4-worker CPU-bound map measured 2.4× over sequential
    where the GIL build measured 1.0×. CI runs pyarallel's full suite
    with the GIL off on both builds.

### Processes (`executor="process"`)

Best for **CPU-bound** work that needs true parallelism:

- Data crunching
- Image/video processing
- Scientific computation

```python
results = parallel_map(compute, data, workers=4, executor="process")
```

!!! warning
    Process executor requires picklable functions. Use module-level named functions, not lambdas or closures.

### Interpreters (`executor="interpreter"`, Python 3.14+)

Best for **pure-Python CPU-bound** work: true parallelism on standard
GIL builds (a 4-worker spin benchmark measured 3.3× over threads) with
worker startup measurably cheaper than process fork/spawn (~50 ms vs
~70 ms cold-pool time-to-first-result on the reference machine), all in
one OS process. Re-run these numbers yourself: [`benchmarks/`](https://github.com/oneryalcin/pyarallel/tree/main/benchmarks).

```python
results = parallel_map(crunch, data, workers=4, executor="interpreter")
```

The process rules apply — importable module-level functions, picklable
retry callables, no shared limiter, no contextvars — plus two of its
own: functions defined in `__main__` are rejected (move them into a
module), and there is no `max_tasks_per_worker` (no worker recycling).
Each interpreter re-imports your module, so import-time cost and module
memory are paid per worker, like a process.

!!! warning "C extensions"
    Extensions without subinterpreter support fail with `ImportError`
    inside workers — **numpy and pandas among them today**. For
    C-extension workloads use `executor="process"`.

### Worker Count

By default, `workers=None` — the stdlib picks a sensible number automatically:

- **Threads**: `min(32, cpu_count + 4)` — Python's `ThreadPoolExecutor` default
- **Processes**: `cpu_count()` — Python's `ProcessPoolExecutor` default

Most of the time you don't need to set `workers` at all. Override only when you have a reason:

```python
# Just use the defaults — they're good
results = parallel_map(fetch, urls)
results = parallel_map(crunch, data, executor="process")

# Override when you know better
results = parallel_map(fetch, urls, workers=100)       # high concurrency for fast APIs
results = parallel_map(crunch, data, executor="process",
                       workers=multiprocessing.cpu_count() - 1)  # leave a core free
```

## Rate Limiting

### Respecting API Limits

Leave a buffer below the actual limit:

```python
from pyarallel import RateLimit

# API allows 100/min — use 90 for safety
results = parallel_map(call_api, ids, workers=4,
                       rate_limit=RateLimit(90, "minute"))
```

### One Limiter per API Key

A `RateLimit` passed directly creates a fresh private limiter each call —
two concurrent maps would each assume they own the full quota. Whenever two
calls spend the same budget, share a `Limiter`:

```python
from pyarallel import Limiter, RateLimit

OPENAI_LIMITER = Limiter(RateLimit(450, "minute"))  # one per API key

# every job against this key, sync or async, passes the same instance
parallel_map(embed, texts, rate_limit=OPENAI_LIMITER)
```

### Burst: Default to Smooth

`burst=1` (the default) spaces requests evenly — the safest choice against
secondary per-second limits. Raise it only when you know the quota
genuinely allows bursts, and keep it well under the documented burst
allowance.

## Memory: Collected vs Streaming

In-flight work is bounded by default on every API (the
[admission window](advanced-features.md#the-admission-window)), so the
memory decision is really one question: do the *results* fit in memory?
A collected map's result list grows to the input size — that is what
"collected" means. When results should not accumulate at all (ETL to a
DB, 10M+ rows), use `parallel_iter` and consume as you go. On
memory-constrained environments (K8s pods, Lambda), that choice is the
one that prevents OOM kills.

## Error Handling Patterns

### Fail-Fast

Iterate the result — first `ExceptionGroup` stops you:

```python
try:
    for value in parallel_map(process, items, workers=4):
        save(value)
except ExceptionGroup as eg:
    for exc in eg.exceptions:
        log.error(exc)
```

### Collect-and-Retry Manually

For more control, inspect partial results and retry selectively:

```python
result = parallel_map(process, items, workers=4)

for idx, value in result.successes():
    save(items[idx], value)

if not result.ok:
    failed = [items[idx] for idx, _ in result.failures()]
    retry_result = parallel_map(process, failed, workers=2)
```

### Built-in Retry vs Tenacity

Reach for the built-in `Retry` first — it owns the concurrency layer,
which is what makes 429 + `Retry-After` handling actually work: the
server-mandated wait pauses the *shared limiter*, slowing the whole
pool instead of one task. See
[Retry](advanced-features.md#retry) and
[server-driven backoff](advanced-features.md#server-driven-backoff-429-retry-after)
in the feature guide.

### Composing with Tenacity

For complex retry strategies (circuit breakers, custom stop conditions), use tenacity inside your function:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential())
def resilient_fetch(url):
    return requests.get(url, timeout=10).json()

results = parallel_map(resilient_fetch, urls, workers=10)
```

## Long Jobs: Checkpoint Early

Any run long enough that restarting it hurts should carry a checkpoint:

```python
results = parallel_map(process, fifty_thousand_items, checkpoint="run.ckpt")
```

Rules of thumb:

- One checkpoint file per (function, input list) pair. Don't share a file
  between different jobs — the function-identity guard will fail closed.
- **Inputs that evolve between runs? Use `checkpoint_key`.** Positional
  rows recompute everything after a prepend or reorder; identity-keyed
  rows keep completed work:

  ```python
  results = parallel_map(fetch, users, checkpoint="run.ckpt",
                         checkpoint_key=lambda u: u.id)
  ```
- Delete the file when you *want* a full recompute (a config change
  hidden inside a captured client object). Plain captured
  config — a changed default, closure value, or partial argument — is
  caught automatically and fails closed.
- Checkpointing requires picklable items and results; an unpicklable
  result stops the run with `CheckpointError` rather than pretending.

## Overnight Jobs: Pair max_errors with checkpoint

The unattended-job failure mode is a dead API at 2 a.m.: without a
guard, the job burns its whole quota failing. `max_errors` caps the
cost, `checkpoint` preserves the successes, and the morning rerun
resumes exactly where it stopped:

```python
result = parallel_map(fetch, users, workers=10,
                      max_errors=10, checkpoint="nightly.ckpt")
```

## Testing and Debugging

`sequential=True` runs every item inline in the calling thread — no
pool, deterministic order, real stack traces, working breakpoints. It
still honors rate limits, retry, and checkpointing, and `workers` is
ignored rather than rejected, so one env flag flips production code
into debug mode:

```python
def test_processing():
    result = parallel_map(process, [1, 2, 3], sequential=True)
    assert list(result) == [expected_1, expected_2, expected_3]
```

The `@parallel` decorator preserves normal call behavior:

```python
def test_decorated_function():
    @parallel(workers=2)
    def double(x):
        return x * 2

    # Test the function directly — no parallel overhead
    assert double(5) == 10

    # Test parallel execution
    assert list(double.map([1, 2, 3])) == [2, 4, 6]
```

The full playbook — retries without sleeping, manufacturing every
`RunStatus`, checkpoint and `Retry-After` tests — is in the
[testing guide](testing.md).

## Performance Tips

1. **Match workers to workload** — too many workers waste resources on context switching
2. **Use rate limiting for external APIs** — protects you and the service
3. **Share one `Limiter` per API key** — separate specs per call each assume the full quota
4. **Prefer threads for I/O** — processes have serialization overhead
5. **Check `result.ok` before iterating** — avoids surprise `ExceptionGroup` raises
6. **Use `on_progress` for long jobs** — for unsized iterables `total`
   counts items admitted so far, not the final size; pass a sized input
   for a real total
7. **Checkpoint anything you'd hate to restart** — `checkpoint="run.ckpt"` is one argument
