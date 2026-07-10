---
title: Batch LLM Calls (OpenAI, Anthropic, LiteLLM)
description: Run thousands of chat-completion calls with rate limiting, Retry-After backoff, checkpoint resume, and an overnight kill-switch.
---

# Batch LLM Calls

Run thousands of chat-completion calls — classification, extraction,
summarization — against a rate-limited LLM API. The problems are always
the same: the provider throttles you (429s), individual calls fail
randomly, calls are expensive enough that you never want to pay for one
twice, and a dead API at 2 a.m. must not burn the quota retrying forever.

```python
import openai
from pyarallel import parallel_map, Limiter, RateLimit, Retry

client = openai.OpenAI()

def classify(ticket):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Classify the support ticket: billing, bug, or feature."},
            {"role": "user", "content": ticket},
        ],
    )
    return response.choices[0].message.content

# One Limiter per API key — every job that spends this quota shares it.
limiter = Limiter(RateLimit(450, "minute", burst=10))

result = parallel_map(
    classify, tickets,
    workers=10,
    rate_limit=limiter,
    retry=Retry.for_http(               # 429 + Retry-After, prewired (v0.9)
        attempts=4,
        backoff=2.0,
        on=(openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError),
        statuses={429, 500, 502, 503, 504},
    ),
    checkpoint="classify.ckpt",         # paid-for answers survive a crash
    max_errors=20,                      # dead API → abort near 20 failures, not at 20,000
)

for idx, label in result.successes():
    save(tickets[idx], label)
for idx, exc in result.failures():
    log_failed(tickets[idx], exc)
```

Why each policy is there:

- **`Retry.for_http()`** — when the provider says `Retry-After: 30`
  (numeric *or* HTTP-date form), that wait replaces the backoff *and*
  pauses the shared limiter. One throttled call slows the whole pool;
  without it, all 10 workers discover the 429 separately and the retry
  storm makes it worse. `statuses=` widens the default `{429, 503}` to
  the 5xx codes `openai.InternalServerError` represents;
  `APIConnectionError` carries no status, so the type filter alone
  decides for it. (Verified against the real `openai` exception types.)
- **`checkpoint=`** — LLM calls cost real money. Completed answers are
  persisted as they finish; a crash at item 8,000 of 10,000 resumes
  with 2,000 calls, not 10,000. Inputs that evolve between runs? Key
  rows by identity with `checkpoint_key=lambda t: t.id`.
- **`max_errors=20`** — the overnight-job guard. If the API dies, the
  run stops admitting work once the 20th post-retry failure lands — at
  most one admission window (default `2 × workers`) is still in flight —
  and returns partial results; the morning rerun resumes from the
  checkpoint. Shrink `window_size` to tighten that bound on expensive
  calls, at some throughput cost.

The recipe is client-agnostic: swap the `openai` call for
[LiteLLM](https://docs.litellm.ai/) (multi-provider routing), or raw
`httpx` — pyarallel only sees a function and its exceptions.

