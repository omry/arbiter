from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from email.message import EmailMessage
import imaplib
import socket
import threading

import pytest

from agent_arbiter_imap.client import IMAPClient, IMAPOperationError
from agent_arbiter_imap.config import IMAPConfig, MailTlsMode


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
        login_ok: bool = True,
        search_ok: bool = True,
        support_move: bool = True,
        selectable_mailboxes: set[str] | None = None,
        search_uids: tuple[str, ...] = ("40", "41", "42"),
        messages: dict[str, bytes] | None = None,
    ) -> None:
        self.host = "127.0.0.1"
        self.port = port
        self.commands: list[str] = []
        self.login_ok = login_ok
        self.search_ok = search_ok
        self.support_move = support_move
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
            with socket.create_connection((self.host, self.port), timeout=1):
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
        connection.sendall(b"* OK Agent Arbiter test IMAP server ready\r\n")
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
            return (
                f"* {uid} FETCH (UID {uid} RFC822 {{{len(body)}}}\r\n".encode()
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
        selectable_mailboxes: set[str] | None = None,
        search_uids: tuple[str, ...] = ("40", "41", "42"),
        messages: dict[str, bytes] | None = None,
    ) -> LocalIMAPServer:
        runner = _LocalIMAPServerRunner(
            free_tcp_port_factory(),
            login_ok=login_ok,
            search_ok=search_ok,
            support_move=support_move,
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


def _client(server: LocalIMAPServer) -> IMAPClient:
    return IMAPClient(
        IMAPConfig(
            host=server.host,
            port=server.port,
            username="user@example.com",
            password="secret",
            tls=MailTlsMode.none,
        )
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
    assert messages[0].text_body == "Plain text body for 42\n"
    assert messages[0].snippet == "Plain text body for 42"
    assert [message.uid for message in search_results] == ["42"]
    assert message.rfc822_message_id == "<message-42@example.com>"

    assert any("LOGIN user@example.com" in command for command in imap_server.commands)
    assert any(command.endswith("EXAMINE INBOX") for command in imap_server.commands)
    assert any(command.endswith("UID SEARCH ALL") for command in imap_server.commands)
    assert any(
        command.endswith('UID SEARCH TEXT "invoice"')
        for command in imap_server.commands
    )
    assert any(
        command.endswith("UID FETCH 42 (RFC822)") for command in imap_server.commands
    )


def test_imap_client_mutations_against_local_server(
    imap_server: LocalIMAPServer,
) -> None:
    client = _client(imap_server)

    client.move_message(source_folder="INBOX", uid="42", destination_folder="Archive")
    client.mark_message_read(folder="INBOX", uid="42", read=True)
    client.mark_message_read(folder="INBOX", uid="42", read=False)
    client.delete_message(folder="INBOX", uid="42")

    assert any(command.endswith("SELECT INBOX") for command in imap_server.commands)
    assert any(
        command.endswith("UID MOVE 42 Archive") for command in imap_server.commands
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
    assert any(command.endswith("EXPUNGE") for command in imap_server.commands)


def test_imap_client_falls_back_when_move_is_unsupported(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(support_move=False)
    client = _client(imap_server)

    client.move_message(source_folder="INBOX", uid="42", destination_folder="Archive")

    assert any(
        command.endswith("UID MOVE 42 Archive") for command in imap_server.commands
    )
    assert any(
        command.endswith("UID COPY 42 Archive") for command in imap_server.commands
    )
    assert any(
        command.endswith(r"UID STORE 42 +FLAGS.SILENT (\Deleted)")
        for command in imap_server.commands
    )
    assert any(command.endswith("EXPUNGE") for command in imap_server.commands)


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
    assert message.snippet == "Plain multipart body for 43"


def test_imap_client_surfaces_login_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(login_ok=False)

    with pytest.raises(imaplib.IMAP4.error, match="LOGIN failed"):
        _client(imap_server).list_messages(folder="INBOX", limit=1)


def test_imap_client_surfaces_folder_selection_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(selectable_mailboxes={"INBOX"})

    with pytest.raises(IMAPOperationError, match="select folder Missing"):
        _client(imap_server).get_message(folder="Missing", uid="42")


def test_imap_client_surfaces_search_failure(
    imap_server_factory: Callable[..., LocalIMAPServer],
) -> None:
    imap_server = imap_server_factory(search_ok=False)

    with pytest.raises(IMAPOperationError, match="search messages"):
        _client(imap_server).search_messages(folder="INBOX", query="invoice", limit=1)
