# Reusable Execution Session Lifecycle Design

## Status

**Reviewed design; implementation deliberately deferred.**

This document defines the lifecycle and API that would be implemented if a
real workload justifies the feature. It does not add a public class, change
executor ownership, start `0.11.0`, or promise that the feature will ship.

The preceding benchmark earned this design pass, not implementation. Two clean
CPython 3.12 campaigns independently returned `advance-to-design`: repeated
small process maps saved roughly 110-114 ms per additional call, while the
thread control stayed below 1 ms. A standard GIL-enabled CPython 3.14 context
run was also clean for process and interpreter executors. Those measurements
used a reused stdlib executor as an optimistic lower bound; they did not measure
the API designed here.

## Decision Summary

If implementation is later authorized, the first public slice should add a
sync-only, explicitly resource-owning `ParallelSession` with these properties:

- `executor`, `workers`, `worker_init`, and `max_tasks_per_worker` are fixed for
  the session lifetime;
- `map()` and `starmap()` reuse one executor across sequential, fully completed
  calls;
- retry, limiting, timeouts, callbacks, item identity, checkpoints,
  `max_errors`, and `StopToken` remain isolated per call;
- exactly one call may be active; concurrent and callback-reentrant calls fail
  immediately rather than queueing invisibly;
- only a fully exhausted, infrastructure-healthy call returns the session to
  idle, even when individual items failed;
- every truncated or abandoned call retires the session permanently and starts
  prompt cancellation of queued work;
- a broken executor makes the session terminally broken; it is never rebuilt
  behind the caller's back;
- `close()` is explicit and idempotent, and the context manager is the primary
  ownership form;
- `iter()` is excluded from the first public slice. Its lifecycle is specified
  below, but it must pass a separate usability gate because a suspended or
  abandoned iterator can own the session indefinitely.

The implementation remains demand-gated after this design is approved.

## Review Record

This design completed a deliberate sequential review on 2026-07-14:

1. repository seam exploration identified residual work, run-local state, and
   broken-executor gaps;
2. architecture review required revision for process shutdown finality, thread
   future quiescence, starmap parity, fork scope, and broken-executor branching;
3. adversarial review required revision for thread initializer wake-up,
   cleanup/state ordering, close-error precedence, preflight boundaries, and
   cancellation deduplication;
4. both architecture and adversarial reviewers approved the revised design.

Review approval means the lifecycle is coherent enough to preserve. It does not
authorize implementation or a release.

## RALPLAN-DR

### Principles

1. **No hidden interleaving.** Once a run returns early, its uncancellable work
   must never overlap a later run on the same public session.
2. **Ownership is visible.** The object that creates the executor owns its
   shutdown. Run-local policies do not quietly become session-global state.
3. **Preserve current run semantics.** Standalone functions keep their current
   prompt cancellation, ordering, checkpoint, callback, and isolation
   contracts.
4. **Fail closed after infrastructure uncertainty.** Worker death or
   initializer failure cannot be repaired by silently constructing workers
   whose external state may differ.
5. **Keep the first surface small.** Reuse the existing map engines and expose
   only the operations supported by benchmark evidence and a deterministic
   lifecycle.

### Decision Drivers

1. **Correctness after early return.** Today, timeout, cancellation, abort, and
   driver exceptions may return while already-running tasks continue. This is
   the load-bearing constraint, not executor construction itself.
2. **A legible resource lifetime.** Users must know when workers and initialized
   worker state exist, when calls may reuse them, and when shutdown can wait.
3. **Compatibility with existing Pyarallel semantics.** Item ordering, callback
   threading, context propagation, checkpoint ownership, rate-limit sharing,
   and prompt source-error propagation must not drift merely to enable reuse.

### Viable Options

#### Option A — `ParallelSession` high-level facade (selected)

The session owns a fixed executor and exposes Pyarallel-shaped `map()` and
`starmap()` methods. The engines receive a private executor lease instead of
constructing and shutting down a pool directly.

Advantages:

- resource ownership is explicit at the call site;
- method signatures can omit construction-only settings;
- Pyarallel remains responsible for bounded admission, receipts, retries,
  callbacks, checkpoints, and result status;
- a one-active-call state machine gives a single place to reject unsafe reuse;
- no stdlib `Future` or `submit()` API becomes public.

Costs:

- adds one public resource-owning class;
- requires a terminal policy for early-return and broken-pool cases;
- duplicates the existing function signatures at the method level;
- users must understand that reuse is conditional on a clean run.

#### Option B — accept `executor=` objects in existing functions

Let callers construct a stdlib executor and pass it to `parallel_map()` or
`parallel_iter()`.

Advantages:

- no new high-level class;
- advanced callers can configure stdlib executors directly;
- executor reuse is mechanically narrow.

Rejected because:

- `executor=` already means the string executor kind, so the public contract
  becomes overloaded or needs another name;
- ownership and shutdown become ambiguous: should Pyarallel close a borrowed
  executor after timeout or driver failure?
- the same executor could be used concurrently outside Pyarallel, defeating
  run isolation and failure-state accounting;
- custom executors create an open-ended compatibility and testing surface;
- a broken or contaminated borrowed executor cannot be made safe by the run
  engine.

