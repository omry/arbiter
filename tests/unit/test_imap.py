from __future__ import annotations

import ssl
import imaplib
from typing import Any

import pytest

from mail_sentry.config import IMAPConfig, MailTlsMode
from mail_sentry.imap import IMAPClient, IMAPOperationError


MESSAGE_BYTES = (
    b"From: Sender <sender@example.com>\r\n"
    b"To: Bot <bot@example.com>\r\n"
    b"Cc: Watcher <watcher@example.com>\r\n"
    b"Subject: Status update\r\n"
    b"Date: Tue, 03 Mar 2026 12:00:00 +0000\r\n"
    b"Message-ID: <message-42@example.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Plain text body\r\n"
)


class FakeIMAPServer:
    def __init__(self) -> None:
        self.selected: list[dict[str, object]] = []
        self.uid_calls: list[tuple[str, tuple[object, ...]]] = []
        self.login_args: tuple[str, str] | None = None
        self.starttls_context: ssl.SSLContext | None = None
        self.logged_out = False
        self.expunge_calls = 0
        self.raise_on_move = False

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected.append({"mailbox": mailbox, "readonly": readonly})
        return "OK", [b"3"]

    def uid(self, command: str, *args: object) -> tuple[str, list[Any]]:
        self.uid_calls.append((command, args))
        if command == "MOVE" and self.raise_on_move:
            raise imaplib.IMAP4.error("MOVE unavailable")
        if command == "SEARCH":
            return "OK", [b"40 41 42"]
        if command == "FETCH" and args[1] == "(FLAGS)":
            return "OK", [b"42 (UID 42 FLAGS (\\Seen bot.followed_up))"]
        if command == "FETCH" and args[1] == "(RFC822)":
            return "OK", [(b"42 (RFC822 {123}", MESSAGE_BYTES)]
        return "OK", [b"ok"]

    def starttls(self, *, ssl_context: ssl.SSLContext) -> None:
        self.starttls_context = ssl_context

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def expunge(self) -> tuple[str, list[bytes]]:
        self.expunge_calls += 1
        return "OK", [b"42"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "OK", [b"logout"]


def test_list_messages_uses_ssl_login_and_parses_recent_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        assert host == "imap.example.com"
        assert port == 993
        assert timeout == 10.0
        assert ssl_context.check_hostname is False
        return fake_server

    monkeypatch.setattr("mail_sentry.imap.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(
        IMAPConfig(
            host="imap.example.com",
            username="user",
            password="secret",
            verify_peer=False,
            timeout_seconds=10.0,
        )
    )

    messages = client.list_messages(folder="INBOX", limit=2)

    assert fake_server.login_args == ("user", "secret")
    assert fake_server.selected == [{"mailbox": "INBOX", "readonly": True}]
    assert [message.uid for message in messages] == ["42", "41"]
    assert messages[0].subject == "Status update"
    assert messages[0].from_addr == "Sender <sender@example.com>"
    assert messages[0].to == ["Bot <bot@example.com>"]
    assert messages[0].cc == ["Watcher <watcher@example.com>"]
    assert messages[0].flags == ["\\Seen", "bot.followed_up"]
    assert messages[0].text_body == "Plain text body\r\n"
    assert messages[0].html_body is None
    assert messages[0].snippet == "Plain text body"
    assert fake_server.logged_out is True


def test_starttls_uses_configured_context(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeIMAPServer()

    def fake_imap4(host: str, port: int, *, timeout: float) -> FakeIMAPServer:
        assert host == "imap.example.com"
        assert port == 143
        assert timeout == 30.0
        return fake_server

    monkeypatch.setattr("mail_sentry.imap.imaplib.IMAP4", fake_imap4)

    client = IMAPClient(
        IMAPConfig(
            host="imap.example.com",
            port=143,
            tls=MailTlsMode.starttls,
            verify_peer=False,
        )
    )

    client.get_message(folder="INBOX", uid="42")

    assert fake_server.starttls_context is not None
    assert fake_server.starttls_context.check_hostname is False
    assert fake_server.starttls_context.verify_mode == ssl.CERT_NONE


def test_move_falls_back_to_copy_delete_and_expunge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_server = FakeIMAPServer()
    fake_server.raise_on_move = True

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("mail_sentry.imap.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    client.move_message(
        source_folder="INBOX",
        uid="42",
        destination_folder="Archive",
    )

    assert fake_server.selected == [{"mailbox": "INBOX", "readonly": False}]
    assert ("COPY", ("42", "Archive")) in fake_server.uid_calls
    assert ("STORE", ("42", "+FLAGS.SILENT", r"(\Deleted)")) in fake_server.uid_calls
    assert fake_server.expunge_calls == 1


def test_fetch_without_rfc822_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_server = FakeIMAPServer()

    def fake_uid(command: str, *args: object) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            return "OK", [b"42"]
        if command == "FETCH" and args[1] == "(FLAGS)":
            return "OK", [b"42 (UID 42 FLAGS ())"]
        return "OK", [b"missing body"]

    fake_server.uid = fake_uid  # type: ignore[method-assign]

    def fake_imap4_ssl(
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
    ) -> FakeIMAPServer:
        return fake_server

    monkeypatch.setattr("mail_sentry.imap.imaplib.IMAP4_SSL", fake_imap4_ssl)

    client = IMAPClient(IMAPConfig())

    with pytest.raises(IMAPOperationError, match="did not return RFC822"):
        client.list_messages(folder="INBOX", limit=1)
