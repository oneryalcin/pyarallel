"""Does an AIMD limiter beat the fixed token bucket when the real quota is hidden?

Validation gate for issue #53. Runs the real ``async_parallel_map`` + ``Limiter``
+ ``Retry.for_http`` path against an in-process fake API that enforces its own
(hidden, possibly changing) quota and has LLM-shaped latency.

    uv run --with . python benchmarks/adaptive_sim.py [--quick] [--seeds N]

Time is compressed (100 req/s, ~0.4s median latency) so a full run takes a few
minutes. Everything is real wall-clock asyncio — no virtual clock.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
import time
from dataclasses import dataclass, field

from pyarallel import Limiter, RateLimit, Retry, async_parallel_map

CEILING = 100.0  # what the user configured: RateLimit(100, "second")


# --------------------------------------------------------------------------- #
# Prototype adaptive limiter (the thing #53 proposes)
# --------------------------------------------------------------------------- #


class AIMDLimiter(Limiter):
    """Limiter whose rate is a control variable bounded by the configured spec.

    - multiplicative decrease on a throttle, at most once per "window": a
      throttle for a request *sent before* the last cut is the same overload
      event and is ignored (TCP's one-cut-per-window rule);
    - additive increase on success, scaled so 50% -> 100% of ceiling takes
      ``recover_s`` seconds at any ceiling.
    """

    def __init__(
        self,
        rate_limit: RateLimit,
        *,
        decrease: float = 0.5,
        floor_frac: float = 0.02,
        recover_s: float = 10.0,
    ) -> None:
        super().__init__(rate_limit)
        self.ceiling = self._rate
        self.floor = self.ceiling * floor_frac
        self.decrease = decrease
        self.ai_per_s = self.ceiling * (1 - decrease) / recover_s
        self.last_cut = -math.inf
        self.cuts = 0

    def _set_rate(self, new: float) -> None:
        # caller holds the lock; settle tokens accrued at the old rate first
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated = max(self._updated, now)
        self._rate = min(self.ceiling, max(self.floor, new))

    def on_throttle(self, sent_at: float) -> None:
        with self._lock:
            if sent_at < self.last_cut:
                return
            self.last_cut = self._clock()
            self.cuts += 1
            self._set_rate(self._rate * self.decrease)

    def on_success(self) -> None:
        with self._lock:
            # ~rate successes/s, so per-success step = ai_per_s / rate
            self._set_rate(self._rate + self.ai_per_s / self._rate)


# --------------------------------------------------------------------------- #
# Fake API: hidden token-bucket quota + lognormal latency
# --------------------------------------------------------------------------- #


class ThrottleError(Exception):
    """Duck-types as an aiohttp-style error: status + headers on the exception."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("429")
        self.status = 429
        self.headers = (
            {} if retry_after is None else {"retry-after": f"{retry_after:.3f}"}
        )


@dataclass
class FakeAPI:
    quota: list[tuple[float, float]]  # [(from_t, req_per_s), ...]
    send_retry_after: bool
    # Rejected requests still count against the quota (sliding-window style
    # limiters count attempts, not successes). Debt is capped at 1s of quota.
    rejections_consume: bool
    rng: random.Random
    median_latency: float = 0.4
    sigma: float = 0.7  # LLM-ish: long tail from output length
    t0: float = 0.0
    tokens: float = 0.0
    updated: float = 0.0
    throttles: int = 0
    calls: int = 0
    ok_times: list[float] = field(default_factory=list)

    def start(self) -> None:
        self.t0 = self.updated = time.monotonic()
        self.tokens = self._q(0.0)  # 1s of burst

    def _q(self, t: float) -> float:
        rate = self.quota[0][1]
        for frm, r in self.quota:
            if t >= frm:
                rate = r
        return rate

    async def __call__(self, item: int) -> int:
        self.calls += 1
        now = time.monotonic()
        q = self._q(now - self.t0)
        self.tokens = min(q, self.tokens + (now - self.updated) * q)  # burst = 1s
        self.updated = now
        if self.tokens < 1.0:
            self.throttles += 1
            if self.rejections_consume:
                self.tokens = max(-q, self.tokens - 1.0)
            await asyncio.sleep(0.02)  # rejections are fast
            wait = (1.0 - self.tokens) / q
            raise ThrottleError(wait if self.send_retry_after else None)
        self.tokens -= 1.0
        await asyncio.sleep(
            self.rng.lognormvariate(math.log(self.median_latency), self.sigma)
        )
        self.ok_times.append(time.monotonic() - self.t0)
        return item


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

SCENARIOS = {
    # name: (quota schedule, items, rejections consume quota)
    "steady  (quota=100, cfg=100)": ([(0, 100.0)], 1200, False),
    "drop    (100 -> 40 at t=5s)": ([(0, 100.0), (5, 40.0)], 900, False),
    "shared  (quota=50,  cfg=100)": ([(0, 50.0)], 600, False),
    "drop    +rejects count": ([(0, 100.0), (5, 40.0)], 900, True),
    "shared  +rejects count": ([(0, 50.0)], 600, True),
}


def ideal_seconds(quota: list[tuple[float, float]], n: int) -> float:
    """Time to push n requests through the quota with perfect knowledge."""
    done, t = 0.0, 0.0
    for i, (frm, r) in enumerate(quota):
        end = quota[i + 1][0] if i + 1 < len(quota) else math.inf
        if done + (end - frm) * r >= n:
            return frm + (n - done) / r
        done += (end - frm) * r
    return t


@dataclass
class Run:
    wall: float
    throttles: int
    failed: int
    timeline: list[int]
    cuts: int | None


async def run_once(
    quota, n, consume: bool, *, adaptive: bool, retry_after: bool, seed: int
) -> Run:
    api = FakeAPI(quota, retry_after, consume, random.Random(seed))
    limiter = (
        AIMDLimiter(RateLimit(CEILING)) if adaptive else Limiter(RateLimit(CEILING))
    )

    async def call(item: int) -> int:
        sent = time.monotonic()
        try:
            out = await api(item)
        except ThrottleError:
            if adaptive:
                limiter.on_throttle(sent)
            raise
        if adaptive:
            limiter.on_success()
        return out

    retry = Retry.for_http(
        on=(ThrottleError,), attempts=12, backoff=0.25, max_delay=4.0
    )
    api.start()
    t = time.monotonic()
    res = await async_parallel_map(
        call, range(n), concurrency=256, rate_limit=limiter, retry=retry
    )
    wall = time.monotonic() - t
    buckets = [0] * (int(wall) + 1)
    for s in api.ok_times:
        buckets[min(int(s), len(buckets) - 1)] += 1
    return Run(
        wall=wall,
        throttles=api.throttles,
        failed=len(list(res.failures())),
        timeline=buckets,
        cuts=limiter.cuts if adaptive else None,
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--quick",
        action="store_true",
        help="1/3 the items (smoke test; ends before the t=5s drop)",
    )
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--only", default="", help="run scenarios whose name contains this")
    args = ap.parse_args()

    print(
        f"ceiling (configured) = {CEILING:.0f}/s, concurrency=256, "
        "latency lognormal median 0.4s σ=0.7\n"
    )
    hdr = (
        f"{'scenario':30} {'Retry-After':11} {'limiter':6} {'ideal s':>7} "
        f"{'wall s':>7} {'429s':>6} {'429/item':>8} {'failed Σ':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    timelines = {}
    for name, (quota, n, consume) in SCENARIOS.items():
        if args.only not in name:
            continue
        n = n // 3 if args.quick else n
        ideal = ideal_seconds(quota, n)
        for ra in (True, False):
            for adaptive in (False, True):
                runs = [
                    await run_once(
                        quota, n, consume, adaptive=adaptive, retry_after=ra, seed=s
                    )
                    for s in range(args.seeds)
                ]
                wall = statistics.mean(r.wall for r in runs)
                thr = statistics.mean(r.throttles for r in runs)
                failed = sum(r.failed for r in runs)
                label = "aimd" if adaptive else "fixed"
                print(
                    f"{name:30} {'yes' if ra else 'no':11} {label:6} {ideal:7.1f} "
                    f"{wall:7.1f} {thr:6.0f} {thr / n:8.2f} {failed:8d}"
                )
                timelines[(name, ra, label)] = runs[0].timeline
        print()

    drops = [
        k for k in SCENARIOS if k.startswith("drop") and (k, True, "fixed") in timelines
    ]
    if drops:
        print("successes per second, drop scenarios (quota 100 -> 40 at t=5):")
    for drop in drops:
        print(f" {drop}")
        for ra in (True, False):
            for label in ("fixed", "aimd"):
                tl = timelines[(drop, ra, label)]
                print(
                    f"  RA={'yes' if ra else 'no ':3} {label:5} "
                    + " ".join(f"{v:3d}" for v in tl)
                )


if __name__ == "__main__":
    asyncio.run(main())
