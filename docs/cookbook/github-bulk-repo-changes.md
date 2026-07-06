---
title: Bulk GitHub Repo Changes Across an Org
description: Apply one change to hundreds of repositories without tripping GitHub's abuse detection — pool-wide Retry-After, identity-keyed resume, and a migration kill-switch.
---

# Bulk GitHub Repo Changes Across an Org

Platform engineering has to touch every repo in an org: add a
`CODEOWNERS` file, enable branch protection, bump a shared workflow.
Do it with a naive `ThreadPoolExecutor` and you hit the wall GitHub
built for exactly this — *secondary rate limits* (abuse detection) that
return `403`/`429` with a `Retry-After` header when you burst mutations.
Worse, this is a **write** fan-out across hundreds of repos: if it starts
going wrong, you want to stop, not power through 500 half-applied
changes.

```python
import time
from github import Github, GithubException
from pyarallel import parallel_map, Limiter, RateLimit, Retry

gh = Github(TOKEN)

def add_codeowners(repo_full_name):
    repo = gh.get_repo(repo_full_name)
    repo.create_file(
        ".github/CODEOWNERS",
        "Add CODEOWNERS",
        "* @my-org/platform-team\n",
        branch=repo.default_branch,
    )
    return repo_full_name

def is_secondary_limit(exc):
    # Secondary/abuse limits are 403 or 429; primary is 403 with a
    # rate-limit-remaining of 0. Retry both — fail fast on 404/422.
    return isinstance(exc, GithubException) and exc.status in (403, 429)

def retry_after(exc):
    headers = getattr(exc, "headers", None) or {}
    value = headers.get("retry-after")
    return float(value) if value else None

repos = [r.full_name for r in gh.get_organization("my-org").get_repos()]

# The token's 5,000 req/hr budget — shared across every job that uses it.
budget = Limiter(RateLimit(5000, "hour"))

result = parallel_map(
    add_codeowners, repos,
    workers=8,
    rate_limit=budget,
    retry=Retry(
        attempts=4,
        backoff=2.0,
        retry_if=is_secondary_limit,
        wait_from=retry_after,          # one worker's Retry-After pauses the whole pool
    ),
    checkpoint="codeowners.ckpt",
    checkpoint_key=lambda name: name,   # resume by repo — never re-mutate a done one
    max_errors=25,                      # the kill-switch: abort the migration if it goes wrong
)

for idx, name in result.successes():
    print(f"done: {name}")
for idx, exc in result.failures():
    print(f"needs attention: {repos[idx]} — {exc}")
```

Why each policy earns its place:

- **`wait_from=retry_after`** — GitHub's docs literally instruct you to
  honor `Retry-After` on secondary limits. Pyarallel applies it
  *pool-wide*: one worker's throttle signal pauses the shared limiter, so
  all eight back off together instead of each discovering the block
  separately and making the abuse detection angrier.
- **`checkpoint_key=lambda name: name`** — a mutation must never run
  twice. Keyed by repo name, a resumed run skips every repo already
  changed, even if the input list was reordered.
- **`max_errors=25`** — the feature that turns a dangerous bulk write
  into a safe one. If changes start failing (a bad token scope, an API
  incident), the run stops admitting work and hands back partial
  results instead of grinding a broken change across the whole org.
- **`result.failures()`** — yields `(index, exception)` pairs; index
  back into `repos` for the exact repos left untouched, typed and ready
  to feed into a narrower rerun.

This pattern generalizes to any GitHub bulk operation — opening PRs across
repos (the classic abuse-detection trigger), updating topics, syncing
labels — and to any API with `Retry-After` secondary limits.
