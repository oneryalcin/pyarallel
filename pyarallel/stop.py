"""Cooperative stop: ``StopToken`` — land the plane, don't crash it.

A token is a latch: ``stop()`` flips it once, from any thread — a
SIGTERM handler, a notebook interrupt hook, a spend-limit watchdog —
and every run holding the token ceases admission, cancels what can be
cancelled, keeps completed checkpoint rows, and reports
``RunStatus.CANCELLED``.

Honest asymmetry, stated once: the async engine cancels in-flight
tasks (asyncio can); the sync engine cannot kill running threads —
in-flight items finish on their own time in the background, exactly as
with ``timeout=``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class StopToken:
    """Cooperative cancellation latch for collected runs.

    Pass the same token to one or more ``parallel_map`` /
    ``async_parallel_map`` calls via ``stop=``; call ``.stop()`` to
    cancel them. Thread-safe and idempotent — safe to call from signal
    handlers and watchdog threads. A stopped token stays stopped: every
    subsequent run given it cancels immediately (use a fresh token per
    campaign).

    Example::

        token = StopToken()
        signal.signal(signal.SIGTERM, lambda *_: token.stop())

        result = parallel_map(fn, items, stop=token, checkpoint="run.ckpt")
        if result.status is RunStatus.CANCELLED:
            ...  # completed rows are already in the checkpoint
    """

    __slots__ = ("_event", "_lock", "_callbacks")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def stop(self) -> None:
        """Flip the latch. Idempotent; callable from any thread."""
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            cb()

    @property
    def stopped(self) -> bool:
        """True once ``stop()`` has been called."""
        return self._event.is_set()

    def _register(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Engine hook: run *callback* on stop (immediately if already
        stopped). Returns an unregister function — engines must call it
        when their run ends, or a long-lived token accumulates dead
        callbacks."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)

                def _unregister() -> None:
                    with self._lock:
                        if callback in self._callbacks:
                            self._callbacks.remove(callback)

                return _unregister
        # Already stopped: fire now, nothing to unregister.
        callback()
        return lambda: None
