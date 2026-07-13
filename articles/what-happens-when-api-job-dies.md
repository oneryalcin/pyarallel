# What Happens When Your 50,000-Item API Job Dies?

*A public embedding pipeline, two injected failures, and the job-level runtime a thread pool does not provide.*

You have 50,000 document chunks to embed.

Eight workers are sending batches to an API. The service returns `429 Retry-After: 30`. One worker sleeps; the others are still free to send requests. Hours later, after tens of thousands of successful embeddings, the process dies.

You run the command again.

Does it continue from the last completed batch, or purchase the same work twice? Can the final result distinguish “everything finished” from “everything that happened to run succeeded”? When the server asks one request to back off, who tells the other seven workers?

None of those are questions for `ThreadPoolExecutor`. A thread pool runs functions. It does not own quotas, retry coordination, durable completion, or the meaning of a finished run.

Yet somebody has to.

## This workload shape is real

At the revision examined for this case study, OpenAI’s open-source [Knowledge Retrieval starter](https://github.com/openai/openai-knowledge-retrieval) contains an embedding pipeline with exactly this shape. Its [`embed_texts_concurrent()` implementation](https://github.com/openai/openai-knowledge-retrieval/blob/f62c5dd49955d2bc793e0a55989863dca61f1ead/ingestion/pipeline.py#L113) does several sensible things:

- divides texts into batches;
- chooses a configurable worker count;
- shares one API client;
- submits batches through `ThreadPoolExecutor`;
- retries failed batches with backoff;
- reads `Retry-After` when available;
- tracks progress as futures finish;
- reconstructs the original order afterward.

This is not bad code. It is competent application code growing a small execution runtime because the workload requires one.

This is a case study, not an upstream dependency proposal: at the pinned revision, the starter supports [Python 3.10+](https://github.com/openai/openai-knowledge-retrieval/blob/f62c5dd49955d2bc793e0a55989863dca61f1ead/pyproject.toml#L7), while pyarallel requires [Python 3.12+](https://github.com/oneryalcin/pyarallel/blob/main/pyproject.toml#L6).

The orchestration is roughly this:

```python
batches = make_batches(texts, batch_size)
completed = []

def embed_with_retry(batch):
    for attempt in range(max_attempts):
        try:
            return call_embedding_api(batch)
        except Exception as error:
            if attempt + 1 == max_attempts:
                raise
            time.sleep(retry_delay(error, attempt))

with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [pool.submit(embed_with_retry, batch) for batch in batches]
    for future in as_completed(futures):
        completed.append(future.result())
        report_progress()

embeddings = restore_input_order(completed)
```

That abbreviated version already needs batching, retry state, sleeping, futures, completion tracking, and order restoration.

Production pressure usually adds more: a rate limiter, a failure threshold, graceful termination, result bookkeeping, and some way to resume a job after the laptop, pod, or VM disappears.

The code around the API call becomes larger than the API call.

## “But the SDK already retries”

It does—and that distinction matters.

The [OpenAI Python client retries connection failures, timeouts, 408, 409, 429, and 5xx responses by default](https://github.com/openai/openai-python#retries). It also understands server-directed retry delays. If all you need is “try this one request again,” use the SDK feature. Do not add another retry library merely to reproduce it.

But a client owns a request, not the executor surrounding it.

If worker 3 receives `Retry-After: 30`, the client can pause worker 3. It cannot close the admission gate used by workers 1, 2, and 4 through 8. It cannot record that batch 417 was durably completed. It cannot decide whether 10 failed batches should stop the next 10,000 from being submitted. It cannot tell the caller that a run was truncated rather than complete.

Those are job-level policies. They require one owner above both the client and the pool.

## Two failures worth injecting before production does

I use two tests for this class of job.

### Failure 1: the server pushes back

Start eight workers against a local server with a hard request quota. Once the quota is exceeded, the server returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 1
```

Then measure requests at the server, not inside the client.

Per-request retry can make every item eventually succeed while the pool continues hitting the service during the requested quiet period. That is a locally successful result and a globally impolite client.

The desired invariant is stronger:

> Once any worker receives a valid server delay, no newly admitted request crosses the shared gate until that delay expires.

### Failure 2: the machine disappears

Kill the worker process with `SIGKILL` after the server has accepted roughly 40% of the batches. No cleanup callback, no graceful exception, no final checkpoint flush.

Run the same command again and count calls at the server.

The important number is not how quickly the rerun finishes. It is how many previously successful calls are purchased again. A durable checkpoint should never claim work that was not completed, and a rerun should replay at most the calls that were in flight at the kill instant—not every completed batch before them.

These are failure-injection experiments, not claims that the referenced OpenAI project suffered a production incident. The public pipeline is the real workload; the failures are deliberately introduced so the contract can be observed instead of assumed.

## Give the job one owner

Here is the same embedding shape using [pyarallel](https://github.com/oneryalcin/pyarallel), a zero-dependency fan-out layer for rate-limited API jobs. Assume `texts` contains the loaded corpus; the linked production template covers signals and resource lifecycle end to end.

```python
from dataclasses import dataclass
import os

import openai
from pyarallel import Limiter, RateLimit, Retry, RunStatus, parallel_map

MODEL = "text-embedding-3-small"
BATCH_SIZE = 100
DATASET_FINGERPRINT = "docs-2026-07-13-sha256-ab12cd34"

# Let one layer own retries. The OpenAI client normally retries twice;
# disable that here because pyarallel coordinates retry with the pool.
client = openai.OpenAI(max_retries=0, timeout=30)


@dataclass(frozen=True)
class Batch:
    offset: int
    texts: list[str]


def embed_batch(batch: Batch) -> list[list[float]]:
    response = client.embeddings.create(model=MODEL, input=batch.texts)
    return [row.embedding for row in response.data]


batches = [
    Batch(offset, texts[offset : offset + BATCH_SIZE])
    for offset in range(0, len(texts), BATCH_SIZE)
]

# Use the request quota for your account and model; it is configuration,
# not a universal OpenAI constant.
requests_per_minute = int(os.environ["OPENAI_REQUESTS_PER_MINUTE"])
limiter = Limiter(RateLimit(requests_per_minute, "minute", burst=8))

retry = Retry.for_http(
    on=(openai.APIConnectionError, openai.APIStatusError),
    statuses={408, 409, 429, *range(500, 600)},
    attempts=5,
)

result = parallel_map(
    embed_batch,
    batches,
    workers=8,
    rate_limit=limiter,
    retry=retry,
    checkpoint="embeddings.ckpt",
    checkpoint_key=lambda batch: batch.offset,
    checkpoint_version=(MODEL, BATCH_SIZE, DATASET_FINGERPRINT),
    max_errors=10,
    on_progress=lambda done, total: print(f"embedded {done}/{total} batches"),
)

if result.status is not RunStatus.COMPLETED:
    raise RuntimeError(f"embedding run ended as {result.status.value}")

# values() refuses to dress a truncated run up as a complete list.
embeddings = [vector for batch in result.values() for vector in batch]
```

The API call is still ordinary client code. The difference is that the policies around it now have one place to live.

### What each line buys

- **One admission gate.** `Limiter(...)` owns the shared budget. Every initial call and retry passes through it. A valid `Retry-After` moves the limiter’s next-admission floor, so one throttled response slows the pool rather than only the unlucky worker.
- **Durable receipts.** `checkpoint="embeddings.ckpt"` commits each completed batch to SQLite before progress is reported. After a hard kill, the next run loads completed batches and executes the remainder.
- **Stable identity.** `checkpoint_key=lambda batch: batch.offset` identifies each batch. The checkpoint also fingerprints its payload, so changed text under the same offset is recomputed rather than silently reused.
- **Semantic invalidation.** `checkpoint_version=(MODEL, BATCH_SIZE, DATASET_FINGERPRINT)` covers meaning that function inspection cannot see. Change the model, batch shape, or dataset and the old checkpoint fails closed.
- **Bounded failure.** `max_errors=10` stops admission when the service is dead or credentials are broken. Damage is approximately the failure threshold plus already-admitted work—not the entire remaining dataset.
- **Honest completion.** `RunStatus` separates `COMPLETED`, `TIMED_OUT`, `ABORTED`, and `CANCELLED`. Asking a truncated result for all values raises rather than returning a partial list with the shape of a complete one.

## Run the failure injection yourself

The pyarallel repository carries the quota and crash experiments as one self-checking script:

```bash
git clone https://github.com/oneryalcin/pyarallel
cd pyarallel
python -m pip install .
python examples/resilience_demo.py
```

It uses a local standard-library HTTP server—no credentials and no paid API calls.

A fresh run produced this abridged output. Timings and the exact kill boundary vary:

```text
ACT 1 — the server throttles; one 429 pauses the WHOLE pool
  items completed : 80/80
  429s drawn      : 1
  server silence  : 1.0s

ACT 2 — client-side RateLimit
  items completed : 40/40
  server said 429 : 0 times

ACT 3 — SIGKILL, then rerun
  run 1: killed at item 48; server had served 48 calls
  run 2: server served only the remaining 72 calls
         48 paid-for calls loaded from the checkpoint
```

The decisive receipts come from outside the executor: the server measured the quiet period after `Retry-After`, and its request counter showed that completed calls were not repeated after restart. Only calls in flight at the kill boundary may replay—at most one per worker.

The script exits non-zero when a claim fails and runs in CI against the built wheel.

## Centralizing infrastructure does not make it infallible

Pyarallel once defined `.ok` as “every recorded item succeeded.” A timed-out generator could therefore report `.ok=True` even though unseen inputs were never admitted. Centralizing the job layer did not make it infallible; it made that contradiction visible enough to regression-test and fix once.

## What this does not solve

Pyarallel is deliberately single-machine and single-stage. If step B consumes step A across a cluster, use a workflow or distributed execution system such as Prefect or Ray.

If you need a durable queue with independent workers, use Celery or another task queue. If you only need to call a function twenty times with no quota, retry, resume, or partial-failure contract, `ThreadPoolExecutor.map()` is already enough.

There is another honest limitation in the embedding example: API providers may enforce several budgets at once—requests per minute, tokens per minute, and model-specific limits. The example’s `RateLimit` controls request admission; batching controls token volume indirectly. Pyarallel does not yet expose weighted composite quotas, so workloads requiring exact token-weighted admission still need provider-specific accounting.

## The real abstraction

“Run this function concurrently” is the easy part.

The production job is:

- admit work without overrunning a shared service;
- coordinate retries with that admission gate;
- stop wasting calls when failure becomes systemic;
- preserve completed expensive work across process death;
- and report whether the whole input was actually processed.

You can build that runtime around every API loop. Many good projects do.

Or it can be a named, separately tested layer.

One function. Many inputs. One service pushing back.

```bash
pip install pyarallel
```

Further reading: [demo instructions](#run-the-failure-injection-yourself) · [documentation](https://oneryalcin.github.io/pyarallel/) · [comparison and non-goals](https://oneryalcin.github.io/pyarallel/getting-started/comparison/) · [production API-job template](https://github.com/oneryalcin/pyarallel/blob/main/examples/07_production_api_job.py)
