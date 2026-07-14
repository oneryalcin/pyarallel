# Reusable Execution Session Benchmark Spike

## Goal

Determine whether reusing process or interpreter workers across repeated small
maps saves enough real wall time to justify designing a public execution-session
API. The spike measures the opportunity; it does not add `ParallelPool`, change
engine lifecycle, or start `0.11.0`.

The current contract is deliberately simple: every non-sequential
`parallel_map()` and `parallel_iter()` call creates a fresh executor
(`pyarallel/core.py:267-304`, `pyarallel/core.py:652-657`,
`pyarallel/core.py:1428-1446`) and shuts it down before the call or stream owns
no more work (`pyarallel/core.py:1012-1022`, `pyarallel/core.py:1525-1529`). The
committed benchmark lab already records cold first-result cost and states that
process/interpreter startup is paid on every call (`benchmarks/bench.py:24-35`,
`benchmarks/bench.py:152-166`, `benchmarks/README.md:59-69`). This spike tests
whether eliminating that repeated cost matters in user-shaped repeated calls.

## Decision This Spike Can Make

The result is one of three explicit outcomes:

1. **Advance to API design:** ordinary repeated process maps show material
   savings without relying on a synthetic expensive initializer. Write and
   review a separate lifecycle/API plan before changing `pyarallel/`.
2. **Initializer-specific evidence only:** reuse is compelling only when
   `worker_init` is deliberately expensive. Keep the candidate open, publish
   the break-even numbers, and wait for a concrete model-loading user before
   designing an API.
3. **Defer:** savings are small, unstable, or explained by a biased harness.
   Record the negative result and do not create a public abstraction.

A positive benchmark does **not** authorize implementation directly. It earns a
separate design pass covering ownership, concurrent calls, failure recovery,
shutdown, cancellation, and state isolation.

## Measurement Principles

- Measure the smallest claim: worker reuse, not a hypothetical scheduler.
- Compare like with like and expose unavoidable differences.
- Record absolute time and ratios; a large ratio over microseconds is not a
  product reason.
- Keep every workload importable and stdlib-only, matching the existing lab
  contract (`benchmarks/_workloads.py:1-11`).
- Treat thread execution as a negative control because its startup is already
  near zero in recorded runs (`benchmarks/RESULTS.md:31-34`).
- Never turn noisy shared-runner wall time into CI pass/fail criteria
  (`benchmarks/README.md:71-80`).

## Benchmark Shape

Add one `execution-session` section to the existing harness rather than a
second script. For each supported executor, measure three strategies:

1. **`pyarallel_fresh`:** repeated public `parallel_map()` calls. This is the
   current user cost and includes Pyarallel's driver.
2. **`stdlib_fresh`:** construct the equivalent stdlib executor for every map,
   run `Executor.map()`, then shut it down. This isolates executor startup from
   Pyarallel's driver cost.
3. **`stdlib_reused`:** construct one stdlib executor, run all maps through it,
   then shut it down. This is an optimistic lower bound on what a future
   Pyarallel session could achieve.

Do not compare `pyarallel_fresh` directly with `stdlib_reused` and call the
difference “reuse savings.” Report two separate quantities:

- **raw reuse opportunity:** `stdlib_fresh / stdlib_reused` and absolute
  milliseconds saved per additional call;
- **current integration tax:** `pyarallel_fresh - stdlib_fresh`, reported so a
  future design knows how much of the remaining cost belongs to the engine.

The raw stdlib lane intentionally does not exercise retries, rate limiting,
checkpointing, callbacks, or `ItemResult`; those are lifecycle design work, not
evidence about pool startup.

### Exact Gate Cells

One timed sample constructs everything its strategy owns, performs every map in
the cell, verifies results and worker receipts, and completes shutdown. The
default pass contains exactly these required cells:

