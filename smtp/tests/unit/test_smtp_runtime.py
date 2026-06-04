from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path
import smtplib

import pytest

from arbiter_smtp import SMTPRuntime
from arbiter_smtp.config import (
    SMTPConfig,
    SMTPIdempotencyConfig,
    SMTPLimitsConfig,
    SMTPServicePolicyConfig,
)


class FakeSMTPClient:
    def __init__(self) -> None:
        self.message: EmailMessage | None = None
        self.sender: str | None = None
        self.recipients: list[str] | None = None
        self.tested = False

    def test_connection(self) -> None:
        self.tested = True

    def send(
        self,
        message: EmailMessage,
        sender: str,
        recipients: list[str],
    ) -> None:
        self.message = message
        self.sender = sender
        self.recipients = recipients


class FailingSMTPClient(FakeSMTPClient):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def send(
        self,
        message: EmailMessage,
        sender: str,
        recipients: list[str],
    ) -> None:
        super().send(message, sender, recipients)
        raise self._exc


class FailingSMTPTestClient(FakeSMTPClient):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def test_connection(self) -> None:
        super().test_connection()
        raise self._exc


class RecordingSMTPClientFactory:
    def __init__(
        self,
        client_factory: Callable[[], FakeSMTPClient] = FakeSMTPClient,
    ) -> None:
        self._client_factory = client_factory
        self.clients: list[FakeSMTPClient] = []

    def __call__(self, config: SMTPConfig) -> FakeSMTPClient:
        client = self._client_factory()
        self.clients.append(client)
        return client


def _runtime(
    *,
    cache_dir: Path,
    factory: RecordingSMTPClientFactory | None = None,
    runtime_cls: type[SMTPRuntime] = SMTPRuntime,
) -> SMTPRuntime:
    return runtime_cls(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={
            "bot": SMTPServicePolicyConfig(
                idempotency=SMTPIdempotencyConfig(cache_dir=str(cache_dir)),
            )
        },
        smtp_client_factory=factory or RecordingSMTPClientFactory(),
    )


def test_runtime_tests_accounts_without_sending(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop",
            "checks": ["connect", "ehlo", "noop", "tls"],
            "delivery": "skipped",
            "reason": "read-only SMTP account test does not send mail",
        }
    }

    assert len(factory.clients) == 1
    assert factory.clients[0].tested is True
    assert factory.clients[0].message is None


def test_runtime_reports_account_test_failure(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPTestClient(RuntimeError("login failed"))
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop",
            "error_type": "RuntimeError",
            "message": "login failed",
        }
    }


def test_send_email_replays_same_idempotency_key_from_persistent_cache(
    tmp_path: Path,
) -> None:
    first_factory = RecordingSMTPClientFactory()
    first_runtime = _runtime(cache_dir=tmp_path, factory=first_factory)

    first_result = first_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    second_factory = RecordingSMTPClientFactory()
    second_runtime = _runtime(cache_dir=tmp_path, factory=second_factory)
    second_result = second_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(first_factory.clients) == 1
    assert second_factory.clients == []
    assert second_result.message_id == first_result.message_id
    assert second_result.recipient_count == first_result.recipient_count
    assert second_result.idempotency_replayed is True


def test_send_email_rejects_idempotency_key_with_different_payload(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    with pytest.raises(ValueError, match="idempotency_key was reused"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Different subject",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    assert len(factory.clients) == 1


def test_send_email_without_idempotency_key_is_not_deduped(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )
    runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert len(factory.clients) == 2


def test_send_email_retries_after_idempotency_record_expires(tmp_path: Path) -> None:
    class ImmediatelyExpiringSMTPRuntime(SMTPRuntime):
        def _idempotency_expire_seconds(
            self,
            smtp_policy: SMTPServicePolicyConfig,
        ) -> int:
            return 0

    factory = RecordingSMTPClientFactory()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        runtime_cls=ImmediatelyExpiringSMTPRuntime,
    )

    first_result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )
    second_result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(factory.clients) == 2
    assert second_result.message_id != first_result.message_id
    assert second_result.idempotency_replayed is False


def test_send_email_clears_idempotency_reservation_after_rate_limit(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    policy = SMTPServicePolicyConfig(
        limits=SMTPLimitsConfig(max_messages_per_minute=0),
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
    )
    runtime = SMTPRuntime(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={"bot": policy},
        smtp_client_factory=factory,
    )

    with pytest.raises(ValueError, match="max_messages_per_minute"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    policy.limits.max_messages_per_minute = None
    result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(factory.clients) == 1
    assert result.idempotency_replayed is False


def test_send_email_keeps_idempotency_reservation_after_partial_acceptance(
    tmp_path: Path,
) -> None:
    refused = {"bcc@example.com": (550, b"Recipient rejected")}
    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(smtplib.SMTPRecipientsRefused(refused))
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            bcc=["bcc@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    with pytest.raises(ValueError, match="idempotency_key is already in progress"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            bcc=["bcc@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    assert len(factory.clients) == 1


def test_send_email_keeps_idempotency_reservation_after_server_disconnect(
    tmp_path: Path,
) -> None:
    failing_factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(smtplib.SMTPServerDisconnected("connection lost"))
    )
    failing_runtime = _runtime(cache_dir=tmp_path, factory=failing_factory)

    with pytest.raises(smtplib.SMTPServerDisconnected):
        failing_runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    success_factory = RecordingSMTPClientFactory()
    success_runtime = _runtime(cache_dir=tmp_path, factory=success_factory)
    with pytest.raises(ValueError, match="idempotency_key is already in progress"):
        success_runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    assert len(failing_factory.clients) == 1
    assert success_factory.clients == []


def test_send_email_clears_idempotency_reservation_when_all_recipients_refused(
    tmp_path: Path,
) -> None:
    refused = {"to@example.com": (550, b"Recipient rejected")}
    failing_factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(smtplib.SMTPRecipientsRefused(refused))
    )
    failing_runtime = _runtime(cache_dir=tmp_path, factory=failing_factory)

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        failing_runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    success_factory = RecordingSMTPClientFactory()
    success_runtime = _runtime(cache_dir=tmp_path, factory=success_factory)
    result = success_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(failing_factory.clients) == 1
    assert len(success_factory.clients) == 1
    assert result.idempotency_replayed is False


def test_runtime_rejects_invalid_idempotency_config(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="SMTP idempotency expiration_days must be positive: bot",
    ):
        SMTPRuntime(
            accounts={"primary": SMTPConfig(policy="bot")},
            policies={
                "bot": SMTPServicePolicyConfig(
                    idempotency=SMTPIdempotencyConfig(
                        expiration_days=0,
                        cache_dir=str(tmp_path),
                    ),
                )
            },
            smtp_client_factory=RecordingSMTPClientFactory(),
        )

    with pytest.raises(
        ValueError,
        match="SMTP idempotency cache_dir must be non-empty: bot",
    ):
        SMTPRuntime(
            accounts={"primary": SMTPConfig(policy="bot")},
            policies={
                "bot": SMTPServicePolicyConfig(
                    idempotency=SMTPIdempotencyConfig(cache_dir="  "),
                )
            },
            smtp_client_factory=RecordingSMTPClientFactory(),
        )
