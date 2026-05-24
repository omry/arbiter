from __future__ import annotations

from email.message import EmailMessage
import smtplib
import ssl

from .config import SmtpConfigLike, validate_smtp_config


class SmtpSubmissionClient:
    def __init__(self, config: SmtpConfigLike) -> None:
        validate_smtp_config(config)
        self._config = config

    def send(self, message: EmailMessage, sender: str, recipients: list[str]) -> None:
        ssl_context = self._build_ssl_context()
        smtp_client: smtplib.SMTP | smtplib.SMTP_SSL
        if self._config.tls == "implicit":
            smtp_client = smtplib.SMTP_SSL(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
                context=ssl_context,
            )
        else:
            smtp_client = smtplib.SMTP(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
            )

        with smtp_client as server:
            server.ehlo()
            if self._config.tls == "starttls":
                server.starttls(context=ssl_context)
                server.ehlo()

            if self._config.authenticate:
                server.login(self._config.username, self._config.password)

            refused_recipients = server.send_message(
                message,
                from_addr=sender,
                to_addrs=recipients,
            )
            if refused_recipients:
                raise smtplib.SMTPRecipientsRefused(refused_recipients)

    def _build_ssl_context(self) -> ssl.SSLContext:
        if self._config.verify_peer:
            return ssl.create_default_context()

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
