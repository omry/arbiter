from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import smtplib

import pytest

from arbiter_server.storage import PluginStorage
from arbiter_smtp import (
    SMTPSentCopyError,
    SMTPRuntime,
    SentCopyDestination,
)
from arbiter_smtp.config import (
    SMTPConfig,
    SMTPIdempotencyConfig,
    SMTPLimitsConfig,
    SMTPSentCopyAccountConfig,
    SMTPSentCopyFailureMode,
    SMTPSentCopyPolicyConfig,
    SMTPServicePolicyConfig,
)
from arbiter_smtp.idempotency import SMTPIdempotencyStore


class FakeSMTPClient:
    def __init__(self) -> None:
        self.message_bytes: bytes | None = None
        self.sender: str | None = None
        self.recipients: list[str] | None = None
        self.tested = False

    def test_connection(self) -> None:
        self.tested = True

    def send(
        self,
        message_bytes: bytes,
        sender: str,
        recipients: list[str],
    ) -> None:
        self.message_bytes = message_bytes
        self.sender = sender
        self.recipients = recipients


class FailingSMTPClient(FakeSMTPClient):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def send(
        self,
        message_bytes: bytes,
        sender: str,
        recipients: list[str],
    ) -> None:
        super().send(message_bytes, sender, recipients)
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


class RecordingSentMessageAppender:
    def __init__(
        self,
        *,
        destination: SentCopyDestination | None = None,
        append_error: Exception | None = None,
    ) -> None:
        self.destination = destination or SentCopyDestination(
            account="primary",
            folder="Sent",
        )
        self.append_error = append_error
        self.checked: list[dict[str, object]] = []
        self.resolved: list[dict[str, object]] = []
        self.appended: list[dict[str, object]] = []

    def _destination_for(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination:
        if folder is not None:
            return SentCopyDestination(account=account, folder=folder)
        return self.destination

    def check_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination:
        self.checked.append({"account": account, "folder": folder})
        return self._destination_for(account=account, folder=folder)

    def resolve_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination:
        self.resolved.append({"account": account, "folder": folder})
        return self._destination_for(account=account, folder=folder)

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None:
        self.appended.append(
            {
                "account": account,
                "folder": folder,
                "message_bytes": message_bytes,
            }
        )
        if self.append_error is not None:
            raise self.append_error


def _runtime(
    *,
    cache_dir: Path,
    factory: RecordingSMTPClientFactory | None = None,
    runtime_cls: type[SMTPRuntime] = SMTPRuntime,
    account: SMTPConfig | None = None,
    policy: SMTPServicePolicyConfig | None = None,
    sent_message_appender: RecordingSentMessageAppender | None = None,
) -> SMTPRuntime:
    return runtime_cls(
        accounts={"primary": account or SMTPConfig(policy="bot")},
        policies={
            "bot": policy
            or SMTPServicePolicyConfig(
                idempotency=SMTPIdempotencyConfig(cache_dir=str(cache_dir))
            )
        },
        smtp_client_factory=factory or RecordingSMTPClientFactory(),
        sent_message_appender=sent_message_appender,
    )


def test_runtime_tests_accounts_without_sending(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    progress_calls: list[str] = []

    assert runtime.test_accounts(progress=progress_calls.append) == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_idempotency",
            "checks": ["connect", "ehlo", "noop", "tls", "idempotency_storage"],
            "delivery": "skipped",
            "reason": "read-only SMTP account test does not send mail",
        }
    }

    assert len(factory.clients) == 1
    assert factory.clients[0].tested is True
    assert progress_calls == ["primary"]


def test_runtime_tests_required_sent_copy_destination(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    appender = RecordingSentMessageAppender()
    policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        policy=policy,
        sent_message_appender=appender,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "ok",
            "stage": "connect_auth_noop_idempotency_sent_copy",
            "checks": [
                "connect",
                "ehlo",
                "noop",
                "tls",
                "idempotency_storage",
                "sent_copy_destination",
            ],
            "delivery": "skipped",
            "reason": "read-only SMTP account test does not send mail",
        }
    }
    assert appender.resolved == [{"account": "primary", "folder": None}]


def test_runtime_tests_required_sent_copy_destination_failure(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory, policy=policy)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency_sent_copy",
            "error_type": "RuntimeError",
            "message": (
                "send_email sent-copy preflight failed: "
                "IMAP sent-copy appender is not configured"
            ),
        }
    }


def test_send_email_uses_plugin_storage_for_default_idempotency_cache(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = SMTPRuntime(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={"bot": SMTPServicePolicyConfig()},
        smtp_client_factory=factory,
        plugin_storage=PluginStorage(plugin_name="smtp", root=tmp_path),
    )

    runtime.send_email(
        account="primary",
        to=["recipient@example.com"],
        subject="Hello",
        text_body="Body",
        idempotency_key="send-1",
    )

    assert (tmp_path / "smtp" / "idempotency").exists()
    assert factory.clients[0].message_bytes is not None


def test_runtime_reports_account_test_failure(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPTestClient(RuntimeError("login failed"))
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency",
            "error_type": "RuntimeError",
            "message": "login failed",
        }
    }


def test_runtime_reports_smtp_authentication_failure_readably(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPTestClient(
            smtplib.SMTPAuthenticationError(
                535,
                b"5.7.8 Error: authentication failed: (reason unavailable)",
            )
        )
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency",
            "error_type": "SMTPAuthenticationError",
            "message": (
                "SMTP authentication failed (535): "
                "5.7.8 Error: authentication failed: (reason unavailable)"
            ),
        }
    }


