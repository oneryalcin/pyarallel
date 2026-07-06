# Real-World Patterns

Practical examples for common workloads. Each pattern shows a complete,
copy-pasteable approach — not a toy example.

## Batch LLM Calls

Run thousands of chat-completion calls — classification, extraction,
summarization — against a rate-limited LLM API. The problems are always
the same: the provider throttles you (429s), individual calls fail
randomly, calls are expensive enough that you never want to pay for one
twice, and a dead API at 2 a.m. must not burn the quota retrying forever.

```python
import openai
from pyarallel import parallel_map, Limiter, RateLimit, Retry

client = openai.OpenAI()

def classify(ticket):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Classify the support ticket: billing, bug, or feature."},
            {"role": "user", "content": ticket},
        ],
    )
    return response.choices[0].message.content

def retry_after(exc):
    """Honor the server's Retry-After header when present."""
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response else None
    return float(header) if header else None

# One Limiter per API key — every job that spends this quota shares it.
limiter = Limiter(RateLimit(450, "minute", burst=10))

result = parallel_map(
    classify, tickets,
    workers=10,
    rate_limit=limiter,
    retry=Retry(
        attempts=4,
        backoff=2.0,
        on=(openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError),
        wait_from=retry_after,          # a 429 pauses the WHOLE pool
    ),
    checkpoint="classify.ckpt",         # paid-for answers survive a crash
    max_errors=20,                      # dead API → abort near 20 failures, not at 20,000
)

for idx, label in result.successes():
    save(tickets[idx], label)
for idx, exc in result.failures():
    log_failed(tickets[idx], exc)
```

Why each policy is there:

- **`wait_from=retry_after`** — when the provider says `Retry-After: 30`,
  that wait replaces the backoff *and* pauses the shared limiter. One
  throttled call slows the whole pool; without it, all 10 workers
  discover the 429 separately and the retry storm makes it worse.
- **`checkpoint=`** — LLM calls cost real money. Completed answers are
  persisted as they finish; a crash at item 8,000 of 10,000 resumes
  with 2,000 calls, not 10,000. Inputs that evolve between runs? Key
  rows by identity with `checkpoint_key=lambda t: t.id`.
- **`max_errors=20`** — the overnight-job guard. If the API dies, the
  run stops admitting work once the 20th post-retry failure lands — at
  most one admission window (default `2 × workers`) is still in flight —
  and returns partial results; the morning rerun resumes from the
  checkpoint. Shrink `batch_size` to tighten that bound on expensive
  calls, at some throughput cost.

