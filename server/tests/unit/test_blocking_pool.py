import asyncio
import subprocess
import sys
import textwrap
import threading

import pytest

from arbiter_server.blocking import BlockingOperationPool


def test_process_can_exit_with_a_cancelled_request_and_a_hung_operation():
    script = textwrap.dedent(
        """
        import asyncio
        import threading
        from arbiter_server.blocking import BlockingOperationPool

        async def main():
            pool = BlockingOperationPool(1)
            entered = threading.Event()
            never_released = threading.Event()

            def hung_operation():
                entered.set()
                never_released.wait()

            request = asyncio.create_task(pool.run(hung_operation))
            assert await asyncio.to_thread(entered.wait, 5)
            request.cancel()
            try:
                await request
            except asyncio.CancelledError:
                pass
            pool.close()

        asyncio.run(main())
        print("shutdown completed", flush=True)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "shutdown completed"


def test_failed_thread_start_does_not_execute_later_or_leak_capacity(monkeypatch):
    pool = BlockingOperationPool(1)
    executed = []

    def cannot_start(_thread):
        raise RuntimeError("thread creation failed")

    async def exercise():
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", cannot_start)
            with pytest.raises(RuntimeError, match="thread creation failed"):
                await pool.run(lambda: executed.append("failed submission"))
        await pool.run(lambda: executed.append("later submission"))

    try:
        asyncio.run(exercise())
        assert executed == ["later submission"]
    finally:
        pool.close()


def test_cancellation_before_worker_start_releases_capacity_without_execution(
    monkeypatch,
):
    pool = BlockingOperationPool(1)
    executed = []

    async def exercise():
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", lambda _thread: None)
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


def test_closed_pool_rejects_calls_without_leaking_admission_capacity():
    pool = BlockingOperationPool(1)
    pool.close()

    async def exercise():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="shut down"):
                await pool.run(lambda: pytest.fail("closed pool executed work"))

    asyncio.run(exercise())


def test_close_cancels_work_that_has_not_started(monkeypatch):
    pool = BlockingOperationPool(1)
    executed = []

    async def exercise():
        monkeypatch.setattr(threading.Thread, "start", lambda _thread: None)
        pending = asyncio.create_task(pool.run(lambda: executed.append("unexpected")))
        await asyncio.sleep(0)
        pool.close()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(exercise())
    assert executed == []


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
