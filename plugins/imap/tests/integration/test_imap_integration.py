from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from email.message import EmailMessage, MIMEPart
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import imaplib
import re
import socket
import threading

import pytest

from arbiter_imap.client import IMAPClient, IMAPOperationError
from arbiter_imap.config import IMAPConfig, MailTlsMode


def _message_bytes(uid: str) -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "Bot <bot@example.com>"
    message["Cc"] = "Watcher <watcher@example.com>"
    message["Subject"] = f"Status update {uid}"
    message["Date"] = "Tue, 03 Mar 2026 12:00:00 +0000"
    message["Message-ID"] = f"<message-{uid}@example.com>"
    message.set_content(f"Plain text body for {uid}")
    return message.as_bytes()


def _multipart_message_bytes(uid: str) -> bytes:
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "Bot <bot@example.com>"
    message["Subject"] = f"Multipart update {uid}"
    message["Date"] = "Tue, 03 Mar 2026 12:00:00 +0000"
    message["Message-ID"] = f"<multipart-{uid}@example.com>"
    message.set_content(f"Plain multipart body for {uid}")
    message.add_alternative(f"<p>HTML multipart body for {uid}</p>", subtype="html")
    message.add_attachment(
        b"ignored attachment",
        maintype="application",
        subtype="octet-stream",
        filename="ignored.bin",
    )
    return message.as_bytes()


def _imap_value(value: object) -> bytes:
    if value is None:
        return b"NIL"
    if isinstance(value, list):
        return b"(" + b" ".join(_imap_value(item) for item in value) + b")"
    if isinstance(value, int):
        return str(value).encode()
    raw = str(value).encode()
    if b"\r" in raw or b"\n" in raw:
        return b"{" + str(len(raw)).encode() + b"}\r\n" + raw
    return b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'


def _envelope(message: MIMEPart) -> list[object]:
    def addresses(name: str) -> list[object] | None:
        values = []
        for personal, address in getaddresses(message.get_all(name, [])):
            mailbox, _, host = address.partition("@")
            values.append([personal or None, None, mailbox, host])
        return values or None

    return [
        message.get("Date"),
        message.get("Subject"),
        addresses("From"),
        None,
        None,
        addresses("To"),
        addresses("Cc"),
        None,
        None,
        message.get("Message-ID"),
    ]


def _body_structure(message: MIMEPart) -> list[object]:
    def params(header: str) -> list[object] | None:
        values = []
        for name, value in message.get_params(header=header, failobj=[])[1:]:
            values.extend([name.upper(), value])
        return values or None

    disposition = message.get_content_disposition()
    dsp = [disposition.upper(), params("Content-Disposition")] if disposition else None
    if message.is_multipart() and message.get_content_maintype() != "message":
        return [
            *[_body_structure(part) for part in message.iter_parts()],
            message.get_content_subtype().upper(),
            params("Content-Type"),
            dsp,
        ]
    payload = message.get_payload()
    encoded = payload.encode() if isinstance(payload, str) else message.as_bytes()
    result: list[object] = [
        message.get_content_maintype().upper(),
        message.get_content_subtype().upper(),
        params("Content-Type"),
        message.get("Content-ID"),
        None,
        message.get("Content-Transfer-Encoding", "7BIT"),
        len(encoded),
    ]
    if message.get_content_maintype() == "text":
        result.append(encoded.count(b"\n"))
    elif message.get_content_type() == "message/rfc822":
        assert isinstance(payload, list)
        nested = payload[0]
        assert isinstance(nested, MIMEPart)
        result.extend(
            [_envelope(nested), _body_structure(nested), encoded.count(b"\n")]
        )
    result.extend([None, dsp])
    return result


def _section_part(message: MIMEPart, section: str) -> MIMEPart:
    for index in section.split("."):
        if message.is_multipart():
            message = list(message.iter_parts())[int(index) - 1]
        else:
            assert index == "1"
    return message


@dataclass(frozen=True)
class LocalIMAPServer:
    host: str
    port: int
    commands: list[str]
    stop: object


