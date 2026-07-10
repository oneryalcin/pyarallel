---
title: Batch Embedding Generation with Resume
description: Generate embeddings for thousands of texts with a shared rate-limit budget, retry, and crash-safe checkpointing.
---

# Batch Embedding Generation

Call an embedding API for thousands of texts with rate limiting and retry.
Works with OpenAI, Cohere, HuggingFace Inference API, or any HTTP endpoint.

```python
import openai
from pyarallel import parallel_map, Limiter, RateLimit, Retry

client = openai.OpenAI()

def embed(text):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding

texts = ["document one", "document two", ...]  # thousands of texts

# One Limiter = one budget. Reuse it across every call that spends
# this API key's quota.
limiter = Limiter(RateLimit(500, "minute", burst=20))

result = parallel_map(
    embed, texts,
    workers=10,
    rate_limit=limiter,
    retry=Retry.for_http(               # 429 + Retry-After, prewired (v0.9)
        attempts=4,
        backoff=1.0,
        on=(openai.RateLimitError, openai.APIConnectionError),
    ),
    window_size=100,                         # in-flight window (default 2 x workers)
    checkpoint="embeddings.ckpt",           # crash at item 40k? rerun resumes
)

embeddings = result.values()  # list of vectors, same order as texts
```

What each piece buys you:

- **Shared `Limiter`** — the quota belongs to the API key, not this call.
  Pass the same instance to other jobs against the same key and they draw
  from one budget. Retries draw tokens too — a retry storm can't blow
  through the limit.
- **`Retry.for_http()`** — when the API returns 429 with `Retry-After`
  (numeric or HTTP-date form), that wait replaces the backoff *and*
  pauses the shared limiter, so one throttled task slows the whole pool
  instead of every worker discovering the limit separately.
  `APIConnectionError` carries no status; the type filter decides for it.
- **`checkpoint=`** — every completed embedding is persisted (SQLite).
  If the run dies at item 40,000, rerunning the same line executes only
  the remainder. Delete the file when inputs or model change semantics
  beyond what the built-in guards catch.

Async version with `httpx` for higher throughput:

```python
import httpx
from pyarallel import async_parallel_map, RateLimit, Retry

async def embed(text):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": "text-embedding-3-small", "input": text},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]

result = await async_parallel_map(
    embed, texts,
    concurrency=20,
    rate_limit=RateLimit(500, "minute"),
    retry=Retry(attempts=3, on=(httpx.HTTPStatusError, httpx.ConnectError)),
)
```


