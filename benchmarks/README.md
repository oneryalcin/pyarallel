# pyarallel benchmark lab

The performance sentences in the docs — "1.0× under the GIL, 2.4× on
free-threaded builds", "3.4× over threads for interpreters on a standard
build", "interpreter workers start faster than process fork/spawn", "one windowed
engine, O(n) driver" — used to rest on throwaway scripts that ran once on
one machine. This directory replaces them with **one runnable harness** so
the claims are falsifiable on *your* machine.

`bench.py` is stdlib + pyarallel only. `_workloads.py` holds the target
functions (they must live in a real importable module, not the script's
`__main__`, or the `process`/`interpreter` executors can't re-import them in
a worker). Nothing here is imported by the production package; deterministic
harness tests import these modules directly.

## Running it

The harness prints its machine context — including `sys._is_gil_enabled()`
— so you always know which build produced a number.

```bash
# Standard build, default Python (3.12/3.13): thread + process only.
uv run --with . python benchmarks/bench.py

# Standard build with the interpreter executor (needs 3.14+, GIL on):
PYTHONPATH="$PWD" uv run --no-project \
    --python cpython-3.14.6-macos-aarch64-none \
    --with . python benchmarks/bench.py

# Free-threaded build (GIL off): uv manages 3.13t / 3.14t for you.
PYTHONPATH="$PWD" uv run --no-project --python 3.14t \
    --with . python benchmarks/bench.py

# Fast pass (fewer items/reps — ratios are weaker, see below):
uv run --with . python benchmarks/bench.py --quick

# Machine-readable:
uv run --with . python benchmarks/bench.py --json

# Add non-gating one-worker, one-item, and no-op session diagnostics:
uv run --with . python benchmarks/bench.py --diagnostic
```

**On free-threaded builds — what actually worked here:** the `3.14t` command
above lets uv download and manage the free-threaded build; no manual install is
needed. The `PYTHONPATH` prefix is required for the isolated 3.14 interpreter
benchmark targets. If the build isn't cached yet, run `uv python install 3.14t`
first. `3.13t` works the same way and does not need the prefix because it has no
interpreter executor. Watch out: on a box that has *both* 3.14
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
| **execution-session** | Repeated small maps through fresh Pyarallel pools, fresh stdlib pools, and one reused stdlib pool. | Decides whether worker reuse is stable and material enough to earn a separate lifecycle/API design. It does not benchmark a public session API. |

## Reading the execution-session result

The session spike measures three deliberately separate strategies:

- `pyarallel_fresh` is today's public `parallel_map()` cost.
- `stdlib_fresh` creates and closes an equivalent stdlib executor for every
  map.
- `stdlib_reused` sends every map through one stdlib executor and closes it at
  the end.

`stdlib_fresh / stdlib_reused` is the raw worker-reuse opportunity.
`pyarallel_fresh - stdlib_fresh` is reported separately as current integration
cost. **`stdlib_reused` is an optimistic lower bound, not a promised Pyarallel
speedup:** it does not include rate limiting, retries, checkpoints, callbacks,
streaming ownership, cancellation, or concurrent-call isolation.

The default pass uses seven samples per strategy and reports every sample under
`execution_session.cells.*.strategies.*.samples_ns` in JSON. A cell is noisy
when either stdlib lane has `MAD / median > 0.10`. Run the default 3.12/3.13
pass exactly twice: both runs must be clean and agree before the candidate can
advance; any noisy run means `defer-noisy`. Do not take a third run to find a
preferred answer. `--quick` runs only `P1`, `P3`, and `T10` with three samples
and always returns `smoke-only`.

On a standard GIL-enabled 3.14 build the harness also records interpreter
mirrors (`IP*`/`IPI*`). CPython subinterpreters initialize isolated import paths
at process startup, so launch the 3.14 command with `PYTHONPATH="$PWD"` as shown
above. The harness fails fast when it is absent instead of producing per-item
import failures. Interpreter cells are contextual; the process cells own the
product verdict.

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

## Continuous regression pilot

`benchmarks/codspeed/` contains a deliberately narrower CodSpeed suite for
continuous pull-request comparisons. It tracks deterministic public-API
overhead through the sequential and thread executors. It does not replace this
lab: process and interpreter execution, free-threaded scaling, and cold-worker
startup remain machine- and build-sensitive measurements owned by `bench.py`.

The CodSpeed workflow is observational while the project establishes a stable
baseline. Its dashboard or badge should not be presented as coverage of the
executor comparisons documented above.

The workflow also runs a separate memory instrument over two input sizes:

- `parallel_iter` is drained without retaining outputs, so its peak memory
  should be governed mainly by the fixed 64-item window rather than total input
  size.
- `parallel_map` retains all results and provides the expected O(n) comparison.

Both paths run at 1,000 and 10,000 items with four thread workers. CodSpeed
records peak memory, total allocated bytes, and allocation count. This probe is
observational: its first purpose is to establish whether streaming peak memory
stays materially flatter than collected-map peak memory across the 10x input
increase.
