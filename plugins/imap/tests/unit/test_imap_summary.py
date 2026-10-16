from __future__ import annotations

import imaplib
import io
import pytest

from arbiter_imap.client import IMAPClient, IMAPOperationError
from arbiter_imap.config import IMAPConfig, IMAPLimitsConfig
from arbiter_imap.fetch import body_parts, fetch_attributes
import arbiter_imap.transport as transport
from arbiter_imap.transport import (
    BoundedIMAP4,
    BoundedIMAP4SSL,
    metadata_literal_guard,
)
from arbiter_server.errors import OperationError


ENVELOPE = b'(NIL "=?utf-8?b?Y2Fmw6k=?=" (("Sender" NIL "sender" "example.com")) NIL NIL NIL NIL NIL NIL "<42@example.com>")'
PLAIN = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 999999999 1 NIL NIL)'
HTML = b'("TEXT" "HTML" NIL NIL NIL "7BIT" 999999999 1 NIL NIL)'
ATTACHMENT = b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 999999999 1 NIL ("ATTACHMENT" ("FILENAME" "secret.txt")))'


class SummaryServer:
    def __init__(self, structure=PLAIN, bodies=None):
        self.structure = structure
        self.bodies = bodies or {"1": b"message preview"}
        self.calls = []
        self.selected = []

    def select(self, mailbox, readonly=False):
        self.selected.append(readonly)
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b"42"]
        if args[1] == "(ENVELOPE FLAGS BODYSTRUCTURE)":
            return "OK", [
                b"1 (UID 42 ENVELOPE "
                + ENVELOPE
                + b" FLAGS (\\Seen) BODYSTRUCTURE "
                + self.structure
                + b")"
            ]
        request = args[1]
        assert request.startswith("(BODY.PEEK[") and not request.startswith(
            "(BODY.PEEK[]"
        )
        section = request.split("[", 1)[1].split("]", 1)[0]
        maximum = int(request.split("<0.", 1)[1].split(">", 1)[0])
        content = self.bodies[section][:maximum]
        return "OK", [
            (f"1 (BODY[{section}]<0> {{{len(content)}}}".encode(), content),
            b" UID 42)",
        ]

    def logout(self):
        return "BYE", [b"done"]


def make_client(monkeypatch, server, *, preview=16384):
    config = IMAPConfig(
        limits=IMAPLimitsConfig(max_message_bytes=128, max_preview_bytes=preview)
    )
    client = IMAPClient(config)
    monkeypatch.setattr(client, "_connect", lambda: server)
    return client


@pytest.mark.parametrize("operation", ["list_messages", "search_messages"])
def test_summaries_keep_oversized_messages_visible_and_do_not_fetch_attachments(
    monkeypatch, operation
):
    server = SummaryServer(b"(" + PLAIN + b" " + ATTACHMENT + b' "MIXED")')
    client = make_client(monkeypatch, server)
    kwargs = {"folder": "INBOX", "limit": 1}
    if operation == "search_messages":
        kwargs["query"] = "invoice"
    messages = getattr(client, operation)(**kwargs)
    assert len(messages) == 1
    assert messages[0].subject == "café"
    assert messages[0].from_addr == "Sender <sender@example.com>"
    assert messages[0].snippet == "message preview"
    assert messages[0].text_body is None and messages[0].html_body is None
    assert messages[0].attachments[0].id == "part-2"
    assert messages[0].attachments[0].filename == "secret.txt"
    assert messages[0].attachments[0].size == 999999999
    assert server.selected == [True]
    assert [args[1] for command, args in server.calls if command == "FETCH"] == [
        "(ENVELOPE FLAGS BODYSTRUCTURE)",
        "(BODY.PEEK[1]<0.16384>)",
    ]


def test_preview_budget_is_shared_across_plain_and_html_fallback(monkeypatch):
    server = SummaryServer(
        b"(" + PLAIN + b" " + HTML + b' "ALTERNATIVE")',
        {"1": b"  ", "2": b"html text with a long tail"},
    )
    result = make_client(monkeypatch, server, preview=8).list_messages(
        folder="INBOX", limit=1
    )
    assert result[0].snippet == "html t"
    assert [args[1] for command, args in server.calls if command == "FETCH"][1:] == [
        "(BODY.PEEK[1]<0.8>)",
        "(BODY.PEEK[2]<0.6>)",
    ]


def test_attachment_only_message_never_fetches_a_preview(monkeypatch):
    server = SummaryServer(ATTACHMENT)
    result = make_client(monkeypatch, server).list_messages(folder="INBOX", limit=1)
    assert result[0].snippet == ""
    assert len(result[0].attachments) == 1
    assert len([call for call in server.calls if call[0] == "FETCH"]) == 1


