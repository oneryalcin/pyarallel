"""Stable application identity across collected and streaming result APIs."""

import asyncio
import sys
import time

import pytest

from pyarallel import (
    CheckpointError,
    RunStatus,
    StopToken,
    async_parallel_iter,
    async_parallel_map,
    async_parallel_starmap,
    parallel_iter,
    parallel_map,
    parallel_starmap,
)


def _double(value: int) -> int:
    return value * 2


async def _async_double(value: int) -> int:
    return value * 2


class TestSyncItemKey:
    @pytest.mark.parametrize("sequential", [False, True])
    def test_map_keys_success_failure_callback_and_duplicates(self, sequential):
        seen = []

        def work(value):
            if value == 2:
                raise ValueError("bad")
            return value * 10

        result = parallel_map(
            work,
            [1, 2, 3],
            workers=2,
            sequential=sequential,
            item_key=lambda _value: "shared",
            on_result=seen.append,
        )

        assert [(item.index, item.key) for item in result.item_results()] == [
            (0, "shared"),
            (1, "shared"),
            (2, "shared"),
        ]
        assert {item.index for item in seen} == {0, 1, 2}
        assert all(item.key == "shared" for item in seen)
        assert isinstance(result.item_results()[1].error, ValueError)

    def test_starmap_key_receives_source_tuple(self):
        result = parallel_starmap(
            lambda left, right: left + right,
            [(1, 2), (3, 4)],
            item_key=lambda args: f"{args[0]}:{args[1]}",
        )

        assert [item.key for item in result.item_results()] == ["1:2", "3:4"]

    def test_process_executor_keeps_unpicklable_key_on_driver(self):
        result = parallel_map(
            _double,
            [1, 2],
            workers=2,
            executor="process",
            item_key=lambda value: f"item-{value}",
        )

        assert [item.key for item in result.item_results()] == ["item-1", "item-2"]

    def test_stream_key_ledger_stays_window_bounded(self):
        window = 3
        stream = parallel_iter(
            _double,
            range(50),
            workers=2,
            window_size=window,
            item_key=str,
        )

        for _item in stream:
            assert stream.gi_frame is not None
            assert len(stream.gi_frame.f_locals["keys"]) <= window

    @pytest.mark.parametrize("ordered", [False, True])
    def test_stream_keys_keep_index_ordering_contract(self, ordered):
        def work(value):
            if value == 0:
                time.sleep(0.02)
            if value == 1:
                raise RuntimeError("failed")
            return value

        items = list(
            parallel_iter(
                work,
                [0, 1, 2],
                workers=3,
                ordered=ordered,
                item_key=lambda value: f"item-{value}",
            )
        )

        assert {item.index: item.key for item in items} == {
            0: "item-0",
            1: "item-1",
            2: "item-2",
        }
        if ordered:
            assert [item.index for item in items] == [0, 1, 2]


class TestAsyncItemKey:
    async def test_map_and_callback_carry_keys(self):
        seen = []

        async def work(value):
            if value == 2:
                raise ValueError("bad")
            return value * 10

        result = await async_parallel_map(
            work,
            [1, 2, 3],
            item_key=lambda value: f"customer-{value}",
            on_result=seen.append,
        )

        assert [item.key for item in result.item_results()] == [
            "customer-1",
            "customer-2",
            "customer-3",
        ]
        assert {item.key for item in seen} == {
            "customer-1",
            "customer-2",
            "customer-3",
        }

    async def test_starmap_key_receives_source_tuple(self):
        async def add(left, right):
            return left + right

        result = await async_parallel_starmap(
            add,
            [(1, 2), (3, 4)],
            item_key=lambda args: f"{args[0]}:{args[1]}",
        )

        assert [item.key for item in result.item_results()] == ["1:2", "3:4"]

    async def test_stream_keys_async_source(self):
        async def source():
            for value in range(3):
                yield value

        async def work(value):
            await asyncio.sleep(0)
            return value

        items = [
            item
            async for item in async_parallel_iter(
                work,
                source(),
                ordered=True,
                item_key=lambda value: f"row-{value}",
            )
        ]

        assert [(item.index, item.key) for item in items] == [
            (0, "row-0"),
            (1, "row-1"),
            (2, "row-2"),
        ]

    async def test_stream_key_ledger_stays_window_bounded(self):
        window = 3
        stream = async_parallel_iter(
            _async_double,
            range(50),
            concurrency=2,
            window_size=window,
            item_key=str,
        )

        async for _item in stream:
            assert stream.ag_frame is not None
            assert len(stream.ag_frame.f_locals["keys"]) <= window

    async def test_async_key_callable_is_rejected_before_task_creation(self):
        calls = []

        async def work(value):
            calls.append(value)
            return value

        async def key(value):
            return str(value)

        with pytest.raises(TypeError, match="synchronous"):
            await async_parallel_map(work, [1], item_key=key)

        assert calls == []


