# Recorded benchmark runs

Actual output from `benchmarks/bench.py` on the author's machine. Re-run it
yourself (see [README.md](README.md)) — the point of the lab is that you
don't have to trust these numbers. Timing is machine- and build-specific;
what should reproduce is the *direction and rough magnitude* of each ratio.

## Machine

| | |
|---|---|
| Platform | macOS-26.4.1-arm64 (Apple Silicon, 10 cores) |
| Builds | CPython **3.14.6** standard (GIL on) and **3.14.6+freethreaded** (GIL off), both uv-managed |
| Harness | `bench.py` default pass (5 reps, warmup, median; 300 CPU items × `spin(140_000)` ≈ 5 ms/item) |
| Date | 2026-07-10 |

## Run 1 — standard build, GIL **enabled** (`cpython-3.14.6`)

```
[cpu-scaling]  300 items x spin(n=140000)
  thread w1          1656.97 ms
  thread w4          1633.13 ms   scaling 1.01x  (claim: 1.0x GIL / 2.4x free-threaded)
  process w4          536.02 ms   thread/process 3.05x
  interpreter w4      491.25 ms   thread/interp 3.32x  (claim: 3.4x on GIL)

[engine-overhead]  20000 no-op items (O(n) driver)
  plain loop           0.020 us/item
  parallel_map         9.982 us/item   (+9.961 us/item engine)
  parallel_iter       10.398 us/item   (+10.378 us/item engine)

[worker-startup]  cold pool, workers=1, time to first result
  thread              0.09 ms
  process            72.03 ms
  interpreter        50.43 ms  (claim: ~30 ms)
```

## Run 2 — free-threaded build, GIL **disabled** (`cpython-3.14.6+freethreaded`)

```
[cpu-scaling]  300 items x spin(n=140000)
  thread w1          1559.02 ms
  thread w4           501.30 ms   scaling 3.11x  (claim: 1.0x GIL / 2.4x free-threaded)
  process w4          533.06 ms   thread/process 0.94x
  interpreter w4      714.90 ms   thread/interp 0.70x  (claim: 3.4x on GIL)

[engine-overhead]  20000 no-op items (O(n) driver)
  plain loop           0.029 us/item
  parallel_map        10.017 us/item   (+9.988 us/item engine)
  parallel_iter       10.149 us/item   (+10.12 us/item engine)

[worker-startup]  cold pool, workers=1, time to first result
  thread              0.12 ms
  process            83.26 ms
  interpreter       102.71 ms  (claim: ~30 ms)
```

The repo's default interpreter (3.12) was also spot-checked with `--quick`:
it degrades gracefully to `thread` + `process` only (no interpreter
executor before 3.14) and reports the same ~1.0× thread scaling under its
GIL. That build can't test the interpreter or free-threading claims, which
is why the recorded runs above use 3.14 / 3.14t.

## Do the documented claims hold?

| Claim (source) | Documented | Measured | Verdict |
|---|---|---|---|
| Threads don't scale CPU work under the GIL | ~1.0× | **1.01×** (Run 1) | ✅ holds |
| Threads scale CPU work on free-threaded builds | **2.4×** | **3.11×** (Run 2) | ✅ holds — the doc number is conservative; measured *higher* |
| Interpreters beat GIL threads on CPU work (standard build) | **3.4×** | **3.32×** (Run 1) | ✅ holds |
| `parallel_map` / `parallel_iter` are an O(n) driver | flat, small per item | **~10 µs/item**, flat, both builds | ✅ holds |
| Interpreter workers start faster than fork/spawn | interpreter < process | 50 ms vs 72 ms (Run 1) | ✅ holds on the GIL build |
| Interpreter worker start-up | **~30 ms** | **~50 ms** (GIL) / **~103 ms** (FT) | ⚠️ **does not reproduce** — see below |

### ⚠️ The "~30 ms" interpreter start-up number does not reproduce here

Cold-pool time-to-first-result for a 1-worker interpreter pool measured
**~50 ms on the standard build and ~103 ms on the free-threaded build** on
this machine (3.14.6) — not the ~30 ms the docs and the v0.7 plan quote. The
*qualitative* claim survives: on the standard build the interpreter pool
still starts faster than a process pool (50 ms vs 72 ms), which is the point
of the sentence. But the specific "~30 ms" figure is off by ~1.7×–3.4×.
Likely causes: the original throwaway was on a different machine / earlier
3.14 build, and/or it timed bare `InterpreterPoolExecutor` spin-up without
the first-task import round-trip that this harness (deliberately) includes.

On the free-threaded build the interpreter also loses to threads on CPU work
(0.70×) and to its own process pool on start-up (103 ms vs 83 ms) — expected,
since free-threading already gives threads true parallelism, so the
interpreter's per-worker re-import cost is pure overhead there. The 3.4×
interpreter win is a **standard-build** phenomenon, exactly as documented.

**Not fudged.** Per the lab's charter, the "~30 ms" mismatch is reported
rather than smoothed over. Whether to reword the docs claim is left to a
maintainer.

### A note on `--quick`

`--quick` (40 items) reports the interpreter ratio at ~1.8× on the GIL
build, not ~3.4× — the 40-item run is dominated by the one-time ~50 ms pool
start-up. This is expected and documented; use the default pass to read the
steady-state CPU-parallelism ratio.