#### Option C — `ParallelPool` raw-pool facade

Expose a class named around the executor pool, potentially including `submit()`
or future-like operations.

Advantages:

- communicates worker reuse directly;
- follows stdlib executor vocabulary.

Rejected because the abstraction is not a general pool. It owns a sequence of
Pyarallel runs with rate limiting, retries, result ledgers, callbacks,
checkpoints, and stop semantics. `ParallelPool` invites assumptions about raw
submission, concurrent scheduling, and executor compatibility that the first
surface should explicitly reject.

#### Option D — implicit executor cache behind standalone functions

Cache pools by executor configuration and reuse them automatically.

Rejected because it hides process lifetime, initializer state, shutdown, fork
safety, failure contamination, memory retention, and cross-call concurrency.
It would make a currently stateless function API stateful without a visible
ownership boundary.

#### Option E — replace the pool after every abnormal run

Return promptly from the failed run, detach its old executor, and create a new
one so the same session can continue.

Rejected for the first version. Old tasks could still run while the new pool
starts, so process count and side effects overlap. The new workers would rerun
`worker_init` against potentially changed external state. Automatic replacement
also makes a stable session identity falsely imply stable worker state.

### Premortem

1. **A timed-out process task writes after the next map starts.** The caller
   assumes run B follows run A, but run A's uncancellable task mutates the same
   database during run B. Prevention: any truncated run transitions the session
   to `RETIRED`; no later call can acquire it.
2. **A worker dies and the session silently rebuilds.** The initializer opens a
   new model, credential, or snapshot, so later items run under different state
   while the object still looks continuous. Prevention: broken executors are
   terminal, the infrastructure exception is surfaced, and recreation requires
   a new explicit session.
3. **A callback recursively calls `session.map()`.** Silent serialization
   deadlocks because the outer driver waits for the callback and the nested call
   waits for the active lease. Prevention: acquisition is non-blocking and
   raises immediately while `ACTIVE`.

## Scope

### First public implementation slice, if authorized

- synchronous `map()`;
- synchronous `starmap()`;
- thread, process, and Python 3.14+ interpreter executors;
- fixed workers, initializer, and process worker-recycling configuration;
- context-manager and explicit close;
- sequential calls from one or more caller threads, but never concurrent calls;
- all current per-run collected-map policies.

### Designed but held behind a separate gate

- streaming `iter()` on a session. The terminal rules are defined here, but
  the public iterator ownership shape must be validated before it ships.

### Non-goals

- no async session: async execution uses asyncio tasks, not this executor seam;
- no `sequential=True`: inline debugging has no pool to reuse;
- no concurrent maps, queued maps, scheduler, priorities, or nested submission;
- no raw `submit()`, public futures, borrowed/custom executor, or pool resizing;
- no automatic recovery, worker-generation rotation, or retry of a whole run;
- no session-scoped checkpoint, callback, retry, timeout, stop token, numeric
  rate limit, result ledger, index sequence, or item-key registry;
- no decorator binding or decorator-default precedence in the first slice;
- no guarantee that a configured number of workers is eagerly started;
- no promise that benchmarked stdlib savings equal shipped end-to-end savings;
- no release number, changelog feature entry, roadmap completion, or user-facing
  API documentation until implementation is separately approved and complete.

## Proposed Public API

The advanced surface requires an explicit executor kind rather than defaulting
quietly to threads:

```python
class ParallelSession:
    def __init__(
        self,
        *,
        executor: Literal["thread", "process", "interpreter"],
        workers: int | None = None,
        worker_init: Callable[[], None] | None = None,
        max_tasks_per_worker: int | None = None,
    ) -> None: ...

    def map[T, R](
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        *,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_result: Callable[[ItemResult[R]], None] | None = None,
        window_size: int | None = None,
        retry: Retry | None = None,
        item_key: Callable[[T], str | int | bytes] | None = None,
        checkpoint: str | Path | None = None,
        checkpoint_key: Callable[[T], str | int | bytes] | None = None,
        checkpoint_version: VersionToken | None = None,
        max_errors: int | None = None,
        stop: StopToken | None = None,
    ) -> ParallelResult[R]: ...

    def starmap[R](
        self,
        fn: Callable[..., R],
        items: Iterable[tuple[Any, ...]],
        *,
        rate_limit: Limiter | RateLimit | float | None = None,
        timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_result: Callable[[ItemResult[R]], None] | None = None,
        window_size: int | None = None,
        retry: Retry | None = None,
        item_key: Callable[[tuple[Any, ...]], str | int | bytes] | None = None,
    ) -> ParallelResult[R]: ...

    def close(self, *, wait: bool = True) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
```

`VersionToken` above is explanatory shorthand for the existing
`checkpoint_version` union, not a proposed public alias.

Example of the intended ownership:

```python
with ParallelSession(
    executor="process",
    workers=4,
    worker_init=load_model,
) as session:
    first = session.map(classify, first_batch)
    second = session.map(classify, second_batch)
```

Construction-only arguments do not appear on methods. `starmap()` mirrors the
current standalone starmap surface, including its lack of checkpoint options.
Standalone functions remain unchanged.

