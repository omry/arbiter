"""Bound IMAP literals before the standard library allocates their contents."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import imaplib
import ssl

from arbiter_server.errors import OperationError, Replay


METADATA_TRANSPORT_BYTES = 50 * 1024 * 1024


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


def check_metadata_size(size: int, maximum: int) -> None:
    if size > maximum:
        raise OperationError(
            "upstream_protocol_error",
            "IMAP metadata response exceeds the internal safety budget.",
            plugin="imap",
            domain_code="metadata_too_large",
            details={"max_metadata_bytes": maximum},
            transient="no",
            replay="unknown",
        )


class _BoundedLiteralMixin:
    _max_literal_bytes: int
    _message_literal_guard_active = False
    _literal_bytes_remaining: int | None = None
    _metadata_guard_active = False
    _metadata_bytes_limit: int | None = None
    shutdown: Callable[[], None]

    @contextmanager
    def message_literal_guard(
        self, maximum: int | None = None, *, metadata: bool = False
    ) -> Iterator[None]:
        previous = self._message_literal_guard_active
        previous_maximum = self._max_literal_bytes
        previous_remaining = self._literal_bytes_remaining
        previous_metadata = self._metadata_guard_active
        previous_metadata_limit = self._metadata_bytes_limit
        self._message_literal_guard_active = True
        self._metadata_guard_active = metadata
        self._metadata_bytes_limit = METADATA_TRANSPORT_BYTES if metadata else None
        if maximum is not None:
            self._max_literal_bytes = maximum
            self._literal_bytes_remaining = maximum
        try:
            yield
        finally:
            self._message_literal_guard_active = previous
            self._metadata_guard_active = previous_metadata
            self._metadata_bytes_limit = previous_metadata_limit
            self._max_literal_bytes = previous_maximum
            self._literal_bytes_remaining = previous_remaining

    @contextmanager
    def metadata_literal_guard(self) -> Iterator[None]:
        with self.message_literal_guard(METADATA_TRANSPORT_BYTES, metadata=True):
            yield

    def _consume_metadata_bytes(self, size: int) -> None:
        if not self._metadata_guard_active or self._literal_bytes_remaining is None:
            return
        if size > self._literal_bytes_remaining:
            try:
                self.shutdown()
            finally:
                self._check_metadata_overflow(size)
        self._literal_bytes_remaining -= size

    def _check_metadata_overflow(self, size: int) -> None:
        limit = self._metadata_bytes_limit or 0
        remaining = self._literal_bytes_remaining or 0
        check_metadata_size(limit - remaining + size, limit)

    def readline(self) -> bytes:
        line = super().readline()  # type: ignore[misc]
        self._consume_metadata_bytes(len(line))
        return line

    def read(self, size: int) -> bytes:
        maximum = (
            self._max_literal_bytes
            if self._literal_bytes_remaining is None
            else self._literal_bytes_remaining
        )
        if self._message_literal_guard_active and size > maximum:
            # A rejected literal remains unread. Close rather than attempting
            # to resynchronize the session by sending another IMAP command.
            try:
                self.shutdown()
            finally:
                if self._metadata_guard_active:
                    self._check_metadata_overflow(size)
                check_message_size(size, maximum, replay="unknown")
        if (
            self._message_literal_guard_active
            and self._literal_bytes_remaining is not None
        ):
            self._literal_bytes_remaining -= size
        return super().read(size)  # type: ignore[misc]


@contextmanager
def message_literal_guard(server: object, maximum: int | None = None) -> Iterator[None]:
    if isinstance(server, _BoundedLiteralMixin):
        with server.message_literal_guard(maximum):
            yield
        return
    # Unit-level fakes do not need transport-level allocation protection.
    yield


@contextmanager
def metadata_literal_guard(server: object) -> Iterator[None]:
    if isinstance(server, _BoundedLiteralMixin):
        with server.metadata_literal_guard():
            yield
        return
    # Unit-level fakes do not need transport-level allocation protection.
    yield


class BoundedIMAP4(_BoundedLiteralMixin, imaplib.IMAP4):
    def __init__(self, host: str, port: int, *, timeout: float, max_literal_bytes: int):
        self._max_literal_bytes = max_literal_bytes
        self._message_literal_guard_active = False
        self._metadata_guard_active = False
        self._metadata_bytes_limit = None
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
        self._metadata_guard_active = False
        self._metadata_bytes_limit = None
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)
