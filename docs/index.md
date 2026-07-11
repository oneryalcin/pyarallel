# Pyarallel

**One function. Many inputs. One rate-limited service pushing back.**

Pyarallel is the fan-out layer for API jobs that need more than a thread
pool: shared rate limits, coordinated `Retry-After` backoff, per-item retry,
crash-safe resume, graceful shutdown, and an honest final status. Sync and
async, with no runtime dependencies.

```python
from pyarallel import Limiter, RateLimit, Retry, parallel_map

limiter = Limiter(RateLimit(100, "minute"))

result = parallel_map(
    fetch,
    urls,
    workers=12,
    rate_limit=limiter,
    retry=Retry.for_http(on=(HttpError,), attempts=5),
    checkpoint="fetch.ckpt",
    checkpoint_version="fetch-v2",
    max_errors=20,
)

result.raise_on_failure()
save_all(result.values())
```

When one task receives `429 Retry-After`, the shared limiter pauses the
pool instead of letting every worker rediscover the quota. If the process
dies, rerunning the same job loads completed items from the checkpoint
instead of paying for them twice.

## See the claims fail or pass

The repository includes a self-checking resilience demo. It starts a local
fake API, proves that one 429 pauses the whole pool, kills a checkpointed
child process with `SIGKILL`, and verifies that the next run performs only
the remaining work:

```bash
python examples/resilience_demo.py
```

No credentials or external service required. The same demo runs against the
built wheel in CI.

## Choose your path

- **New here?** Run the [Quick Start](getting-started/quickstart.md) for one
  complete API-job pattern.
- **Choosing an API?** The [one-page decision guide](getting-started/choosing-an-api.md)
  covers collected, streaming, sync, async, decorators, and executors.
- **Building a real job?** Start from the [Cookbook](cookbook/index.md) or the
  runnable [production template](https://github.com/oneryalcin/pyarallel/blob/main/examples/07_production_api_job.py).
- **Evaluating the fit?** Read the [honest comparison](getting-started/comparison.md),
  including when the standard library, Prefect, or Ray is a better choice.
- **Something went wrong?** Go directly to [Troubleshooting](user-guide/troubleshooting.md)
  or [Testing](user-guide/testing.md).

## The contract

- **Explicit:** ordinary calls remain ordinary; `.map()` opts into fan-out.
- **Coordinated:** retry and rate limiting share one view of server pressure.
- **Bounded:** iterables are consumed lazily through an admission window.
- **Resumable:** completed items are committed before progress is reported.
- **Honest:** `RunStatus` distinguishes completion, timeout, abort, and
  cancellation; a truncated run is never `.ok`.

Pyarallel deliberately is not a task queue, DAG engine, or distributed
system. It is for one common shape: apply one function to many inputs while
an external service controls the pace.

## License

MIT — see [LICENSE](https://github.com/oneryalcin/pyarallel/blob/main/LICENSE.md).
