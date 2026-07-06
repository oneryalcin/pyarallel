#!/usr/bin/env python3
"""
Resilient API Jobs (v0.5 features)
==================================

The overnight-job toolkit: streaming with a bounded window, early abort
on a dead API, resumable runs keyed by item identity, per-item
accounting, and a debug flag.

Run with: python examples/06_resilient_api_jobs.py
"""

import os
import random
import tempfile
import time
from pathlib import Path

from pyarallel import Aborted, Retry, parallel_iter, parallel_map

random.seed(7)


def fetch(item_id):
    """Simulate an API call: mostly fast, occasionally slow or failing."""
    time.sleep(random.uniform(0.005, 0.02))
    if item_id % 37 == 0 and item_id > 0:
        raise ConnectionError(f"transient error on {item_id}")
    return {"id": item_id, "value": item_id * 10}


print("Example 1: Streaming with a bounded window")
print("=" * 50)
# Input is consumed lazily, a bounded window is in flight, and a slow
# item never stalls the items behind it. ordered=True yields in input
# order; attempts/duration give per-item accounting.
count = 0
for item in parallel_iter(
    fetch,
    range(60),
    workers=8,
    ordered=True,
    retry=Retry(attempts=3, backoff=0.05, jitter=False),
):
    count += 1
    if item.index < 3:
        print(
            f"  item {item.index}: ok={item.ok} "
            f"attempts={item.attempts} duration={item.duration * 1000:.1f}ms"
        )
print(f"Streamed {count} results in input order")
print()


print("Example 2: Stop paying for a dead API (max_errors)")
print("=" * 50)


def dead_api(item_id):
    raise ConnectionError("api is down")


result = parallel_map(dead_api, range(10_000), workers=8, max_errors=10)
real = [e for _, e in result.failures() if not isinstance(e, Aborted)]
unrun = [e for _, e in result.failures() if isinstance(e, Aborted)]
print(f"10,000 items against a dead API: {len(real)} real calls failed,")
print(f"{len(unrun)} items never ran (marked Aborted) — cost stayed bounded")
print()


print("Example 3: Resume that survives evolving inputs (checkpoint_key)")
print("=" * 50)
ckpt = Path(tempfile.mkdtemp()) / "job.ckpt"
calls = []


def embed(doc):
    calls.append(doc["id"])
    time.sleep(0.005)
    return {"id": doc["id"], "vector": [len(doc["text"])]}


docs = [{"id": f"doc-{i}", "text": f"text {i}"} for i in range(20)]
parallel_map(embed, docs, checkpoint=ckpt, checkpoint_key=lambda d: d["id"])
print(f"Run 1: embedded {len(calls)} documents")

# Next week: three new documents prepended — only they run.
calls.clear()
new_docs = [{"id": f"doc-new-{i}", "text": f"new {i}"} for i in range(3)]
result = parallel_map(
    embed, new_docs + docs, checkpoint=ckpt, checkpoint_key=lambda d: d["id"]
)
print(f"Run 2 (3 new docs prepended): only {len(calls)} executed — {calls}")
print(f"Full result still complete: {len(result.ok_values())} vectors")
print()


print("Example 4: One flag flips production code into debug mode")
print("=" * 50)
# sequential=True: no pool, real stack traces, working breakpoints.
debug = os.environ.get("DEBUG") == "1"
result = parallel_map(fetch, range(5), workers=8, sequential=debug)
print(f"sequential={debug}: {len(result.ok_values())} results")
print("Set DEBUG=1 to run inline in the calling thread")
