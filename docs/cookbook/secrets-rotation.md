---
title: Bulk Secrets Rotation Without Double-Rotating
description: Rotate thousands of credentials across services with identity-keyed resume so a crash never rotates the same secret twice, plus early abort when rotations start failing.
---

# Bulk Secrets Rotation Without Double-Rotating

Security mandates rotating every service credential in Vault (or a
secrets manager). Unlike most fan-out jobs, this one has a **correctness**
requirement that plain parallelism gets wrong: rotation is *not
idempotent*. Rotate the same secret twice mid-flight and you invalidate
the copy a running service just picked up — an outage you caused. So a
crash at secret 4,000 of 10,000 must resume at 4,000 and **never**
re-rotate the 3,999 already done. Vault also rate-limits with
`429 + Retry-After`, and a cascade of rotation failures should stop the
job before it takes services down.

```python
import hvac
from pyarallel import parallel_map, Limiter, RateLimit, Retry

client = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)

def rotate(secret_path):
    # Generate a new credential and write it atomically.
    new_value = generate_credential()
    client.secrets.kv.v2.create_or_update_secret(
        path=secret_path, secret={"value": new_value}
    )
    notify_consumers(secret_path)   # tell services to reload
    return secret_path

def is_throttled(exc):
    return getattr(exc, "status_code", None) == 429

def retry_after(exc):
    headers = getattr(exc, "headers", None) or {}
    value = headers.get("Retry-After")
    return float(value) if value else None

secret_paths = list_secrets_to_rotate()   # thousands

result = parallel_map(
    rotate, secret_paths,
    workers=6,
    rate_limit=Limiter(RateLimit(100, "minute")),
    retry=Retry(
        attempts=3,
        retry_if=is_throttled,
        wait_from=retry_after,
    ),
    checkpoint="rotation.ckpt",
    checkpoint_key=lambda p: p,     # the whole point: never rotate a path twice
    max_errors=10,                  # rotation failures cascade — abort fast
)

print(f"rotated {len(result.successes())} secrets")

# The typed partial results turn "which of my 10k secrets is now in a
# half-rotated state?" from a 3 a.m. nightmare into a list. failures()
# yields (index, exception) — index back into the input to name the secret.
for idx, exc in result.failures():
    alert_oncall(f"secret {secret_paths[idx]} may be half-rotated: {exc}")
```

Why this is a pyarallel job and not a `ThreadPoolExecutor` job:

- **`checkpoint_key=lambda p: p`** — this isn't a performance nicety, it's
  the correctness guarantee. Identity-keyed resume means a re-run after a
  crash skips every already-rotated secret. Positional resume would break
  the instant the input list changed order between runs.
- **`max_errors=10`** — rotations that fail can cascade into outages.
  Stopping after ten failures and returning partial results is the safe
  default; grinding through is not.
- **`result.failures()` with typed errors** — after a partial run you need
  to know *exactly* which secrets are in an ambiguous state, not just
  that "some failed." The structured result is that list.

The same shape covers any non-idempotent bulk mutation under a rate
limit: rotating cloud IAM keys, cycling API tokens across a fleet,
re-issuing certificates (where a repeat can burn a quota), migrating
records where a double-write corrupts state.