class _LocalIMAPServerRunner:
    def __init__(
        self,
        port: int,
        *,
        host: str = "127.0.0.1",
        close_host: str | None = None,
        login_ok: bool = True,
        search_ok: bool = True,
        support_move: bool = True,
        non_message_literal: bool = False,
        selectable_mailboxes: set[str] | None = None,
        search_uids: tuple[str, ...] = ("40", "41", "42"),
        messages: dict[str, bytes] | None = None,
    ) -> None:
        self.host = host
        self.close_host = close_host or host
        self.port = port
        self.commands: list[str] = []
        self.login_ok = login_ok
        self.search_ok = search_ok
        self.support_move = support_move
        self.non_message_literal = non_message_literal
        self.selectable_mailboxes = selectable_mailboxes or {"INBOX", "Archive"}
        self.search_uids = search_uids
        self.messages = messages or {}
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._socket: socket.socket | None = None

    def start(self) -> LocalIMAPServer:
        self._thread.start()
        assert self._ready.wait(timeout=5)
        return LocalIMAPServer(
            host=self.host,
            port=self.port,
            commands=self.commands,
            stop=self,
        )

    def close(self) -> None:
        self._stop.set()
        try:
            with socket.create_connection((self.close_host, self.port), timeout=1):
                pass
        except OSError:
            pass
        self._thread.join(timeout=5)
        if self._socket is not None:
            self._socket.close()

    def _serve(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            self._socket = listener
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen()
            listener.settimeout(0.2)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    connection, _addr = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with connection:
                    self._handle_connection(connection)

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.sendall(b"* OK Arbiter test IMAP server ready\r\n")
        reader = connection.makefile("rb")
        while not self._stop.is_set():
            raw_line = reader.readline()
            if not raw_line:
                return
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            self.commands.append(line)
            tag, command, rest = self._split_command(line)
            response = self._response(tag, command, rest)
            connection.sendall(response)
            if command == "LOGOUT":
                return

    def _split_command(self, line: str) -> tuple[str, str, str]:
        parts = line.split(" ", 2)
        tag = parts[0]
        command = parts[1].upper() if len(parts) >= 2 else ""
        rest = parts[2] if len(parts) >= 3 else ""
        return tag, command, rest

    def _response(self, tag: str, command: str, rest: str) -> bytes:
        if command == "LOGIN":
            if not self.login_ok:
                return f"{tag} NO LOGIN failed\r\n".encode()
            return f"{tag} OK LOGIN completed\r\n".encode()
        if command == "CAPABILITY":
            return (
                b"* CAPABILITY IMAP4rev1 UIDPLUS MOVE\r\n"
                + f"{tag} OK CAPABILITY completed\r\n".encode()
            )
        if command == "NOOP":
            if self.non_message_literal:
                literal = b"ordinary IMAP response"
                return (
                    b"* OK {"
                    + str(len(literal)).encode()
                    + b"}\r\n"
                    + literal
                    + b"\r\n"
                    + f"{tag} OK NOOP completed\r\n".encode()
                )
            return f"{tag} OK NOOP completed\r\n".encode()
        if command in {"EXAMINE", "SELECT"}:
            mailbox = self._mailbox_name(rest)
            if mailbox not in self.selectable_mailboxes:
                return f"{tag} NO no such mailbox\r\n".encode()
            state = "READ-ONLY" if command == "EXAMINE" else "READ-WRITE"
            return (
                b"* 3 EXISTS\r\n"
                b"* OK [UIDVALIDITY 1] UIDs valid\r\n"
                + f"{tag} OK [{state}] {command} completed\r\n".encode()
            )
        if command == "UID":
            return self._uid_response(tag, rest)
        if command == "EXPUNGE":
            return b"* 42 EXPUNGE\r\n" + f"{tag} OK EXPUNGE completed\r\n".encode()
        if command == "LOGOUT":
            return b"* BYE logging out\r\n" + f"{tag} OK LOGOUT completed\r\n".encode()
        return f"{tag} BAD unsupported command\r\n".encode()

    def _uid_response(self, tag: str, rest: str) -> bytes:
        subcommand, _, args = rest.partition(" ")
        subcommand = subcommand.upper()
        if subcommand == "SEARCH":
            if not self.search_ok:
                return f"{tag} NO SEARCH failed\r\n".encode()
            uid_list = " ".join(self.search_uids)
            return f"* SEARCH {uid_list}\r\n{tag} OK SEARCH completed\r\n".encode()
        if subcommand == "FETCH":
            uid, _, fetch_items = args.partition(" ")
            if fetch_items == "(FLAGS)":
                return (
                    f"* {uid} FETCH (UID {uid} FLAGS (\\Seen bot.followed_up))\r\n"
                    f"{tag} OK FETCH completed\r\n"
                ).encode()
            body = self.messages.get(uid, _message_bytes(uid))
            if fetch_items == "(ENVELOPE FLAGS BODYSTRUCTURE)":
                message = BytesParser(policy=policy.default).parsebytes(body)
                return (
                    f"* {uid} FETCH (UID {uid} FLAGS (\\Seen bot.followed_up) ENVELOPE ".encode()
                    + _imap_value(_envelope(message))
                    + b" BODYSTRUCTURE "
                    + _imap_value(_body_structure(message))
                    + f")\r\n{tag} OK FETCH completed\r\n".encode()
                )
            preview = re.fullmatch(
                r"\(BODY\.PEEK\[([0-9.]+)\]<0\.([0-9]+)>\)", fetch_items
            )
            if preview:
                message = BytesParser(policy=policy.default).parsebytes(body)
                part = _section_part(message, preview[1])
                payload = part.get_payload()
                assert isinstance(payload, str)
                content = payload.encode()[: int(preview[2])]
                return (
                    f"* {uid} FETCH (UID {uid} BODY[{preview[1]}]<0> {{{len(content)}}}\r\n".encode()
                    + content
                    + f")\r\n{tag} OK FETCH completed\r\n".encode()
                )
            if fetch_items == "(RFC822.SIZE)":
                return f"* {uid} FETCH (UID {uid} RFC822.SIZE {len(body)})\r\n{tag} OK FETCH completed\r\n".encode()
            return (
                f"* {uid} FETCH (UID {uid} BODY[]<0> {{{len(body)}}}\r\n".encode()
                + body
                + b")\r\n"
                + f"{tag} OK FETCH completed\r\n".encode()
            )
        if subcommand == "MOVE":
            if not self.support_move:
                return f"{tag} BAD MOVE unsupported\r\n".encode()
            return f"{tag} OK MOVE completed\r\n".encode()
        if subcommand == "STORE":
            uid, _, _flags = args.partition(" ")
            return (
                f"* {uid} FETCH (UID {uid} FLAGS (\\Seen))\r\n"
                f"{tag} OK STORE completed\r\n"
            ).encode()
        if subcommand == "COPY":
            return f"{tag} OK COPY completed\r\n".encode()
        if subcommand == "EXPUNGE":
            uid = args.strip()
            return (
                f"* {uid} EXPUNGE\r\n" f"{tag} OK UID EXPUNGE completed\r\n"
            ).encode()
        return f"{tag} BAD unsupported UID command\r\n".encode()

    def _mailbox_name(self, value: str) -> str:
        return value.strip().strip('"')


@pytest.fixture
def imap_server_factory(
    free_tcp_port_factory: Callable[[], int],
) -> Iterator[Callable[..., LocalIMAPServer]]:
    runners: list[_LocalIMAPServerRunner] = []

    def start_server(
        *,
        login_ok: bool = True,
        search_ok: bool = True,
        support_move: bool = True,
        non_message_literal: bool = False,
        selectable_mailboxes: set[str] | None = None,
        search_uids: tuple[str, ...] = ("40", "41", "42"),
        messages: dict[str, bytes] | None = None,
    ) -> LocalIMAPServer:
        runner = _LocalIMAPServerRunner(
            free_tcp_port_factory(),
            login_ok=login_ok,
            search_ok=search_ok,
            support_move=support_move,
            non_message_literal=non_message_literal,
            selectable_mailboxes=selectable_mailboxes,
            search_uids=search_uids,
            messages=messages,
        )
        runners.append(runner)
        return runner.start()

    try:
        yield start_server
    finally:
        for runner in reversed(runners):
            runner.close()


@pytest.fixture
def imap_server(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> LocalIMAPServer:
    return imap_server_factory()


def _client(
    server: LocalIMAPServer, *, max_message_bytes: int | None = None
) -> IMAPClient:
    config = IMAPConfig(
        host=server.host,
        port=server.port,
        username="user@example.com",
        password="secret",
        tls=MailTlsMode.none,
    )
    if max_message_bytes is not None:
        config.limits.max_message_bytes = max_message_bytes
    return IMAPClient(config)


def test_imap_does_not_limit_non_message_literals(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    server = imap_server_factory(non_message_literal=True)

    _client(server, max_message_bytes=8).test_connection(folders=[])

    assert any(command.endswith("NOOP") for command in server.commands)


@pytest.mark.parametrize("operation", ["get_message", "get_attachment"])
def test_imap_rejects_an_oversized_literal_when_server_lies_about_size(
    imap_server: LocalIMAPServer, monkeypatch, operation
) -> None:
    from arbiter_server.errors import OperationError

    runner = imap_server.stop
    assert isinstance(runner, _LocalIMAPServerRunner)
    original = runner._uid_response

    def response(tag, rest):
        if rest == "FETCH 42 (RFC822.SIZE)":
            return f"* 42 FETCH (UID 42 RFC822.SIZE 8)\r\n{tag} OK FETCH completed\r\n".encode()
        return original(tag, rest)

    monkeypatch.setattr(runner, "_uid_response", response)
    monkeypatch.setattr(
        "arbiter_imap.client.BytesParser",
        lambda **kwargs: pytest.fail(
            "oversized content must not reach the MIME parser"
        ),
    )
    config = IMAPConfig(
        host=imap_server.host,
        port=imap_server.port,
        tls=MailTlsMode.none,
        username="user@example.com",
        password="secret",
    )
    config.limits.max_message_bytes = 8
    client = IMAPClient(config)
    arguments: dict[str, object] = {"folder": "INBOX", "uid": "42"}
    if operation == "get_attachment":
        arguments["attachment_id"] = "part-1"

    with pytest.raises(OperationError) as caught:
        getattr(client, operation)(**arguments)

    assert caught.value.domain_code == "message_too_large"
    assert any(
        command.endswith("UID FETCH 42 (BODY.PEEK[]<0.9>)")
        for command in imap_server.commands
    )
    assert not any(
        " STORE " in command or " APPEND " in command
        for command in imap_server.commands
    )


@pytest.mark.parametrize("operation", ["get_message", "get_attachment"])
@pytest.mark.parametrize("estimate_delta", [-1, 1])
def test_imap_reads_messages_with_estimated_sizes(
    imap_server_factory: Callable[..., LocalIMAPServer],
    monkeypatch,
    operation,
    estimate_delta,
) -> None:
    content = _multipart_message_bytes("42")
    server = imap_server_factory(messages={"42": content})
    runner = server.stop
    assert isinstance(runner, _LocalIMAPServerRunner)
    original = runner._uid_response

    def response(tag, rest):
        if rest == "FETCH 42 (RFC822.SIZE)":
            size = len(content) + estimate_delta
            return f"* 42 FETCH (UID 42 RFC822.SIZE {size})\r\n{tag} OK FETCH completed\r\n".encode()
        return original(tag, rest)

    monkeypatch.setattr(runner, "_uid_response", response)
    client = _client(server, max_message_bytes=len(content))
    if operation == "get_message":
        assert (
            client.get_message(folder="INBOX", uid="42").subject
            == "Multipart update 42"
        )
    else:
        assert (
            client.get_attachment(
                folder="INBOX", uid="42", attachment_id="part-3"
            ).content
            == b"ignored attachment"
        )
    assert not any(" STORE " in command for command in server.commands)


@pytest.mark.parametrize("operation", ["list_messages", "search_messages"])
def test_imap_summary_wire_fetches_skip_attachments_and_keep_large_mail_visible(
    imap_server_factory, operation
) -> None:
    content = _multipart_message_bytes("42")
    server = imap_server_factory(messages={"42": content}, search_uids=("42",))
    config = IMAPConfig(
        host=server.host,
        port=server.port,
        tls=MailTlsMode.none,
        username="user",
        password="secret",
    )
    config.limits.max_message_bytes = 16
    config.limits.max_preview_bytes = 12
    client = IMAPClient(config)
    arguments = {"folder": "INBOX", "limit": 1}
    if operation == "search_messages":
        arguments["query"] = "multipart"
    messages = getattr(client, operation)(**arguments)
    assert messages[0].subject == "Multipart update 42"
    assert messages[0].snippet == "Plain multip"
    assert messages[0].text_body is None and messages[0].html_body is None
    attachment = messages[0].attachments[0]
    assert attachment.id == "part-3" and attachment.filename == "ignored.bin"
    assert any(
        command.endswith("UID FETCH 42 (BODY.PEEK[1.1]<0.12>)")
        for command in server.commands
    )
    assert not any(
        "BODY.PEEK[]" in command or "BODY.PEEK[2]" in command or " STORE " in command
        for command in server.commands
    )
    # The metadata id continues to identify the same explicit retrieval.
    config.limits.max_message_bytes = len(content)
    assert (
        IMAPClient(config)
        .get_attachment(folder="INBOX", uid="42", attachment_id=attachment.id)
        .content
        == b"ignored attachment"
    )


def test_imap_preview_literal_larger_than_budget_is_rejected_before_read(
    imap_server_factory, monkeypatch
) -> None:
    server = imap_server_factory(search_uids=("42",))
    runner = server.stop
    original = runner._uid_response

    def response(tag, rest):
        if "BODY.PEEK[1]" in rest:
            # No payload is sent. The operation must close immediately rather
            # than waiting for bytes or accepting the dishonest literal size.
            return b"* 42 FETCH (UID 42 BODY[1]<0> {999999999}\r\n"
        return original(tag, rest)

    monkeypatch.setattr(runner, "_uid_response", response)
    config = IMAPConfig(
        host=server.host,
        port=server.port,
        tls=MailTlsMode.none,
        username="user",
        password="secret",
    )
    config.limits.max_preview_bytes = 8
    with pytest.raises(IMAPOperationError) as caught:
        IMAPClient(config).list_messages(folder="INBOX", limit=1)
    assert caught.value.code == "upstream_protocol_error"
    assert not any(" STORE " in command for command in server.commands)


def test_imap_metadata_literal_and_uid_after_it_are_supported_on_wire(
    imap_server_factory, monkeypatch
) -> None:
    server = imap_server_factory(search_uids=("42",))
    runner = server.stop
    original = runner._uid_response

    def response(tag, rest):
        result = original(tag, rest)
        if "ENVELOPE" in rest:
            subject = b"Status update 42"
            result = result.replace(b'"Status update 42"', b"{16}\r\n" + subject)
            result = result.replace(b"(UID 42 FLAGS", b"(FLAGS").replace(
                b")\r\n" + tag.encode(), b" UID 42)\r\n" + tag.encode()
            )
        return result

    monkeypatch.setattr(runner, "_uid_response", response)
    message = _client(server, max_message_bytes=8).list_messages(
        folder="INBOX", limit=1
    )[0]
    assert message.uid == "42" and message.subject == "Status update 42"
    assert message.snippet == "Plain text body for 42"


def test_imap_unsolicited_same_uid_flags_do_not_replace_requested_data(
    imap_server_factory, monkeypatch
) -> None:
    server = imap_server_factory(search_uids=("42",))
    runner = server.stop
    original = runner._uid_response

    def response(tag, rest):
        result = original(tag, rest)
        if rest.startswith("FETCH"):
            result = b"* 42 FETCH (UID 42 FLAGS (\\Seen))\r\n" + result
        return result

    monkeypatch.setattr(runner, "_uid_response", response)
    result = _client(server).list_messages(folder="INBOX", limit=1)
    assert result[0].snippet == "Plain text body for 42"


@pytest.mark.parametrize("operation", ["get_message", "get_attachment"])
def test_imap_reads_a_body_with_uid_after_the_literal(
    imap_server_factory: Callable[..., LocalIMAPServer], monkeypatch, operation
) -> None:
    server = imap_server_factory(messages={"42": _multipart_message_bytes("42")})
    runner = server.stop
    assert isinstance(runner, _LocalIMAPServerRunner)
    original = runner._uid_response

    def response(tag, rest):
        result = original(tag, rest)
        if rest.startswith("FETCH 42 (BODY.PEEK"):
            result = result.replace(b"(UID 42 BODY[]", b"(BODY[]", 1)
            result = result.replace(
                b")\r\n" + tag.encode() + b" OK FETCH",
                b" UID 42)\r\n" + tag.encode() + b" OK FETCH",
                1,
            )
        return result

    monkeypatch.setattr(runner, "_uid_response", response)
    client = _client(server)
    if operation == "get_message":
        assert (
            client.get_message(folder="INBOX", uid="42").subject
            == "Multipart update 42"
        )
    else:
        assert (
            client.get_attachment(
                folder="INBOX", uid="42", attachment_id="part-3"
            ).content
            == b"ignored attachment"
        )


@pytest.mark.parametrize("variant", ["case", "nested-flags"])
def test_imap_accepts_numeric_fetch_attributes_on_real_wire(
    imap_server: LocalIMAPServer, monkeypatch, variant
) -> None:
    runner = imap_server.stop
    assert isinstance(runner, _LocalIMAPServerRunner)
    original = runner._uid_response

    def response(tag, rest):
        result = original(tag, rest)
        if rest.startswith("FETCH 42") and "(FLAGS)" not in rest:
            header, separator, remaining = result.partition(b"\r\n")
            if variant == "case":
                header = header.replace(b"UID ", b"uId ").replace(
                    b"RFC822.SIZE", b"rfc822.size"
                )
            else:
                header = header.replace(
                    b"(UID 42", b"(FLAGS (UID 42 RFC822.SIZE 9) UID 42", 1
                )
            return header + separator + remaining
        return result

    monkeypatch.setattr(runner, "_uid_response", response)
    assert (
        _client(imap_server).get_message(folder="INBOX", uid="42").subject
        == "Status update 42"
    )


def test_imap_client_reads_and_searches_against_local_server(
    imap_server: LocalIMAPServer,
) -> None:
    client = _client(imap_server)

    messages = client.list_messages(folder="INBOX", limit=2)
    search_results = client.search_messages(folder="INBOX", query="invoice", limit=1)
    message = client.get_message(folder="INBOX", uid="42")

    assert [message.uid for message in messages] == ["42", "41"]
    assert messages[0].subject == "Status update 42"
    assert messages[0].from_addr == "Sender <sender@example.com>"
    assert messages[0].to == ["Bot <bot@example.com>"]
    assert messages[0].cc == ["Watcher <watcher@example.com>"]
    assert messages[0].flags == ["\\Seen", "bot.followed_up"]
    assert messages[0].text_body is None
    assert messages[0].snippet == "Plain text body for 42"
    assert [message.uid for message in search_results] == ["42"]
    assert message.rfc822_message_id == "<message-42@example.com>"

    assert any("LOGIN user@example.com" in command for command in imap_server.commands)
    assert any(command.endswith('EXAMINE "INBOX"') for command in imap_server.commands)
    assert any(command.endswith("UID SEARCH ALL") for command in imap_server.commands)
    assert any(
        command.endswith('UID SEARCH TEXT "invoice"')
        for command in imap_server.commands
    )
    assert any(
        command.endswith("UID FETCH 42 (BODY.PEEK[]<0.52428801>)")
        for command in imap_server.commands
    )


def test_imap_client_connection_probe_selects_mailbox_with_spaces(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    folder = "Archives._Misc.firestats.Various payments"
    imap_server = imap_server_factory(selectable_mailboxes={folder})
    client = _client(imap_server)

    client.test_connection(folders=[folder])

    assert any(
        command.endswith(f'EXAMINE "{folder}"') for command in imap_server.commands
    )


def test_imap_client_mutations_against_local_server(
    imap_server: LocalIMAPServer,
) -> None:
    client = _client(imap_server)

    client.move_message(source_folder="INBOX", uid="42", destination_folder="Archive")
    client.mark_message_read(folder="INBOX", uid="42", read=True)
    client.mark_message_read(folder="INBOX", uid="42", read=False)
    client.delete_message(folder="INBOX", uid="42")

    assert any(command.endswith('SELECT "INBOX"') for command in imap_server.commands)
    assert any(
        command.endswith('UID MOVE 42 "Archive"') for command in imap_server.commands
    )
    assert any(
        command.endswith(r"UID STORE 42 +FLAGS.SILENT (\Seen)")
        for command in imap_server.commands
    )
    assert any(
        command.endswith(r"UID STORE 42 -FLAGS.SILENT (\Seen)")
        for command in imap_server.commands
    )
    assert any(
        command.endswith(r"UID STORE 42 +FLAGS.SILENT (\Deleted)")
        for command in imap_server.commands
    )
    assert any(command.endswith("UID EXPUNGE 42") for command in imap_server.commands)


def test_imap_client_falls_back_when_move_is_unsupported(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(support_move=False)
    client = _client(imap_server)

    client.move_message(source_folder="INBOX", uid="42", destination_folder="Archive")

    assert any(
        command.endswith('UID MOVE 42 "Archive"') for command in imap_server.commands
    )
    assert any(
        command.endswith('UID COPY 42 "Archive"') for command in imap_server.commands
    )
    assert any(
        command.endswith(r"UID STORE 42 +FLAGS.SILENT (\Deleted)")
        for command in imap_server.commands
    )
    assert any(command.endswith("UID EXPUNGE 42") for command in imap_server.commands)


def test_imap_client_quotes_text_search_query(
    imap_server: LocalIMAPServer,
) -> None:
    client = _client(imap_server)

    client.search_messages(folder="INBOX", query='invoice "special" \\path', limit=1)

    assert any(
        command.endswith(r'UID SEARCH TEXT "invoice \"special\" \\path"')
        for command in imap_server.commands
    )


def test_imap_client_parses_multipart_messages(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(
        search_uids=("43",),
        messages={"43": _multipart_message_bytes("43")},
    )
    client = _client(imap_server)

    message = client.get_message(folder="INBOX", uid="43")

    assert message.subject == "Multipart update 43"
    assert message.text_body == "Plain multipart body for 43\n"
    assert message.html_body == "<p>HTML multipart body for 43</p>\n"
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.id == "part-3"
    assert attachment.filename == "ignored.bin"
    assert attachment.content_type == "application/octet-stream"
    assert attachment.size == len(b"ignored attachment")
    assert attachment.disposition == "attachment"
    assert attachment.content_id is None
    assert attachment.inline is False
    assert message.snippet == "Plain multipart body for 43"


def test_imap_client_fetches_attachment_content(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(
        search_uids=("43",),
        messages={"43": _multipart_message_bytes("43")},
    )
    client = _client(imap_server)

    attachment_content = client.get_attachment(
        folder="INBOX",
        uid="43",
        attachment_id="part-3",
    )

    assert attachment_content.attachment.filename == "ignored.bin"
    assert attachment_content.content == b"ignored attachment"


def test_imap_client_surfaces_login_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(login_ok=False)

    from arbiter_imap.errors import IMAPAuthenticationError, imap_operation_error

    with pytest.raises(
        IMAPAuthenticationError, match="IMAP authentication failed"
    ) as caught:
        _client(imap_server).list_messages(folder="INBOX", limit=1)
    error = imap_operation_error(caught.value, replay="safe")
    assert error.code == "upstream_auth_failed"
    assert error.domain_code == "authentication_failed"


def test_imap_client_surfaces_folder_selection_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(selectable_mailboxes={"INBOX"})

    with pytest.raises(
        IMAPOperationError,
        match="open folder for read-only access: Missing",
    ):
        _client(imap_server).get_message(folder="Missing", uid="42")


def test_imap_client_surfaces_search_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(search_ok=False)

    with pytest.raises(IMAPOperationError, match="search messages"):
        _client(imap_server).search_messages(folder="INBOX", query="invoice", limit=1)
