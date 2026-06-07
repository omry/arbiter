from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from email import policy
from email.parser import BytesParser
import os
import smtplib
import ssl
import threading
import time
from typing import Any

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import MISSING
import pytest

from arbiter_server.app import ArbiterApp
from arbiter_server.services import RuntimeRegistry
from arbiter_smtp import SMTPRuntime
from arbiter_smtp.client import SMTPSubmissionClient
from arbiter_smtp.config import MailTlsMode, SMTPConfig, SMTPServicePolicyConfig

_TEST_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDJTCCAg2gAwIBAgIUF2mAPoQq0+eoL6IXiNe5nXt51zcwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MDYwNTE5NTg0MFoXDTM2MDYw
MjE5NTg0MFowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEA1cL0yOY9+EmlYakO/UB3qLr5a81Hz/FSELB2vSz3Fo2Q
B3JgQT6wbT1L3RqB3xxDf70XNGBNqyDvay/APbOL76sN86U3nqq8gFAmh0yZuSKq
h8jMlweu6+w5Y8h7XUO9sexFpENVWxfA8sRimLSwoBmbUYC5lSNJJEUkhmUTLjtR
7V85hcg0ilnv7VF56GtQWly1VSMTR9eSybi/prDXykJ0FxbErSUYySAxk0PlfaR7
F/4MFYaNgQyEi6E+DgSDK3KpBb2Nhm/AegEPNOk/a5bu6bv2VBB779IrdlilwAII
9UubNhgOHoBipzFz9pPlXOhQzJepsjGhR4UY9fCVtwIDAQABo28wbTAdBgNVHQ4E
FgQUrO+6GgJcU6GMmqMozk0kzaV07FMwHwYDVR0jBBgwFoAUrO+6GgJcU6GMmqMo
zk0kzaV07FMwDwYDVR0TAQH/BAUwAwEB/zAaBgNVHREEEzARgglsb2NhbGhvc3SH
BH8AAAEwDQYJKoZIhvcNAQELBQADggEBAF0yseUBf6SoCrqP1nWp6zBU16nIJ15P
GOnfmhIYZBNKH+ENXpD/S1qBh9iZFIz4D0WbedNVY9OcQthwWJmF01G82phZdS0H
G7EjUnPSo9s2s8XgtVCbGejuiJJZS0hC0Kcv1N90nSqsAKwSNNKFYMQJwRVzckMc
0U+YhsudVIUn7NLCLOjCg/o/gwv0WmwjX3w5SCsvSOMBWA6Abo5KCJI4xGpWHqwr
92xYwM5IWgDKUZdBCryauoSzhNDZGuzYinscVUcp7SQMrBO6hlG8UYFn11dXmIMX
Qy4BYJcWoAYYPdBkI6HboWPeYRzeOeBDyrZNqtL2XmB9kj7+qnJAjSU=
-----END CERTIFICATE-----
"""

_TEST_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDVwvTI5j34SaVh
qQ79QHeouvlrzUfP8VIQsHa9LPcWjZAHcmBBPrBtPUvdGoHfHEN/vRc0YE2rIO9r
L8A9s4vvqw3zpTeeqryAUCaHTJm5IqqHyMyXB67r7DljyHtdQ72x7EWkQ1VbF8Dy
xGKYtLCgGZtRgLmVI0kkRSSGZRMuO1HtXzmFyDSKWe/tUXnoa1BaXLVVIxNH15LJ
uL+msNfKQnQXFsStJRjJIDGTQ+V9pHsX/gwVho2BDISLoT4OBIMrcqkFvY2Gb8B6
AQ806T9rlu7pu/ZUEHvv0it2WKXAAgj1S5s2GA4egGKnMXP2k+Vc6FDMl6myMaFH
hRj18JW3AgMBAAECggEAAcmTimqCciULgPmM6okzdvG5TDF3PEHkUcdn9sT9e+7L
GYTXUTRY/VDZ5YtnMppl5heKriFwBNJNrjPEA8AQl3xyrWrgQC9lTB1fdRoq3uVv
T5taOpkR2V8sS61NesYqO+ah3nHBsPVN47nIqUY5g90y80eERb75yZfaalVGB8jr
SEURXOFw7/TWJcZNN4Z0GfFaaz26f7VX8lWURa75KYBc2MuUCe2YSy/TF7t7PmQt
issik1HQNZ1orOMESXsLIprGlnpdwF7CghnTgr6zOxrppXD9tqExuCRNzy2spHKL
NZ6TaL7x29GknjOnoc70Kae2PrBI1BhjZqlHPdI2QQKBgQDyr8nMz+fIQFP62DqZ
Sj2ic1P/u1RzFOWnunjjOaUyPI4yk/QEwsRmpWmlUalBCODwUJdLBsx0xACpoUdO
Te+tUbhpyXCORWjKlM9tZihlm5sNNVCfvRiFoN+tw9maDtN6wphpZ4t0ntc4B0IZ
6C9tf3P2psdyzx35aJC9aKUo7wKBgQDhfPP2E920gx8ik/H57NBsUN2P826Df6G6
oEna5tuaHr5e3n/KbGrenvx9g6vwMcOSu9+TEGn3XtwR5sIHz1fRV+aJZPbvBtyB
o6LdViEYgIAzwiLbcDzwFBM27oh48vaoSEoSW3bUANPwxS4HJ1hV8hFcw3a/Hk3R
bKbbCdAPuQKBgQCB2tO01o4kFV+aOjborNPb56/Lh3YBee4EWH+0vbSJ8+L2ZzpL
jde/QMUNo2tYFCMgE09Q4ttlozbjjRt1Z7hWWgK9//5M8hDHTObMl7wH2kIVyDBS
uMC1R8ZH2SLHDyXTaupYhAIXraJlJWUWLamrAmaPVOAlq3NTb8L6xlKRWwKBgQCV
muvaRtAAJkcQEAyp/39Bfl2iVqbqRFIvmo2l2Sm2ldNE6mbrDQfS8LUhKa14Tewu
fMwXrPpBkAR/NBVkTSM82A8y9XQInwrKUKGMLMsEkK1+qb2qzksAFrGw7o5JgRo6
CMxsZZbvjiUQSCMDyA0J6POwElfE8fw7iNUj2tzasQKBgQCNgimLm4ZggBHp6znz
aYbgUFiU65d6/7pXak1N3lFomLsFfh2vAAJ4i6iZ8o1damJzxjArMO6rP2AuYifj
W7vFjhNYHzH9Dps8y+MtYyc+REaP4tMHw9VtLU3zWUQ7ePrmYANVBS2n1Vi+ca6G
ExcruTfHGIOOQ+TRKDh3kYyq7Q==
-----END PRIVATE KEY-----
"""


