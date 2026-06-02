from __future__ import annotations

import ssl
from email.message import EmailMessage
import smtplib
from typing import Any

import pytest
from arbiter_smtp.config import MailTlsMode, SMTPConfig
from arbiter_smtp.client import SMTPSubmissionClient


class FakeServer:
    def __init__(self) -> None:
        self.starttls_context: ssl.SSLContext | None = None
        self.sent_message: EmailMessage | None = None
        self.sent_from: str | None = None
        self.sent_to: list[str] | None = None
        self.login_args: tuple[str, str] | None = None
        self.ehlo_calls = 0
        self.refused_recipients: dict[str, tuple[int, bytes]] = {}

    def __enter__(self) -> "FakeServer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def ehlo(self) -> None:
        self.ehlo_calls += 1

    def starttls(self, *, context: ssl.SSLContext) -> None:
        self.starttls_context = context

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(
        self,
        message: EmailMessage,
        from_addr: str,
        to_addrs: list[str],
    ) -> dict[str, tuple[int, bytes]]:
        self.sent_message = message
        self.sent_from = from_addr
        self.sent_to = to_addrs
        return self.refused_recipients


def _smtp_config(
    *,
    starttls: bool | None = None,
    use_ssl: bool | None = None,
    authenticate: bool | None = None,
    **overrides: Any,
) -> SMTPConfig:
    if use_ssl:
        tls = MailTlsMode.implicit
    elif starttls is False:
        tls = MailTlsMode.none
    else:
        tls = MailTlsMode.starttls

    if authenticate is None:
        authenticate = bool(overrides.get("username"))

    return SMTPConfig(tls=tls, authenticate=authenticate, **overrides)


def test_build_ssl_context_disables_verification_when_verify_peer_is_false() -> None:
    client = SMTPSubmissionClient(_smtp_config(verify_peer=False))

    context = client._build_ssl_context()

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_build_ssl_context_verifies_peer_by_default() -> None:
    client = SMTPSubmissionClient(_smtp_config())

    context = client._build_ssl_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_send_uses_unverified_context_for_starttls(monkeypatch) -> None:
    fake_server = FakeServer()

    def fake_smtp(host: str, port: int, timeout: float) -> FakeServer:
        assert host == "smtp.example.com"
        assert port == 587
        assert timeout == 30.0
        return fake_server

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fake_smtp)

    client = SMTPSubmissionClient(
        _smtp_config(
            host="smtp.example.com",
            verify_peer=False,
            authenticate=True,
            username="user",
            password="secret",
        )
    )

    message = EmailMessage()
    message["Subject"] = "Hello"

    client.send(message, sender="agent@example.com", recipients=["to@example.com"])

    assert fake_server.starttls_context is not None
    assert fake_server.starttls_context.check_hostname is False
    assert fake_server.starttls_context.verify_mode == ssl.CERT_NONE
    assert fake_server.login_args == ("user", "secret")
    assert fake_server.sent_from == "agent@example.com"
    assert fake_server.sent_to == ["to@example.com"]


def test_send_uses_smtp_ssl_when_use_ssl_is_enabled(monkeypatch) -> None:
    fake_server = FakeServer()

    def fail_plain_smtp(*args, **kwargs) -> None:
        raise AssertionError("SMTP should not be used when use_ssl is enabled")

    def fake_smtp_ssl(
        host: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> FakeServer:
        assert host == "smtp.example.com"
        assert port == 465
        assert timeout == 30.0
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE
        return fake_server

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fail_plain_smtp)
    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP_SSL", fake_smtp_ssl)

    client = SMTPSubmissionClient(
        _smtp_config(
            host="smtp.example.com",
            port=465,
            use_ssl=True,
            starttls=False,
            verify_peer=False,
        )
    )

    message = EmailMessage()
    message["Subject"] = "Hello"

    client.send(message, sender="agent@example.com", recipients=["to@example.com"])

    assert fake_server.starttls_context is None
    assert fake_server.login_args is None
    assert fake_server.ehlo_calls == 1
    assert fake_server.sent_from == "agent@example.com"
    assert fake_server.sent_to == ["to@example.com"]


def test_send_skips_login_when_username_is_not_configured(monkeypatch) -> None:
    fake_server = FakeServer()

    def fake_smtp(host: str, port: int, timeout: float) -> FakeServer:
        return fake_server

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fake_smtp)

    client = SMTPSubmissionClient(_smtp_config())
    message = EmailMessage()
    message["Subject"] = "Hello"

    client.send(message, sender="agent@example.com", recipients=["to@example.com"])

    assert fake_server.login_args is None


def test_send_propagates_connection_errors(monkeypatch) -> None:
    def fake_smtp(host: str, port: int, timeout: float) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fake_smtp)

    client = SMTPSubmissionClient(_smtp_config())
    message = EmailMessage()
    message["Subject"] = "Hello"

    with pytest.raises(OSError, match="connection refused"):
        client.send(message, sender="agent@example.com", recipients=["to@example.com"])


def test_send_propagates_authentication_errors(monkeypatch) -> None:
    fake_server = FakeServer()

    def fake_login(username: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    class FailingLoginServer(FakeServer):
        def login(self, username: str, password: str) -> None:
            fake_login(username, password)

    fake_server = FailingLoginServer()

    def fake_smtp(host: str, port: int, timeout: float) -> FakeServer:
        return fake_server

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fake_smtp)

    client = SMTPSubmissionClient(
        _smtp_config(authenticate=True, username="user", password="secret")
    )
    message = EmailMessage()
    message["Subject"] = "Hello"

    with pytest.raises(smtplib.SMTPAuthenticationError):
        client.send(message, sender="agent@example.com", recipients=["to@example.com"])


def test_send_raises_when_some_recipients_are_refused(monkeypatch) -> None:
    fake_server = FakeServer()
    fake_server.refused_recipients = {
        "bcc@example.com": (550, b"Recipient rejected"),
    }

    def fake_smtp(host: str, port: int, timeout: float) -> FakeServer:
        return fake_server

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", fake_smtp)

    client = SMTPSubmissionClient(_smtp_config())
    message = EmailMessage()
    message["Subject"] = "Hello"

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        client.send(
            message,
            sender="agent@example.com",
            recipients=["to@example.com", "bcc@example.com"],
        )

    assert excinfo.value.recipients == {
        "bcc@example.com": (550, b"Recipient rejected"),
    }
