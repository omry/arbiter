"""Bound synchronous operation work without blocking the ASGI event loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
import threading
from typing import Any, TypeVar

from .errors import OperationError

Result = TypeVar("Result")


def validate_worker_limit(maximum: int) -> None:
    if type(maximum) is not int or maximum < 1:
        raise ValueError(
            "arbiter.limits.max_blocking_workers must be a positive integer"
        )


class BlockingOperationPool:
    def __init__(self, maximum: int) -> None:
        validate_worker_limit(maximum)
        self._maximum = maximum
        self._slots = threading.BoundedSemaphore(maximum)
        self._state_lock = threading.Lock()
        self._pending: set[Future[Any]] = set()
        self._closed = False

    async def run(
        self,
        operation: Callable[[], Result],
        *,
        on_finished: Callable[[], None] | None = None,
    ) -> Result:
        if not self._slots.acquire(blocking=False):
            if on_finished is not None:
                on_finished()
            raise OperationError(
                "resource_exhausted",
                "Server operation capacity is full; try again later.",
                details={"max_blocking_workers": self._maximum},
                transient="yes",
                replay="safe",
            )
        future: Future[Result] = Future()
        future.add_done_callback(self._finished)
        if on_finished is not None:
            future.add_done_callback(lambda _future: on_finished())
        try:
            with self._state_lock:
                self._pending.add(future)
                if self._closed:
                    raise RuntimeError("Operation pool is shut down")
                # A blocked operation must not hold the interpreter open after
                # request cancellation and server shutdown. The semaphore
                # bounds these threads without an executor's exit-time join.
                threading.Thread(
                    target=self._execute,
                    args=(future, operation),
                    name="arbiter-operation",
                    daemon=True,
                ).start()
        except BaseException:
            # No work queue survives a failed thread start. Cancellation also
            # runs the completion callback that returns this admission slot.
            future.cancel()
            raise
        # Cancelling the ASGI waiter cannot stop a running thread. Its slot
        # belongs to the underlying future until work actually finishes (or a
        # queued future is cancelled before it starts).
        return await asyncio.wrap_future(future)

    def _finished(self, future: Future[Any]) -> None:
        with self._state_lock:
            if future not in self._pending:
                return
            self._pending.remove(future)
        self._slots.release()

    def _execute(self, future: Future[Result], operation: Callable[[], Result]) -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = operation()
        except BaseException as exc:
            self._finished(future)
            future.set_exception(exc)
        else:
            # A completed Future can wake its waiter before its callbacks run.
            # Return capacity before publishing completion to that waiter.
            self._finished(future)
            future.set_result(result)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            pending = tuple(self._pending)
        for future in pending:
            future.cancel()