The recipe is client-agnostic: swap the `openai` call for
[LiteLLM](https://docs.litellm.ai/) (multi-provider routing), or raw
`httpx` — pyarallel only sees a function and its exceptions.

## Batch Embedding Generation

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

def retry_after(exc):
    """Honor the server's Retry-After header when present."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    header = response.headers.get("retry-after")
    return float(header) if header else None

texts = ["document one", "document two", ...]  # thousands of texts

# One Limiter = one budget. Reuse it across every call that spends
# this API key's quota.
limiter = Limiter(RateLimit(500, "minute", burst=20))

result = parallel_map(
    embed, texts,
    workers=10,
    rate_limit=limiter,
    retry=Retry(
        attempts=4,
        backoff=1.0,
        on=(openai.RateLimitError, openai.APIConnectionError),
        wait_from=retry_after,              # a 429 pauses the WHOLE pool
    ),
    batch_size=100,                         # in-flight window (default 2 x workers)
    checkpoint="embeddings.ckpt",           # crash at item 40k? rerun resumes
)

embeddings = result.values()  # list of vectors, same order as texts
```

What each piece buys you:

- **Shared `Limiter`** — the quota belongs to the API key, not this call.
  Pass the same instance to other jobs against the same key and they draw
  from one budget. Retries draw tokens too — a retry storm can't blow
  through the limit.
- **`wait_from`** — when the API returns 429 with `Retry-After`, that wait
  replaces the backoff *and* pauses the shared limiter, so one throttled
  task slows the whole pool instead of every worker discovering the limit
  separately.
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


## Web Scraping

Scraping more than a handful of pages means solving several problems at once:
you need concurrency (sequential fetches are too slow), rate limiting (too fast
and you get IP-banned), retry (pages fail randomly with 503s and timeouts),
and error tracking (you need to know *which* URLs failed so you can retry them).
With raw `ThreadPoolExecutor` you end up hand-rolling all of this every time.

```python
import httpx
from bs4 import BeautifulSoup
from pyarallel import parallel_map, RateLimit, Retry

def scrape_page(url):
    r = httpx.get(url, timeout=10, follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return {
        "url": url,
        "title": soup.title.string.strip() if soup.title else None,
        "links": [a["href"] for a in soup.find_all("a", href=True)],
    }

urls = [...]  # hundreds or thousands of URLs

result = parallel_map(
    scrape_page, urls,
    workers=10,
    rate_limit=RateLimit(5, "second"),     # 5 req/s — polite enough to avoid bans
    retry=Retry(attempts=3, backoff=2.0, on=(httpx.ConnectError, httpx.ReadTimeout)),
    timeout=300.0,                          # 5 min wall-clock limit for the whole job
)
```

**What pyarallel handles for you here:**

- **Rate limiting** prevents IP bans — the token bucket spreads requests evenly
  instead of bursting. `RateLimit(5, "second")` means one request every 200ms,
  not 5 requests in the first 10ms then silence.
- **Retry with backoff** handles transient 503s and connection resets without
  you writing try/except loops inside the worker.
- **Timeout** kills the entire operation if a site is unresponsive, instead of
  hanging forever on one slow page.
- **Structured errors** tell you exactly which URLs failed and why:

```python
if not result.ok:
    for idx, exc in result.failures():
        print(f"Failed: {urls[idx]} — {type(exc).__name__}: {exc}")

    # Retry just the failures with more patience
    failed_urls = [urls[idx] for idx, _ in result.failures()]
    retry_result = parallel_map(
        scrape_page, failed_urls,
        workers=3,
        rate_limit=RateLimit(1, "second"),
        retry=Retry(attempts=5, backoff=5.0),
    )
```

For large crawls (10K+ URLs), use streaming so results don't accumulate in
memory:

```python
from pyarallel import parallel_iter

for item in parallel_iter(scrape_page, url_generator(), workers=10,
                          rate_limit=RateLimit(5, "second"), batch_size=500):
    if item.ok:
        save_to_db(item.value)
    else:
        log_failed_url(item.index, item.error)
```

## Dataset Enrichment via API

Enrich rows in a dataset by calling an external API per row. Works with
pandas DataFrames, HuggingFace datasets, or any iterable of records.

```python
import requests
from pyarallel import parallel_map, RateLimit, Retry

def geocode(address):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data:
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
    return None

addresses = df["address"].tolist()

result = parallel_map(
    geocode, addresses,
    workers=4,
    rate_limit=RateLimit(1, "second"),     # respect Nominatim's rate limit
    retry=Retry(attempts=2, on=(requests.ConnectionError, requests.Timeout)),
)

df["lat"] = [r["lat"] if r else None for r in result]
df["lon"] = [r["lon"] if r else None for r in result]
```

### With HuggingFace Datasets

```python
import requests
from datasets import load_dataset
from pyarallel import parallel_map, RateLimit

dataset = load_dataset("imdb", split="train")

def classify_sentiment(text):
    r = requests.post("http://localhost:8000/predict", json={"text": text})
    return r.json()["label"]

result = parallel_map(
    classify_sentiment,
    dataset["text"],
    workers=8,
    rate_limit=RateLimit(100, "second"),
    batch_size=500,
)

dataset = dataset.add_column("predicted_sentiment", result.values())
```

## Bulk File Download

Download hundreds of files with concurrency control and retry:

```python
import httpx
from pathlib import Path
from pyarallel import async_parallel_map, RateLimit, Retry

async def download(url):
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        filename = Path("downloads") / url.split("/")[-1]
        filename.write_bytes(r.content)
        return {"url": url, "size": len(r.content)}

urls = [...]  # hundreds of file URLs

result = await async_parallel_map(
    download, urls,
    concurrency=20,
    retry=Retry(attempts=3, backoff=2.0, on=(httpx.ConnectError, httpx.ReadTimeout)),
)

if not result.ok:
    for idx, exc in result.failures():
        print(f"Failed: {urls[idx]} — {exc}")
```

## Streaming ETL Pipeline

Process millions of database rows without loading everything into memory:

```python
import json
from pyarallel import parallel_iter

def fetch_rows():
    """Yield rows from a database cursor — never loads full result set."""
    cursor.execute("SELECT * FROM events WHERE processed = false")
    while batch := cursor.fetchmany(1000):
        yield from batch

def transform(row):
    return {
        "event_id": row["id"],
        "timestamp": row["created_at"].isoformat(),
        "payload": json.loads(row["raw_data"]),
    }

output_buffer = []
for item in parallel_iter(transform, fetch_rows(), workers=8, batch_size=500):
    if item.ok:
        output_buffer.append(item.value)
        if len(output_buffer) >= 1000:
            write_parquet_batch(output_buffer)
            output_buffer.clear()
    else:
        log_error(item.index, item.error)
```

## CPU-Bound Fan-Out

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
  gives the same true parallelism with ~30 ms workers and no OS
  processes — but C extensions like Pillow/numpy don't support
  subinterpreters yet, so *this* recipe stays on `"process"`. See
  [choosing the right executor](best-practices.md#interpreters-executorinterpreter-python-314).
- **Heavy multiprocessing** (shared memory, per-worker state, CPU
  pinning) is [mpire](https://pypi.org/project/mpire/)'s specialty —
  see the [comparison](../getting-started/comparison.md#vs-mpire) for
  where each tool wins.
