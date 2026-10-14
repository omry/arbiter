import asyncio

import pytest

from arbiter_server.blocking import BlockingOperationPool


def test_failed_thread_start_cannot_leave_commands_queued_for_later(monkeypatch):
    pool = BlockingOperationPool(1)
    executed = []

    def cannot_start():
        raise RuntimeError("thread creation failed")

    async def exercise():
        with monkeypatch.context() as patch:
            # The real executor queues its work item before starting a thread.
            patch.setattr(pool._executor, "_adjust_thread_count", cannot_start)
            with pytest.raises(RuntimeError, match="thread creation failed"):
                await pool.run(lambda: executed.append("failed submission"))
        with pytest.raises(RuntimeError, match="shutdown"):
            await pool.run(lambda: executed.append("later submission"))

    try:
        asyncio.run(exercise())
        assert executed == []
    finally:
        pool.close()


def test_cancellation_before_worker_start_releases_capacity_without_execution(
    monkeypatch,
):
    pool = BlockingOperationPool(1)
    executed = []

    async def exercise():
        with monkeypatch.context() as patch:
            patch.setattr(pool._executor, "_adjust_thread_count", lambda: None)
            queued = asyncio.create_task(pool.run(lambda: executed.append("cancelled")))
            await asyncio.sleep(0)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            await asyncio.sleep(0)
        await pool.run(lambda: executed.append("next"))

    try:
        asyncio.run(asyncio.wait_for(exercise(), timeout=2))
        assert executed == ["next"]
    finally:
        pool.close()


def test_operation_exception_releases_capacity():
    pool = BlockingOperationPool(1)

    def failing_operation():
        raise ValueError("expected failure")

    async def exercise():
        with pytest.raises(ValueError, match="expected failure"):
            await pool.run(failing_operation)
        assert await pool.run(lambda: "next") == "next"

    try:
        asyncio.run(exercise())
    finally:
        pool.close()