class TestCheckpointInteroperation:
    def test_checkpoint_key_fallback_is_evaluated_once_on_live_and_cached_runs(
        self, tmp_path
    ):
        checkpoint = tmp_path / "fallback.db"
        calls = []

        def key(value):
            calls.append(value)
            return f"record-{value}"

        first = parallel_map(
            _double,
            [1, 2],
            checkpoint=checkpoint,
            checkpoint_key=key,
        )
        second = parallel_map(
            _double,
            [1, 2],
            checkpoint=checkpoint,
            checkpoint_key=key,
        )

        assert calls == [1, 2, 1, 2]
        assert [item.key for item in first.item_results()] == ["record-1", "record-2"]
        assert [item.key for item in second.item_results()] == ["record-1", "record-2"]
        assert [item.attempts for item in second.item_results()] == [0, 0]

    def test_same_callable_serves_both_roles_once(self, tmp_path):
        calls = []

        def key(value):
            calls.append(value)
            return value

        result = parallel_map(
            _double,
            [1, 2],
            checkpoint=tmp_path / "same.db",
            checkpoint_key=key,
            item_key=key,
        )

        assert calls == [1, 2]
        assert [item.key for item in result.item_results()] == [1, 2]

    def test_distinct_callables_each_run_once(self, tmp_path):
        checkpoint_calls = []
        item_calls = []

        def checkpoint_key(value):
            checkpoint_calls.append(value)
            return value

        def item_key(value):
            item_calls.append(value)
            return f"item-{value}"

        result = parallel_map(
            _double,
            [1, 2],
            checkpoint=tmp_path / "distinct.db",
            checkpoint_key=checkpoint_key,
            item_key=item_key,
        )

        assert checkpoint_calls == [1, 2]
        assert item_calls == [1, 2]
        assert [item.key for item in result.item_results()] == ["item-1", "item-2"]

    def test_invalid_return_error_matrix(self, tmp_path):
        with pytest.raises(TypeError, match="item_key"):
            parallel_map(_double, [1], item_key=lambda _value: True)

        with pytest.raises(CheckpointError, match="checkpoint_key"):
            parallel_map(
                _double,
                [1],
                checkpoint=tmp_path / "checkpoint-invalid.db",
                checkpoint_key=lambda _value: True,
            )

        def shared(_value):
            return True

        with pytest.raises(CheckpointError, match="checkpoint_key"):
            parallel_map(
                _double,
                [1],
                checkpoint=tmp_path / "shared-invalid.db",
                checkpoint_key=shared,
                item_key=shared,
            )

    async def test_async_checkpoint_fallback_is_evaluated_once(self, tmp_path):
        calls = []

        def key(value):
            calls.append(value)
            return f"record-{value}"

        result = await async_parallel_map(
            _async_double,
            [1, 2],
            checkpoint=tmp_path / "async-fallback.db",
            checkpoint_key=key,
        )

        assert calls == [1, 2]
        assert [item.key for item in result.item_results()] == ["record-1", "record-2"]

    def test_cached_and_live_results_keep_aligned_fallback_keys(self, tmp_path):
        checkpoint = tmp_path / "mixed.db"
        parallel_map(_double, [2], checkpoint=checkpoint, checkpoint_key=int)

        result = parallel_map(
            _double,
            [1, 2],
            checkpoint=checkpoint,
            checkpoint_key=int,
        )

        assert [item.key for item in result.item_results()] == [1, 2]
        assert [item.attempts for item in result.item_results()] == [1, 0]

    @pytest.mark.parametrize("api", [parallel_map, async_parallel_map])
    async def test_async_checkpoint_key_is_closed_and_rejected(self, api, tmp_path):
        async def key(value):
            return str(value)

        checkpoint = tmp_path / f"awaitable-{api.__name__}.db"
        if api is parallel_map:
            with pytest.raises(CheckpointError, match="synchronous"):
                api(_double, [1], checkpoint=checkpoint, checkpoint_key=key)
        else:
            with pytest.raises(CheckpointError, match="synchronous"):
                await api(
                    _async_double,
                    [1],
                    checkpoint=checkpoint,
                    checkpoint_key=key,
                )


