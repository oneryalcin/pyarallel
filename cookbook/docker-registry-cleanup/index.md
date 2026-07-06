# Bulk Docker Registry Tag Cleanup

Container registries accumulate cruft: thousands of stale images from CI builds that nobody prunes because the cleanup script always dies. It dies because registries rate-limit aggressively, and once you're throttled some registries fail *even read-only calls*, so a naive loop grinds through `429`s with no idea when it can resume — a commonly reported failure mode when scripting bulk deletes against a registry.

The example below uses the OCI Distribution Spec's manifest-delete endpoint (`DELETE .../manifests/<digest>`), which works against GHCR, GitLab, Harbor, ECR, and Artifact Registry. Docker Hub is the exception: its registry rejects this call, and tag deletion goes through the separate Hub REST API (`hub.docker.com/v2/...`) with different auth — the pyarallel mechanics below are identical, only the `delete_tag` body changes.

pyarallel turns this into a job you can actually finish:

```
import httpx
from pyarallel import parallel_map, RateLimit, Retry

def delete_manifest(tag):
    # Deletes the manifest by digest. NOTE: a digest can be shared by
    # several tags — deleting it removes the image for all of them.
    # Resolve the digest with the right Accept header and confirm no
    # live tag still points at it before pruning.
    r = httpx.delete(
        f"https://registry.example.com/v2/{REPO}/manifests/{tag.digest}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.oci.image.manifest.v1+json",
        },
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
    delete_manifest, stale_tags,
    workers=4,
    rate_limit=RateLimit(100, "minute"),   # stay UNDER the limit, don't discover it via 429
    retry=Retry(
        attempts=3,
        retry_if=is_throttled,
        wait_from=retry_after,             # honor the registry's Retry-After
    ),
    checkpoint="cleanup.ckpt",             # crash partway? the rerun resumes
    checkpoint_key=lambda t: t.digest,     # idempotent by digest — never re-delete
    max_errors=20,                         # hit a wall of 429s? stop, don't grind
)

print(f"deleted {len(result.successes())} tags")
for idx, exc in result.failures():
    print(f"skipped: {stale_tags[idx].name} — {exc}")
```

The three policies that matter here:

- **`RateLimit`** — the point is to *stay under* the registry's budget so you never trip the throttle, instead of the hand-roll's "fire everything, discover the limit as a `429` storm."
- **`checkpoint_key=lambda t: t.digest`** — deletion is idempotent by digest but re-issuing thousands of deletes for already-gone tags wastes the budget you're trying to conserve. Resume skips them.
- **`max_errors=20`** — when a registry throttles hard enough that even reads fail, there is no point continuing. Abort cheaply, return what got deleted, rerun after the window resets.

The pyarallel mechanics — rate limit, `Retry-After`, digest-keyed resume, `max_errors` — carry over to any registry with a REST delete API: GitHub Container Registry, GitLab, Harbor, AWS ECR, Google Artifact Registry. Only the `delete_manifest` body changes per registry (Docker Hub, as noted, needs its separate Hub API).