@pytest.mark.parametrize("operation", ["list_messages", "search_messages"])
@pytest.mark.parametrize(
    "structure",
    [
        PLAIN[:-4] + b'("INLINE" NIL))',
        HTML[:-4] + b'("INLINE" NIL))',
        b"(" + PLAIN + b" " + HTML + b' "ALTERNATIVE" NIL ("INLINE" NIL))',
    ],
)
def test_normal_inline_text_bodies_keep_bounded_previews(
    monkeypatch, operation, structure
):
    server = SummaryServer(structure)
    client = make_client(monkeypatch, server, preview=8)
    arguments = {"folder": "INBOX", "limit": 1}
    if operation == "search_messages":
        arguments["query"] = "preview"

    result = getattr(client, operation)(**arguments)

    assert result[0].snippet == "message"
    assert server.calls[-1][1][1] == "(BODY.PEEK[1]<0.8>)"


@pytest.mark.parametrize(
    "structure",
    [
        PLAIN[:-4] + b'("INLINE" ("FILENAME" "private.txt")))',
        PLAIN.replace(b'NIL NIL "7BIT"', b'"private@example.com" NIL "7BIT"'),
    ],
)
def test_inline_text_attachments_still_do_not_fetch_previews(monkeypatch, structure):
    server = SummaryServer(structure)

    result = make_client(monkeypatch, server).list_messages(folder="INBOX", limit=1)

    assert result[0].snippet == ""
    assert len(result[0].attachments) == 1
    assert len([call for call in server.calls if call[0] == "FETCH"]) == 1


def test_summary_ignores_independent_same_uid_flags_in_both_fetch_phases(monkeypatch):
    server = SummaryServer()
    original = server.uid

    def response(command, *args):
        status, data = original(command, *args)
        if command == "FETCH":
            data = [b"1 (UID 42 FLAGS (\\Seen))", *data]
        return status, data

    monkeypatch.setattr(server, "uid", response)
    result = make_client(monkeypatch, server).list_messages(folder="INBOX", limit=1)
    assert result[0].snippet == "message preview"
    assert result[0].flags == ["\\Seen"]


def test_preview_still_rejects_two_requested_body_responses(monkeypatch):
    server = SummaryServer()
    original = server.uid

    def response(command, *args):
        status, data = original(command, *args)
        if command == "FETCH" and args[1].startswith("(BODY.PEEK["):
            data = [*data, *data]
        return status, data

    monkeypatch.setattr(server, "uid", response)
    with pytest.raises(IMAPOperationError):
        make_client(monkeypatch, server).list_messages(folder="INBOX", limit=1)


@pytest.mark.parametrize(
    "encoding,payload,expected",
    [
        (b"BASE64", b"cHJldmlldyBsb25nZXI=", "preview l"),
        (b"QUOTED-PRINTABLE", b"caf=C3=A9 and more", "café an"),
    ],
)
def test_previews_decode_only_a_bounded_transfer_encoded_prefix(
    monkeypatch, encoding, payload, expected
):
    server = SummaryServer(
        PLAIN.replace(b'"7BIT"', b'"' + encoding + b'"'), {"1": payload}
    )
    result = make_client(monkeypatch, server, preview=12).list_messages(
        folder="INBOX", limit=1
    )
    assert result[0].snippet == expected


def test_fetch_literals_and_uid_trailers_do_not_parse_message_content_as_metadata():
    subject = b"UID 999 BODYSTRUCTURE ()"
    data = [
        (b"1 (ENVELOPE (NIL {" + str(len(subject)).encode() + b"}", subject),
        b" NIL NIL NIL NIL NIL NIL NIL NIL) UID 42 FLAGS ())",
    ]
    attrs = fetch_attributes(data, "42")
    assert attrs[b"UID"] == b"42"
    envelope = attrs[b"ENVELOPE"]
    assert isinstance(envelope, list) and envelope[1] == subject


@pytest.mark.parametrize(
    "response",
    [
        b"1 (UID 42 UID 42)",
        b"1 (UID 42",
        b"1 (UID 41 FLAGS ())",
        b'1 (UID 42 FLAGS ("unterminated))',
        b"1 (UID 42) 2 (UID 42)",
    ],
)
def test_malformed_or_ambiguous_metadata_is_rejected(response):
    with pytest.raises(ValueError):
        fetch_attributes([response], "42")


def test_nested_attachment_text_is_excluded_and_part_ids_keep_leaf_order(monkeypatch):
    nested = b"(" + PLAIN + b' "MIXED" NIL ("ATTACHMENT" ("FILENAME" "nested.mime")))'
    server = SummaryServer(
        b"(" + nested + b" " + PLAIN + b' "MIXED")', {"2": b"visible body"}
    )
    result = make_client(monkeypatch, server).list_messages(folder="INBOX", limit=1)
    assert result[0].snippet == "visible body"
    assert server.calls[-1][1][1] == "(BODY.PEEK[2]<0.16384>)"


