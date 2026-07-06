---
title: Bulk File Download
description: Download hundreds of files concurrently with retry and structured error handling.
---

# Bulk File Download

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

