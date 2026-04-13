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

Do not oversell behavior. If batching is lazy only with `batch_size`, say that.
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
