"""Bound IMAP responses before the standard library retains their contents."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import imaplib
import ssl
from typing import Any, BinaryIO, NoReturn

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
    _max_response_bytes = 64 * 1024 * 1024
    _response_bytes_remaining: int | None = None
    _response_limit_exceeded = False
    _message_literal_guard_active = False
    _literal_bytes_remaining: int | None = None
    _metadata_guard_active = False
    _metadata_bytes_limit: int | None = None
    shutdown: Callable[[], None]
    untagged_responses: dict[str, list[Any]]

    def _response_stream(self) -> BinaryIO:
        # Python 3.14 moved to _file and a separate socket read buffer. Use
        # one stream for both framing and literal reads on every supported
        # version; mixing those two buffers loses prefetched literal bytes.
        stream = getattr(self, "_file", None)
        return stream if stream is not None else getattr(self, "file")

    def _close_for_limit(self, error: Exception) -> NoReturn:
        self._response_limit_exceeded = True
        try:
            self.shutdown()
        except Exception:
            # Preserve the allocation-limit error even if shutdown also fails.
            pass
        raise error

    def _response_overflow(self) -> NoReturn:
        self._close_for_limit(
            OperationError(
                "upstream_protocol_error",
                "IMAP server response exceeds the configured upstream limit.",
                plugin="imap",
                domain_code="response_too_large",
                details={"max_response_bytes": self._max_response_bytes},
                transient="no",
                replay="unknown",
            )
        )

    def _retained_response_bytes(self) -> int:
        # imaplib retains unsolicited responses between commands. A fresh
        # command must not replenish the space occupied by those responses.
        retained = 0
        for name, values in self.untagged_responses.items():
            retained += len(name)
            for value in values:
                # Charge framing per retained entry, including empty replies.
                # Otherwise arbitrary empty replies occupy list slots while
                # appearing to consume no budget across successive commands.
                retained += len(name) + 5
                if isinstance(value, tuple):
                    retained += sum(len(part) for part in value)
                else:
                    retained += len(value)
        return retained

    def _command(self, name: str, *args: Any) -> bytes:
        if self._response_limit_exceeded:
            self._response_overflow()
        self._response_bytes_remaining = (
            self._max_response_bytes - self._retained_response_bytes()
        )
        if self._response_bytes_remaining <= 0:
            self._response_overflow()
        return super()._command(name, *args)  # type: ignore[misc]

    def _remaining_response_bytes(self) -> int:
        if self._response_limit_exceeded:
            self._response_overflow()
        if self._response_bytes_remaining is None:
            # The greeting precedes the first command, including CAPABILITY
            # inside imaplib's constructor. It needs the same protection.
            self._response_bytes_remaining = self._max_response_bytes
        return self._response_bytes_remaining

    def _consume_response_bytes(self, size: int) -> None:
        remaining = self._remaining_response_bytes()
        if size > remaining:
            self._response_overflow()
        self._response_bytes_remaining = remaining - size

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
        self._max_literal_bytes = (
            self._max_literal_bytes if maximum is None else maximum
        )
        self._literal_bytes_remaining = self._max_literal_bytes
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
                self._check_metadata_overflow(size)
            except OperationError as error:
                self._close_for_limit(error)
        self._literal_bytes_remaining -= size

    def _check_metadata_overflow(self, size: int) -> None:
        limit = self._metadata_bytes_limit or 0
        remaining = self._literal_bytes_remaining or 0
        check_metadata_size(limit - remaining + size, limit)

    def readline(self) -> bytes:
        remaining = self._remaining_response_bytes()
        if remaining == 0:
            self._response_overflow()
        if self._metadata_guard_active and self._literal_bytes_remaining is not None:
            if self._literal_bytes_remaining == 0:
                self._consume_metadata_bytes(1)
            remaining = min(remaining, self._literal_bytes_remaining)
        # Read at most one byte beyond a budget to detect overflow. Retain
        # imaplib's independent per-line ceiling as well.
        # imaplib's private limit is absent from its typing stubs.
        line_maximum: int = getattr(imaplib, "_MAXLINE")
        line = self._response_stream().readline(min(line_maximum + 1, remaining + 1))
        self._consume_response_bytes(len(line))
        self._consume_metadata_bytes(len(line))
        if len(line) > line_maximum:
            self._close_for_limit(imaplib.IMAP4.error("IMAP response line is too long"))
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
                if self._metadata_guard_active:
                    self._check_metadata_overflow(size)
                check_message_size(
                    self._max_literal_bytes - maximum + size,
                    self._max_literal_bytes,
                    replay="unknown",
                )
            except OperationError as error:
                self._close_for_limit(error)
        if (
            self._message_literal_guard_active
            and self._literal_bytes_remaining is not None
        ):
            self._literal_bytes_remaining -= size
        self._consume_response_bytes(size)
        # Avoid allocating a claimed literal's full size before it arrives.
        # SocketIO timeouts terminate our session; this client does not use
        # imaplib's resumable IDLE reader.
        parts = []
        stream = self._response_stream()
        while size:
            part = stream.read(min(size, 64 * 1024))
            if not part:
                break
            parts.append(part)
            size -= len(part)
        return b"".join(parts)


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
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float,
        max_literal_bytes: int,
        max_response_bytes: int,
    ):
        self._max_literal_bytes = max_literal_bytes
        self._max_response_bytes = max_response_bytes
        self._response_bytes_remaining = None
        self._response_limit_exceeded = False
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
        max_response_bytes: int,
    ):
        self._max_literal_bytes = max_literal_bytes
        self._max_response_bytes = max_response_bytes
        self._response_bytes_remaining = None
        self._response_limit_exceeded = False
        self._message_literal_guard_active = False
        self._metadata_guard_active = False
        self._metadata_bytes_limit = None
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)
