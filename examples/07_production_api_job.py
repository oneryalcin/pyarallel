#!/usr/bin/env python3
"""The production golden template — every recommended pattern in one job.

This is the shape to copy for a real overnight API job. One file, zero
credentials (the API is a local stdlib fake so the script is runnable),
zero dependencies beyond pyarallel. Run it:

    python examples/07_production_api_job.py

Every production pattern, wired together the recommended way:

  - ONE reusable HTTP client at module scope (never per-call)
  - ``Retry.for_http`` — 429/503 + both ``Retry-After`` dialects handled
  - a shared ``Limiter`` so the whole pool obeys one budget
  - ``checkpoint=`` + stable ``checkpoint_key=`` + ``checkpoint_version=``
    so a rerun resumes instead of re-spending
  - ``max_errors=`` so a dead API costs tens of calls, not thousands
  - SIGTERM/SIGINT → ``StopToken`` for graceful shutdown (Kubernetes
    sends SIGTERM before it kills your pod)
  - explicit ``RunStatus`` handling — partial results processed honestly

The script asserts its own claims and exits non-zero if any fail.
Port 8974 in use? Set ``DEMO_PORT`` to any free port.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyarallel import (
    Limiter,
    ParallelResult,
    RateLimit,
    Retry,
    RunStatus,
    StopToken,
    parallel_map,
)

PORT = int(os.environ.get("DEMO_PORT", "8974"))
BASE = f"http://127.0.0.1:{PORT}"

# ---------------------------------------------------------------------------
# Pattern 1: module-scope client, limiter, and retry policy — created ONCE.
#
# With httpx or requests this would be ONE ``httpx.Client()`` / ``Session()``
# shared by every worker (both are thread-safe); stdlib's equivalent is a
# shared opener. A client per call means a TCP+TLS handshake per call.
# ---------------------------------------------------------------------------

OPENER = urllib.request.build_opener()

# One budget for the whole pool. A bare ``rate_limit=25`` would also work,
# but constructing the Limiter yourself lets several jobs share it.
LIMITER = Limiter(RateLimit(25, "second", burst=5))

# 429/503 with either Retry-After dialect (seconds or HTTP-date), capped
# exponential backoff with jitter for everything else retryable.
HTTP_RETRY = Retry.for_http(on=(urllib.error.HTTPError,), attempts=5, backoff=0.5)

# Bump this when the meaning of a result changes (new prompt, new model,
# new schema) — old checkpoint rows then refuse to mix with new ones.
JOB_VERSION = ("users-api", "v1")


def fetch_user(record: dict[str, str]) -> dict[str, str]:
    """The unit of work — module-level so any executor could run it."""
    url = f"{BASE}/users/{record['user_id']}"
    with OPENER.open(url, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Pattern 2: SIGTERM → StopToken. Kubernetes, systemd, and Ctrl-C all speak
# signals; a StopToken turns them into "finish in-flight items, commit the
# checkpoint, report CANCELLED" instead of losing paid-for work.
# ---------------------------------------------------------------------------

STOP = StopToken()


def _request_stop(signum: int, frame: object) -> None:
    print(f"\n  signal {signum} received — draining, checkpoint stays valid…")
    STOP.stop()


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


# ---------------------------------------------------------------------------
# Pattern 3: the call itself — every safety net declared in one place.
# ---------------------------------------------------------------------------


def run_job(
    records: list[dict[str, str]], checkpoint: str
) -> ParallelResult[dict[str, str]]:
    def progress(done: int, total: int) -> None:
        if done % 20 == 0 or done == total:
            print(f"  progress: {done}/{total}")

    return parallel_map(
        fetch_user,
        records,
        workers=8,
        rate_limit=LIMITER,
        retry=HTTP_RETRY,
        timeout=120,  # total wall-clock budget for the run
        checkpoint=checkpoint,
        checkpoint_key=lambda r: r["user_id"],  # stable across reruns
        checkpoint_version=JOB_VERSION,
        max_errors=10,  # a dead API aborts after 10 failures, not 10,000
        stop=STOP,
        on_progress=progress,
    )


# ---------------------------------------------------------------------------
# Pattern 4: honest result handling. ``result.values()`` refuses to hand
# back a partial list dressed as a whole — so branch on status FIRST, and
# use ``ok_values()`` when partial results are exactly what you want.
# ---------------------------------------------------------------------------


def report(result: ParallelResult[dict[str, str]]) -> int:
    """Process the outcome; return the process exit code."""
    if result.status is RunStatus.CANCELLED:
        saved = result.ok_values()
        print(f"  cancelled — {len(saved)} completed items are in the checkpoint;")
        print("  rerun the same command to resume.")
        return 143  # 128 + SIGTERM, so orchestrators see a signal exit
    if result.status is RunStatus.TIMED_OUT:
        print(f"  hit the wall-clock budget with {len(result.ok_values())} done;")
        print("  rerun to resume from the checkpoint.")
        return 1
    if result.status is RunStatus.ABORTED:
        print("  aborted after max_errors=10 failures — is the API down?")
        for index, error in result.failures()[:3]:
            print(f"    item {index}: {error}")
        return 1

    # COMPLETED: every item ran. Individual items may still have failed.
    if not result.ok:
        print(f"  completed with {len(result.failures())} permanent failures:")
        for index, error in result.failures():
            print(f"    item {index}: {error}")
        return 1

    rows = result.values()  # safe here: status is COMPLETED and all ok
    retried = [r for r in result.item_results() if r.attempts > 1]
    print(f"  done: {len(rows)} rows; {len(retried)} items needed retries")
    return 0


# ---------------------------------------------------------------------------
# The local fake API (so this template runs anywhere): a request quota
# (over → 429 Retry-After) and a transient 503 on the first call for some
# users — the two failure modes every real API eventually shows you.
# ---------------------------------------------------------------------------


class _FakeAPI:
    def __init__(self, quota_per_second: int) -> None:
        self.quota = quota_per_second
        self.lock = threading.Lock()
        self.window = -1
        self.in_window = 0
        self.seen: set[str] = set()
        self.ok = 0
        self.throttled = 0
        self.transient = 0

    def respond(self, user_id: str) -> tuple[int, dict[str, str], str | None]:
        with self.lock:
            window = int(time.monotonic())
            if window != self.window:
                self.window, self.in_window = window, 0
            if self.in_window >= self.quota:
                self.throttled += 1
                return 429, {"error": "quota exceeded"}, "1"
            self.in_window += 1
            if user_id.endswith("3") and user_id not in self.seen:
                self.seen.add(user_id)
                self.transient += 1
                return 503, {"error": "temporarily unavailable"}, "0.2"
            self.seen.add(user_id)
            self.ok += 1
            return 200, {"user_id": user_id, "plan": "pro"}, None


def _serve(api: _FakeAPI) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            status, payload, retry_after = api.respond(self.path.rsplit("/", 1)[-1])
            body = json.dumps(payload).encode()
            self.send_response(status)
            if retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


def main() -> None:
    import tempfile

    print("production API job template — local fake API, zero credentials")
    print(f"  API: {BASE} (quota 40/s → 429; some users 503 on first call)\n")

    api = _FakeAPI(quota_per_second=40)
    try:
        server = _serve(api)
    except OSError as exc:
        sys.exit(
            f"cannot bind 127.0.0.1:{PORT} ({exc}) — set DEMO_PORT to a free port."
        )
    threading.Thread(target=server.serve_forever, daemon=True).start()

    records = [{"user_id": f"u{i:04d}"} for i in range(60)]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_job(records, os.path.join(tmp, "users.ckpt"))
            code = report(result)
    finally:
        server.shutdown()

    # The template asserts its own claims (CI runs this file):
    assert result.status is RunStatus.COMPLETED, result.status
    assert result.ok and len(result.values()) == 60
    retried = [r for r in result.item_results() if r.attempts > 1]
    assert len(retried) >= 6, "the transient 503s must be retried, not fatal"
    assert api.throttled == 0, "the client-side limit should prevent all 429s"
    print("\nall assertions passed — this file is the shape to copy.")
    sys.exit(code)


if __name__ == "__main__":
    main()