def test_body_structure_accepts_literals_parameters_and_rfc2231_filenames():
    structure = [
        b"APPLICATION",
        b"PDF",
        None,
        None,
        None,
        b"BASE64",
        b"120",
        None,
        [b"ATTACHMENT", [b"FILENAME*", b"utf-8''caf%C3%A9.pdf"]],
    ]
    part = list(body_parts(structure))[0]
    assert part.headers.get_filename() == "café.pdf"
    assert part.encoded_size == 120 and part.within_attachment


def test_body_structure_scales_with_ignored_parameters_and_decodes_continuations():
    parameters = []
    for index in range(2_000):
        parameters.extend([f"ignored-{index}".encode(), b"discarded"])
    parameters.extend(
        [
            b"FILENAME*0*",
            b"utf-8''caf%C3",
            b"FILENAME*1*",
            b"%A9.pdf",
        ]
    )
    structure = [
        b"APPLICATION",
        b"PDF",
        None,
        None,
        None,
        b"BASE64",
        b"120",
        None,
        [b"ATTACHMENT", parameters],
    ]

    part = list(body_parts(structure))[0]

    assert part.headers.get_filename() == "café.pdf"
    params = part.headers.get_params(header="Content-Disposition", failobj=[])
    assert params[1:] == [("filename", "café.pdf")]


@pytest.mark.parametrize("cls", [BoundedIMAP4, BoundedIMAP4SSL])
def test_preview_guard_closes_before_a_literal_exceeds_the_remaining_budget(
    monkeypatch, cls
):
    server = cls.__new__(cls)
    server._max_literal_bytes = 128
    calls = []
    closed = []
    monkeypatch.setattr(server, "shutdown", lambda: closed.append(True))
    server._file = io.BytesIO(b" " * 128)
    with server.message_literal_guard(8):
        assert server.read(3) == b" " * 3
        with pytest.raises(OperationError):
            server.read(6)
    assert server._file.tell() == 3 and closed == [True]
    assert server._max_literal_bytes == 128
    assert server._literal_bytes_remaining is None


@pytest.mark.parametrize("cls", [BoundedIMAP4, BoundedIMAP4SSL])
def test_metadata_guard_reports_upstream_error_and_restores_state(monkeypatch, cls):
    server = cls.__new__(cls)
    server._max_literal_bytes = 128
    calls = []
    closed = []
    monkeypatch.setattr(transport, "METADATA_TRANSPORT_BYTES", 8)
    monkeypatch.setattr(server, "shutdown", lambda: closed.append(True))
    monkeypatch.setattr(
        imaplib.IMAP4, "read", lambda self, size: calls.append(size) or b" " * size
    )
    with metadata_literal_guard(server):
        with pytest.raises(OperationError) as caught:
            server.read(9)
    assert caught.value.code == "upstream_protocol_error"
    assert caught.value.domain_code == "metadata_too_large"
    assert calls == [] and closed == [True]
    assert server._max_literal_bytes == 128
    assert server._literal_bytes_remaining is None
    assert server._metadata_bytes_limit is None


@pytest.mark.parametrize("cls", [BoundedIMAP4, BoundedIMAP4SSL])
def test_metadata_guard_counts_response_framing_and_literals(monkeypatch, cls):
    server = cls.__new__(cls)
    server._max_literal_bytes = 128
    closed = []
    monkeypatch.setattr(transport, "METADATA_TRANSPORT_BYTES", 8)
    monkeypatch.setattr(server, "shutdown", lambda: closed.append(True))
    server._file = io.BytesIO(b" " * 6)
    monkeypatch.setattr(imaplib.IMAP4, "read", lambda self, size: b" " * size)
    with metadata_literal_guard(server):
        assert server.readline() == b" " * 6
        with pytest.raises(OperationError) as caught:
            server.read(3)
    assert caught.value.code == "upstream_protocol_error"
    assert caught.value.domain_code == "metadata_too_large"
    assert closed == [True]


def test_oversized_fake_preview_is_a_protocol_error_not_silently_truncated(monkeypatch):
    server = SummaryServer()
    original = server.uid

    def response(command, *args):
        if command == "FETCH" and args[1].startswith("(BODY.PEEK["):
            return "OK", [(b"1 (UID 42 BODY[1]<0> {9}", b"oversized"), b")"]
        return original(command, *args)

    monkeypatch.setattr(server, "uid", response)
    with pytest.raises(IMAPOperationError) as caught:
        make_client(monkeypatch, server, preview=8).list_messages(
            folder="INBOX", limit=1
        )
    assert caught.value.code == "upstream_protocol_error"