The first slice does not add public lifecycle exceptions or a public state
enum. Misuse (`ACTIVE`, `RETIRED`, `BROKEN`, or `CLOSED`) raises `RuntimeError`
with a stable, actionable message. The original infrastructure exception is
chained where available. This keeps the public surface small; dedicated
exception types should be added only if a real caller needs programmatic
session orchestration.

## Construction and Worker Start

The constructor validates the same worker options as a standalone pool call and
constructs the stdlib executor. Stdlib may start workers lazily on first
submission; neither construction nor `__enter__` promises that all workers or
their initializers have run.

There is no `start()`, `warmup()`, health probe, or eager rendezvous. A warmup
would execute user code, distort initializer semantics, and require a synthetic
picklable task. If eager readiness becomes a real requirement, it needs its own
contract and evidence.

Validation remains fail-fast for:

- unsupported executor names;
- `workers < 1`;
- interpreter execution before Python 3.14;
- non-process `max_tasks_per_worker` or values below one;
- non-picklable process initializers;
- non-importable interpreter initializers.

Initializer runtime failure can occur only once a worker starts. That is an
executor infrastructure failure and transitions the session to `BROKEN`.

### Method preflight boundary

Every method performs pure, non-resource-owning validation before atomically
acquiring `IDLE -> ACTIVE`. The following failures leave the session `IDLE` and
usable:

- timeout, window, and max-error value validation;
- checkpoint option dependencies such as `checkpoint_key` without
  `checkpoint`;
- process/interpreter callable resolution and importability checks;
- process/interpreter retry picklability checks;
- rate-limit specification normalization that does not wait or mutate a shared
  limiter;
- starmap callable adaptation and interpreter `__main__` rejection.

After pure preflight succeeds, the call acquires the session. Opening or
validating a checkpoint, consuming the source, evaluating item/checkpoint keys,
waiting on a limiter, submitting work, and invoking callbacks all happen while
`ACTIVE`; failures there retire the session. Checkpoint open deliberately stays
post-acquisition because it owns a live resource and may inspect or create a
file. The boundary is based on side effects and ownership, not on whether an
error looks likely or harmless.

## State Machine

The public class has an internal lock and these internal states:

| State | Meaning | Calls allowed | `close()` |
|---|---|---|---|
| `IDLE` | Executor exists and the previous run ended cleanly | One caller may atomically acquire | Shuts down and becomes `CLOSED` |
| `ACTIVE` | One map/starmap owns the run lease | None; fail immediately | Fails immediately; it does not interrupt another caller |
| `RETIRED` | A run was truncated/abandoned or driver integrity is uncertain | None, permanently | Idempotently finishes shutdown and becomes `CLOSED` |
| `BROKEN` | Executor/worker infrastructure failed | None, permanently | Idempotently finishes shutdown and becomes `CLOSED` |
| `CLOSED` | Shutdown was requested | None | No-op, including repeated calls |

Transitions:

```text
construct ------------------------------> IDLE
IDLE -- atomic acquire -----------------> ACTIVE
ACTIVE -- clean, fully exhausted -------> IDLE
ACTIVE -- timeout/cancel/abort ----------> RETIRED
ACTIVE -- source/key/checkpoint/callback
          or other driver exception ----> RETIRED
ACTIVE -- broken executor/init/worker ---> BROKEN
IDLE/RETIRED/BROKEN -- close -----------> CLOSED
CLOSED -- close ------------------------> CLOSED
```

There is deliberately no `RETIRED -> IDLE`, `BROKEN -> IDLE`, or automatic
executor generation change. A new session is the recovery boundary.

### Atomic acquisition

State acquisition is non-blocking. Two racing caller threads cannot both start;
one transitions `IDLE -> ACTIVE`, and the other gets `RuntimeError`. Calls are
not queued or silently serialized. The engine runs outside the state lock, and
the final transition occurs under the lock.

This also rejects reentrancy from `on_result`, `on_progress`, a source iterator,
`item_key`, `checkpoint_key`, or other driver-thread code. Failing immediately
is required to avoid self-deadlock.

Sequential handoff between different caller threads is allowed when the session
is idle. Per-task thread context is still copied at submission, so each run sees
the submitting caller's context. The session is accurately described as
single-active-run, not generally thread-safe.

## Run Completion Classification

A run is reusable only when all of the following are true:

1. the input source was exhausted;
2. every admitted future resolved;
3. all normal result publication and checkpoint writes completed;
4. no timeout, `StopToken`, or `max_errors` halt occurred;
5. no driver exception escaped;
6. no broken-executor signal was observed.

For thread execution, consuming the run's last private completion-queue entry is
not sufficient evidence for item 2. The current thread wrapper can publish its
outcome immediately before the submitted callable returns and its `Future`
becomes done. The session lease must track every submitted future and perform a
clean-path quiescence barrier before releasing `ACTIVE -> IDLE`. Run-local
cleanup, including checkpoint-store closure, must also finish before that state
transition.

Individual task failures do not retire the session. They are ordinary
`ItemResult` failures, and a fully exhausted result remains
`RunStatus.COMPLETED`. Retry exhaustion likewise remains item-level unless it
triggers `max_errors`.

The following outcomes always retire the session, even if a race means no task
is still running by finalization:

