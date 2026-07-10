# pyarallel benchmark lab

The performance sentences in the docs — "1.0× under the GIL, 2.4× on
free-threaded builds", "3.4× over threads for interpreters on a standard
build", "interpreter workers start faster than process fork/spawn", "one windowed
engine, O(n) driver" — used to rest on throwaway scripts that ran once on
one machine. This directory replaces them with **one runnable harness** so
the claims are falsifiable on *your* machine.

`bench.py` is stdlib + pyarallel only. `_workloads.py` holds the two target
functions (they must live in a real importable module, not the script's
`__main__`, or the `process`/`interpreter` executors can't re-import them in
a worker). Nothing here is imported by the package or the test suite.

## Running it

The harness prints its machine context — including `sys._is_gil_enabled()`
— so you always know which build produced a number.

```bash
# Standard build, default Python (3.12/3.13): thread + process only.
uv run --with . python benchmarks/bench.py

# Standard build with the interpreter executor (needs 3.14+, GIL on):
uv run --no-project --python cpython-3.14.6-macos-aarch64-none \
    --with . python benchmarks/bench.py

# Free-threaded build (GIL off): uv manages 3.13t / 3.14t for you.
uv run --no-project --python 3.14t --with . python benchmarks/bench.py

# Fast pass (fewer items/reps — ratios are weaker, see below):
uv run --with . python benchmarks/bench.py --quick

# Machine-readable:
uv run --with . python benchmarks/bench.py --json
```

**On free-threaded builds — what actually worked here:** `uv run --python
3.14t ...` Just Works — uv downloads and manages the free-threaded build; no
manual install needed. If it isn't cached yet, `uv python install 3.14t`
first. `3.13t` works the same way. Watch out: on a box that has *both* 3.14
variants installed, a bare `--python 3.14` may resolve to the free-threaded
one — don't guess, read the `GIL:` line the harness prints. To force the
GIL-on build, pass the full `cpython-3.14.6-...` specifier (or a Homebrew
`python3.14`).

`--with .` installs pyarallel into the ephemeral env; `--no-project` keeps
uv from touching the project's `.venv` (so switching Python builds doesn't
churn it).

## What each benchmark backs

| Benchmark | Measures | Documented claim it backs |
|---|---|---|
| **cpu-scaling** | `parallel_map` of a pure-Python CPU spin: `thread` workers=1 vs 4, plus `process`/`interpreter` at 4. | `thread_scaling` → "1.0× under the GIL, **2.4×** on free-threaded"; `thread/interpreter` on a GIL build → "**3.4×**, interpreters get true CPU parallelism on standard builds". |
| **engine-overhead** | Per-item wall time of `parallel_map` and `parallel_iter` over a no-op vs a plain loop, at two sizes a decade apart. | "one windowed engine, **O(n)** driver" — flat per-item cost across a 10× size range is the linearity evidence. |
| **worker-startup** | Time to first result on a *cold* pool (workers=1) per executor. | Interpreter cold-start beats process fork/spawn on GIL builds (~50 ms vs ~72 ms recorded; machine-dependent). |

### Why the item count matters (and why `--quick` under-reports)

`parallel_map` builds a **fresh pool per call**, so `process` and
`interpreter` pay their cold start-up on *every* call. The CPU parallelism
the 2.4×/3.4× claims describe only dominates once the per-call compute
outweighs that start-up. That is a real property of the API, not a
measurement trick — and it's exactly why **worker-startup** measures the
start-up cost on its own. The default run uses enough items (300) to reach
the steady-state ratio; `--quick` uses 40 and its interpreter ratio is
start-up-bound and low. Read the ratio at the item count that matches how
you actually call the library.

## Why this is not in CI

CI runs on shared cloud runners with noisy neighbours, throttled/burstable
CPUs, and no control over the Python build. A wall-clock speedup measured
there is dishonest: it would flap between "0.8×" and "3×" run to run and
prove nothing. So this lab is deliberately **for humans re-verifying on
their own hardware**, not a gate. The correctness of the executors *is*
tested in CI (including free-threaded and 3.14 interpreter jobs) — what
can't be trusted on shared runners is the *timing*, which is all this lab
produces.

Recorded runs from the author's machine live in [RESULTS.md](RESULTS.md),
each labelled with platform / Python / GIL status, with a verdict on whether
each documented claim reproduced.
