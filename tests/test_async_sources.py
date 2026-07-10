"""AsyncIterable sources: async cursors and paginated APIs feed the async
engines directly, with end-to-end backpressure (v0.9).

Production bug this prevents: users had to drain async sources into a
list first — a million-row cursor materialized in memory just to feed it
back out — because the engines only accepted sync iterables.
"""

import asyncio

import pytest

from pyarallel import (
    RunStatus,
    async_parallel_iter,
    async_parallel_map,
    async_parallel_starmap,
)


async def _double(x):
    return x * 2


async def _agen(n):
    for i in range(n):
        yield i


class TestBasicShapes:
    async def test_map_async_generator_source(self):
        r = await async_parallel_map(_double, _agen(10))
        assert r.ok
        assert r.values() == [i * 2 for i in range(10)]

    async def test_starmap_async_source(self):
        async def add(a, b):
            return a + b

        async def pairs():
            for i in range(5):
                yield (i, i)

        r = await async_parallel_starmap(add, pairs())
        assert r.values() == [0, 2, 4, 6, 8]

    async def test_iter_async_source_unordered(self):
        seen = set()
        async for item in async_parallel_iter(_double, _agen(10)):
            assert item.ok
            seen.add((item.index, item.value))
        assert seen == {(i, i * 2) for i in range(10)}

    async def test_iter_async_source_ordered(self):
        got = [
            (item.index, item.value)
            async for item in async_parallel_iter(_double, _agen(10), ordered=True)
        ]
        assert got == [(i, i * 2) for i in range(10)]

    async def test_sync_iterables_still_work(self):
        r = await async_parallel_map(_double, [1, 2, 3])
        assert r.values() == [2, 4, 6]
        r2 = await async_parallel_map(_double, (i for i in range(3)))
        assert r2.values() == [0, 2, 4]

    async def test_async_preferred_when_both(self):
        """An object that is both Iterable and AsyncIterable is consumed
        async — the async protocol is why you'd build such a thing."""

        class Both:
            def __iter__(self):
                raise AssertionError("sync path must not be used")

            def __aiter__(self):
                return _agen(3)

        r = await async_parallel_map(_double, Both())
        assert r.values() == [0, 2, 4]


class TestBackpressure:
    async def test_source_pulled_at_most_one_window_ahead(self):
        """The whole point: a huge async cursor must not be drained into
        memory — admission stays within the in-flight window."""
        pulls = 0
        gate = asyncio.Event()

        async def source():
            nonlocal pulls
            for i in range(1000):
                pulls += 1
                yield i

        async def blocked(x):
            await gate.wait()
            return x

        run = asyncio.create_task(
            async_parallel_map(blocked, source(), concurrency=2, window_size=4)
        )
        # let admission settle: spin until the pull count stops moving
        prev = -1
        while pulls != prev:
            prev = pulls
            await asyncio.sleep(0.02)
        assert pulls <= 4  # window, not the thousand
        gate.set()
        r = await run
        assert r.ok
        assert pulls == 1000

    async def test_no_drain_after_timeout(self):
        """A stopped run must never touch the source again."""
        pulls = 0

        async def source():
            nonlocal pulls
            while True:
                pulls += 1
                yield pulls

        async def slow(x):
            await asyncio.sleep(30)
            return x

        r = await async_parallel_map(
            slow, source(), concurrency=2, window_size=3, timeout=0.2
        )
        assert r.status is RunStatus.TIMED_OUT
        pulls_at_return = pulls
        await asyncio.sleep(0.05)
        assert pulls == pulls_at_return  # source untouched after the stop
        assert pulls <= 3  # never admitted beyond the window anyway

    async def test_no_drain_after_abort(self):
        pulls = 0

        async def source():
            nonlocal pulls
            while True:
                pulls += 1
                yield pulls

        async def boom(x):
            raise ValueError(f"item {x}")

        r = await async_parallel_map(
            boom, source(), concurrency=1, window_size=2, max_errors=2
        )
        assert r.status is RunStatus.ABORTED
        assert pulls <= 2 + 2  # abort point plus at most one window


class TestDeadlineDuringSourceWait:
    async def test_timeout_binds_while_source_awaits(self):
        """New power vs the sync engine: a source stuck in await (slow
        DB, dead paginator) cannot outlive the deadline."""

        async def stuck_source():
            yield 1
            await asyncio.Event().wait()  # never set — awaits forever
            yield 2

        r = await async_parallel_map(_double, stuck_source(), timeout=0.3)
        assert r.status is RunStatus.TIMED_OUT
        assert r.ok_values() == [2]  # the item that made it


class TestClosureContract:
    async def test_engine_never_closes_the_source(self):
        """The caller owns the source: breaking out of the stream (or a
        stop) must not run the source's finally — closing is the
        caller's job (wrap in aclosing() if you want that)."""
        closed = False

        async def source():
            nonlocal closed
            try:
                for i in range(100):
                    yield i
            finally:
                closed = True

        src = source()
        stream = async_parallel_iter(_double, src, concurrency=2)
        async for _item in stream:
            break
        await stream.aclose()
        assert closed is False
        await src.aclose()  # caller closes — works, and only now
        assert closed is True

    async def test_source_exception_propagates(self):
        async def poisoned():
            yield 1
            raise RuntimeError("cursor died")

        with pytest.raises(RuntimeError, match="cursor died"):
            await async_parallel_map(_double, poisoned())


class TestComposition:
    async def test_checkpoint_with_async_source(self, tmp_path):
        calls = {"n": 0}

        async def counted(x):
            calls["n"] += 1
            return x * 2

        ckpt = str(tmp_path / "run.ckpt")
        first = await async_parallel_map(counted, _agen(5), checkpoint=ckpt)
        assert first.ok and calls["n"] == 5
        second = await async_parallel_map(counted, _agen(5), checkpoint=ckpt)
        assert second.values() == first.values()
        assert calls["n"] == 5  # all five served from the checkpoint

    async def test_unsized_progress_total_counts_seen(self):
        totals = []
        await async_parallel_map(
            _double, _agen(6), on_progress=lambda done, total: totals.append(total)
        )
        assert totals  # fired
        assert all(t <= 6 for t in totals)
