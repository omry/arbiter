"""Bound IMAP literals before the standard library allocates their contents."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import imaplib
import ssl

from arbiter_server.errors import OperationError, Replay


def check_message_size(size: int, maximum: int, *, replay: Replay = "safe") -> None:
    if size > maximum:
        raise OperationError(
            "policy_denied",
            "IMAP message exceeds the configured size limit.",
            plugin="imap",
            domain_code="message_too_large",
            details={"max_message_bytes": maximum},
            transient="no",
            replay=replay,
        )


class _BoundedLiteralMixin:
    _max_literal_bytes: int
    _message_literal_guard_active = False
    shutdown: Callable[[], None]

    @contextmanager
    def message_literal_guard(self) -> Iterator[None]:
        previous = self._message_literal_guard_active
        self._message_literal_guard_active = True
        try:
            yield
        finally:
            self._message_literal_guard_active = previous

    def read(self, size: int) -> bytes:
        if self._message_literal_guard_active and size > self._max_literal_bytes:
            # A rejected literal remains unread. Close rather than attempting
            # to resynchronize the session by sending another IMAP command.
            try:
                self.shutdown()
            finally:
                check_message_size(size, self._max_literal_bytes, replay="unknown")
        return super().read(size)  # type: ignore[misc]


@contextmanager
def message_literal_guard(server: object) -> Iterator[None]:
    if isinstance(server, _BoundedLiteralMixin):
        with server.message_literal_guard():
            yield
        return
    # Unit-level fakes do not need transport-level allocation protection.
    yield


class BoundedIMAP4(_BoundedLiteralMixin, imaplib.IMAP4):
    def __init__(self, host: str, port: int, *, timeout: float, max_literal_bytes: int):
        self._max_literal_bytes = max_literal_bytes
        self._message_literal_guard_active = False
        super().__init__(host, port, timeout=timeout)


class BoundedIMAP4SSL(_BoundedLiteralMixin, imaplib.IMAP4_SSL):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
        max_literal_bytes: int,
    ):
        self._max_literal_bytes = max_literal_bytes
        self._message_literal_guard_active = False
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)
