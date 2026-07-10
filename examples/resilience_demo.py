#!/usr/bin/env python3
"""The pyarallel resilience demo — every claim on the box, proven locally.

One file, zero credentials, zero dependencies beyond pyarallel (the fake
API is stdlib ``http.server``, the client is stdlib ``urllib``). Run it:

    python examples/resilience_demo.py

What you'll watch happen, in order:

  ACT 1 — a fake API enforces a quota; a full-speed pool slams into it,
          collects 429s with ``Retry-After``, and ``Retry.for_http``
          honors them: ONE throttled response pauses the WHOLE pool
          (the server measures the gap — it doesn't take our word).
  ACT 2 — the same job with a client-side ``RateLimit`` never draws a
          single 429: stay under the limit instead of discovering it.
  ACT 3 — a checkpointed run is SIGKILLED mid-flight (a real kill of a
          real child process, not a simulation). The same command runs
          again and resumes: completed rows load from SQLite, only the
          remainder executes. The server's request counter is the
          receipt — paid-for calls were not repeated.

The demo asserts its own claims and exits non-zero if any fail.
Port 8973 in use? Set ``DEMO_PORT`` to any free port.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyarallel import Limiter, RateLimit, Retry, parallel_map

PORT = int(os.environ.get("DEMO_PORT", "8973"))
BASE = f"http://127.0.0.1:{PORT}"


# ---------------------------------------------------------------------------
# The fake API: a quota of N requests/second; over-quota → 429 Retry-After.
# ---------------------------------------------------------------------------


class _QuotaState:
    """Per-second window quota + a request log for the final receipts."""

    def __init__(self, per_second: int) -> None:
        self.per_second = per_second
        self.lock = threading.Lock()
        self.window = -1
        self.in_window = 0
        self.total_ok = 0
        self.total_429 = 0
        self.last_request_at = 0.0
        self.max_gap = 0.0

    def admit(self) -> bool:
        with self.lock:
            now = time.monotonic()
            # Measure the pool-pause as the LONGEST silence between any
            # two consecutive requests (OK or 429): a granted-just-before
            # -the-pause straggler can't fake it, and a real pool-wide
            # Retry-After pause shows up as ~1s of total silence.
            if self.last_request_at:
                self.max_gap = max(self.max_gap, now - self.last_request_at)
            self.last_request_at = now
            window = int(now)
            if window != self.window:
                self.window = window
                self.in_window = 0
            if self.in_window >= self.per_second:
                self.total_429 += 1
                return False
            self.in_window += 1
            self.total_ok += 1
            return True

    def snapshot(self) -> dict[str, float]:
        with self.lock:
            return {
                "ok": self.total_ok,
                "throttled": self.total_429,
                "pool_silence": round(self.max_gap, 2),
            }

    def reset(self) -> None:
        with self.lock:
            self.total_ok = 0
            self.total_429 = 0
            self.last_request_at = 0.0
            self.max_gap = 0.0
            # Fresh quota window too — without this, the previous act's
            # tail requests share a window with the next act's burst and
            # bleed a spurious 429 across the boundary.
            self.window = -1
            self.in_window = 0


def _make_server(quota: _QuotaState) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path == "/stats":
                body = json.dumps(quota.snapshot()).encode()
                self.send_response(200)
            elif self.path == "/reset":
                quota.reset()
                body = b"{}"
                self.send_response(200)
            elif quota.admit():
                time.sleep(0.01)  # a little latency, like a real API
                body = json.dumps({"id": self.path.rsplit("/", 1)[-1]}).encode()
                self.send_response(200)
            else:
                body = b'{"error": "quota exceeded"}'
                self.send_response(429)
                self.send_header("Retry-After", "1")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass  # keep the demo output clean

    return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


def _get(path: str) -> dict[str, float]:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# The job under test — module-level so every pyarallel executor could run it.
# ---------------------------------------------------------------------------


def fetch(i: int) -> dict[str, float]:
    with urllib.request.urlopen(f"{BASE}/item/{i}", timeout=10) as resp:
        return json.loads(resp.read())


# The 429 dance, prewired: both Retry-After dialects, statuses, backoff.
HTTP_RETRY = Retry.for_http(on=(urllib.error.HTTPError,), attempts=8, backoff=0.5)


# ---------------------------------------------------------------------------
# Child mode: the process that gets SIGKILLED (and rerun) in Act 3.
# ---------------------------------------------------------------------------


def child_run(n_items: int, checkpoint: str) -> None:
    def progress(done: int, total: int) -> None:
        print(f"progress {done}/{total}", flush=True)

    result = parallel_map(
        fetch,
        range(n_items),
        workers=8,
        rate_limit=Limiter(RateLimit(40, "second", burst=10)),
        retry=HTTP_RETRY,
        checkpoint=checkpoint,
        on_progress=progress,
    )
    print(f"child done ok={result.ok}", flush=True)


# ---------------------------------------------------------------------------
# The three acts.
# ---------------------------------------------------------------------------


def act1_429s_pause_the_pool() -> None:
    print("\nACT 1 — the server throttles; one 429 pauses the WHOLE pool")
    print("-" * 64)
    _get("/reset")
    # 80 items against a 30/s quota: even if the burst straddles two
    # per-second windows (60 admitted), at least 20 MUST get 429s.
    result = parallel_map(
        fetch,
        range(80),
        workers=8,
        # The shared Limiter IS the pause channel: 200/s is effectively
        # unthrottled, but Retry-After can only slow a pool that shares
        # a limiter — delete this and every worker rediscovers the 429
        # on its own.
        rate_limit=Limiter(RateLimit(200, "second")),
        retry=HTTP_RETRY,
    )
    stats = _get("/stats")
    throttled = int(stats["throttled"])
    print(f"  items completed : {len(result.ok_values())}/80 (every 429 retried)")
    print(f"  429s drawn      : {throttled} — and THAT is the point:")
    print("                    8 uncoordinated workers rediscover a quota")
    print("                    once per worker per window (dozens of wasted")
    print("                    calls). Here a single 429 paused the whole")
    print(f"                    pool — the server measured {stats['pool_silence']}s of")
    print("                    total silence — so almost no second one happened.")
    assert result.ok, "Act 1: all items must complete despite throttling"
    assert throttled >= 1, "Act 1: the demo needs at least one real 429"
    assert throttled <= 16, "Act 1: pool pause should prevent a 429 storm"
    assert stats["pool_silence"] >= 0.8, "Act 1: pool did not honor Retry-After"


def act2_stay_under_the_limit() -> None:
    print("\nACT 2 — same job, client-side RateLimit: zero 429s")
    print("-" * 64)
    _get("/reset")
    result = parallel_map(
        fetch,
        range(40),
        workers=8,
        rate_limit=Limiter(RateLimit(20, "second", burst=5)),  # under the quota
        retry=HTTP_RETRY,
    )
    stats = _get("/stats")
    print(f"  items completed : {len(result.ok_values())}/40")
    print(f"  server said 429 : {int(stats['throttled'])} times")
    print("                    stay under the quota instead of discovering it.")
    assert result.ok
    assert stats["throttled"] == 0, "Act 2: the rate limit should prevent all 429s"


def act3_kill_and_resume(checkpoint: str) -> None:
    n_items = 120
    print(f"\nACT 3 — SIGKILL a checkpointed run of {n_items}, then rerun it")
    print("-" * 64)
    _get("/reset")
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "--child",
        str(n_items),
        checkpoint,
    ]

    # First run: kill it (for real) once ~40% of items have completed.
    child = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    assert child.stdout is not None
    kill_at = n_items * 2 // 5
    done_before_kill = 0
    for line in child.stdout:
        if line.startswith("progress"):
            done_before_kill = int(line.split()[1].split("/")[0])
            if done_before_kill >= kill_at:
                child.kill()  # SIGKILL — no cleanup, no goodbye
                break
    child.wait()
    calls_first = int(_get("/stats")["ok"])
    print(f"  run 1: KILLED at item {done_before_kill} (SIGKILL, no cleanup)")
    print(f"         server had served {calls_first} calls — money spent")

    # Second run: the SAME command. It must resume, not restart.
    _get("/reset")
    t0 = time.monotonic()
    rerun = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=120)
    elapsed = time.monotonic() - t0
    calls_second = int(_get("/stats")["ok"])
    saved = n_items - calls_second
    print(f"  run 2: same command, {elapsed:.1f}s — resumed from SQLite")
    print(f"         server served only {calls_second} calls for {n_items} items")
    print(f"         {saved} paid-for calls NOT repeated (loaded from checkpoint)")
    print("         (only calls in flight AT the kill instant can repeat —")
    print("          at most one per worker; completed rows never do)")
    assert "child done ok=True" in rerun.stdout, "Act 3: rerun must complete"
    assert calls_second < n_items, "Act 3: rerun must not repeat completed work"
    assert saved >= done_before_kill - 8, "Act 3: checkpoint must cover completed work"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child_run(int(sys.argv[2]), sys.argv[3])
        return

    import tempfile

    print("pyarallel resilience demo — local, zero credentials, stdlib only")
    print(f"fake API: {BASE}  (quota: 30 req/s, over-quota → 429 Retry-After: 1)")

    quota = _QuotaState(per_second=30)
    try:
        server = _make_server(quota)
    except OSError as exc:
        sys.exit(
            f"cannot bind 127.0.0.1:{PORT} ({exc}) — something is already "
            f"using it. Set DEMO_PORT to any free port and rerun."
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    t0 = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            act1_429s_pause_the_pool()
            act2_stay_under_the_limit()
            act3_kill_and_resume(os.path.join(tmp, "demo.ckpt"))
        print("\n" + "=" * 64)
        print(f"all claims verified in {time.monotonic() - t0:.1f}s:")
        print("  429 + Retry-After honored, one throttle paused the whole pool,")
        print("  a client-side limit prevented throttling entirely, and a")
        print("  SIGKILLed run resumed from its checkpoint without repeating")
        print("  paid-for work. This is the stack you'd otherwise hand-roll.")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
