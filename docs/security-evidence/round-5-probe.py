"""Independent bounded IMAP transcript probes for merged PR 32.

These use the real stdlib command/response parser and Arbiter mixin.  Socket and
TLS setup are intentionally bypassed; no live account or external service is
used.
"""

from __future__ import annotations

import imaplib
import io
import re
import sys
import time

import arbiter_imap.client as client_module
from arbiter_imap.client import IMAPClient, IMAPOperationError
from arbiter_imap.config import IMAPConfig
from arbiter_imap.transport import BoundedIMAP4, BoundedIMAP4SSL
from arbiter_server.errors import OperationError


class Trace(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.read_sizes: list[int] = []
        self.readline_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)

    def readline(self, size: int = -1) -> bytes:
        self.readline_sizes.append(size)
        return super().readline(size)


def connection(
    cls: type[BoundedIMAP4] | type[BoundedIMAP4SSL],
    transcript: bytes,
    *,
    response_limit: int = 256,
    message_limit: int = 16,
):
    server = cls.__new__(cls)
    server.state = "SELECTED"
    server.literal = None
    server.tagpre = b"A"
    server.tagnum = 0
    server.tagged_commands = {}
    server.untagged_responses = {}
    server.is_readonly = True
    server._idle_capture = False
    server.debug = 0
    server._cmd_log = {}
    server._cmd_log_idx = 0
    server._cmd_log_len = 10
    server._mode_ascii()
    server.tagre = re.compile(rb"(?P<tag>A\d+) (?P<type>[A-Z]+) (?P<data>.*)")
    server._max_literal_bytes = message_limit
    server._max_response_bytes = response_limit
    server._response_bytes_remaining = None
    server._response_limit_exceeded = False
    server._message_literal_guard_active = False
    server._literal_bytes_remaining = None
    server._metadata_guard_active = False
    server._metadata_bytes_limit = None
    server._file = Trace(transcript)
    if not isinstance(getattr(imaplib.IMAP4, "file", None), property):
        server.file = server._file
    server.sent: list[bytes] = []
    server.closed = 0
    server.send = server.sent.append

    def shutdown() -> None:
        server.closed += 1

    server.shutdown = shutdown
    return server


def check(condition: bool, description: str) -> None:
    if not condition:
        raise AssertionError(description)


def expect_domain(call, domain: str) -> OperationError:
    try:
        call()
    except OperationError as error:
        check(error.domain_code == domain, f"expected {domain}, got {error.domain_code}")
        return error
    raise AssertionError(f"expected OperationError {domain}")


def run_for(cls: type[BoundedIMAP4] | type[BoundedIMAP4SSL]) -> int:
    checks = 0

    # A long greeting is rejected after reading only one byte beyond budget.
    greeting = connection(cls, b"* OK " + b"g" * 200 + b"\r\n", response_limit=23)
    expect_domain(greeting._get_response, "response_too_large")
    check(greeting._file.tell() == 24, "greeting read exceeded bounded lookahead")
    check(not greeting.sent and greeting.closed == 1, "greeting fail-close mismatch")
    checks += 1

    # A command accepts its exact framing boundary and rejects one byte below it.
    reply = b"* 7 EXISTS\r\nA0 OK complete\r\n"
    exact = connection(cls, reply, response_limit=len(reply))
    check(exact.noop()[0] == "OK", "exact response boundary failed")
    short = connection(cls, reply, response_limit=len(reply) - 1)
    expect_domain(short.noop, "response_too_large")
    check(short.closed == 1, "framing overflow did not close")
    checks += 1

    # APPEND continuation and completion share one inbound response budget.
    append_reply = b"+ continue\r\nA0 OK appended\r\n"
    append_ok = connection(cls, append_reply, response_limit=len(append_reply))
    check(append_ok.append("INBOX", None, None, b"abc")[0] == "OK", "APPEND failed")
    append_small = connection(cls, append_reply, response_limit=len(append_reply) - 1)
    expect_domain(
        lambda: append_small.append("INBOX", None, None, b"abc"),
        "response_too_large",
    )
    check(append_small.closed == 1, "APPEND overflow did not close")
    checks += 1

    # Empty unsolicited responses retained by imaplib reduce the next budget.
    first = b"* X\r\n" * 6 + b"A0 OK first\r\n"
    second = b"A1 OK second\r\n"
    cached = connection(cls, first + second, response_limit=len(first))
    check(cached.noop()[0] == "OK", "first cached-response command failed")
    check(cached.untagged_responses["X"] == [b""] * 6, "empty cache shape changed")
    expect_domain(cached.noop, "response_too_large")
    check(cached.closed == 1, "cached-response overflow did not close")
    checks += 1

    # Two bodies may each fit, but together cannot exceed the message budget.
    size = b"* 1 FETCH (UID 42 RFC822.SIZE 6)\r\nA0 OK size\r\n"
    body1 = b"* 1 FETCH (UID 42 BODY[] {6}\r\nabcdef)\r\n"
    body2 = b"* 2 FETCH (UID 42 BODY[] {5}\r\nghijk)\r\n"
    aggregate = connection(
        cls,
        size + body1 + body2 + b"A1 OK body\r\n",
        response_limit=1024,
        message_limit=10,
    )
    config = IMAPConfig()
    config.limits.max_message_bytes = 10
    expect_domain(lambda: IMAPClient(config)._fetch_message_bytes(aggregate, "42"), "message_too_large")
    check(aggregate._file.read_sizes == [6], "second aggregate literal payload was read")
    check(aggregate.closed == 1, "aggregate overflow did not close")
    checks += 1

    # Duplicate full bodies inside the aggregate limit are rejected before MIME parsing.
    flags = b"* 1 FETCH (UID 42 FLAGS ())\r\nA0 OK flags\r\n"
    size = b"* 1 FETCH (UID 42 RFC822.SIZE 2)\r\nA1 OK size\r\n"
    body = b"* 1 FETCH (UID 42 BODY[] {2}\r\nhi)\r\n"
    ambiguous = connection(cls, flags + size + body + body + b"A2 OK body\r\n")
    parsed = 0
    original_parser = client_module.BytesParser

    class ForbiddenParser:
        def __init__(self, **kwargs):
            pass

        def parsebytes(self, data):
            nonlocal parsed
            parsed += 1
            raise AssertionError("ambiguous body reached MIME parser")

    client_module.BytesParser = ForbiddenParser
    try:
        try:
            IMAPClient(IMAPConfig())._fetch_message(ambiguous, "42")
        except IMAPOperationError:
            pass
        else:
            raise AssertionError("ambiguous body was accepted")
    finally:
        client_module.BytesParser = original_parser
    check(parsed == 0 and ambiguous.closed == 1, "ambiguous body was not fail-closed")
    checks += 1

    # Once a response limit is exceeded, another command cannot write to the wire.
    failed = connection(cls, b"A0 OK " + b"z" * 40 + b"\r\n", response_limit=8)
    expect_domain(failed.noop, "response_too_large")
    sent_before = list(failed.sent)
    expect_domain(failed.noop, "response_too_large")
    check(failed.sent == sent_before, "command was written after fail-close")
    checks += 1

    return checks


def main() -> None:
    started = time.perf_counter()
    total = 0
    for transport in (BoundedIMAP4, BoundedIMAP4SSL):
        total += run_for(transport)
    elapsed = time.perf_counter() - started
    print(f"python={sys.version.split()[0]} checks={total} elapsed={elapsed:.6f}s")


if __name__ == "__main__":
    main()