- `RunStatus.TIMED_OUT`;
- `RunStatus.CANCELLED` from `StopToken`;
- `RunStatus.ABORTED` from `max_errors`;
- source iteration failure;
- `item_key` or `checkpoint_key` failure;
- limiter, checkpoint, progress callback, or result callback failure;
- `KeyboardInterrupt`, `SystemExit`, or another escaping driver-side
  `BaseException`.

The state rule is status-based, not timing-based. The same timeout must not
sometimes leave a reusable session merely because its last worker happened to
finish a microsecond earlier.

## Residual Work and Retirement

On transition to `RETIRED`, the session atomically disables reuse and calls
`cancel()` on every future still tracked by the run. It does **not** call
`executor.shutdown()` yet. Queued work is cancelled where the runtime supports
it. Already-running thread, process, or interpreter work cannot be interrupted
and may continue performing side effects. The session object is unusable
immediately, so that residual work cannot overlap a later call through the same
resource boundary.

Deferring physical shutdown is required because `concurrent.futures` does not
promise that `shutdown(wait=False)` can later be upgraded into a join. In
particular, a process executor may discard the manager-thread and process
references during its first shutdown call. The first explicit `close()` or
`__exit__` therefore owns the executor's one physical shutdown decision.

Retirement does not claim that work stopped. Error text and future user docs
must say that a new session can run concurrently with residual work from the
old one. Callers needing strict no-overlap must make task functions bounded and
then call `close(wait=True)` before constructing a replacement.

This conservative policy gives up reuse after an abnormal call. That is
intentional: the feature optimizes the clean repeated-call path, not failure
recovery.

## Close and Context-Manager Semantics

`close(wait=True)` is idempotent, with **first-close-wins** wait semantics.
The first closer atomically transitions the session to `CLOSED` before invoking
the potentially blocking physical shutdown, so a racing map cannot acquire an
executor whose shutdown has begun. It then calls exactly
`executor.shutdown(wait=wait, cancel_futures=True)`.

- From `IDLE`, it requests executor shutdown and moves to `CLOSED`.
- From `RETIRED` or `BROKEN`, it performs the executor's first physical shutdown
  using the requested wait policy.
- From `CLOSED`, it is a no-op.
- From `ACTIVE`, it raises immediately. One caller may not asynchronously close
  another caller's run through this first API.

With `wait=True`, running tasks are allowed to finish, so the call can block
forever if user code never returns. With `wait=False`, queued futures are
cancelled and shutdown returns promptly, but running work and its side effects
may continue. `wait=False` never makes the session reusable and does not promise
prompt Python process exit: the interpreter's executor-exit handling may still
wait for pending work. A later `close(wait=True)` is a no-op and cannot upgrade
an earlier non-waiting shutdown into a join.

If the first physical shutdown raises, that exception propagates and is retained
internally as the close cause. The session remains `CLOSED`; retrying shutdown
through this object is unsafe and every later `close()` remains a no-op. A later
attempt to use the closed session raises the normal closed-state `RuntimeError`
chained from that cause. If `__exit__` encounters only a shutdown error, it
propagates it. If the `with` body and shutdown both fail, the shutdown error
propagates with the body error retained as its Python exception context,
matching normal context-manager cleanup precedence.

`__exit__` calls `close(wait=True)`, matching the deterministic ownership
expectation of stdlib executors. This produces an important warning: a
`session.map(..., timeout=...)` call can return promptly, but immediately
leaving the surrounding `with` block may then wait for uncancellable tasks.
The map timeout bounds the run driver, not worker termination or context-manager
shutdown. Callers whose task may hang indefinitely must put a timeout inside the
task and may choose explicit `close(wait=False)` instead of a context manager.

There is no correctness dependency on `__del__`. The implementation should not
promise finalizer timing or attempt automatic pool replacement. At most, a
best-effort `ResourceWarning` may be considered separately; it is not required
for the first slice.

### External contract probe

The Python 3.14 [`Executor.shutdown()` documentation](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)
guarantees that `wait=True` waits for pending futures and that `wait=False`
returns immediately while the Python program still waits for pending futures
before exit. It does not guarantee that a second shutdown call can upgrade an
earlier non-waiting call.

A focused local probe on the supported CPython 3.12.8 and 3.14.6 runtimes
submitted a one-second process task, called `shutdown(wait=False)`, and then
immediately called `shutdown(wait=True)`. Both second calls returned in 0.0000
seconds rather than joining the task. The CPython process-executor source also
clears manager-thread and process references during its first non-waiting
shutdown. The design therefore relies only on the documented first-call wait
contract and makes the first close final.

## Broken Executor Contract

`concurrent.futures.BrokenExecutor` and its process/thread/interpreter
specializations are infrastructure failures, not ordinary item failures in a
reusable session.

When detected during submission or completion:

1. stop admitting source items;
2. classify the session as `BROKEN`;
3. cancel every still-tracked future without physically shutting down the
   executor;
4. raise the infrastructure exception from the current session call;
5. reject every later operation with `RuntimeError` chained from the stored
   cause;
6. keep `close()` safe and idempotent.

There is no health-check submission and no automatic reconstruction. The
worker initializer may have established database connections, loaded state, or
acquired credentials; recreating that state is an application decision.

