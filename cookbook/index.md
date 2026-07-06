# Cookbook

Complete, copy-pasteable recipes for real workloads — not toy examples. Each one shows the specific combination of policies (rate limiting, retry with server-driven backoff, checkpoint/resume, early abort) that makes pyarallel the right tool, not just "a parallel loop."

The through-line for most of these: they're jobs where **repeating work is expensive or dangerous**, or where **the rate budget is a shared external resource** — exactly where a hand-rolled semaphore + retry silently does the wrong thing.

## APIs & LLMs

- [**Batch LLM Calls**](https://oneryalcin.github.io/pyarallel/cookbook/llm-batch-calls/index.md) — thousands of chat-completion calls with `Retry-After` backoff, checkpoint resume, and an overnight kill-switch. OpenAI, Anthropic, LiteLLM.
- [**Batch Embedding Generation**](https://oneryalcin.github.io/pyarallel/cookbook/embeddings/index.md) — embed thousands of texts with a shared rate-limit budget and crash-safe resume.
- [**Dataset Enrichment via API**](https://oneryalcin.github.io/pyarallel/cookbook/dataset-enrichment/index.md) — enrich pandas / HuggingFace rows by calling an API per row.

## Web & files

- [**Polite Web Scraping at Scale**](https://oneryalcin.github.io/pyarallel/cookbook/web-scraping/index.md) — rate-limited to avoid IP bans, retry on transient failures, per-URL error tracking.
- [**Bulk File Download**](https://oneryalcin.github.io/pyarallel/cookbook/bulk-file-download/index.md) — hundreds of files concurrently with retry and structured errors.

## Infrastructure & ops

- [**Bulk GitHub Repo Changes**](https://oneryalcin.github.io/pyarallel/cookbook/github-bulk-repo-changes/index.md) — apply one change across a whole org without tripping abuse detection; resume, and a migration kill-switch.
- [**Bulk Docker Registry Cleanup**](https://oneryalcin.github.io/pyarallel/cookbook/docker-registry-cleanup/index.md) — delete thousands of image tags without a 429 storm; resumable.
- [**Bulk Secrets Rotation**](https://oneryalcin.github.io/pyarallel/cookbook/secrets-rotation/index.md) — rotate thousands of credentials with identity-keyed resume so a crash never rotates the same secret twice.

## Data & science

- [**Streaming ETL Pipeline**](https://oneryalcin.github.io/pyarallel/cookbook/streaming-etl/index.md) — process millions of rows at constant memory.
- [**Batch NCBI E-utilities / Entrez**](https://oneryalcin.github.io/pyarallel/cookbook/ncbi-entrez-batch/index.md) — fetch tens of thousands of GenBank/PubMed records under NCBI's rate limit with one shared budget and resume.

## CPU-bound

- [**CPU-Bound Fan-Out**](https://oneryalcin.github.io/pyarallel/cookbook/cpu-fanout/index.md) — image resizing, PDF parsing, hashing across process or interpreter workers.

______________________________________________________________________

New to pyarallel? Start with the [Quick Start](https://oneryalcin.github.io/pyarallel/getting-started/quickstart/index.md), or see the [Comparison](https://oneryalcin.github.io/pyarallel/getting-started/comparison/index.md) to decide whether it fits your problem.
