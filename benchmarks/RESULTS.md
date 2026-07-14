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


## Addendum — 2026-07-11, harness v2 (two-size engine-overhead)

The engine-overhead benchmark now measures two input sizes a decade
apart (one size cannot demonstrate linearity — flat per-item overhead
across a 10× range is the actual evidence). Standard build, same
machine as above, `--quick`:

```
[engine-overhead]  no-op items, two sizes (flat per-item = linear)
  n=   500
    parallel_map         9.145 us/item   (+9.113 us/item engine)
    parallel_iter       10.564 us/item   (+10.532 us/item engine)
  n=  5000
    parallel_map         8.994 us/item   (+8.965 us/item engine)
    parallel_iter       10.035 us/item   (+10.006 us/item engine)
```

Per-item overhead is flat across the range (9.11 → 8.97 µs/item):
the O(n)-driver claim holds as *measured linearity*, not extrapolation.
The single-size numbers in the runs above remain valid as recorded.

## Reusable execution-session spike — 2026-07-14

This spike asks whether repeated small maps save enough wall time when one
executor is reused to justify a separate public lifecycle design. It does not
benchmark a Pyarallel session implementation. `stdlib_reused` is an optimistic
lower bound; the product verdict is owned by the precommitted noise and control
rules in the [reviewed plan](../docs/development/plans/reusable-execution-session-benchmark.md).

Machine: macOS 26.4.1, Apple Silicon, 10 cores, `spawn`. Each strategy has one
untimed warmup and seven timed samples. The JSON artifacts match the checked-in
serializer and contain every raw nanosecond sample, derived metric, threshold,
calibration sample, and per-worker initializer duration:

- [CPython 3.12.8 run 1](results/2026-07-14-reusable-session-cpython312-run1.json)
- [CPython 3.12.8 run 2](results/2026-07-14-reusable-session-cpython312-run2.json)
- [CPython 3.14.6 standard, GIL enabled](results/2026-07-14-reusable-session-cpython314.json)

Only the post-fix official runs are recorded. During harness development,
failed contract probes exposed two invalid assumptions—subinterpreter callable
globals do not preserve initializer state, and initializer evidence must not
share a directory with worker rendezvous markers. The final harness persists
initializer proof before task publication and isolates the two marker
namespaces; invalid development runs did not enter the classifier.

### Official run 1 — CPython 3.12.8

Calibration: 4.881 ms task spin, 98.692 ms initializer. Verdict:
**advance-to-design**.

| Cell | Pyarallel fresh ms | stdlib fresh ms | stdlib reused ms | Reuse | Saved / extra call ms | Integration delta ms | Fresh MAD/median | Reused MAD/median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 124.283 | 122.349 | 123.889 | 0.988× | — | 1.934 | 0.0077 | 0.0063 |
| P3 | 373.017 | 366.980 | 145.381 | 2.524× | 110.800 | 6.038 | 0.0033 | 0.0031 |
| P10 | 1259.491 | 1238.074 | 225.895 | 5.481× | 112.464 | 21.417 | 0.0069 | 0.0076 |
| P64 | 2002.332 | 1969.804 | 960.917 | 2.050× | 112.099 | 32.528 | 0.0036 | 0.0069 |
| T10 | 414.410 | 408.951 | 405.063 | 1.010× | 0.432 | 5.459 | 0.0081 | 0.0045 |
| PI3 | 664.909 | 666.653 | 221.321 | 3.012× | 222.666 | -1.744 | 0.0099 | 0.0169 |
| PI10 | 2178.385 | 2154.349 | 228.833 | 9.415× | 213.946 | 24.035 | 0.0031 | 0.0146 |

### Official run 2 — CPython 3.12.8

Calibration: 4.862 ms task spin, 99.573 ms initializer. Verdict:
**advance-to-design**.

