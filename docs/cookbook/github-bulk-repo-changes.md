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

def is_rate_limit(exc):
    # Retry ONLY rate limits — never a plain permission/scope 403, which
    # will fail identically on every retry. A rate-limit 403/429 carries
    # a Retry-After header, an exhausted x-ratelimit-remaining, or an
    # "abuse"/"secondary rate limit" message; a bad-scope 403 carries none.
    if not isinstance(exc, GithubException) or exc.status not in (403, 429):
        return False
    headers = getattr(exc, "headers", None) or {}
    if headers.get("retry-after") or headers.get("x-ratelimit-remaining") == "0":
        return True
    message = str(getattr(exc, "data", "")).lower()
    return "secondary rate limit" in message or "abuse" in message

def github_wait(exc):
    headers = getattr(exc, "headers", None) or {}
    if headers.get("retry-after"):                       # secondary limit, told when
        return float(headers["retry-after"])
    if headers.get("x-ratelimit-remaining") == "0" and headers.get("x-ratelimit-reset"):
        return max(0.0, float(headers["x-ratelimit-reset"]) - time.time())  # primary: wait for reset
    return 60.0                                          # secondary, no header: GitHub says wait >= 60s

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
        retry_if=is_rate_limit,
        wait_from=github_wait,          # one worker's throttle wait pauses the whole pool
    ),
    # Why not Retry.for_http()? GitHub signals rate limits as 403 WITH
    # rate-limit headers — but plain 403 is also "no permission", which
    # must NOT be retried. Status alone can't tell them apart, so this
    # recipe keeps its header-aware predicate. for_http covers APIs
    # where status is the whole signal.
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

- **`wait_from=github_wait`** — GitHub's docs are specific: honor
  `Retry-After` when present, wait until `x-ratelimit-reset` when the
  primary budget is exhausted, and back off at least 60s on a secondary
  limit with no header. `retry_if` deliberately does *not* retry a plain
  permission 403 — that would fail identically on every attempt. Pyarallel
  applies the wait *pool-wide*: one worker's throttle signal pauses the
  shared limiter, so all eight back off together instead of each
  discovering the block separately and making abuse detection angrier.
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