Standalone function behavior must not change incidentally in this slice. If
existing standalone conversion of a broken future into an item failure is to be
reconsidered, that requires a separate compatibility decision and tests.

The lease alone cannot classify an exception that the current engine has
already converted to an item failure. The engine therefore needs one narrow,
explicit branch at submission and `Future.result()`: an owned standalone lease
retains the current item-failure behavior, while a session lease records and
re-raises `BrokenExecutor`. This difference must be direct-testable and must not
broaden to ordinary task exceptions.

Thread futures need one additional exactly-once publication rule. Today, a
thread wrapper publishes a normal outcome before its callable returns, while a
done callback is attached only for process/interpreter futures. If the thread
initializer fails, the wrapper never runs: its future fails with
`BrokenThreadPool`, but no event wakes the driver. The refactor must attach a
fallback done callback to thread futures as well and coordinate it with a
per-submission publication flag:

1. the wrapper atomically claims publication before enqueueing a normal outcome
   or driver-visible `BaseException`;
2. the future done callback atomically checks the same flag;
3. if the wrapper claimed it, the callback publishes nothing;
4. if the wrapper never ran and the future is cancelled, the callback claims
   and suppresses publication; the existing timeout/cancel/abort aftermath owns
   the item's final placeholder classification;
5. if the wrapper never ran and the future completed without cancellation, the
   callback claims the flag and enqueues the failed future for infrastructure
   classification, covering initializer-driven `BrokenThreadPool`.

The publication flag operation and queue put must have a deliberate order, and
callbacks must not be registered or invoked while holding a non-reentrant state
lock. This prevents both the initializer-failure hang and duplicate absorption
of ordinary thread results. The fallback is part of the engine prerequisite,
not a generic executor-health monitor.

## State Ownership Matrix

| Concern | Owner | Cross-call behavior |
|---|---|---|
| Executor kind and worker count | Session | Fixed |
| Worker initializer | Session/actual worker generation | Runs once per actual worker start, not once per call |
| Process worker recycling | Session/stdlib executor | Continues across calls; replacement workers rerun initializer |
| Task callable | Run | May differ between calls |
| Source and admission window | Run | Fresh; never shared |
| Completion queue and result ledger | Run | Fresh; late callbacks can reach only the retired run's private queue |
| Indices and item keys | Run | Restart from zero; no cross-call uniqueness |
| Retry object/wrapper | Run | Fresh binding to that task |
| Numeric/`RateLimit` limiter | Run | New limiter per call |
| Explicit `Limiter` instance | Caller | Shared only because caller passed the same object |
| Timeout, max-errors halt, `StopToken` | Run/caller | Never session defaults; a stopped token keeps its existing caller-owned latch semantics |
| Progress/result callbacks | Run | Driver-thread only; no cross-call callback registry |
| Checkpoint store | Run | Opened and closed on every call |
| Checkpoint path/version/key policy | Run | No implicit session campaign |
| Thread contextvars | Submission | Fresh caller context copied per item |

## Worker Identity and Initializer Semantics

Reuse promises one executor instance, not a fixed set of worker identities.
Stdlib executors start workers lazily and may start fewer than `workers`.
Process `max_tasks_per_worker` deliberately replaces workers. Every actual
replacement runs `worker_init` before taking work.

Therefore documentation must say:

- initializer work is amortized across calls only while that worker survives;
- `workers=N` is a concurrency ceiling, not an eager N-worker receipt;
- worker-local caches may disappear when a process is recycled;
- a broken worker does not trigger a transparent new session generation.

## Process, Pickle, and Fork Boundaries

`ParallelSession` is not picklable. Attempted serialization should fail with a
clear `TypeError`; transferring an executor-owning object into a worker would
make shutdown ownership undefined.

The session records its creating process ID. Any method, including `close()`,
used after `fork()` in a different PID fails before touching the inherited
executor. The inherited session is unusable in the child. Prefer spawn; a fresh
session may be constructed in the child only where the platform and runtime
permit safe post-fork executor creation. The PID check is relevant even when the
configured executor is `thread`, because locks and worker-thread state do not
survive fork safely.

Normal process/interpreter callable and retry pickling/importability rules stay
unchanged and are evaluated per run. A session may map different importable
functions across calls.

## Streaming Design and Gate

A future `session.iter()` must obey these invariants:

- acquiring the iterator transitions `IDLE -> ACTIVE` before source
  consumption;
- the iterator owns that lease until complete exhaustion or explicit close;
- clean exhaustion with all admitted work resolved returns to `IDLE`;
- `max_errors`, source failure, infrastructure failure, explicit early close,
  or generator finalization retires/breaks the session using the same rules as
  collected maps;
- a suspended iterator prevents every other session operation;
- an early `break` does not itself guarantee deterministic close if another
  reference to the iterator remains;
- no later run may start merely because queued futures were cancelled.

The first implementation slice excludes `iter()` because returning a bare
generator makes resource ownership too easy to abandon accidentally. Before
streaming ships, a focused design/review must select and test one of:

1. a public closeable/context-managed `SessionStream` object;
2. a documented `contextlib.closing(session.iter(...))` contract plus robust
   session cleanup behavior;
