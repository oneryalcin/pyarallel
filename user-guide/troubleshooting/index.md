# Troubleshooting

Symptoms first. Each entry is a real failure mode — several are bugs this library itself shipped and fixed, which is exactly why they're documented.

## "I set `rate_limit` but I still get 429s"

The three usual causes, in order of likelihood:

1. **Two pools, two budgets.** A `RateLimit(...)` spec passed to each call creates an independent bucket per call site — every job assumes it owns the full quota. Construct **one `Limiter` per API key** at module scope and pass that instance everywhere (sync and async can share it):

   ```
   OPENAI_LIMITER = Limiter(RateLimit(450, "minute"))
   ```

1. **Burst spikes into a windowed quota.** `burst=20` can put 20 requests into the same server-side second even when your average is fine. Servers meter in windows, not token buckets — keep burst small and leave ~10% headroom under the documented quota.

1. **You're not alone on the key.** Dashboards, cron jobs, and colleagues share quotas. The limiter can only pace what goes through it — budget for the traffic it can't see.

## "The job looks hung"

It's probably paused on purpose. A 429/503 with `Retry-After` raises the shared limiter's floor and the **whole pool** holds — that is the headline feature, and from the outside it looks like a hang. Check:

- Pass `on_progress=` so pauses are visible instead of silent.
- A hostile or misconfigured server can send `Retry-After: 3600`; `Retry.for_http(...)` caps how long it will honor via `max_server_wait` (default 600s). Lower it if an hour-long nap is worse than a failed item.
- If there's no rate limiting or retry in play at all, suspect your own function: one item blocking forever holds its worker. In async, set `task_timeout=`; in sync, put a timeout inside the function (e.g. on the HTTP call).

## "`CheckpointError` on rerun"

The checkpoint refuses to resume when the work's identity changed — that's the guardrail, not a corruption:

- **Function moved or renamed**: the checkpoint stores a task signature. If the refactor didn't change the *meaning* of results, delete the file (re-spends the run) or restore the name.
- **`checkpoint_version` changed**: a bumped version token (new prompt, new model, new schema) deliberately refuses to mix rows from the old meaning. Delete the file to start clean, or restore the old token to keep resuming the old run.
- **Inputs evolve between runs**: without `checkpoint_key=`, identity falls back to item position — inserting one row re-keys everything after it. Give items a stable key (`checkpoint_key=lambda r: r["id"]`).

## "Pickling errors with `executor="process"`"

Process (and interpreter) workers receive the function by serialization: lambdas, closures, inner functions, and bound methods of local objects all fail. Move the worker function to **module scope**, pass its inputs as items, and keep them picklable. Threads have no such constraint — if the work is I/O-bound, `executor="thread"` (the default) sidesteps this entirely.

## "`on_progress` totals keep changing"

For a generator or any unsized source, `total` is the number of items *admitted so far* — the library can't know the final count without consuming the source, so the total is provisional and grows. Pass a sized input (a list) when the progress denominator matters.

## "`values()` raised instead of giving me what completed"

By design. `values()` answers "give me **the** results" and refuses to hand back a partial list dressed as a whole — it raises `TimeoutError`, `Aborted`, or `Cancelled` on a truncated run, and `ExceptionGroup` when items failed. Branch on status first; opt into partials explicitly:

```
result = parallel_map(fetch, items, timeout=600, max_errors=25)

if result.status is RunStatus.COMPLETED and result.ok:
    rows = result.values()
else:
    rows = result.ok_values()        # explicit: partial results
    failed = result.failures()       # (index, exception) pairs
```

## "The run TIMED_OUT but my function is still running"

Python threads cannot be killed. When the wall-clock `timeout=` hits, pyarallel stops admitting work and returns honestly — but sync work already *inside* a worker runs to completion in the background. If that matters (side effects, spend), either put timeouts inside the function, or use the async API where `task_timeout=` genuinely cancels the task at its next await point.

## Still stuck?

`sequential=True` runs the identical call inline with real stack traces and working breakpoints — the fastest way to separate "my function is wrong" from "the parallel machinery surprised me." Then see the [testing guide](https://oneryalcin.github.io/pyarallel/user-guide/testing/index.md) for making the failure reproducible, and [open an issue](https://github.com/oneryalcin/pyarallel/issues) with the sequential repro.
