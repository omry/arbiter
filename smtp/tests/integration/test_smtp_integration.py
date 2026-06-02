from __future__ import annotations

from collections.abc import Callable, Iterator
from email import policy
from email.parser import BytesParser
import smtplib
import ssl
from typing import Any

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import MISSING
import pytest
import trustme

from agent_arbiter.app import ArbiterApp
from agent_arbiter.services import RuntimeRegistry
from agent_arbiter_smtp import SMTPRuntime
from agent_arbiter_smtp.client import SMTPSubmissionClient
from agent_arbiter_smtp.config import MailTlsMode, SMTPConfig, SMTPServicePolicyConfig


class CapturingHandler:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def handle_DATA(self, server, session, envelope) -> str:
        self.envelopes.append(envelope)
        return "250 Message accepted for delivery"


class RejectingRcptHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.rejected_recipients: list[str] = []

    async def handle_RCPT(self, server, session, envelope, address, options) -> str:
        self.rejected_recipients.append(address)
        return "550 Recipient rejected"


class PartiallyRejectingRcptHandler(CapturingHandler):
    def __init__(self, rejected_recipient: str) -> None:
        super().__init__()
        self.rejected_recipient = rejected_recipient
        self.rejected_recipients: list[str] = []

    async def handle_RCPT(self, server, session, envelope, address, options):
        if address == self.rejected_recipient:
            self.rejected_recipients.append(address)
            return "550 Recipient rejected"
        return MISSING


class RejectingDataHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.data_attempts = 0

    async def handle_DATA(self, server, session, envelope) -> str:
        self.data_attempts += 1
        return "554 Message rejected during DATA"


class DisconnectingDataHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.data_attempts = 0

    async def handle_DATA(self, server, session, envelope) -> str:
        self.data_attempts += 1
        raise ConnectionResetError("connection lost during DATA")


def _build_server_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


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


def _build_app(smtp_config: SMTPConfig) -> ArbiterApp:
    return ArbiterApp(
        RuntimeRegistry(
            {
                "smtp": SMTPRuntime(
                    accounts={"primary": smtp_config},
                    policies={"bot": SMTPServicePolicyConfig()},
                    smtp_client_factory=SMTPSubmissionClient,
                )
            }
        ),
    )


def _send_test_message(app: ArbiterApp) -> None:
    app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        subject="Integration Hello",
        text_body="Plain body",
        html_body="<p>HTML body</p>",
    )


def _assert_captured_message(handler: CapturingHandler) -> None:
    assert len(handler.envelopes) == 1

    envelope = handler.envelopes[0]
    parsed_message = BytesParser(policy=policy.default).parsebytes(envelope.content)

    assert envelope.mail_from == "agent@example.com"
    assert envelope.rcpt_tos == [
        "to@example.com",
        "cc@example.com",
        "bcc@example.com",
    ]
    assert parsed_message["From"] == "Arbiter <agent@example.com>"
    assert parsed_message["To"] == "to@example.com"
    assert parsed_message["Cc"] == "cc@example.com"
    assert parsed_message["Subject"] == "Integration Hello"
    assert parsed_message["Bcc"] is None


@pytest.fixture
def server_certificate_paths(tmp_path) -> tuple[str, str]:
    ca = trustme.CA()
    server_cert = ca.issue_cert("localhost", "127.0.0.1")

    cert_path = tmp_path / "smtp-server.pem"
    key_path = tmp_path / "smtp-server.key"
    server_cert.cert_chain_pems[0].write_to_path(cert_path)
    server_cert.private_key_pem.write_to_path(key_path)

    return str(cert_path), str(key_path)


@pytest.fixture
def smtp_server_factory(
    free_tcp_port_factory: Callable[[], int],
    server_certificate_paths: tuple[str, str],
) -> Iterator[Callable[..., tuple[CapturingHandler, Controller]]]:
    controllers: list[Controller] = []

    def start_server(
        *,
        starttls: bool = False,
        use_ssl: bool = False,
        handler: CapturingHandler | None = None,
        **controller_kwargs,
    ) -> tuple[CapturingHandler, Controller]:
        cert_path, key_path = server_certificate_paths
        active_handler = handler or CapturingHandler()
        controller = Controller(
            active_handler,
            hostname="127.0.0.1",
            port=free_tcp_port_factory(),
            tls_context=(
                _build_server_ssl_context(cert_path, key_path) if starttls else None
            ),
            ssl_context=(
                _build_server_ssl_context(cert_path, key_path) if use_ssl else None
            ),
            **controller_kwargs,
        )
        controller.start()
        controllers.append(controller)
        return active_handler, controller

    try:
        yield start_server
    finally:
        for controller in reversed(controllers):
            controller.stop()


def test_send_email_submits_to_plain_smtp_server(smtp_server_factory) -> None:
    handler, controller = smtp_server_factory()
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    result = app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        subject="Integration Hello",
        text_body="Plain body",
        html_body="<p>HTML body</p>",
    )

    assert result.tool == "send_email"
    assert result.recipient_count == 3
    _assert_captured_message(handler)


def test_send_email_submits_html_only_message(smtp_server_factory) -> None:
    handler, controller = smtp_server_factory()
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    result = app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        subject="Integration Hello",
        html_body="<p>HTML only</p>",
    )

    assert result.tool == "send_email"
    assert result.recipient_count == 1
    assert len(handler.envelopes) == 1

    envelope = handler.envelopes[0]
    parsed_message = BytesParser(policy=policy.default).parsebytes(envelope.content)

    assert envelope.mail_from == "agent@example.com"
    assert envelope.rcpt_tos == ["to@example.com"]
    assert parsed_message["From"] == "Arbiter <agent@example.com>"
    assert parsed_message["To"] == "to@example.com"
    assert parsed_message["Subject"] == "Integration Hello"
    assert parsed_message["Bcc"] is None
    assert parsed_message.get_content_type() == "text/html"
    assert parsed_message.is_multipart() is False
    assert "<p>HTML only</p>" in parsed_message.get_content()