3. a stream-scoped context manager such as `session.stream(...)`.

That review must prove deterministic lease release, early-break behavior,
cross-thread close behavior, garbage-collection non-reliance, and API snapshot
cost. Until then, callers use standalone `parallel_iter()` with its existing
one-run executor ownership.

## Internal Architecture Seam

The implementation should not fork a second map engine. Introduce one small
private executor-lease boundary used by collected map and, only later, streaming.

Conceptually:

```python
class _ExecutionLease(Protocol):
    executor: Executor
    executor_kind: ExecutorType

    def track(self, future: Future[Any]) -> None: ...
    def finish(
        self,
        outcome: _RunExit,
        cleanup: Callable[[], None],
    ) -> None: ...
```

`_RunExit` is an internal, explicit classification such as `CLEAN`,
`TRUNCATED`, `DRIVER_ERROR`, or `BROKEN`. It is not inferred from exception text
or from whether `Future.cancel()` returned true.

- Standalone functions acquire an owned per-call lease. Its finish behavior
  reproduces current shutdown semantics exactly.
- A session atomically acquires a session lease. Clean finish releases it back
  to `IDLE`; truncated/error finish retires it; infrastructure failure breaks
  it.
- The session lease maintains a condition-protected, bounded set of unresolved
  submitted futures. Future done-callbacks remove entries. `finish(CLEAN)` waits
  for that set to become empty before releasing the session. A truncated or
  broken finish snapshots the set and attempts `cancel()` on every entry.
- `finish()` owns the complete finalization order and is invoked exactly once by
  one outer `finally`; the engine must not call `store.close()` or
  `pool.shutdown()` afterward. For a session lease it keeps the state `ACTIVE`,
  cancels tracked work immediately for abnormal outcomes, invokes run-local
  cleanup, performs clean quiescence when applicable, and only then publishes
  `IDLE`, `RETIRED`, or `BROKEN`. A racing caller therefore cannot acquire
  between result publication and cleanup failure.
- The owned standalone lease preserves today's order: executor shutdown first,
  then checkpoint cleanup. If shutdown itself raises, later cleanup remains
  skipped exactly as it is now. This parity is locked before adding the public
  class.
- The run engine continues to allocate its limiter, task wrapper, checkpoint
  store, source, window, completion queue, counters, ledgers, keys, and callbacks
  locally.
- `parallel_starmap()` continues to adapt its callable and delegate to the same
  collected engine.

The abstraction earns its place because ownership finalization is duplicated in
collected and streaming engines and because raw `pool.shutdown()` is precisely
the seam that changes. It must remain private and small; it is not a general
executor adapter.

The highest-risk refactor is finalization. Implement it in two phases if coding
is authorized:

1. move standalone pool creation/shutdown behind the lease without adding the
   public class, proving behavior parity;
2. add the session owner and public methods only after the parity suite is
   green.

### Finalization exception precedence

The finalizer first captures whether the body is returning normally or already
has an escaping exception. For a session, cleanup runs while acquisition is
still blocked. If cleanup fails, the run is terminally retired and no result is
returned.

- cleanup failure by itself propagates;
- when body execution and cleanup both fail, the cleanup exception propagates
  with the body exception retained as its Python exception context, matching
  ordinary `finally` semantics;
- state transition and cancellation still complete before propagation;
- clean future quiescence and successful cleanup are both required before
  `IDLE` is published.

These rules apply to `Exception` and `BaseException`; cleanup must not leave the
session `ACTIVE` merely because error propagation is complex.

## Error Messages

Without new public exception types, messages are part of usability but not a
machine protocol. Required forms should identify the recovery:

- active: `ParallelSession already has an active call; concurrent and reentrant calls are not supported`;
- retired: `ParallelSession cannot be reused after a timed-out, cancelled, aborted, or failed run; close it and create a new session`;
- broken: `ParallelSession executor is broken; close it and create a new session`;
- closed: `ParallelSession is closed`;
- forked: `ParallelSession cannot be used in a different process; create a new session after fork`.

The stored broken-executor cause is chained on later broken-state errors.

## Documentation Contract If Implemented

Implementation would require, in the same PR:

- API reference for construction, methods, close behavior, and every fixed vs
  run-local option;
- one user-facing process example with repeated small batches or expensive
  initialization;
- a prominent warning that early termination retires the session and that
  context-manager exit can wait for uncancellable tasks;
- cookbook guidance that threads showed no meaningful startup saving in the
  benchmark and that long single maps have little reason to use a session;
- API snapshot and typing assertion updates;
- roadmap wording that distinguishes design approval from implementation;
- an Unreleased changelog entry only after the public API exists.

This design document itself is developer-facing and does not add user-facing
claims before implementation.

## Implementation Sequence If Demand Arrives

1. Re-run or extend the benchmark on the motivating workload and record the
   expected end-to-end saving.
2. Add parity tests that lock current standalone finalization behavior.
3. Introduce `_RunExit` and the private owned lease; keep public functions
   behavior-identical.
4. Add session state/lock ownership and tests without exporting the class.
5. Add `map()` and `starmap()` methods and lifecycle tests for every executor.
6. Add broken-executor, fork, reentrancy, checkpoint, limiter, callback, and
   context-isolation tests.