class CapturingHandler:
    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self._debug_callback: Callable[[str], None] | None = None

    def _debug_event(self, event: str) -> None:
        if self._debug_callback is not None:
            self._debug_callback(event)

    async def handle_DATA(self, server, session, envelope) -> str:
        self._debug_event(
            f"handler DATA from={envelope.mail_from!r} rcpt={envelope.rcpt_tos!r}"
        )
        self.envelopes.append(envelope)
        return "250 Message accepted for delivery"


class RejectingRcptHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.rejected_recipients: list[str] = []

    async def handle_RCPT(self, server, session, envelope, address, options) -> str:
        self._debug_event(f"handler RCPT rejecting address={address!r}")
        self.rejected_recipients.append(address)
        return "550 Recipient rejected"


class PartiallyRejectingRcptHandler(CapturingHandler):
    def __init__(self, rejected_recipient: str) -> None:
        super().__init__()
        self.rejected_recipient = rejected_recipient
        self.rejected_recipients: list[str] = []

    async def handle_RCPT(self, server, session, envelope, address, options):
        if address == self.rejected_recipient:
            self._debug_event(f"handler RCPT partially rejecting address={address!r}")
            self.rejected_recipients.append(address)
            return "550 Recipient rejected"
        self._debug_event(f"handler RCPT accepting via default address={address!r}")
        return MISSING


class RejectingDataHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.data_attempts = 0

    async def handle_DATA(self, server, session, envelope) -> str:
        self._debug_event("handler DATA rejecting message")
        self.data_attempts += 1
        return "554 Message rejected during DATA"


class DisconnectingDataHandler(CapturingHandler):
    def __init__(self) -> None:
        super().__init__()
        self.data_attempts = 0

    async def handle_DATA(self, server, session, envelope) -> str:
        self._debug_event("handler DATA disconnecting during message")
        self.data_attempts += 1
        raise ConnectionResetError("connection lost during DATA")