| ID | Executor | Workers | Calls | Items/call | Task | Initializer | Purpose |
|---|---:|---:|---:|---:|---|---|---|
| `P1` | process | 4 | 1 | 8 | calibrated 5 ms spin | none | Equivalent one-call ownership control |
| `P3` | process | 4 | 3 | 8 | calibrated 5 ms spin | none | Early amortization gate |
| `P10` | process | 4 | 10 | 8 | calibrated 5 ms spin | none | Primary ordinary-reuse gate |
| `P64` | process | 4 | 10 | 64 | calibrated 5 ms spin | none | Startup-amortized consistency gate |
| `T10` | thread | 4 | 10 | 8 | calibrated 5 ms spin | none | Low-startup negative control |
| `PI3` | process | 4 | 3 | 8 | initialized no-op receipt | calibrated 100 ms CPU initializer | Initializer-only gate |
| `PI10` | process | 4 | 10 | 8 | initialized no-op receipt | calibrated 100 ms CPU initializer | Initializer amortization gate |

On a standard GIL-enabled Python 3.14 build, mirror `P1/P3/P10/P64/PI3/PI10`
with `executor="interpreter"` and `I`-prefixed IDs. These cells are required in
the published record when that build is available but do not change the process
owned verdict. The default standard-Python pass therefore has seven cells on
3.12/3.13 and thirteen on 3.14, not a Cartesian product.

`--quick` runs `P1`, `P3`, and `T10` with three samples as a wiring smoke test
and emits `verdict="smoke-only"`. `--diagnostic` adds explicitly labelled
one-worker, one-item, and no-op cells; diagnostics never feed the classifier.
The harness prints the exact cell and timed-sample count before it starts and
asserts that the generated IDs equal the declared set.

The existing `spin()` workload remains the CPU primitive
(`benchmarks/_workloads.py:15-26`). Calibrate its loop count once, before any
warmup or timed sample, with bounded exponential search followed by at most twelve
binary-search steps. Accept a median single-process duration from 3-8 ms around
the 5 ms target or abort without a verdict. Calibrate the initializer loop the
same way against a 100 ms target, accepting 75-150 ms. Publish both loop counts
and calibration samples. Pass the initializer count through a benchmark-only
environment variable read by the importable initializer so process spawn and
subinterpreter re-import see the same value.

Every task returns a receipt containing its ordinary value, `(pid, thread_id)`
worker identity, initialized-state flag, and worker-local initializer duration
when present. The initializer measures its own `perf_counter_ns()` interval and
stores it in worker-local module state.

To prove equivalent four-worker ownership without warming the measured pool,
each call receives a unique pre-created rendezvous directory. Its first tasks
atomically create one marker named from `(pid, thread_id)` with
`os.open(..., O_CREAT | O_EXCL | O_WRONLY)` and poll until four distinct markers
exist before doing the declared no-op/spin. Four blocked tasks force the executor
to materialize four workers; a five-second timeout invalidates the harness
instead of hanging. Directory creation and input construction occur before
timing, while marker creation/waiting occurs inside every strategy's timed map.
Each call uses a fresh empty directory, including calls through a reused
executor. All rendezvous directories live under one run-scoped
`TemporaryDirectory` outside the repository and are removed in `finally` on
success, timeout, or validation failure, so stale markers cannot bias another
sample or dirty the workspace. Receipts and marker names must agree on exactly
four identities. Initializer tasks also check state inside this timed work, so
no untimed probe can move initialization outside the boundary.

### Sampling and Noise Control

- Warm up one complete sample per strategy/cell outside timing. Warmup pools are
  closed and never reused by a timed sample.
- Collect seven timed samples by default and three under `--quick`.
- Use the fixed six-permutation cycle `ABC, BCA, CAB, CBA, ACB, BAC` for
  (`pyarallel_fresh`, `stdlib_fresh`, `stdlib_reused`); the seventh repetition
  uses `ABC`. Tests pin this exact order.
- Report every raw sample in `--json`, plus median, median absolute deviation,
  ratio, and absolute milliseconds saved per additional call. For one call,
  report only the raw fresh/reused difference; per-additional-call savings is
  undefined because there is no reused call to amortize.
- Mark a scenario `noisy` when `MAD / median > 0.10` for either stdlib lane.
  No noisy scenario may satisfy an advance gate.
- Generate inputs before timing and compare all receipt values with the same
  expected list inside the sample. A wrong value, false initializer flag,
  non-positive initializer duration, or worker-count mismatch invalidates the
  run rather than becoming a timing number.
- Use `time.perf_counter_ns()` and derive displayed milliseconds only after
  aggregation.
- Record `multiprocessing.get_context().get_start_method()` in machine context;
  process launch cost is not comparable without fork/spawn/forkserver identity.

