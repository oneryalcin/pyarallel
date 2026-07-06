---
title: Dataset Enrichment via API (pandas, HuggingFace)
description: Enrich DataFrame or dataset rows by calling an external API per row, with rate limiting and retry.
---

# Dataset Enrichment via API

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