def test_runtime_reports_idempotency_storage_test_failure(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()

    def failing_store_factory(cache_dir: str) -> SMTPIdempotencyStore:
        raise RuntimeError(f"cache not writable: {cache_dir}")

    runtime = SMTPRuntime(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={
            "bot": SMTPServicePolicyConfig(
                idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
            )
        },
        smtp_client_factory=factory,
        idempotency_store_factory=failing_store_factory,
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency",
            "error_type": "RuntimeError",
            "message": f"cache not writable: {tmp_path}",
        }
    }
    assert factory.clients[0].tested is True


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


def test_send_email_saves_sent_copy_after_smtp_success(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    appender = RecordingSentMessageAppender()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        sent_message_appender=appender,
    )

    result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        bcc=["secret@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert result.sent_copy == {
        "status": "saved",
        "account": "primary",
        "folder": "Sent",
    }
    assert len(factory.clients) == 1
    assert appender.resolved == [{"account": "primary", "folder": None}]
    assert len(appender.appended) == 1
    message_bytes = appender.appended[0]["message_bytes"]
    assert isinstance(message_bytes, bytes)
    assert b"Subject: Hello" in message_bytes
    assert b"Date:" in message_bytes
    assert b"Message-ID:" in message_bytes
    assert b"Bcc:" not in message_bytes
    assert factory.clients[0].message_bytes == message_bytes
    assert factory.clients[0].recipients == ["to@example.com", "secret@example.com"]


def test_send_email_uses_sent_copy_folder_override(tmp_path: Path) -> None:
    appender = RecordingSentMessageAppender()
    runtime = _runtime(
        cache_dir=tmp_path,
        account=SMTPConfig(
            policy="bot",
            sent_copy=SMTPSentCopyAccountConfig(folder="Sent Messages"),
        ),
        sent_message_appender=appender,
    )

    result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert appender.resolved == [{"account": "primary", "folder": "Sent Messages"}]
    assert result.sent_copy == {
        "status": "saved",
        "account": "primary",
        "folder": "Sent Messages",
    }


def test_send_email_skips_sent_copy_without_appender_by_default(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    result = runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
    )

    assert len(factory.clients) == 1
    assert result.sent_copy == {
        "status": "skipped",
        "account": "primary",
        "reason": "IMAP sent-copy appender is not configured",
    }


def test_send_email_required_sent_copy_fails_before_smtp_when_unresolved(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory, policy=policy)

    with pytest.raises(RuntimeError, match="sent-copy preflight failed"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
        )

    assert factory.clients == []


def test_send_email_required_sent_copy_preflight_clears_idempotency_reservation(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory, policy=policy)

    with pytest.raises(RuntimeError, match="sent-copy preflight failed"):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    appender = RecordingSentMessageAppender()
    retry_runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        policy=policy,
        sent_message_appender=appender,
    )
    result = retry_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(factory.clients) == 1
    assert result.idempotency_replayed is False
    assert result.sent_copy == {
        "status": "saved",
        "account": "primary",
        "folder": "Sent",
    }


def test_send_email_required_sent_copy_append_failure_reports_submitted_message(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    appender = RecordingSentMessageAppender(append_error=RuntimeError("append failed"))
    policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        policy=policy,
        sent_message_appender=appender,
    )

    with pytest.raises(SMTPSentCopyError) as exc_info:
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
        )

    assert len(factory.clients) == 1
    assert exc_info.value.result.sent_copy == {
        "status": "failed",
        "account": "primary",
        "folder": "Sent",
        "reason": "append failed",
        "error_type": "RuntimeError",
    }


def test_send_email_idempotency_replay_does_not_append_sent_copy_twice(
    tmp_path: Path,
) -> None:
    first_appender = RecordingSentMessageAppender()
    first_runtime = _runtime(
        cache_dir=tmp_path,
        sent_message_appender=first_appender,
    )

    first_result = first_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    second_factory = RecordingSMTPClientFactory()
    second_appender = RecordingSentMessageAppender()
    second_runtime = _runtime(
        cache_dir=tmp_path,
        factory=second_factory,
        sent_message_appender=second_appender,
    )
    second_result = second_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(first_appender.appended) == 1
    assert second_factory.clients == []
    assert second_appender.appended == []
    assert second_result.idempotency_replayed is True
    assert second_result.sent_copy == first_result.sent_copy


def test_send_email_idempotency_replay_retries_failed_sent_copy_without_resend(
    tmp_path: Path,
) -> None:
    first_factory = RecordingSMTPClientFactory()
    first_appender = RecordingSentMessageAppender(
        append_error=RuntimeError("append failed")
    )
    first_policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    first_runtime = _runtime(
        cache_dir=tmp_path,
        factory=first_factory,
        policy=first_policy,
        sent_message_appender=first_appender,
    )

    with pytest.raises(SMTPSentCopyError):
        first_runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="Plain body",
            idempotency_key="send-1",
        )

    second_factory = RecordingSMTPClientFactory()
    second_appender = RecordingSentMessageAppender()
    second_policy = SMTPServicePolicyConfig(
        idempotency=SMTPIdempotencyConfig(cache_dir=str(tmp_path)),
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    second_runtime = _runtime(
        cache_dir=tmp_path,
        factory=second_factory,
        policy=second_policy,
        sent_message_appender=second_appender,
    )

    result = second_runtime.send_email(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="Plain body",
        idempotency_key="send-1",
    )

    assert len(first_factory.clients) == 1
    assert second_factory.clients == []
    assert len(first_appender.appended) == 1
    assert len(second_appender.appended) == 1
    assert result.idempotency_replayed is True
    assert result.sent_copy == {
        "status": "saved",
        "account": "primary",
        "folder": "Sent",
    }
    assert (
        second_appender.appended[0]["message_bytes"]
        == first_factory.clients[0].message_bytes
    )


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
