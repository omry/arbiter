"""Exercise real imaplib parsing with bounded, recorded protocol transcripts."""

from __future__ import annotations

import imaplib
import io
import re

import pytest
from omegaconf import OmegaConf

from arbiter_imap.client import IMAPClient, IMAPOperationError
from arbiter_imap.config import IMAPConfig
from arbiter_imap.transport import BoundedIMAP4, BoundedIMAP4SSL
from arbiter_server.errors import OperationError


class RecordedReads(io.BytesIO):
    def __init__(self, transcript):
        super().__init__(transcript)
        self.literal_reads = []
        self.line_limits = []

    def read(self, size=-1):
        self.literal_reads.append(size)
        return super().read(size)

    def readline(self, size=-1):
        self.line_limits.append(size)
        return super().readline(size)


@pytest.fixture(params=[BoundedIMAP4, BoundedIMAP4SSL])
def connection(request):
    def build(transcript=b"", *, response=256, message=16):
        server = request.param.__new__(request.param)
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
        server._max_literal_bytes = message
        server._max_response_bytes = response
        server._file = RecordedReads(transcript)
        if not isinstance(getattr(imaplib.IMAP4, "file", None), property):
            # Keep the fixture usable with the original pre-3.14 reader too,
            # so these regressions can be proved against the old source.
            server.file = server._file
        server.wire = []
        server.closed = []
        server.send = lambda data: server.wire.append(data)
        server.shutdown = lambda: server.closed.append(True)
        return server

    return build


def test_nested_limit_is_independent_and_composes():
    config = OmegaConf.structured(IMAPConfig())
    assert config.limits.upstream_server.max_response_bytes == 64 * 1024 * 1024
    overridden = OmegaConf.merge(
        config, {"limits": {"upstream_server": {"max_response_bytes": 1024}}}
    )
    assert overridden.limits.upstream_server.max_response_bytes == 1024
    assert overridden.limits.max_message_bytes == 50 * 1024 * 1024
    assert overridden.limits.max_preview_bytes == 16 * 1024


@pytest.mark.parametrize("maximum", [0, -1, True, None, 1.5])
def test_invalid_upstream_limit_fails_before_connect(maximum):
    from arbiter_imap import IMAPRuntime, IMAPServicePlugin
    from arbiter_server.services import ConfigCheckError

    config = IMAPConfig()
    config.limits.upstream_server.max_response_bytes = maximum
    with pytest.raises(ValueError, match="limits.upstream_server.max_response_bytes"):
        IMAPClient(config)
    with pytest.raises(ValueError, match="limits.upstream_server.max_response_bytes"):
        IMAPRuntime(accounts={"bot": config}, policies={})
    with pytest.raises(
        ConfigCheckError, match="limits.upstream_server.max_response_bytes"
    ):
        IMAPServicePlugin().check_config(accounts={"bot": config}, policies={})


@pytest.mark.parametrize(
    "command,args",
    [
        ("NOOP", ()),
        ("UID", ("SEARCH", None, "ALL")),
        ("UID", ("FETCH", "42", "(FLAGS)")),
        ("UID", ("FETCH", "42", "(RFC822.SIZE)")),
        ("EXAMINE", ('"INBOX"',)),
        ("LOGIN", ('"user"', '"secret"')),
        ("STARTTLS", ()),
        ("CAPABILITY", ()),
        ("LOGOUT", ()),
    ],
)
def test_unguarded_command_rejects_unsolicited_literal_before_payload(
    connection, command, args
):
    server = connection(b"* 9 FETCH (UID 99 BODY[] {512}\r\n" + b"x" * 512)
    if command in {"LOGIN", "STARTTLS"}:
        server.state = "NONAUTH"
    with pytest.raises(OperationError) as caught:
        server._simple_command(command, *args)
    assert caught.value.domain_code == "response_too_large"
    assert caught.value.code == "upstream_protocol_error"
    assert caught.value.details == {"max_response_bytes": 256}
    assert server._file.literal_reads == []
    assert server.closed
    before = list(server.wire)
    with pytest.raises(OperationError):
        server.noop()
    assert server.wire == before


def test_repeated_literals_share_the_complete_response_budget(connection):
    chunk = b"* 9 FETCH (UID 99 BODY[] {8}\r\n" + b"x" * 8 + b")\r\n"
    server = connection(chunk * 10, response=len(chunk) * 2)
    with pytest.raises(OperationError, match="upstream limit"):
        server.noop()
    assert server._file.literal_reads == [8, 8]
    assert server.closed


def test_full_fetch_has_an_aggregate_message_literal_budget(connection):
    transcript = b"* 1 FETCH (UID 42 RFC822.SIZE 16)\r\nA0 OK size\r\n"
    body = b"* 1 FETCH (UID 42 BODY[] {16}\r\n" + b"x" * 16 + b")\r\n"
    server = connection(transcript + body * 8 + b"A1 OK body\r\n", response=1024)
    config = IMAPConfig()
    config.limits.max_message_bytes = 16
    with pytest.raises(OperationError) as caught:
        IMAPClient(config)._fetch_message_bytes(server, "42")
    assert caught.value.domain_code == "message_too_large"
    assert caught.value.details == {"max_message_bytes": 16}
    assert server._file.literal_reads == [16]
    assert server.closed