7. Export `ParallelSession`, update typing/API snapshots, and write user docs.
8. Re-run the benchmark through the actual session API and publish honest
   end-to-end numbers.
9. Run normal and adversarial independent review. Do not merge while any
   lifecycle, residual-work, or documentation gate remains open.

## Test Specification

No timing threshold belongs in CI. Correctness should be proved through state,
worker identity, initializer receipts, bounded admission, and deterministic
events.

### Unit tests

- constructor accepts every valid fixed configuration and rejects every current
  invalid worker option;
- constructor requires explicit `executor=`;
- state transitions match the table, including idempotent close;
- atomic acquisition permits exactly one racing caller;
- concurrent and callback-reentrant calls fail immediately rather than block;
- calls after retired, broken, closed, forked, or unpickled misuse fail with the
  required recovery message;
- the session cannot be pickled;
- `close(wait=True)` and `close(wait=False)` pass the exact shutdown flags;
- the first closer publishes `CLOSED` before physical shutdown, preventing a
  racing acquire while it waits;
- each `_RunExit` value drives the correct owned-lease and session-lease
  finalization;
- a session clean finish waits until every tracked future is formally done,
  including a test hook that pauses a thread task after completion-queue
  publication but before callable return;
- a thread initializer failure with no run timeout wakes the driver exactly
  once, while an ordinary thread result is never absorbed twice;
- a deterministic one-worker timeout/stop case cancels queued thread futures
  and proves the fallback emits neither a duplicate event nor an ordinary
  `CancelledError` receipt; halt aftermath remains the sole classifier;
- standalone owned-lease behavior remains byte-for-byte equivalent in shutdown
  choices to the pre-refactor behavior;
- session method signatures omit constructor-owned settings and preserve all
  collected-map per-run settings;
- starmap adapts but does not fork lifecycle logic.

### Integration tests shared by thread/process/interpreter

- two clean calls reuse worker receipts from one executor generation;
- two calls may use different functions, iterable types, window sizes, retries,
  callbacks, and item-key functions;
- per-call indices restart at zero and duplicate keys across calls are allowed;
- ordinary item failures in call one do not prevent a clean call two;
- callback receipts never cross calls and always execute on the relevant driver
  thread;
- completion queues and result ledgers from call one cannot publish into call
  two;
- an explicit shared `Limiter` spans calls, while numeric and `RateLimit` inputs
  create independent call budgets;
- thread contextvars are captured from each submitting call, not construction;
- process/interpreter callable and retry validation is repeated per call;
- every pure preflight failure leaves the session idle and permits a following
  valid call; every post-acquisition checkpoint-open failure retires it;
- the worker initializer runs per actual worker generation, not per call;
- process recycling can replace a worker across calls and reruns initialization;
- every checkpoint store closes at the end of its call and the next call can
  open the same or a different checkpoint safely;
- `checkpoint_key` populates result keys exactly as standalone map does;
- a stopped `StopToken` retains its existing caller-owned latch semantics and
  retires the acquired session call;
- repeated thread starmaps exercise the ordinary `_apply_star` adapter and
  preserve original-tuple `item_key` values;
- repeated process/interpreter starmaps exercise the resolved-target adapter,
  preserve importability and interpreter `__main__` rejection, and prove that
  both adapter paths reuse the same executor lifecycle rather than constructing
  a hidden pool.

### Lifecycle and recovery tests

- clean sized and unsized maps return `ACTIVE -> IDLE`;
- a fully exhausted map with item failures remains reusable;
- timeout returns promptly, produces the existing result status, transitions to
  `RETIRED`, rejects a next call, and lets `close(wait=False)` return promptly;
- cancellation and max-error abort follow the same terminal policy and preserve
  completed-work salvage/no-source-drain behavior;
- source, key, limiter, checkpoint, progress callback, and result callback
  exceptions propagate promptly and retire the session;
- injected checkpoint-close failure occurs before terminal state publication,
  prevents a racing acquire, retires the session, and follows the documented
  exception precedence when a body error is already active;
- `KeyboardInterrupt`/`BaseException` finalization retires rather than releases;
- a worker deliberately terminated with `os._exit()` makes process session
  `BROKEN`, stops admission, surfaces the infrastructure failure, and causes
  later calls to fail with the stored cause;
- initializer failure makes the session broken and never triggers automatic
  reconstruction;
- close from `ACTIVE` fails without cancelling the active caller;
- context-manager exit from `IDLE` waits and closes;
- context-manager exit after retirement waits for a finite slow task, with a
  separate non-waiting close test proving the documented alternative;
- the first `close(wait=False)` is the only physical shutdown call and a later
  `close(wait=True)` is a no-op rather than a false join promise;
- a fault-injected shutdown exception propagates, leaves `CLOSED`, is retained
  as the cause of later closed-state errors, and cannot trigger a second
  physical shutdown;
- `__exit__` tests cover shutdown failure both with and without an exception
  already leaving the body;
- no test uses a truly hung worker; finite events and subprocess deadlines keep
  the suite killable.

### Streaming gate tests, before `iter()` may ship