## Decision Gates

The classifier consumes medians only after correctness/ownership checks. For
each cell, define `speedup = fresh_median / reused_median`. For calls greater
than one, define `saved_per_extra_call_ms = (fresh_median - reused_median) /
(calls - 1)`. Calls=1 has no per-extra-call value. A zero/negative median,
missing cell/strategy, wrong receipt, or worker-count mismatch yields
`verdict="invalid-harness"`; no product conclusion may be published.

Controls must pass before product gates:

- `P1`: `abs(stdlib_fresh - stdlib_reused) <= max(10 ms, 15% of
  stdlib_fresh)`. Failure means ownership/timing is not equivalent and yields
  `invalid-harness`.
- `T10`: `saved_per_extra_call_ms <= 10 ms`. Failure yields
  `invalid-harness`; the low-startup control says the harness is measuring
  something besides pool reuse.
- Every required cell must have `MAD / median <= 0.10` for both stdlib
  strategies. Rerun the full default pass once when this fails. A second noisy
  pass yields outcome 3 (`defer-noisy`), not a hand-picked winner.
- Two clean default passes must produce the same classifier outcome. Any
  disagreement, including values straddling a threshold without tripping the
  noise rule, yields outcome 3 (`defer-unstable`). Do not take a third run as a
  tie-breaker.

With valid controls, outcome 1 (`advance-to-design`) requires **all** ordinary
predicates:

| Cell | Required raw stdlib result |
|---|---|
| `P3` | speedup >= 1.25 and saved/extra call >= 10 ms |
| `P10` | speedup >= 1.50 and saved/extra call >= 20 ms |
| `P64` | speedup >= 1.10 and saved/extra call >= 5 ms |

If any ordinary predicate fails, outcome 2 (`initializer-specific`) requires
both `PI3` and `PI10` to have speedup >= 2.00 and saved/extra call >= 50 ms,
with exactly four worker identities per call and every reported initializer
duration inside the accepted 75-150 ms calibration band. Otherwise the result
is outcome 3 (`defer`). These Boolean rules are implemented in a pure function
and pinned with boundary-value tests, including equality, just-below thresholds,
zero/negative medians, calls=1, missing cells, invalid controls, and noise.

`pyarallel_fresh` is reported beside both stdlib strategies but does not enter
the reuse classifier: it is a different engine. Its median integration delta is
recorded for a later design plan, and the prose must not convert that delta into
reuse savings. Interpreter mirrors and free-threaded runs are context only.

## Files and Implementation Steps

1. Extend `benchmarks/_workloads.py` with importable calibrated spin,
   initializer, receipt task, and worker-local state. Keep it dependency-free
   and safe under process spawn/interpreter import
   (`benchmarks/_workloads.py:1-31`).
2. Add `benchmarks/_session_bench.py` as the importable deterministic seam:
   scenario dataclasses, exact cell enumeration, six-permutation scheduling,
   aggregation, control validation, and pure verdict classification. It imports
   workloads with package-relative imports. `benchmarks/bench.py` retains its
   script entry point and uses a dual import (`from benchmarks...` when imported,
   sibling import when executed directly) so both `python benchmarks/bench.py`
   and `import benchmarks._session_bench` work.
3. Extend `benchmarks/bench.py` with calibration, strategy execution,
   correctness/worker-receipt checks, JSON fields, and a concise human
   `execution-session` table. Preserve all existing JSON keys and output
   sections (`benchmarks/bench.py:169-186`, `benchmarks/bench.py:189-267`). Add
   `tests/test_session_benchmark.py` for deterministic helpers only; its CI
   command is the normal `pytest -q` suite, with no timing threshold assertions.
4. Update `benchmarks/README.md` with the exact command, interpretation rules,
   and the warning that `stdlib_reused` is an optimistic lower bound, not a
   promised Pyarallel speedup (`benchmarks/README.md:15-36`,
   `benchmarks/README.md:51-84`).
5. Run the default benchmark twice on the standard local Python, then on a
   standard GIL-enabled Python 3.14 for interpreter evidence. Add labelled raw
   output and the explicit outcome verdict to `benchmarks/RESULTS.md`, following
   its existing machine/build/date format (`benchmarks/RESULTS.md:1-17`).