class IntegrationController(Controller):
    def __init__(
        self,
        *args: Any,
        ready_timeout: float,
        **kwargs: Any,
    ) -> None:
        self._integration_ready_timeout = ready_timeout
        self._debug_enabled = os.environ.get("ARBITER_SMTP_TEST_DEBUG") == "1"
        self._debug_started = time.monotonic()
        self._debug_events: list[str] = []
        super().__init__(*args, ready_timeout=ready_timeout, **kwargs)

    def debug_event(self, event: str) -> None:
        elapsed = time.monotonic() - self._debug_started
        entry = f"+{elapsed:.3f}s {event}"
        self._debug_events.append(entry)
        if self._debug_enabled:
            print(f"[smtp-test] {entry}", flush=True)

    def _run(self, ready_event: threading.Event) -> None:
        self.debug_event("controller thread entering _run")
        asyncio.set_event_loop(self.loop)
        try:
            self.debug_event(
                f"creating server host={self.hostname!r} port={self.port!r}"
            )
            self.server_coro = self._create_server()
            self.server = self.loop.run_until_complete(self.server_coro)
            sockets = getattr(self.server, "sockets", None) or []
            socket_names = [socket.getsockname() for socket in sockets]
            self.debug_event(f"server bound sockets={socket_names!r}")
        except Exception as error:
            self._thread_exception = error
            self.debug_event(f"server _run failed before ready: {error!r}")
            return

        def set_ready_event(*_args: object) -> None:
            ready_event.set()

        self.loop.call_soon(set_ready_event)
        self.debug_event("ready event scheduled")
        self.loop.run_forever()
        self.debug_event("event loop stopped")

        assert self.server is not None
        self.server.close()
        self.loop.run_until_complete(self.server.wait_closed())
        self.loop.close()
        self.server = None
        self.debug_event("server closed")

    def factory(self):
        self.debug_event("SMTP factory invoked")
        smtp = super().factory()
        self.debug_event(f"SMTP factory returned {type(smtp).__name__}")
        return smtp

    def start(self) -> None:
        assert self._thread is None, "SMTP daemon already running"
        self._factory_invoked.clear()
        self.debug_event("start requested")

        ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready_event,))
        self._thread.daemon = True
        self._thread.start()
        self.debug_event("controller thread started")

        start = time.monotonic()
        deadline = start + self._integration_ready_timeout
        if not ready_event.wait(self._integration_ready_timeout):
            if self._thread_exception is not None:
                raise self._thread_exception
            raise TimeoutError(
                "SMTP server failed to start "
                f"within {self._integration_ready_timeout:.1f}s. "
                "This might happen if the system is too busy. "
                "Try increasing the `ready_timeout` parameter."
            )

        last_probe_error: BaseException | None = None
        probe_attempts = 0
        while time.monotonic() < deadline:
            if self._thread_exception is not None:
                raise self._thread_exception
            try:
                probe_attempts += 1
                self.debug_event(f"probe {probe_attempts} starting")
                self._probe_server()
                self.debug_event(f"probe {probe_attempts} succeeded")
                break
            except (OSError, smtplib.SMTPException) as exc:
                last_probe_error = exc
                self.debug_event(f"probe {probe_attempts} failed: {exc!r}")
            time.sleep(0.05)
        else:
            detail = (
                f" Last probe error: {last_probe_error!r}."
                if last_probe_error is not None
                else ""
            )
            raise TimeoutError(
                "SMTP server started, but did not respond "
                f"within {self._integration_ready_timeout:.1f}s. "
                "This might happen if the system is too busy. "
                "Try increasing the `ready_timeout` parameter."
                f"{detail} {self._readiness_diagnostics(probe_attempts)}"
            )

        if self._thread_exception is not None:
            raise self._thread_exception
        if self.smtpd is None:
            raise RuntimeError("Unknown Error, failed to init SMTP server")

    def _probe_server(self) -> None:
        hostname = self.hostname or self._localhost
        if self.ssl_context is None:
            self.debug_event(
                f"probe connecting SMTP host={hostname!r} port={self.port}"
            )
            with smtplib.SMTP(hostname, self.port, timeout=1.0):
                return

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.debug_event(f"probe connecting SMTPS host={hostname!r} port={self.port}")
        with smtplib.SMTP_SSL(
            hostname,
            self.port,
            timeout=1.0,
            context=context,
        ):
            return

    def _readiness_diagnostics(self, probe_attempts: int) -> str:
        thread = self._thread
        return (
            "SMTP readiness diagnostics: "
            f"host={self.hostname!r}, "
            f"port={self.port!r}, "
            f"implicit_tls={self.ssl_context is not None}, "
            f"starttls={self.SMTP_kwargs.get('tls_context') is not None}, "
            f"thread_alive={thread.is_alive() if thread is not None else None}, "
            f"thread_exception={self._thread_exception!r}, "
            f"factory_invoked={self._factory_invoked.is_set()}, "
            f"smtpd_ready={self.smtpd is not None}, "
            f"probe_attempts={probe_attempts}, "
            f"events={self._debug_events!r}."
        )


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
    cert_path = tmp_path / "smtp-server.pem"
    key_path = tmp_path / "smtp-server.key"
    cert_path.write_text(_TEST_CERT_PEM, encoding="ascii")
    key_path.write_text(_TEST_KEY_PEM, encoding="ascii")

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
        controller = IntegrationController(
            active_handler,
            hostname="localhost",
            port=free_tcp_port_factory(),
            tls_context=(
                _build_server_ssl_context(cert_path, key_path) if starttls else None
            ),
            ssl_context=(
                _build_server_ssl_context(cert_path, key_path) if use_ssl else None
            ),
            ready_timeout=10.0,
            **controller_kwargs,
        )
        active_handler._debug_callback = controller.debug_event
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