- lease acquisition happens before source consumption;
- clean exhaustion returns to idle;
- explicit close, early break with close, source failure, max-error termination,
  and infrastructure failure retire/break the session;
- a suspended stream rejects every new call;
- iterator abandonment cannot depend on CPython reference counting for
  correctness;
- cross-thread close has an explicit tested outcome;
- bounded window, prompt close, no-source-drain, ordering, and interpreter-close
  tests retain current standalone semantics.

### End-to-end and workspace-isolation tests

- a subprocess opens a process session, performs two calls, closes it, and exits
  without child processes or checkpoint sidecars left open;
- a controlled worker event proves `close(wait=False)` itself returns promptly;
  subprocess termination is asserted separately only after finite work is
  released, because non-waiting close does not promise prompt interpreter exit;
- a forked child cannot use or close the inherited session; fresh child-session
  construction is tested only on a platform/runtime combination that documents
  it as safe;
- two sessions with separate checkpoints and limiters do not share state;
- a session exception leaves no repository files, temporary checkpoints, or
  benchmark rendezvous artifacts;
- Windows spawn, macOS spawn, Linux process, Python 3.14 interpreter, and
  free-threaded CI lanes exercise their supported matrix without timing gates.

### Static, typing, and documentation verification

- `tests/api_snapshot.txt` changes only for the intentionally exported class and
  methods;
- typing assertions cover generic map/starmap inference, callback item types,
  context-manager `Self`, and invalid construction/method options;
- `pytest -q`, Ruff, MyPy, and strict MkDocs build pass;
- executable docs compile the public example and verify its arguments against
  the live signatures;
- the actual session benchmark records raw artifacts and keeps its result out of
  CI pass/fail logic.

### Observability and diagnostic acceptance

The first slice adds no metrics system or public state property. Diagnostics are
provided by:

- explicit state-specific error text;
- original exception chaining for broken executors;
- existing `ParallelResult.status` and `ItemResult` receipts;
- test-only worker/initializer receipts;
- benchmark JSON for performance claims.

Logs must not be emitted implicitly by the library. If production demand later
requires state observation, a read-only public property can be designed from
real use rather than exposing the private state enum prematurely.

## Definition of Done for a Future Implementation

Implementation is not done until all of the following are true:

- the motivating real workload still shows material end-to-end benefit;
- standalone behavior parity is locked before the executor seam refactor;
- every state transition and abnormal-exit class is tested;
- no run-local state leaks across calls;
- no residual work can overlap a later call on the same session;
- broken infrastructure never auto-recovers;
- process/interpreter/spawn/fork boundaries are tested where supported;
- user docs contain the timeout-versus-shutdown warning and honest benchmark
  scope;
- API snapshot, typing, lint, full tests, strict docs, and the actual API
  benchmark pass;
- a normal reviewer and an adversarial lifecycle reviewer independently approve;
- the PR contains no unrelated release or `0.11.0` work.

## ADR

### Decision

Design a high-level `ParallelSession` that serially reuses one fixed executor
for clean `map()` and `starmap()` calls. Make abnormal completion and broken
infrastructure terminal, keep every policy other than worker lifecycle per run,
and defer streaming plus all implementation until real demand clears a separate
gate.

### Drivers

- prevent background work from one call interleaving with the next;
- make ownership, shutdown, and initialized worker state explicit;
- preserve existing Pyarallel function behavior and keep the new surface small.

### Alternatives considered

- borrowed executor injection;
- raw `ParallelPool` facade;
- implicit global executor cache;
- automatic executor replacement after abnormal completion.

### Why this decision

The benchmark shows a real clean-path opportunity, but every more permissive
design either hides executor ownership or permits old and new work to overlap.
A terminal abnormal state is conservative, predictable, and implementable
without turning Pyarallel into a scheduler or recovery manager.

### Consequences

- repeated clean process/interpreter calls can amortize startup and initializer
  cost;
- a timeout, cancellation, abort, or driver failure sacrifices the session;
- users must explicitly construct a replacement and decide whether to wait for
  residual work;
- streaming remains standalone until its iterator ownership is earned;
- method signatures add maintenance cost but keep configuration boundaries
  legible;
- no implementation proceeds solely because this design is approved.

### Follow-ups

1. Wait for a concrete repeated-map or expensive-initializer workload.
2. Validate this API against that workload and measure expected end-to-end gain.
3. If implementation is authorized, execute the two-phase internal seam then
   public surface sequence above.
4. Design streaming ownership separately only after collected sessions prove
   useful.

## Review and Execution Handoff

Available specialist roles for a future implementation include `executor` for
the private seam/public class, `test-engineer` for lifecycle and subprocess
coverage, `writer` for user-facing documentation, and independent
`code-reviewer`/`critic` lanes for normal and adversarial review. Because the
first slice changes one load-bearing engine seam, a single implementation owner
should integrate it; tests and review can run in parallel after parity is
locked.

Goal-mode execution is intentionally not started by this design. If demand
arrives, the work should be tracked as one durable goal with explicit gates for
standalone parity, lifecycle implementation, cross-runtime verification,
documentation, and independent review. Team verification should compare each
claimed state transition and error path against this document rather than
treating reviewer consensus as evidence.
