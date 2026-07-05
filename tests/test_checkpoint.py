"""Tests for checkpoint/resume — crash at item 40k must not restart from zero."""

from pyarallel import async_parallel_map, parallel, parallel_map


class TestResume:
    def test_resume_skips_completed_items(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        attempts = {"count": {}}

        def work(x):
            attempts["count"][x] = attempts["count"].get(x, 0) + 1
            if x == 3 and attempts["count"][3] == 1:
                raise ValueError("simulated crash")
            return x * 2

        first = parallel_map(work, [1, 2, 3, 4], checkpoint=ckpt)
        assert not first.ok
        assert len(first.successes()) == 3

        second = parallel_map(work, [1, 2, 3, 4], checkpoint=ckpt)
        assert second.ok
        assert list(second) == [2, 4, 6, 8]
        # Only the failed item re-ran; completed items came from disk.
        assert attempts["count"] == {1: 1, 2: 1, 3: 2, 4: 1}

    def test_changed_item_is_recomputed(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(x):
            calls.append(x)
            return x * 10

        assert parallel_map(work, [1, 2, 3], checkpoint=ckpt).ok
        calls.clear()

        result = parallel_map(work, [1, 9, 3], checkpoint=ckpt)
        assert list(result) == [10, 90, 30]
        assert calls == [9]  # same-position changed input recomputed, rest cached

    def test_none_results_are_cached(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(x):
            calls.append(x)
            return None

        assert parallel_map(work, [1, 2], checkpoint=ckpt).ok
        calls.clear()

        result = parallel_map(work, [1, 2], checkpoint=ckpt)
        assert result.values() == [None, None]
        assert calls == []  # a legitimate None is a hit, not a miss

    def test_resume_with_batched_generator(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        attempts = {"count": {}}

        def work(x):
            attempts["count"][x] = attempts["count"].get(x, 0) + 1
            if x == 5 and attempts["count"][5] == 1:
                raise ValueError("crash mid-stream")
            return x * 2

        first = parallel_map(work, (i for i in range(8)), batch_size=3, checkpoint=ckpt)
        assert not first.ok

        second = parallel_map(
            work, (i for i in range(8)), batch_size=3, checkpoint=ckpt
        )
        assert second.ok
        assert list(second) == [i * 2 for i in range(8)]
        assert attempts["count"][5] == 2
        assert all(attempts["count"][i] == 1 for i in range(8) if i != 5)

    def test_unpicklable_result_reported_as_failure(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"

        def work(x):
            return lambda: x  # lambdas can't be pickled

        result = parallel_map(work, [1], checkpoint=ckpt)
        assert not result.ok
        assert len(result.failures()) == 1

    def test_progress_counts_cached_items(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        assert parallel_map(lambda x: x, [1, 2, 3], checkpoint=ckpt).ok

        seen = []
        parallel_map(
            lambda x: x,
            [1, 2, 3],
            checkpoint=ckpt,
            on_progress=lambda done, total: seen.append((done, total)),
        )
        assert seen[-1] == (3, 3)  # cached items still reach 100%


class TestAsyncResume:
    async def test_async_resume_skips_completed_items(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        attempts = {"count": {}}

        async def work(x):
            attempts["count"][x] = attempts["count"].get(x, 0) + 1
            if x == 2 and attempts["count"][2] == 1:
                raise ValueError("simulated crash")
            return x * 2

        first = await async_parallel_map(work, [1, 2, 3], checkpoint=ckpt)
        assert not first.ok

        second = await async_parallel_map(work, [1, 2, 3], checkpoint=ckpt)
        assert second.ok
        assert list(second) == [2, 4, 6]
        assert attempts["count"] == {1: 1, 2: 2, 3: 1}


class TestDecoratorCheckpoint:
    def test_map_accepts_checkpoint(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        @parallel(workers=2)
        def work(x):
            calls.append(x)
            return x + 1

        assert list(work.map([1, 2], checkpoint=ckpt)) == [2, 3]
        calls.clear()
        assert list(work.map([1, 2], checkpoint=ckpt)) == [2, 3]
        assert calls == []