6. Update issue #32 and the roadmap with outcome 1, 2, or 3. For outcome 1,
   create a separately reviewed lifecycle/API plan; do not turn benchmark code
   into production code in the same change.

## Correctness Checks for the Harness

- Every strategy returns the same ordered values for every call.
- Every call reports exactly four distinct worker identities in gate cells; the
  initializer task also reports true initialized state and a positive in-band
  worker-local initializer duration for every receipt.
- Strategy-owned pools close even when result validation raises.
- The run-scoped rendezvous temporary directory is removed on success and every
  failure path; a regression test injects a validation failure and finds no
  remaining sample directory or workspace artifact.
- Process targets remain importable under spawn; interpreter targets remain
  importable on Python 3.14, following the constraints already tested in
  `tests/test_parity.py:317-330` and `tests/test_interpreter.py:186-196`.
- JSON output is deterministic in shape and contains machine context, scenario
  dimensions, raw samples, aggregates, noise flags, thresholds, and verdict.
- `--quick` exercises exactly `P1/P3/T10` with fewer samples; it is a smoke mode
  and cannot produce the published verdict.
- Existing CPU-scaling, engine-overhead, and worker-startup output remains
  present and semantically unchanged.

`tests/test_session_benchmark.py` imports `benchmarks._session_bench` and covers
exact scenario enumeration, the six-permutation order, aggregation, every
classifier boundary, and JSON shape. Do not assert wall-clock thresholds in
pytest or CI.

## Explicit Non-goals

- No `ParallelPool`, `Session`, executor injection, or public API prototype.
- No edits under `pyarallel/` during this spike.
- No promise that raw stdlib reuse equals an eventual Pyarallel speedup.
- No concurrent calls, nested maps, async session, checkpoint sharing, limiter
  ownership, retry state, callback isolation, or cancellation design.
- No CI performance gate and no benchmark on shared GitHub runners.
- No claim based only on the deliberately expensive initializer lane.
- No `0.11.0` version, release, or changelog feature entry.

## Risks and Mitigations

- **Apples-to-oranges comparison:** separate raw reuse opportunity from current
  integration tax; never label the full Pyarallel-vs-reused gap as reuse.
- **Synthetic initializer proves its own premise:** ordinary no-initializer
  evidence owns the advance gate; initializer-only wins remain outcome 2.
- **Thermal/noisy timing:** rotate order, retain raw samples, report MAD, rerun
  the default pass, and refuse noisy winners.
- **Spawn/import artifacts:** keep all targets in `_workloads.py` and verify
  process plus interpreter lanes explicitly.
- **Benchmark grows too slow:** the default owns exactly seven process/thread
  cells (thirteen with interpreter mirrors), `--quick` owns three smoke cells,
  and `--diagnostic` expands one-worker/one-item cases. With three strategies,
  one warmup, and seven samples, the standard default schedules 147 timed plus
  21 warmup strategy-samples (273 plus 39 on 3.14). Print these counts before
  running and assert them in deterministic tests; do not silently omit a cell.
- **Premature API design:** stop at the verdict. Lifecycle semantics require a
  new plan because current engines own shutdown and cancellation deeply
  (`pyarallel/core.py:903-1022`, `pyarallel/core.py:1493-1529`).

## Verification

Before publishing a result:

1. Ruff and strict mypy pass for the benchmark files and deterministic harness
   tests.
2. The existing full pytest suite and strict MkDocs build pass; benchmark
   changes must not affect runtime behavior.
3. `benchmarks/bench.py --quick --json` parses as JSON and covers every declared
   quick cell and all three strategies. Executor coverage belongs to the
   default record, not the smoke pass.
4. Two default process-capable runs yield the same gate verdict.
5. A standard GIL-enabled Python 3.14 run records interpreter results; the
   harness prints GIL state so a free-threaded build cannot be mislabeled.
6. One normal and one adversarial reviewer inspect the harness for biased
   ownership, accidental warm-pool leakage, incorrect timing boundaries,
   result-check omission, noisy-threshold abuse, and overclaimed prose.

## Stop Condition

The spike is complete when `RESULTS.md` contains labelled raw evidence, noise
statistics, and exactly one of the three verdicts; the roadmap and issue #32
match that verdict; deterministic tests and repository gates pass; and no
production/public API code was added.