class TestAdmissionBoundaries:
    def test_deadline_crossing_during_keying_does_not_start_task(self):
        calls = []

        def key(value):
            time.sleep(0.02)
            return f"item-{value}"

        def work(value):
            calls.append(value)
            return value

        result = parallel_map(
            work,
            [1, 2],
            workers=1,
            window_size=1,
            timeout=0.005,
            item_key=key,
        )

        assert result.status is RunStatus.TIMED_OUT
        assert calls == []
        assert [item.key for item in result.item_results()] == ["item-1", None]

    def test_stop_during_keying_does_not_start_or_drain(self):
        stop = StopToken()
        calls = []
        pulled = []

        def source():
            for value in range(3):
                pulled.append(value)
                yield value

        def key(value):
            stop.stop()
            return f"item-{value}"

        result = parallel_map(
            lambda value: calls.append(value),
            source(),
            workers=1,
            window_size=1,
            stop=stop,
            item_key=key,
        )

        assert result.status is RunStatus.CANCELLED
        assert pulled == [0]
        assert calls == []
        assert [item.key for item in result.item_results()] == ["item-0"]

    async def test_async_deadline_crossing_during_keying_does_not_start_task(self):
        calls = []

        def key(value):
            time.sleep(0.02)
            return f"item-{value}"

        async def work(value):
            calls.append(value)
            return value

        result = await async_parallel_map(
            work,
            [1, 2],
            concurrency=1,
            window_size=1,
            timeout=0.005,
            item_key=key,
        )

        assert result.status is RunStatus.TIMED_OUT
        assert calls == []
        assert [item.key for item in result.item_results()] == ["item-1", None]

    async def test_async_stop_during_keying_does_not_start_or_drain(self):
        stop = StopToken()
        calls = []
        pulled = []

        async def source():
            for value in range(3):
                pulled.append(value)
                yield value

        def key(value):
            stop.stop()
            return f"item-{value}"

        async def work(value):
            calls.append(value)
            return value

        result = await async_parallel_map(
            work,
            source(),
            concurrency=1,
            window_size=1,
            stop=stop,
            item_key=key,
        )

        assert result.status is RunStatus.CANCELLED
        assert pulled == [0]
        assert calls == []
        assert [item.key for item in result.item_results()] == ["item-0"]

    @pytest.mark.parametrize("async_api", [False, True])
    async def test_max_errors_keys_admitted_item_but_not_unseen_items(self, async_api):
        if async_api:

            async def fail(_value):
                raise ValueError("bad")

            result = await async_parallel_map(
                fail,
                [0, 1, 2],
                concurrency=1,
                window_size=1,
                max_errors=1,
                item_key=lambda value: f"item-{value}",
            )
        else:

            def fail(_value):
                raise ValueError("bad")

            result = parallel_map(
                fail,
                [0, 1, 2],
                sequential=True,
                max_errors=1,
                item_key=lambda value: f"item-{value}",
            )

        assert result.status is RunStatus.ABORTED
        assert [item.key for item in result.item_results()] == ["item-0", None, None]

    def test_key_exception_stops_lazy_source_without_starting_keyed_item(self):
        pulled = []
        calls = []

        def source():
            for value in range(4):
                pulled.append(value)
                yield value

        def key(value):
            if value == 1:
                raise LookupError("missing identity")
            return str(value)

        with pytest.raises(LookupError, match="missing identity"):
            parallel_map(
                lambda value: calls.append(value),
                source(),
                workers=1,
                window_size=1,
                item_key=key,
            )

        assert pulled == [0, 1]
        assert calls == [0]

    async def test_async_stream_key_exception_stops_lazy_source(self):
        pulled = []
        calls = []

        async def source():
            for value in range(4):
                pulled.append(value)
                yield value

        def key(value):
            if value == 1:
                raise LookupError("missing identity")
            return str(value)

        async def work(value):
            calls.append(value)
            return value

        with pytest.raises(LookupError, match="missing identity"):
            async for _item in async_parallel_iter(
                work,
                source(),
                concurrency=1,
                window_size=1,
                item_key=key,
            ):
                pass

        assert pulled == [0, 1]
        assert calls == [0]

    @pytest.mark.skipif(
        sys.version_info < (3, 14), reason="InterpreterPoolExecutor is 3.14+"
    )
    def test_interpreter_executor_keeps_key_callable_on_driver(self):
        result = parallel_map(
            _double,
            [1, 2],
            executor="interpreter",
            item_key=lambda value: f"item-{value}",
        )

        assert [item.key for item in result.item_results()] == ["item-1", "item-2"]
