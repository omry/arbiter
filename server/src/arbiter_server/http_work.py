"""Keep bounded HTTP work on one worker through transport backpressure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import CancelledError
from contextlib import closing
import inspect
import threading
from typing import Any, TypeVar

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from .blocking import BlockingOperationPool
from .errors import OperationError

Result = TypeVar("Result")


class LoopBridge:
    """One admitted worker can await one loop operation at a time."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._stopped = False
        self._task: asyncio.Task[Any] | None = None

    def call(self, operation: Awaitable[Result]) -> Result:
        with self._lock:
            stopped = self._stopped
        if stopped:
            if inspect.iscoroutine(operation):
                operation.close()
            raise asyncio.CancelledError

        async def on_loop() -> Result:
            with self._lock:
                if self._stopped:
                    if inspect.iscoroutine(operation):
                        operation.close()
                    raise asyncio.CancelledError
                self._task = asyncio.current_task()
            try:
                return await operation
            finally:
                with self._lock:
                    self._task = None

        coroutine = on_loop()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            if inspect.iscoroutine(operation):
                operation.close()
            raise
        try:
            return future.result()
        except CancelledError:
            raise asyncio.CancelledError from None

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            task = self._task
        if task is not None:
            # Cancel the actual loop task, not the cross-thread Future: its
            # cleanup must finish before the worker gives back its slot.
            self._loop.call_soon_threadsafe(task.cancel)


class HTTPWork:
    def __init__(self, pool: BlockingOperationPool) -> None:
        self._pool = pool
        self._bridges: set[LoopBridge] = set()

    async def run(self, operation: Callable[[LoopBridge], Result]) -> Result:
        bridge = LoopBridge()
        self._bridges.add(bridge)
        try:
            return await self._pool.run(lambda: operation(bridge))
        finally:
            bridge.stop()
            self._bridges.discard(bridge)

    def close(self) -> None:
        for bridge in tuple(self._bridges):
            bridge.stop()
        self._pool.close()


class BlockingStreamingResponse(Response):
    """Own lookup, file I/O and backpressure within one admission slot."""

    def __init__(
        self,
        work: HTTPWork,
        prepare: Callable[[], tuple[Response, Generator[bytes, None, None]]],
        error_response: Callable[[Exception], Response],
    ) -> None:
        super().__init__()
        self._work = work
        self._prepare = prepare
        self._error_response = error_response

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False

        def stream(bridge: LoopBridge) -> None:
            nonlocal response_started
            try:
                response, chunks = self._prepare()
            except Exception as exc:
                response_started = True
                bridge.call(self._error_response(exc)(scope, receive, send))
                return
            with closing(chunks):
                response_started = True
                bridge.call(
                    send(
                        {
                            "type": "http.response.start",
                            "status": response.status_code,
                            "headers": response.raw_headers,
                        }
                    )
                )
                for chunk in chunks:
                    bridge.call(
                        send(
                            {
                                "type": "http.response.body",
                                "body": chunk,
                                "more_body": True,
                            }
                        )
                    )
                bridge.call(send({"type": "http.response.body", "body": b""}))

        async def serve() -> None:
            try:
                await self._work.run(stream)
            except Exception as exc:
                # Admission can fail before a worker starts. Errors after
                # response start must propagate rather than send new headers.
                if response_started or not isinstance(exc, OperationError):
                    raise
                await self._error_response(exc)(scope, receive, send)

        async def disconnected() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass

        response_task = asyncio.create_task(serve())
        disconnect_task = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait(
                (response_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
            )
            if response_task in done:
                await response_task
        finally:
            response_task.cancel()
            disconnect_task.cancel()
            await asyncio.gather(response_task, disconnect_task, return_exceptions=True)