| Cell | Pyarallel fresh ms | stdlib fresh ms | stdlib reused ms | Reuse | Saved / extra call ms | Integration delta ms | Fresh MAD/median | Reused MAD/median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 124.963 | 124.632 | 124.772 | 0.999× | — | 0.331 | 0.0078 | 0.0174 |
| P3 | 370.832 | 373.257 | 145.259 | 2.570× | 113.999 | -2.425 | 0.0043 | 0.0053 |
| P10 | 1244.824 | 1234.264 | 225.709 | 5.468× | 112.062 | 10.560 | 0.0070 | 0.0120 |
| P64 | 1985.217 | 1980.777 | 963.581 | 2.056× | 113.022 | 4.440 | 0.0111 | 0.0146 |
| T10 | 399.697 | 404.449 | 397.647 | 1.017× | 0.756 | -4.751 | 0.0113 | 0.0059 |
| PI3 | 650.663 | 650.564 | 219.167 | 2.968× | 215.698 | 0.100 | 0.0048 | 0.0072 |
| PI10 | 2186.544 | 2182.728 | 227.954 | 9.575× | 217.197 | 3.816 | 0.0082 | 0.0045 |

### Context run — CPython 3.14.6, GIL enabled

Calibration: 5.044 ms task spin, 101.616 ms initializer. All 13 process and
interpreter cells were valid and clean. The process-owned classifier was
**advance-to-design**; interpreter mirrors remain contextual.

| Cell | Pyarallel fresh ms | stdlib fresh ms | stdlib reused ms | Reuse | Saved / extra call ms | Integration delta ms | Fresh MAD/median | Reused MAD/median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 139.964 | 139.904 | 138.532 | 1.010× | — | 0.059 | 0.0207 | 0.0097 |
| P3 | 414.317 | 415.796 | 160.006 | 2.599× | 127.895 | -1.479 | 0.0036 | 0.0065 |
| P10 | 1374.668 | 1379.342 | 244.308 | 5.646× | 126.115 | -4.674 | 0.0111 | 0.0172 |
| P64 | 2200.324 | 2193.426 | 1006.642 | 2.179× | 131.865 | 6.898 | 0.0068 | 0.0053 |
| T10 | 415.573 | 415.457 | 412.154 | 1.008× | 0.367 | 0.116 | 0.0047 | 0.0084 |
| PI3 | 698.857 | 691.788 | 235.300 | 2.940× | 228.244 | 7.069 | 0.0006 | 0.0077 |
| PI10 | 2324.232 | 2332.204 | 243.564 | 9.575× | 232.071 | -7.972 | 0.0034 | 0.0198 |
| IP1 | 80.681 | 45.612 | 46.220 | 0.987× | — | 35.069 | 0.0042 | 0.0047 |
| IP3 | 245.508 | 137.944 | 69.289 | 1.991× | 34.328 | 107.564 | 0.0076 | 0.0119 |
| IP10 | 811.826 | 461.312 | 148.587 | 3.105× | 34.747 | 350.513 | 0.0033 | 0.0023 |
| IP64 | 1559.080 | 1193.296 | 879.193 | 1.357× | 34.900 | 365.784 | 0.0050 | 0.0053 |
| IPI3 | 524.728 | 422.835 | 144.519 | 2.926× | 139.158 | 101.892 | 0.0020 | 0.0051 |
| IPI10 | 1787.204 | 1410.182 | 152.228 | 9.264× | 139.773 | 377.022 | 0.0017 | 0.0185 |

### Decision: advance to a separate lifecycle/API design

Both authoritative 3.12 runs were clean and independently passed every ordinary
process gate. Reusing the stdlib process pool saved roughly 110–114 ms per
additional small map in `P3`, `P10`, and `P64`, while the thread control stayed
near 1.0× and below 1 ms saved per extra call. The combined verdict is therefore
**`advance-to-design`**.

This does not approve `ParallelPool`, a public session API, implementation work,
or `0.11.0`. It earns a separately planned and reviewed lifecycle design for
ownership, shutdown, failure recovery, cancellation, concurrent calls, and
state isolation. The full Pyarallel-to-reused gap is not labelled reuse savings;
integration deltas remain separate in the tables and raw artifacts.