def test_size_query_rejects_oversized_unsolicited_response(connection):
    server = connection(
        b"* 9 FETCH (UID 99 BODY[] {64}\r\n" + b"z" * 64,
        response=64,
    )
    with pytest.raises(OperationError) as caught:
        IMAPClient(IMAPConfig())._fetch_message_bytes(server, "42")
    assert caught.value.domain_code == "response_too_large"
    assert server._file.literal_reads == []
    assert len(server.wire) == 1
    assert b"RFC822.SIZE" in server.wire[0]


def test_ambiguous_small_full_bodies_are_rejected(connection):
    transcript = b"* 1 FETCH (UID 42 RFC822.SIZE 2)\r\nA0 OK size\r\n"
    body = b"* 1 FETCH (UID 42 BODY[] {2}\r\nhi)\r\n"
    server = connection(transcript + body * 2 + b"A1 OK body\r\n")
    with pytest.raises(IMAPOperationError, match="unambiguously"):
        IMAPClient(IMAPConfig())._fetch_message_bytes(server, "42")
    assert server.closed


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_complete_response_boundary_includes_framing(connection, delta):
    transcript = b"* 3 EXISTS\r\nA0 OK done\r\n"
    server = connection(transcript, response=len(transcript) + delta)
    if delta < 0:
        with pytest.raises(OperationError):
            server.noop()
        assert server.closed
    else:
        assert server.noop()[0] == "OK"
        assert server.closed == []


def test_framing_read_is_bounded_before_a_large_line_is_allocated(connection):
    server = connection(b"* OK " + b"x" * 10000 + b"\r\n", response=32)
    with pytest.raises(OperationError):
        server.noop()
    assert server._file.tell() == 33
    assert server._file.line_limits == [33]


def test_greeting_is_bounded_before_the_first_command(connection):
    server = connection(b"* OK " + b"x" * 10000 + b"\r\n", response=32)
    with pytest.raises(OperationError):
        server._get_response()
    assert server.wire == []
    assert server._file.tell() == 33
    assert server.closed


@pytest.mark.parametrize("response,literal_count", [(80, 1), (112, 2)])
def test_cached_unsolicited_responses_reduce_later_command_budgets(
    connection, response, literal_count
):
    first = b"* 9 FETCH (UID 99 BODY[] {16}\r\n" + b"x" * 16 + b")\r\nA0 OK done\r\n"
    second = first.replace(b"A0", b"A1")
    server = connection(first + second, response=response)
    assert server.noop()[0] == "OK"
    with pytest.raises(OperationError):
        server.noop()
    assert server._file.literal_reads == [16] * literal_count
    assert server.closed


def test_empty_cached_replies_cannot_escape_accounting(connection):
    first = b"* X\r\n" * 5 + b"A0 OK done\r\n"
    server = connection(first + first.replace(b"A0", b"A1"), response=64)
    assert server.noop()[0] == "OK"
    assert server.untagged_responses["X"] == [b""] * 5
    with pytest.raises(OperationError):
        server.noop()
    assert server.closed


@pytest.mark.parametrize("second_attribute", [b"BODY[]", b"BODY[]<0>"])
def test_multiple_body_attributes_in_one_fetch_are_ambiguous(
    connection, second_attribute
):
    transcript = b"* 1 FETCH (UID 42 RFC822.SIZE 2)\r\nA0 OK size\r\n"
    transcript += (
        b"* 1 FETCH (UID 42 BODY[] {2}\r\nhi "
        + second_attribute
        + b" {2}\r\nhi)\r\nA1 OK body\r\n"
    )
    server = connection(transcript)
    with pytest.raises(IMAPOperationError, match="unambiguously"):
        IMAPClient(IMAPConfig())._fetch_message_bytes(server, "42")
    assert server.closed


def test_full_fetch_selects_body_not_unrequested_metadata_literal(connection):
    transcript = b"* 1 FETCH (UID 42 RFC822.SIZE 2)\r\nA0 OK size\r\n"
    transcript += b"* 1 FETCH (UID 42 FLAGS (\\Seen))\r\n"
    transcript += (
        b"* 1 FETCH (UID 42 ENVELOPE {4}\r\nspam BODY[]<0> {2}\r\nhi)\r\n"
        b"A1 OK body\r\n"
    )
    server = connection(transcript)
    assert IMAPClient(IMAPConfig())._fetch_message_bytes(server, "42") == b"hi"
    assert server.closed == []


def test_literal_reads_do_not_preallocate_the_complete_claimed_size(connection):
    payload = b"x" * (128 * 1024 + 7)
    server = connection(payload, response=len(payload))
    assert server.read(len(payload)) == payload
    assert max(server._file.literal_reads) <= 64 * 1024
    assert server._response_bytes_remaining == 0


def test_consumed_responses_allow_a_fresh_command_budget(connection):
    first = b"* SEARCH 42\r\nA0 OK done\r\n"
    server = connection(first + first.replace(b"A0", b"A1"), response=len(first))
    assert server.uid("SEARCH", None, "ALL") == ("OK", [b"42"])
    assert server.uid("SEARCH", None, "ALL") == ("OK", [b"42"])
    assert server.closed == []


def test_shutdown_error_does_not_mask_response_limit(connection):
    server = connection(b"* OK " + b"x" * 100, response=16)

    def fail_shutdown():
        raise OSError("synthetic shutdown failure")

    server.shutdown = fail_shutdown
    with pytest.raises(OperationError) as caught:
        server.noop()
    assert caught.value.domain_code == "response_too_large"
