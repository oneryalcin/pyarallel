---
title: Polite Web Scraping at Scale
description: Scrape thousands of pages with rate limiting to avoid IP bans, retry on transient failures, and per-URL error tracking.
---

# Web Scraping

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

