# Batch NCBI E-utilities / Entrez Queries

Genomics and literature-mining pipelines hammer NCBI's E-utilities — `esearch`, `esummary`, `efetch` — for tens of thousands of IDs. NCBI allows **3 requests/second without an API key, 10 with one**, and it *bans keys that burst past the limit*. The universal hand-roll is `time.sleep(0.34)` scattered through a notebook, which loses hours of work the moment the kernel dies at record 30,000.

Two things make this a poor fit for a plain thread pool, and a natural fit for pyarallel:

1. The rate budget belongs to the **API key**, and it's shared across *all three* E-utility calls — a semaphore-per-function can't express "10 req/s total across esearch, esummary, and efetch."
1. These jobs run for tens of minutes to hours. A crash must resume, not restart.

```
from Bio import Entrez
from pyarallel import parallel_map, Limiter, RateLimit, Retry

Entrez.email = "you@lab.edu"
Entrez.api_key = NCBI_API_KEY

# ONE budget for the key — pass this same object to every E-utility job.
ncbi = Limiter(RateLimit(10, "second"))   # 3/s without a key

def fetch_summary(gene_id):
    handle = Entrez.esummary(db="gene", id=gene_id)
    record = Entrez.read(handle)
    handle.close()
    return record["DocumentSummarySet"]["DocumentSummary"][0]

gene_ids = load_gene_ids()   # tens of thousands

result = parallel_map(
    fetch_summary, gene_ids,
    workers=6,
    rate_limit=ncbi,
    retry=Retry(attempts=4, backoff=1.0, on=(RuntimeError, OSError)),
    checkpoint="gene_summaries.ckpt",   # kernel dies at 30k? rerun resumes
)

# .successes() yields (index, value) and never raises — collect what
# worked and inspect the rest separately. (result.values() would raise an
# ExceptionGroup the moment a single ID fails permanently, discarding the
# whole batch — not what you want over tens of thousands of records.)
summaries = {gene_ids[idx]: s for idx, s in result.successes()}
for idx, exc in result.failures():
    log_failed(gene_ids[idx], exc)
```

A later job against the same key — say pulling full records for the genes you found — passes the **same `ncbi` limiter**, so both jobs draw from one 10 req/s budget instead of collectively doing 20 req/s and getting the key throttled:

```
def fetch_genbank(gene_id):
    handle = Entrez.efetch(db="nuccore", id=gene_id, rettype="gb", retmode="text")
    text = handle.read()
    handle.close()
    return text

# Same limiter → one shared budget across both jobs.
records = parallel_map(
    fetch_genbank, gene_ids,
    rate_limit=ncbi,
    checkpoint="genbank.ckpt",
    retry=Retry(attempts=4, on=(RuntimeError, OSError)),
)
```

For very large ID lists where the results shouldn't all sit in memory, stream instead of collecting — `parallel_iter` keeps a bounded window in flight and writes each record as it arrives:

```
from pyarallel import parallel_iter

for item in parallel_iter(fetch_genbank, gene_ids, rate_limit=ncbi,
                          batch_size=100):
    if item.ok:
        write_fasta(item.value)
    else:
        log_failed(item.index, item.error)
```

Note: `checkpoint=` is a `parallel_map` feature — the streaming engine doesn't take it (you're writing each record to disk as it arrives, so resume is your sink's job: skip IDs whose FASTA already exists). For a resumable *and* memory-bounded run, `parallel_map` with `checkpoint=` over a `batch_size` window is usually the better fit.

The same shape works for any hard-rate-limited scientific API where the budget is per-key and jobs run long: UniProt, Ensembl, PubChem, Crossref, Semantic Scholar (1 req/s per key), the PDB.
