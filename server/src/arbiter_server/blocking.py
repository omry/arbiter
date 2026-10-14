"""Bound synchronous operation work without blocking the ASGI event loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import TypeVar

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
        self._executor = ThreadPoolExecutor(
            max_workers=maximum, thread_name_prefix="arbiter-operation"
        )

    async def run(self, operation: Callable[[], Result]) -> Result:
        if not self._slots.acquire(blocking=False):
            raise OperationError(
                "resource_exhausted",
                "Server operation capacity is full; try again later.",
                details={"max_blocking_workers": self._maximum},
                transient="yes",
                replay="safe",
            )
        try:
            future = self._executor.submit(operation)
        except BaseException:
            # Thread creation can fail after submit has enqueued a work item.
            # Stop this executor and cancel queued work before returning an
            # error, rather than accumulating untracked submissions.
            self.close()
            self._slots.release()
            raise
        # Cancelling the ASGI waiter cannot stop a running thread. Its slot
        # belongs to the underlying future until work actually finishes (or a
        # queued future is cancelled before it starts).
        future.add_done_callback(lambda _future: self._slots.release())
        return await asyncio.wrap_future(future)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
