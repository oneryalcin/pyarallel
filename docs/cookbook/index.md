---
title: Cookbook
description: Adaptable pyarallel recipes for real workloads — batch LLM calls, embeddings, scraping, bulk GitHub/registry operations, scientific-API fetches, secrets rotation, and CPU fan-out.
---

# Cookbook

Adaptable, end-to-end patterns for real workloads — not isolated API snippets.
Each one shows the specific combination of policies (rate limiting,
retry with server-driven backoff, checkpoint/resume, early abort) that
makes pyarallel the right tool, not just "a parallel loop."

The through-line for most of these: they're jobs where **repeating work
is expensive or dangerous**, or where **the rate budget is a shared
external resource** — exactly where a hand-rolled semaphore + retry
silently does the wrong thing.

## APIs & LLMs

- [**Batch LLM Calls**](llm-batch-calls.md) — thousands of
  chat-completion calls with `Retry-After` backoff, checkpoint resume,
  and an overnight kill-switch. OpenAI, Anthropic, LiteLLM.
- [**Batch Embedding Generation**](embeddings.md) — embed thousands of
  texts with a shared rate-limit budget and crash-safe resume.
- [**Dataset Enrichment via API**](dataset-enrichment.md) — enrich
  pandas / HuggingFace rows by calling an API per row.

## Web & files

- [**Polite Web Scraping at Scale**](web-scraping.md) — rate-limited to
  avoid IP bans, retry on transient failures, per-URL error tracking.
- [**Bulk File Download**](bulk-file-download.md) — hundreds of files
  concurrently with retry and structured errors.

## Infrastructure & ops

- [**Bulk GitHub Repo Changes**](github-bulk-repo-changes.md) — apply
  one change across a whole org without tripping abuse detection;
  resume, and a migration kill-switch.
- [**Bulk Docker Registry Cleanup**](docker-registry-cleanup.md) —
  delete thousands of image tags without a 429 storm; resumable.
- [**Bulk Secrets Rotation**](secrets-rotation.md) — rotate thousands of
  credentials with identity-keyed resume so a crash never rotates the
  same secret twice.

## Data & science

- [**Streaming ETL Pipeline**](streaming-etl.md) — process millions of
  rows at constant memory.
- [**Batch NCBI E-utilities / Entrez**](ncbi-entrez-batch.md) — fetch
  tens of thousands of GenBank/PubMed records under NCBI's rate limit
  with one shared budget and resume.

## CPU-bound

- [**CPU-Bound Fan-Out**](cpu-fanout.md) — image resizing, PDF parsing,
  hashing across process or interpreter workers.

---

New to pyarallel? Start with the [Quick Start](../getting-started/quickstart.md),
or see the [Comparison](../getting-started/comparison.md) to decide
whether it fits your problem.
