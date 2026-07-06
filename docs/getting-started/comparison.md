# Comparison — pyarallel vs the Alternatives

Pyarallel occupies one niche: **applying one function to many inputs
against services that throttle you**, with the policies that workload
needs — rate limiting, retry, server-driven backoff, resume — built into
one tool. Several good libraries live nearby. This page says honestly
where each one wins, including where pyarallel is the wrong choice.

## vs the hand-rolled stack (ThreadPoolExecutor + tenacity + semaphore)

This is pyarallel's real competitor — not a library, a *pattern*.
Everyone builds it: an executor for concurrency, [tenacity](https://tenacity.readthedocs.io/)
for retry, a token bucket or semaphore for rate limiting, a dict for
errors, and a "TODO: resume" comment.

```python
# The stack you've written before
import tenacity
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

limiter = Semaphore(10)          # concurrency, but NOT a rate limit

@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(),
    retry=tenacity.retry_if_exception_type(ConnectionError),
)
def fetch_with_retry(url):
    with limiter:
        return fetch(url)

results, errors = {}, {}
with ThreadPoolExecutor(max_workers=10) as pool:
    futures = {pool.submit(fetch_with_retry, u): i for i, u in enumerate(urls)}
    for f in as_completed(futures):
        i = futures[f]
        try:
            results[i] = f.result()
        except Exception as e:
            errors[i] = e
# Still missing: an actual requests-per-minute limit, Retry-After
# handling, resume after a crash, partial-result semantics.
```

```python
# The same job in pyarallel
from pyarallel import parallel_map, RateLimit, Retry

result = parallel_map(
    fetch, urls, workers=10,
    rate_limit=RateLimit(100, "minute"),
    retry=Retry(attempts=3, on=(ConnectionError,)),
    checkpoint="run.ckpt",
)
```

The difference isn't line count — it's the parts the hand-roll never
gets to. A semaphore bounds *concurrency*, not *rate*: 10 workers
against a 100/minute quota still burn it in seconds. Tenacity retries
one call in isolation; it cannot pause the *pool* when the server says
`Retry-After: 30`, so every worker discovers the 429 separately. And
resume-after-crash never gets built because it's a project in itself.

**Use the hand-roll when** you need exactly one of these pieces —
tenacity alone is excellent, and for complex retry strategies (circuit
breakers, custom stop conditions) it composes *inside* pyarallel
workers ([how](../user-guide/best-practices.md#composing-with-tenacity)).

## vs aiometer

[aiometer](https://pypi.org/project/aiometer/) is a small, clean async
concurrency scheduler: `max_at_once` bounds concurrency,
`max_per_second` bounds rate, results stream or collect. If that's all
you need, it's a lovely tool.

Differences that matter in practice:

- **Async-only** (asyncio/trio). Pyarallel has the same API for sync
  and async — most scripts, notebooks, and cron jobs are sync.
- **No retry** — you bring your own, and it can't coordinate with the
  rate limiter (the 429-pauses-the-pool behavior needs both in one
  place).
- **No resume** and no structured partial results — an exception mid-run
  is yours to handle.

**Use aiometer when** you're already fully async, your error handling is
simple, and you want a minimal dependency that schedules and gets out of
the way.

## vs mpire

[mpire](https://pypi.org/project/mpire/) is the multiprocessing
specialist, and in that lane it's *more* featureful than pyarallel:
copy-on-write shared objects, per-worker state, CPU pinning, worker
insights, progress dashboards, dill serialization for exotic objects.

Pyarallel's process executor covers the common CPU-bound fan-out
(`executor="process"`, `worker_init=`, `max_tasks_per_worker=`) but
doesn't try to match that depth. The lane it owns is the other one:
mpire has no rate limiting, no retry policy, no async, no
checkpoint/resume — it isn't built for calling throttled APIs.

**Use mpire when** heavy multiprocessing *is* the job — big
shared-memory datasets, worker state, squeezing cores. **Use pyarallel
when** the job is I/O against services that push back, or when one tool
must cover both sides.

## vs joblib

[joblib](https://joblib.readthedocs.io/) is the scientific-computing
workhorse: `Parallel(n_jobs=-1)(delayed(f)(x) for x in data)`, numpy
memmapping, disk caching via `Memory`, and it's already under sklearn.
For CPU-bound numerical work over arrays it's the default for a reason.

It has no rate limiting, no retry, no async, and its disk cache
memoizes *function calls* — related to, but not the same as, resuming a
half-finished batch run keyed by item identity.

**Use joblib when** you're in the numpy/sklearn world crunching arrays.
**Use pyarallel when** the bottleneck is a remote API's quota, not your
CPU.

## When NOT to use pyarallel

Honesty is the point of this page — these are real disqualifiers, not
straw men:

- **Dependencies between tasks (DAGs, pipelines).** Pyarallel is one
  function over N independent inputs, deliberately. Step B needs step
  A's output? [Prefect](https://www.prefect.io/), [Dagster](https://dagster.io/),
  or Airflow.
- **Distributed across machines.** One process, one machine. Cluster
  scale is [Ray](https://www.ray.io/) or [Dask](https://www.dask.org/).
- **Background jobs / task queues.** Fire-and-forget with brokers,
  schedules, and worker fleets is [Celery](https://docs.celeryq.dev/)
  / RQ / [arq](https://arq-docs.helpmanual.io/) territory.
- **Vectorizable numerical work.** If numpy/polars can express it,
  vectorization beats any pool by orders of magnitude. Parallelize the
  parts that can't vectorize.
- **A five-line one-off with no failures worth handling.**
  `ThreadPoolExecutor.map` is in the stdlib and is fine. Pyarallel earns
  its import when rate limits, retries, partial failures, or resume
  enter the picture.
- **Streaming/event processing.** Continuous consumption from Kafka
  et al. is a different shape — pyarallel runs *batches* to completion.

## The feature grid

Kept short and honest — ✓ means built-in and coordinated, not
"achievable with glue code":

| | pyarallel | hand-roll | aiometer | mpire | joblib |
|---|---|---|---|---|---|
| Sync API | ✓ | ✓ | — | ✓ | ✓ |
| Async API | ✓ | DIY | ✓ | — | — |
| Rate limiting (req/time) | ✓ | DIY | ✓ | — | — |
| Retry with backoff | ✓ | tenacity | — | — | — |
| 429/`Retry-After` slows the pool | ✓ | — | — | — | — |
| Checkpoint/resume | ✓ | — | — | — | caching* |
| Partial results + typed failures | ✓ | DIY | — | ✓ | — |
| Deep multiprocessing (shared mem, pinning) | — | — | — | ✓ | ✓ |
| numpy memmapping | — | — | — | — | ✓ |
| Zero dependencies | ✓ | — | — | — | — |

\* joblib's `Memory` caches function calls to disk — great for repeated
computations, not positional/keyed batch resume.

If your workload is "fan out one function over N items against a
service that throttles you" — LLM calls, embeddings, scraping, SaaS
APIs — that first column is the stack you'd otherwise assemble by hand.
That's the bet. See it in action:
[Real-World Patterns](../user-guide/real-world-patterns.md).
