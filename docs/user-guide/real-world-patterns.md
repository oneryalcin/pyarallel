# Real-World Patterns

Practical examples for common workloads. Each pattern shows a complete,
copy-pasteable approach — not a toy example.

## Batch Embedding Generation

Call an embedding API for thousands of texts with rate limiting and retry.
Works with OpenAI, Cohere, HuggingFace Inference API, or any HTTP endpoint.

```python
import openai
from pyarallel import parallel_map, RateLimit, Retry

client = openai.OpenAI()

def embed(text):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding

texts = ["document one", "document two", ...]  # thousands of texts

result = parallel_map(
    embed, texts,
    workers=10,
    rate_limit=RateLimit(500, "minute"),    # stay under API limits
    retry=Retry(attempts=3, backoff=1.0, on=(openai.RateLimitError, openai.APIConnectionError)),
    batch_size=100,                         # don't submit all at once
)

embeddings = result.values()  # list of vectors, same order as texts
```

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
