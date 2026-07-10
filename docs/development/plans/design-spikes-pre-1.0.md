# Pre-1.0 Design Spike Records

The roadmap requires two pre-freeze checks — not implementations,
decisions. Recorded here so the freeze doesn't rest on unwritten
conclusions.

## Spike 1 — Weighted / composite quotas (deferred to 1.1, additive)

**Question:** does freezing today's `Limiter` surface preclude weighted
acquisition (an item consuming N tokens) or composite budgets
(requests/min AND tokens/min acquired atomically)?

**Conclusion: no — extension is purely additive.** The reasoning:

- Today's public `Limiter` surface is three methods: `wait(timeout=)`,
  `wait_async()`, `pause(seconds)`. Weighted acquisition adds an
  optional parameter (`wait(weight=1)`) with a default that preserves
  every existing call site — no signature breaks.
- Composite budgets compose *around* the current type: a future
  `Limiter.all(rpm_limiter, tpm_limiter)` (or a `CompositeLimiter`)
  can implement the same three-method protocol; the engines call only
  that protocol (`_as_limiter` normalizes at the boundary), so engine
  code needs zero changes.
- Atomic multi-budget acquisition without leaks is an implementation
  concern *inside* such a composite (acquire-all-or-release), invisible
  to the engine contract.
- The one surface that would have blocked this — per-call weight needing
  to reach the limiter from `parallel_map` — already has a home: a
  future `rate_weight=` per-call option or a weight callable, both
  additive keywords under the existing presence-sentinel rules.

**Therefore:** the `Limiter` protocol freezes as-is at 1.0; weighted /
composite lands additively in 1.1 if demand materializes.

## Spike 2 — Streaming checkpoint (deferred, contract required first)

**Question:** why doesn't `parallel_iter` take `checkpoint=`?

**Conclusion: deferred until an at-least-once contract can be stated
crisply — shipping it sooner would undermine the reliability
positioning.** The core problem:

- In collected maps, "row committed to SQLite" and "result delivered to
  the caller" happen inside one call — the checkpoint's claim ("this
  item is done") is unambiguous.
- In streaming, the caller consumes results *as a side effect* (writing
  to a sink). A resumed stream must choose: skip checkpointed items
  (then a sink that crashed after compute but before its own write
  loses data silently) or re-yield them (then every consumer must be
  idempotent — at-least-once delivery).
- "Cached computation" ≠ "sink committed." Any honest design must make
  the consumer's idempotency obligation explicit — e.g.
  `parallel_iter(..., checkpoint=..., replay="none" | "cached")` with
  documentation that `replay="none"` trusts the sink and
  `replay="cached"` requires idempotent writes.

**Required before implementation** (1.1 at the earliest): the replay
semantics above, a decision on whether cached re-yields carry
`attempts=0` metadata (consistent with `item_results()`), and tests
proving no delivery state is claimed that the engine cannot know.
Until then the documented guidance stands: streaming resume is the
sink's job (skip rows whose output already exists), or use
`parallel_map` with `checkpoint=` over a bounded `window_size`.
