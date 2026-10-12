from __future__ import annotations

from contextlib import suppress
import smtplib
import ssl

from arbiter_server.errors import OperationError

from .config import MailTlsMode, SMTPConfig
from .errors import SMTPPreSubmissionError


class SMTPSubmissionClient:
    def __init__(self, config: SMTPConfig) -> None:
        self._config = config

    def test_connection(self) -> None:
        with self._connect() as server:
            self._prepare_session(server)
            self._expect_ok(server.noop(), "NOOP")

    def send(self, message_bytes: bytes, sender: str, recipients: list[str]) -> None:
        try:
            server = self._connect()
        except Exception as exc:
            if isinstance(
                exc,
                (
                    OperationError,
                    smtplib.SMTPResponseException,
                    smtplib.SMTPNotSupportedError,
                ),
            ):
                raise
            raise SMTPPreSubmissionError(exc) from exc
        try:
            try:
                self._prepare_session(server)
            except Exception as exc:
                if isinstance(
                    exc,
                    (
                        OperationError,
                        smtplib.SMTPResponseException,
                        smtplib.SMTPNotSupportedError,
                    ),
                ):
                    raise
                raise SMTPPreSubmissionError(exc) from exc
            try:
                refused_recipients = server.sendmail(sender, recipients, message_bytes)
            except smtplib.SMTPRecipientsRefused as exc:
                # stdlib aborts RCPT processing immediately on 421, before
                # DATA. Its dictionary can omit accepted/unprocessed RCPTs.
                if any(reply[0] == 421 for reply in exc.recipients.values()):
                    raise SMTPPreSubmissionError(exc) from exc
                raise
            if refused_recipients:
                raise smtplib.SMTPRecipientsRefused(refused_recipients)
        finally:
            # DATA acceptance (or the primary rejection) determines delivery.
            # QUIT cleanup must not replace that outcome. SMTPException is an
            # OSError, so this also covers protocol errors while disconnecting.
            with suppress(OSError):
                server.quit()
            with suppress(OSError):
                server.close()

    def _connect(self) -> smtplib.SMTP | smtplib.SMTP_SSL:
        ssl_context = self._build_ssl_context()
        if self._config.tls == MailTlsMode.implicit:
            return smtplib.SMTP_SSL(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
                context=ssl_context,
            )
        return smtplib.SMTP(
            self._config.host,
            self._config.port,
            timeout=self._config.timeout_seconds,
        )

    def _prepare_session(self, server: smtplib.SMTP | smtplib.SMTP_SSL) -> None:
        ssl_context = self._build_ssl_context()
        self._expect_ok(server.ehlo(), "EHLO")
        if self._config.tls == MailTlsMode.starttls:
            self._expect_ok(server.starttls(context=ssl_context), "STARTTLS")
            self._expect_ok(server.ehlo(), "EHLO")

        if self._config.authenticate:
            self._expect_ok(
                server.login(self._config.username, self._config.password), "login"
            )

    def _expect_ok(self, response: tuple[int, bytes], action: str) -> None:
        code, message = response
        if code < 200 or code >= 400:
            raise smtplib.SMTPResponseException(
                code, f"SMTP {action} failed: {self._format_response_message(message)}"
            )

    def _format_response_message(self, message: bytes | str) -> str:
        if isinstance(message, bytes):
            return message.decode("utf-8", errors="replace")
        return message

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self._config.verify_peer:
            return ssl.create_default_context()

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