def test_send_email_preserves_non_ascii_subject_and_display_name(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory()
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Jöhn Döe",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    result = app.runtime_registry.require("smtp", SMTPRuntime).send_email(
        account="primary",
        to=["to@example.com"],
        subject="Héllo ✓",
        text_body="Plain body",
    )

    assert result.tool == "send_email"
    assert result.recipient_count == 1
    assert len(handler.envelopes) == 1

    envelope = handler.envelopes[0]
    parsed_message = BytesParser(policy=policy.default).parsebytes(envelope.content)

    assert parsed_message["From"] == "Jöhn Döe <agent@example.com>"
    assert parsed_message["Subject"] == "Héllo ✓"


def test_send_email_fails_when_server_is_unavailable(free_tcp_port: int) -> None:
    smtp_config = _smtp_config(
        host="127.0.0.1",
        port=free_tcp_port,
        from_email="agent@example.com",
        starttls=False,
        use_ssl=False,
        timeout_seconds=1.0,
    )
    app = _build_app(smtp_config)

    with pytest.raises(OSError):
        _send_test_message(app)


def test_send_email_surfaces_rcpt_rejections(smtp_server_factory) -> None:
    handler, controller = smtp_server_factory(handler=RejectingRcptHandler())
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        _send_test_message(app)

    assert sorted(excinfo.value.recipients) == [
        "bcc@example.com",
        "cc@example.com",
        "to@example.com",
    ]
    assert handler.rejected_recipients == [
        "to@example.com",
        "cc@example.com",
        "bcc@example.com",
    ]
    assert handler.envelopes == []


def test_send_email_fails_closed_when_only_some_recipients_are_refused(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(
        handler=PartiallyRejectingRcptHandler("bcc@example.com")
    )
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    with pytest.raises(smtplib.SMTPRecipientsRefused) as excinfo:
        _send_test_message(app)

    assert excinfo.value.recipients == {
        "bcc@example.com": (550, b"Recipient rejected"),
    }
    assert handler.rejected_recipients == ["bcc@example.com"]
    assert len(handler.envelopes) == 1
    assert handler.envelopes[0].rcpt_tos == [
        "to@example.com",
        "cc@example.com",
    ]


def test_send_email_surfaces_data_rejections(smtp_server_factory) -> None:
    handler, controller = smtp_server_factory(handler=RejectingDataHandler())
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    with pytest.raises(smtplib.SMTPDataError) as excinfo:
        _send_test_message(app)

    assert excinfo.value.smtp_code == 554
    assert handler.data_attempts == 1
    assert handler.envelopes == []


def test_send_email_surfaces_unknown_submission_status_on_disconnect(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(handler=DisconnectingDataHandler())
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=False,
    )
    app = _build_app(smtp_config)

    with pytest.raises(smtplib.SMTPServerDisconnected):
        _send_test_message(app)

    assert handler.data_attempts == 1
    assert handler.envelopes == []


def test_send_email_submits_over_starttls_when_peer_verification_is_disabled(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(starttls=True)
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=True,
        use_ssl=False,
        verify_peer=False,
    )
    app = _build_app(smtp_config)

    _send_test_message(app)

    _assert_captured_message(handler)


def test_send_email_fails_on_starttls_with_invalid_certificate(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(starttls=True)
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=True,
        use_ssl=False,
        verify_peer=True,
    )
    app = _build_app(smtp_config)

    with pytest.raises(ssl.SSLCertVerificationError):
        _send_test_message(app)

    assert handler.envelopes == []


def test_send_email_submits_over_smtps_when_peer_verification_is_disabled(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(use_ssl=True)
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=True,
        verify_peer=False,
    )
    app = _build_app(smtp_config)

    _send_test_message(app)

    _assert_captured_message(handler)


def test_send_email_fails_on_smtps_with_invalid_certificate(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(use_ssl=True)
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=False,
        use_ssl=True,
        verify_peer=True,
    )
    app = _build_app(smtp_config)

    with pytest.raises(ssl.SSLCertVerificationError):
        _send_test_message(app)

    assert handler.envelopes == []


def test_send_email_authenticates_successfully_after_starttls(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(
        starttls=True,
        require_starttls=True,
        auth_required=True,
        auth_callback=lambda mechanism, login, password: (
            login == b"user" and password == b"secret"
        ),
    )
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        authenticate=True,
        username="user",
        password="secret",
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=True,
        use_ssl=False,
        verify_peer=False,
    )
    app = _build_app(smtp_config)

    _send_test_message(app)

    _assert_captured_message(handler)


def test_send_email_surfaces_authentication_failures(
    smtp_server_factory,
) -> None:
    handler, controller = smtp_server_factory(
        starttls=True,
        require_starttls=True,
        auth_required=True,
        auth_callback=lambda mechanism, login, password: False,
    )
    smtp_config = _smtp_config(
        host=controller.hostname,
        port=controller.port,
        authenticate=True,
        username="user",
        password="wrong",
        from_email="agent@example.com",
        from_name="Arbiter",
        starttls=True,
        use_ssl=False,
        verify_peer=False,
    )
    app = _build_app(smtp_config)

    with pytest.raises(smtplib.SMTPAuthenticationError):
        _send_test_message(app)

    assert handler.envelopes == []
