# DevX Principles

These are the product and API design principles for Pyarallel. Use them when
adding features, changing names, or deciding whether a convenience API is worth
the long-term cost.

## 1. Keep the Surface Small

Pyarallel should feel like a few sharp tools, not a framework. New features
must justify their cost in API surface, docs, and maintenance.

## 2. Prefer Explicit Semantics

Different concepts should use different names. Sync pool sizing is not the same
thing as async coroutine concurrency. Convenience should not come from hiding
real semantic differences.

## 3. Optimize for Strong Typing

Return types should make success and failure explicit. Good autocomplete, clear
editor errors, and predictable type checking are part of the product.

## 4. Preserve Native Call Feel

Decorators and wrappers should keep normal call signatures and return values
whenever possible. Parallel behavior should feel additive, not magical.

## 5. Document Tradeoffs Honestly

Do not oversell behavior. If batching is lazy only with `window_size`, say that.
If progress totals are provisional for unsized iterables, say that. Trust comes
from accuracy.

## 6. Prefer One Honest Model Over Two Convenient Ones

If sync and async APIs need different terms, keep them different. If streaming
needs a typed result object, use one instead of mixing values and exceptions in
the same channel.

## 7. Deprecate Deliberately

When an API is wrong or confusing, prefer a clean replacement path over keeping
compatibility forever. Pre-v1 is the time to fix conceptual mistakes.

## 8. Avoid Premature Abstraction

Do not split code or add layers just because a review suggests it. Extract only
when behavior is truly duplicated or when a boundary materially improves
correctness, maintainability, or clarity.

---

## Engineering and Delivery Discipline

The principles above govern the public product. The rules below govern how we
change its implementation. They are developer-facing constraints, not promises
that need to appear in user documentation.

### Design Lenses

These names are shorthand for useful review questions, not authority by quote:

- **John Carmack — invariants and restartability.** State the invariant, then
  ask what happens after interruption, partial completion, retry, cancellation,
  and restart. Describe concrete failure modes rather than calling a path
  “robust.”
- **Martin Fowler — explicit boundaries and deliberate refactoring.** Make
  ownership and seams visible. Refactor because a boundary improves the design,
  not because rearranging code feels cleaner.
- **Robert C. Martin — readable intent and small surfaces.** Names and control
  flow should expose the reason a branch exists. Prefer the smallest public and
  internal surface that expresses the behavior honestly.
- **Gang of Four — patterns must earn their place.** Use a named pattern only
  when repeated pressure has produced the abstraction it solves. Do not begin
  with a pattern and search for somewhere to install it.

The combined bias is toward explicit, boring implementations whose correctness
can be explained from local code and a small number of stated invariants.

### Before Coding

1. **Probe external contracts.** Verify executor, interpreter, filesystem,
   SQLite, HTTP, and dependency behavior with documentation or a focused probe
   before designing around it. Do not implement against memory or analogy.
2. **Plan risky slices.** Concurrency, persistence, cancellation, checkpoint
   compatibility, and public API changes require a written plan that names the
   invariant, failure modes, test shape, rollback boundary, and stop condition.
3. **Fail fast on missing capabilities.** If correctness requires pickling,
   cancellation, an importable worker target, a supported schema, or another
   runtime capability, validate it at the boundary. Silent fallback is allowed
   only when it preserves the documented contract.
4. **Persist before publish.** Commit plans, probes, benchmark receipts, and
   reproducible examples before using their conclusions in public claims. The
   durable artifact must exist before the announcement that depends on it.

### While Changing Load-Bearing Code

- Keep ownership explicit. Admission, completion, persistence, cancellation,
  and reporting should not mutate the same state from hidden locations.
- Avoid hidden interleaving. When ordering matters, use an explicit queue,
  ledger, state transition, or synchronization point; never rely on incidental
  iteration order or callback timing.
- Prefer small, reversible edits over speculative frameworks. Reuse existing
  utilities and delete duplication before introducing another abstraction.
- Re-read the complete load-bearing path after the edit. A local diff is not
  enough when the invariant spans submission, completion, aftermath, cleanup,
  and restart.
- Preserve workspace isolation. Tests and runs must not share checkpoint files,
  mutable globals, ports, temporary directories, or executor state unless that
  sharing is the behavior under test.

### Verification Targets

Happy-path output is necessary but insufficient. Tests should exercise the
contracts most likely to fail across time and interleaving:

- lifecycle: initialization, normal shutdown, cancellation, and cleanup;
- recovery: retry, checkpoint resume, interruption, and process restart;
- ordering: input order, completion order, cached/live interleaving, and
  simultaneous stop conditions;
- isolation: repeated runs, parallel tests, separate workspaces, and stale
  artifact rejection;
- concrete failures: worker death, callback failure, corrupt persistence,
  unsupported runtime capability, and partial completion.

Independent verification is required for load-bearing claims. Reviewer
consensus is useful signal, not proof: reproduce the contract with tests,
probes, static checks, or observable receipts. A reviewer who did not exercise
the disputed path cannot close it by agreement alone.

### Definition of Done

A change is done only when all applicable statements are true:

- The intended behavior and non-goals are explicit.
- The invariant and concrete failure modes are documented in code, tests, or a
  developer plan at the place future maintainers will find them.
- External assumptions were verified against the real dependency or runtime.
- Targeted regression tests cover success, failure, lifecycle, recovery,
  ordering, and isolation in proportion to the risk.
- Formatting, lint, typing, API snapshots, documentation checks, packaging, and
  platform tests pass where the change can affect them.
- User-facing documentation and changelog text match the implementation without
  overstating evidence.
- Load-bearing code was re-read as a whole after the final edit.
- An independent reviewer verified the risky contract, and adversarial review
  found no unresolved blocker.
- The implementation, evidence, and durable documentation are committed before
  release or publication.

### Review Cadence

Review depth follows risk rather than diff size:

- **Documentation-only or mechanical changes:** author self-review plus the
  smallest build or validation that proves the artifact is sound.
- **Localized behavior changes:** targeted regressions and one independent
  review focused on the affected contract.
- **Concurrency, recovery, persistence, executor, or public API changes:** plan
  review before implementation; normal and adversarial reviews after it;
  targeted probes followed by the full applicable verification gate.
- **Release claims:** verify the built artifact and published output, not only
  the source tree. Record links, versions, and receipts before declaring the
  release complete.

If a review discovers a new load-bearing path, add its regression before asking
for another verdict. Repeat until the evidence is clean; do not count rounds or
consensus as a substitute for closure.
