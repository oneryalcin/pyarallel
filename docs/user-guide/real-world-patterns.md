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

## Processing Parquet Files

Transform multiple parquet files in parallel. Threads work well here since
the bottleneck is I/O (disk read/write), not CPU.

```python
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from pyarallel import parallel_map

def process_file(path):
    table = pq.read_table(path)
    filtered = table.filter(pa.compute.equal(table["status"], "active"))
    out_path = path.parent / "processed" / path.name
    pq.write_table(filtered, out_path)
    return {"file": path.name, "rows_in": len(table), "rows_out": len(filtered)}

files = list(Path("data/raw").glob("*.parquet"))

result = parallel_map(process_file, files)

for stats in result:
    print(f"{stats['file']}: {stats['rows_in']} → {stats['rows_out']} rows")
```

For very large file sets, use streaming to avoid holding all results in memory:

```python
from pyarallel import parallel_iter

for item in parallel_iter(process_file, files, batch_size=20):
    if item.ok:
        print(f"Done: {item.value['file']}")
    else:
        print(f"Failed: {files[item.index].name} — {item.error}")
```

!!! note
    For CPU-heavy transforms (not I/O-bound), use `executor="process"` for
    true parallelism. Process executor requires the function to be defined
    at module level and the script to use an `if __name__ == "__main__"` guard.

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
