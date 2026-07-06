"""Tests for checkpoint/resume — crash at item 40k must not restart from zero."""

import pytest

from pyarallel import CheckpointError, async_parallel_map, parallel, parallel_map


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

    def test_unpicklable_result_aborts_loudly(self, tmp_path):
        """A result that can't be checkpointed breaks the resume contract —
        the run must stop with CheckpointError, not mislabel a success."""
        ckpt = tmp_path / "run.ckpt"

        def work(x):
            return lambda: x  # lambdas can't be pickled

        with pytest.raises(CheckpointError, match="i:0"):
            parallel_map(work, [1], checkpoint=ckpt)

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


class TestStaleReuseFailsClosed:
    """The Codex adversarial finding: same checkpoint + different function
    must never silently serve the previous computation's results."""

    def test_different_function_raises(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"

        def embed(x):
            return x * 2

        def summarize(x):
            return x * 100

        assert parallel_map(embed, [1, 2], checkpoint=ckpt).ok
        with pytest.raises(CheckpointError, match="different function"):
            parallel_map(summarize, [1, 2], checkpoint=ckpt)

    def test_edited_function_body_raises(self, tmp_path):
        """Same name, different bytecode — still fails closed."""
        ckpt = tmp_path / "run.ckpt"
        ns1, ns2 = {}, {}
        exec("def work(x):\n    return x * 2", ns1)
        exec("def work(x):\n    return x * 3", ns2)

        assert parallel_map(ns1["work"], [1], checkpoint=ckpt).ok
        with pytest.raises(CheckpointError, match="different function"):
            parallel_map(ns2["work"], [1], checkpoint=ckpt)

    def test_same_function_reopens_cleanly(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"

        def work(x):
            return x + 1

        assert parallel_map(work, [1], checkpoint=ckpt).ok
        assert list(parallel_map(work, [1], checkpoint=ckpt)) == [2]

    async def test_async_different_function_raises(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"

        async def first(x):
            return x

        async def second(x):
            return -x

        assert (await async_parallel_map(first, [1], checkpoint=ckpt)).ok
        with pytest.raises(CheckpointError, match="different function"):
            await async_parallel_map(second, [1], checkpoint=ckpt)


class TestCallableStateGuards:
    """Second-round adversarial findings: captured state must join the
    checkpoint identity (visible config), be tolerated by type (live
    objects, mutable counters), or be rejected (opaque instance state)."""

    def test_changed_closure_config_raises(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"

        def make_worker(factor):
            def work(x):
                return x * factor

            return work

        assert parallel_map(make_worker(2), [1, 2], checkpoint=ckpt).ok
        with pytest.raises(CheckpointError, match="different function"):
            parallel_map(make_worker(3), [1, 2], checkpoint=ckpt)

    def test_same_closure_config_resumes(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        runs = []

        def make_worker(factor):
            def work(x):
                runs.append(x)
                return x * factor

            return work

        assert parallel_map(make_worker(2), [1], checkpoint=ckpt).ok
        runs.clear()
        assert list(parallel_map(make_worker(2), [1], checkpoint=ckpt)) == [2]
        assert runs == []  # identical config — served from disk

    def test_changed_partial_keyword_raises(self, tmp_path):
        import functools

        ckpt = tmp_path / "run.ckpt"

        def scale(x, *, factor):
            return x * factor

        assert parallel_map(functools.partial(scale, factor=2), [1], checkpoint=ckpt).ok
        with pytest.raises(CheckpointError, match="different function"):
            parallel_map(functools.partial(scale, factor=3), [1], checkpoint=ckpt)

    def test_changed_default_raises(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        ns1, ns2 = {}, {}
        exec("def work(x, factor=2):\n    return x * factor", ns1)
        exec("def work(x, factor=3):\n    return x * factor", ns2)

        assert parallel_map(ns1["work"], [1], checkpoint=ckpt).ok
        with pytest.raises(CheckpointError, match="different function"):
            parallel_map(ns2["work"], [1], checkpoint=ckpt)

    def test_bound_method_rejected(self, tmp_path):
        class Client:
            def fetch(self, x):
                return x

        with pytest.raises(CheckpointError, match="bound method"):
            parallel_map(Client().fetch, [1], checkpoint=tmp_path / "run.ckpt")

    def test_callable_object_rejected(self, tmp_path):
        class Worker:
            def __call__(self, x):
                return x

        with pytest.raises(CheckpointError, match="callable object"):
            parallel_map(Worker(), [1], checkpoint=tmp_path / "run.ckpt")

    def test_live_object_closure_is_stable_across_runs(self, tmp_path):
        """A captured client contributes its type, not its address — the
        flagship closure-over-client pattern must survive reruns."""
        ckpt = tmp_path / "run.ckpt"
        calls = []

        class FakeClient:
            def get(self, x):
                calls.append(x)
                return x * 10

        def make_worker():
            client = FakeClient()  # fresh instance (new address) per run

            def work(x):
                return client.get(x)

            return work

        assert parallel_map(make_worker(), [1, 2], checkpoint=ckpt).ok
        calls.clear()
        assert list(parallel_map(make_worker(), [1, 2], checkpoint=ckpt)) == [10, 20]
        assert calls == []  # resumed, no spurious identity mismatch


class TestAsyncCheckpointErrorContract:
    async def test_async_unpicklable_result_raises_plain_checkpoint_error(
        self, tmp_path
    ):
        """The TaskGroup must not leak an ExceptionGroup — `except
        CheckpointError` has to work identically for sync and async."""

        async def work(x):
            return lambda: x  # unpicklable

        with pytest.raises(CheckpointError, match="i:0") as excinfo:
            await async_parallel_map(work, [1], checkpoint=tmp_path / "run.ckpt")
        assert not isinstance(excinfo.value, BaseExceptionGroup)
        assert excinfo.value.__cause__ is not None  # pickle error chain kept


class TestCheckpointKey:
    """checkpoint_key= — identity-keyed rows that survive input evolution."""

    def test_prepending_an_item_only_runs_the_new_item(self, tmp_path):
        """The deferred v0.4 finding: positional rows made resume evaporate
        exactly when jobs evolve. Keyed rows survive a prepend."""
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(item):
            calls.append(item)
            return item * 10

        key = int  # item value is its identity

        assert parallel_map(work, [2, 3, 4], checkpoint=ckpt, checkpoint_key=key).ok
        calls.clear()
        result = parallel_map(work, [1, 2, 3, 4], checkpoint=ckpt, checkpoint_key=key)
        assert list(result) == [10, 20, 30, 40]
        assert calls == [1]  # only the prepended item ran

    def test_reordering_inputs_recomputes_nothing(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(item):
            calls.append(item)
            return item * 10

        assert parallel_map(work, [1, 2, 3], checkpoint=ckpt, checkpoint_key=int).ok
        calls.clear()
        result = parallel_map(work, [3, 1, 2], checkpoint=ckpt, checkpoint_key=int)
        assert list(result) == [30, 10, 20]
        assert calls == []

    def test_cross_type_keys_do_not_collide(self, tmp_path):
        """1, "1", and b"1" must be three distinct rows — plain text
        normalization would silently serve the wrong result."""
        ckpt = tmp_path / "run.ckpt"
        items = [1, "1", b"1"]

        def work(item):
            return repr(item)

        first = parallel_map(work, items, checkpoint=ckpt, checkpoint_key=lambda x: x)
        assert list(first) == ["1", "'1'", "b'1'"]
        # All three rows resumed independently, none served across types.
        calls = []

        def work2(item):
            calls.append(item)
            return repr(item)

        # same signature requirement: reuse work via same file needs same fn;
        # rerun with the original function instead
        second = parallel_map(work, items, checkpoint=ckpt, checkpoint_key=lambda x: x)
        assert list(second) == ["1", "'1'", "b'1'"]

    def test_duplicate_key_raises(self, tmp_path):
        with pytest.raises(CheckpointError, match="duplicate checkpoint_key"):
            parallel_map(
                lambda x: x,
                [1, 1],
                checkpoint=tmp_path / "run.ckpt",
                checkpoint_key=int,
            )

    def test_key_function_error_propagates_at_submit(self, tmp_path):
        def bad_key(item):
            raise RuntimeError("key exploded")

        with pytest.raises(RuntimeError, match="key exploded"):
            parallel_map(
                lambda x: x,
                [1],
                checkpoint=tmp_path / "run.ckpt",
                checkpoint_key=bad_key,
            )

    def test_invalid_key_type_raises(self, tmp_path):
        with pytest.raises(CheckpointError, match="str, int, or bytes"):
            parallel_map(
                lambda x: x,
                [1],
                checkpoint=tmp_path / "run.ckpt",
                checkpoint_key=lambda x: 1.5,
            )

    def test_changed_payload_under_same_key_recomputes(self, tmp_path):
        """key = which row; fingerprint = has the payload changed."""
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(item):
            calls.append(item)
            return item["v"]

        key = lambda item: item["id"]  # noqa: E731
        assert parallel_map(
            work, [{"id": 1, "v": 10}], checkpoint=ckpt, checkpoint_key=key
        ).ok
        calls.clear()
        result = parallel_map(
            work, [{"id": 1, "v": 99}], checkpoint=ckpt, checkpoint_key=key
        )
        assert list(result) == [99]
        assert len(calls) == 1  # same key, changed payload → recomputed

    def test_checkpoint_key_without_checkpoint_rejected(self):
        with pytest.raises(ValueError, match="checkpoint_key requires"):
            parallel_map(lambda x: x, [1], checkpoint_key=int)

    async def test_async_checkpoint_key_resume(self, tmp_path):
        ckpt = tmp_path / "run.ckpt"
        calls = []

        async def work(item):
            calls.append(item)
            return item * 10

        first = await async_parallel_map(
            work, [2, 3], checkpoint=ckpt, checkpoint_key=int
        )
        assert first.ok
        calls.clear()
        second = await async_parallel_map(
            work, [1, 2, 3], checkpoint=ckpt, checkpoint_key=int
        )
        assert list(second) == [10, 20, 30]
        assert calls == [1]

    def test_checkpoint_key_with_max_errors_resumes(self, tmp_path):
        """The evolving-inputs overnight job: keyed rows + early abort."""
        ckpt = tmp_path / "run.ckpt"
        state = {"broken": True}
        calls = []

        def api(item):
            calls.append(item)
            if item >= 5 and state["broken"]:
                raise ConnectionError("down")
            return item * 10

        first = parallel_map(
            api,
            range(20),
            workers=1,
            max_errors=3,
            checkpoint=ckpt,
            checkpoint_key=int,
        )
        assert not first.ok

        state["broken"] = False
        calls.clear()
        # Inputs evolved: two new items prepended — keyed rows still hit.
        second = parallel_map(
            api,
            [100, 101, *range(20)],
            workers=1,
            max_errors=3,
            checkpoint=ckpt,
            checkpoint_key=int,
        )
        assert second.ok
        assert set(calls).isdisjoint(range(5))  # completed items never re-ran


class TestSchemaV2:
    def test_pre_v2_file_fails_closed(self, tmp_path):
        """A v0.4-era positional file (no schema_version) must be refused
        with instructions, never silently migrated or misread."""
        import sqlite3

        path = tmp_path / "old.ckpt"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE results (idx INTEGER PRIMARY KEY,"
            " fingerprint BLOB NOT NULL, value BLOB NOT NULL)"
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('task_signature', 'old.fn:abc')"
        )
        conn.commit()
        conn.close()

        with pytest.raises(CheckpointError, match="unsupported schema"):
            parallel_map(_double, [1], checkpoint=path)

    def test_positional_mode_still_resumes(self, tmp_path):
        """No checkpoint_key: same behavior as before, new on-disk encoding."""
        ckpt = tmp_path / "run.ckpt"
        calls = []

        def work(item):
            calls.append(item)
            return item * 10

        assert parallel_map(work, [1, 2, 3], checkpoint=ckpt).ok
        calls.clear()
        assert list(parallel_map(work, [1, 2, 3], checkpoint=ckpt)) == [10, 20, 30]
        assert calls == []


def _double(x):
    return x * 2
