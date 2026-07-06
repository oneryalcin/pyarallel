---
title: Bulk Docker Registry Tag Cleanup Without a 429 Storm
description: Delete thousands of container image tags under a registry rate limit — resumable so a crash picks up where it stopped, with an early abort when the wall of 429s hits.
---

# Bulk Docker Registry Tag Cleanup

Container registries accumulate cruft: thousands of stale tags from CI
builds that nobody prunes because the cleanup script always dies. It
dies because registries rate-limit aggressively — Docker Hub's is famous
— and once you're throttled, *even read-only calls start failing*, so a
naive loop grinds through hundreds of `429`s with no idea when it can
resume. A real war story from the field: a script deleting 5,000+ tags
got throttled at ~820 and couldn't tell when the wall would lift.

pyarallel turns this into a job you can actually finish:

```python
import httpx
from pyarallel import parallel_map, RateLimit, Retry

def delete_tag(tag):
    r = httpx.delete(
        f"https://registry.example.com/v2/{REPO}/manifests/{tag.digest}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    r.raise_for_status()
    return tag.name

def is_throttled(exc):
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429

def retry_after(exc):
    value = exc.response.headers.get("retry-after")
    return float(value) if value else None

stale_tags = find_stale_tags(REPO)   # thousands

result = parallel_map(
    delete_tag, stale_tags,
    workers=4,
    rate_limit=RateLimit(100, "minute"),   # stay UNDER the limit, don't discover it via 429
    retry=Retry(
        attempts=3,
        retry_if=is_throttled,
        wait_from=retry_after,             # honor the registry's Retry-After
    ),
    checkpoint="cleanup.ckpt",             # crash at tag 821? rerun resumes at 821
    checkpoint_key=lambda t: t.digest,     # idempotent by digest — never re-delete
    max_errors=20,                         # hit a wall of 429s? stop, don't grind
)

print(f"deleted {len(result.successes())} tags")
for idx, exc in result.failures():
    print(f"skipped: {stale_tags[idx].name} — {exc}")
```

The three policies that matter here:

- **`RateLimit`** — the point is to *stay under* the registry's budget so
  you never trip the throttle, instead of the hand-roll's "fire
  everything, discover the limit as a `429` storm."
- **`checkpoint_key=lambda t: t.digest`** — deletion is idempotent by
  digest but re-issuing thousands of deletes for already-gone tags wastes
  the budget you're trying to conserve. Resume skips them.
- **`max_errors=20`** — when a registry throttles hard enough that even
  reads fail, there is no point continuing. Abort cheaply, return what
  got deleted, rerun after the window resets.

Works the same for any registry with a REST delete API and rate limits:
GitHub Container Registry, GitLab, Harbor, AWS ECR, Google Artifact
Registry.
