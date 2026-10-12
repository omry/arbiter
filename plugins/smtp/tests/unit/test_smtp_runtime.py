from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import smtplib
import sqlite3

import pytest

from arbiter_server.errors import OperationError
from arbiter_server.services import operation_input_schema
from arbiter_server.storage import PluginStorage
from arbiter_smtp import (
    SendEmailInput,
    SMTPSentCopyError,
    SMTPRuntime,
    SentCopyDestination,
)
from arbiter_smtp.config import (
    MailTlsMode,
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
        policies={"bot": policy or SMTPServicePolicyConfig()},
        smtp_client_factory=factory or RecordingSMTPClientFactory(),
        plugin_storage=PluginStorage(plugin_name="smtp", root=cache_dir),
        sent_message_appender=sent_message_appender,
    )


def test_send_email_discovery_schema_describes_idempotency_key_for_agents() -> None:
    schema = operation_input_schema(SendEmailInput)

    properties = schema["properties"]
    assert isinstance(properties, dict)
    idempotency_schema = properties["idempotency_key"]
    assert isinstance(idempotency_schema, dict)
    description = idempotency_schema["description"]
    assert isinstance(description, str)
    assert "Generate a unique stable key" in description
    assert "reuse it only when retrying the same account" in description


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


def test_runtime_requires_password_before_authenticated_live_check(
    tmp_path: Path,
) -> None:
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        account=SMTPConfig(
            policy="bot",
            authenticate=True,
            username="user",
            password="",
        ),
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "configuration",
            "error_type": "ValueError",
            "message": "SMTP account missing password for live authentication check",
        }
    }
    assert factory.clients == []


def test_runtime_tests_required_sent_copy_destination(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()
    appender = RecordingSentMessageAppender()
    policy = SMTPServicePolicyConfig(
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
        sent_copy=SMTPSentCopyPolicyConfig(
            on_failure=SMTPSentCopyFailureMode.fail,
        ),
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory, policy=policy)

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency_sent_copy",
            "error_type": "SMTPSentCopyPreflightError",
            "message": "Required Sent-copy destination is unavailable.",
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
            "message": "SMTP account check failed.",
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
            "message": "SMTP authentication failed.",
        }
    }


def test_runtime_reports_idempotency_storage_test_failure(tmp_path: Path) -> None:
    factory = RecordingSMTPClientFactory()

    def failing_store_factory(cache_dir: str) -> SMTPIdempotencyStore:
        raise RuntimeError(f"cache not writable: {cache_dir}")

    runtime = SMTPRuntime(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={"bot": SMTPServicePolicyConfig()},
        smtp_client_factory=factory,
        idempotency_store_factory=failing_store_factory,
        plugin_storage=PluginStorage(plugin_name="smtp", root=tmp_path),
    )

    assert runtime.test_accounts() == {
        "primary": {
            "status": "failed",
            "stage": "connect_auth_noop_idempotency",
            "error_type": "RuntimeError",
            "message": "SMTP account check failed.",
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
    appender = RecordingSentMessageAppender(
        append_error=OperationError(
            "upstream_rejected",
            "APPEND rejected",
            plugin="imap",
            domain_code="command_rejected",
        )
    )
    policy = SMTPServicePolicyConfig(
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
        "reason": "Sent-copy operation failed.",
        "error_type": "OperationError",
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
        append_error=OperationError(
            "upstream_rejected",
            "APPEND rejected",
            plugin="imap",
            domain_code="command_rejected",
        )
    )
    first_policy = SMTPServicePolicyConfig(
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
    )
    runtime = SMTPRuntime(
        accounts={"primary": SMTPConfig(policy="bot")},
        policies={"bot": policy},
        smtp_client_factory=factory,
        plugin_storage=PluginStorage(plugin_name="smtp", root=tmp_path),
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


@pytest.mark.parametrize("exception", [TimeoutError("secret"), OSError("secret")])
def test_uncertain_submission_retains_reservation(tmp_path, exception):
    factory = RecordingSMTPClientFactory(lambda: FailingSMTPClient(exception))
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    args = dict(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="stable",
    )
    with pytest.raises(type(exception)):
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="body",
            idempotency_key="stable",
        )
    with pytest.raises(OperationError) as caught:
        runtime.send_email(
            account="primary",
            to=["to@example.com"],
            subject="Hello",
            text_body="body",
            idempotency_key="stable",
        )
    assert caught.value.code == "conflict"
    assert len(factory.clients) == 1


def test_uncertain_sent_copy_is_not_repeated_on_key_replay(tmp_path):
    appender = RecordingSentMessageAppender(append_error=TimeoutError("secret"))
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        sent_message_appender=appender,
        policy=SMTPServicePolicyConfig(
            sent_copy=SMTPSentCopyPolicyConfig(on_failure=SMTPSentCopyFailureMode.fail)
        ),
    )
    args = dict(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="stable",
    )
    for _ in range(2):
        with pytest.raises(OperationError) as caught:
            runtime.send_email(
                account="primary",
                to=["to@example.com"],
                subject="Hello",
                text_body="body",
                idempotency_key="stable",
            )
        assert caught.value.details["steps"] == {
            "smtp_submission": "succeeded",
            "sent_copy": "unknown",
        }
        assert caught.value.replay == "unknown"
    assert len(factory.clients) == 1
    assert len(appender.appended) == 1


def test_uncertain_sent_copy_retry_is_persisted_before_error(tmp_path):
    appender = RecordingSentMessageAppender(
        append_error=OperationError(
            "upstream_rejected",
            "rejected",
            plugin="imap",
            domain_code="command_rejected",
        )
    )
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        sent_message_appender=appender,
        policy=SMTPServicePolicyConfig(
            sent_copy=SMTPSentCopyPolicyConfig(on_failure=SMTPSentCopyFailureMode.fail)
        ),
    )
    for attempt in range(3):
        if attempt:
            appender.append_error = TimeoutError("private")
        with pytest.raises(OperationError) as caught:
            runtime.send_email(
                account="primary",
                to=["a@example.com"],
                subject="test",
                text_body="test",
                idempotency_key="stable",
            )
        assert caught.value.details["steps"]["sent_copy"] == (
            "failed" if attempt == 0 else "unknown"
        )
    assert len(factory.clients) == 1
    assert len(appender.appended) == 2


def test_sent_copy_replay_record_failure_reports_completed_steps(tmp_path, monkeypatch):
    appender = RecordingSentMessageAppender(
        append_error=OperationError(
            "upstream_rejected",
            "rejected",
            plugin="imap",
            domain_code="command_rejected",
        )
    )
    factory = RecordingSMTPClientFactory()
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=factory,
        sent_message_appender=appender,
        policy=SMTPServicePolicyConfig(
            sent_copy=SMTPSentCopyPolicyConfig(on_failure=SMTPSentCopyFailureMode.fail)
        ),
    )
    with pytest.raises(OperationError):
        runtime.send_email(
            account="primary",
            to=["a@example.com"],
            subject="test",
            text_body="test",
            idempotency_key="stable",
        )
    appender.append_error = None

    def fail(*_args, **_kwargs):
        raise OSError("password=private")

    monkeypatch.setattr(
        runtime._idempotency_store(runtime._policies["bot"]), "store_success", fail
    )
    with pytest.raises(OperationError) as caught:
        runtime.send_email(
            account="primary",
            to=["a@example.com"],
            subject="test",
            text_body="test",
            idempotency_key="stable",
        )
    assert caught.value.code == "partial_failure"
    assert caught.value.domain_code == "idempotency_record_failed"
    assert caught.value.details["steps"] == {
        "smtp_submission": "succeeded",
        "sent_copy": "succeeded",
        "idempotency_record": "failed",
    }
    assert caught.value.replay == "unsafe"
    assert "private" not in str(caught.value.to_dict())
    assert len(factory.clients) == 1


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
                    idempotency=SMTPIdempotencyConfig(expiration_days=0),
                )
            },
            smtp_client_factory=RecordingSMTPClientFactory(),
        )


@pytest.mark.parametrize(
    "exception,code,domain",
    [
        (
            smtplib.SMTPAuthenticationError(535, b"password=secret"),
            "upstream_auth_failed",
            "authentication_failed",
        ),
        (TimeoutError("body=secret"), "upstream_timeout", "timeout"),
        (
            ConnectionRefusedError("password=secret"),
            "upstream_unavailable",
            "connection_failed",
        ),
        (
            smtplib.SMTPServerDisconnected("private-disconnected"),
            "upstream_unavailable",
            "connection_failed",
        ),
    ],
)
def test_plugin_owns_transport_classification(tmp_path, exception, code, domain):
    from arbiter_server.errors import OperationError
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    runtime = _runtime(
        cache_dir=tmp_path,
        factory=RecordingSMTPClientFactory(lambda: FailingSMTPClient(exception)),
    )
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))
    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["recipient@example.com"],
                "subject": "hello",
                "text_body": "body",
            },
            context,
        )
    error = caught.value.to_dict()["error"]
    assert error["code"] == code
    assert error["domain_code"] == domain
    assert "secret" not in str(error)


def test_partial_sent_copy_error_has_steps_and_key_specific_replay(tmp_path):
    from arbiter_server.errors import OperationError

    policy = SMTPServicePolicyConfig(
        sent_copy=SMTPSentCopyPolicyConfig(on_failure=SMTPSentCopyFailureMode.fail)
    )
    appender = RecordingSentMessageAppender(
        append_error=OperationError(
            "upstream_rejected",
            "APPEND rejected",
            plugin="imap",
            domain_code="command_rejected",
        )
    )
    for key in (None, "stable-key"):
        runtime = _runtime(
            cache_dir=tmp_path, policy=policy, sent_message_appender=appender
        )
        with pytest.raises(OperationError) as caught:
            runtime.send_email(
                account="primary",
                to=["recipient@example.com"],
                subject="hello",
                text_body="body",
                idempotency_key=key,
            )
        error = caught.value.to_dict()["error"]
        assert error["code"] == "partial_failure"
        assert error["details"] == {
            "steps": {
                "smtp_submission": "succeeded",
                "sent_copy": "failed",
            }
        }
        assert error["retry"] == {
            "transient": "unknown",
            "replay": "same_idempotency_key" if key else "unsafe",
        }
        assert "secret" not in str(error)


def test_plugin_distinguishes_partial_recipient_acceptance(tmp_path):
    from arbiter_server.errors import OperationError
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    runtime = _runtime(
        cache_dir=tmp_path,
        factory=RecordingSMTPClientFactory(
            lambda: FailingSMTPClient(
                smtplib.SMTPRecipientsRefused(
                    {"refused@example.com": (550, b"secret body")}
                )
            )
        ),
    )
    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            {
                "account": "primary",
                "to": ["accepted@example.com", "refused@example.com"],
                "subject": "hello",
                "text_body": "body",
            },
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )
    assert caught.value.code == "partial_failure"
    assert caught.value.replay == "unsafe"
    assert caught.value.details["accepted_count"] == 1
    assert "secret" not in str(caught.value.to_dict())


@pytest.mark.parametrize(
    "recipient_field,recipients",
    [
        ("to", ["refused@example.com", ""]),
        ("cc", [""]),
        ("bcc", [""]),
    ],
)
def test_plugin_ignores_blank_recipients_when_classifying_refusal(
    tmp_path, recipient_field, recipients
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    runtime = _runtime(
        cache_dir=tmp_path,
        factory=RecordingSMTPClientFactory(
            lambda: FailingSMTPClient(
                smtplib.SMTPRecipientsRefused(
                    {"refused@example.com": (550, b"secret body")}
                )
            )
        ),
    )
    arguments = {
        "account": "primary",
        "to": ["refused@example.com"],
        "subject": "hello",
        "text_body": "body",
    }
    arguments[recipient_field] = recipients

    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            arguments,
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )

    error = caught.value.to_dict()["error"]
    assert error["code"] == "upstream_rejected"
    assert error["details"] == {
        "accepted_count": 0,
        "rejected_count": 1,
        "steps": {"smtp_submission": "failed"},
    }
    assert caught.value.replay == "safe"
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    "recipients,refused",
    [
        (
            ["refused@example.com", "refused@example.com"],
            {"refused@example.com": (550, b"secret body")},
        ),
        (
            ["Refused@example.com", "refused@example.com"],
            {"REFUSED@EXAMPLE.COM": (550, b"secret body")},
        ),
    ],
)
def test_plugin_classifies_duplicate_refusal_and_clears_reservation(
    tmp_path, recipients, refused
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    failing_factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(smtplib.SMTPRecipientsRefused(refused))
    )
    failing_runtime = _runtime(cache_dir=tmp_path, factory=failing_factory)
    arguments = {
        "account": "primary",
        "to": recipients,
        "subject": "hello",
        "text_body": "body",
        "idempotency_key": "duplicate-refusal",
    }
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": failing_runtime}))

    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation("send_email", arguments, context)

    assert caught.value.code == "upstream_rejected"
    assert caught.value.details == {
        "accepted_count": 0,
        "rejected_count": 1,
        "steps": {"smtp_submission": "failed"},
    }
    assert caught.value.replay == "safe"

    success_factory = RecordingSMTPClientFactory()
    success_runtime = _runtime(cache_dir=tmp_path, factory=success_factory)
    result = SMTPServicePlugin().invoke_operation(
        "send_email",
        arguments,
        ServicePluginContext(runtimes=RuntimeRegistry({"smtp": success_runtime})),
    )
    assert isinstance(result, dict)
    assert result["idempotency_replayed"] is False
    assert len(failing_factory.clients) == 1
    assert len(success_factory.clients) == 1


def test_submission_success_is_reported_when_retry_record_write_fails(
    tmp_path, monkeypatch
):
    from arbiter_server.errors import OperationError

    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    store = runtime._idempotency_store(runtime._policies["bot"])

    def fail(*_args, **_kwargs):
        raise OSError("password=secret")

    monkeypatch.setattr(store, "store_success", fail)
    with pytest.raises(OperationError) as caught:
        runtime.send_email(
            account="primary",
            to=["recipient@example.com"],
            subject="hello",
            text_body="body",
            idempotency_key="stable",
        )
    assert len(factory.clients) == 1
    assert caught.value.code == "partial_failure"
    assert caught.value.details["steps"] == {
        "smtp_submission": "succeeded",
        "idempotency_record": "failed",
    }
    assert caught.value.replay == "unsafe"
    assert "secret" not in str(caught.value.to_dict())


def test_warn_sent_copy_failure_does_not_expose_upstream_text(tmp_path):
    runtime = _runtime(
        cache_dir=tmp_path,
        sent_message_appender=RecordingSentMessageAppender(
            append_error=RuntimeError("password=secret-message-body")
        ),
    )
    result = runtime.send_email(
        account="primary",
        to=["recipient@example.com"],
        subject="hello",
        text_body="body",
    )
    assert result.sent_copy is not None and result.sent_copy["status"] == "unknown"
    assert "secret-message-body" not in str(result.sent_copy)


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPAuthenticationError(535, b"password=private-auth"),
        smtplib.SMTPServerDisconnected("body=private-upstream"),
        OSError("/srv/private-cache"),
        RuntimeError("password=private-unexpected"),
    ],
)
def test_account_checks_withhold_private_diagnostics(tmp_path, failure):
    runtime = _runtime(
        cache_dir=tmp_path,
        factory=RecordingSMTPClientFactory(lambda: FailingSMTPTestClient(failure)),
    )
    result = runtime.test_accounts()
    assert "private-" not in str(result)
    primary = result["primary"]
    assert isinstance(primary, dict)
    assert primary["status"] == "failed"


def test_operation_check_withholds_sent_copy_diagnostic(tmp_path):
    class FailingAppender(RecordingSentMessageAppender):
        def check_destination(self, *, account, folder):
            raise RuntimeError("password=private-sent-copy")

    runtime = _runtime(
        cache_dir=tmp_path,
        sent_message_appender=FailingAppender(),
        policy=SMTPServicePolicyConfig(
            sent_copy=SMTPSentCopyPolicyConfig(on_failure=SMTPSentCopyFailureMode.fail)
        ),
    )
    result = runtime.check_operation(
        "send_email",
        {
            "account": "primary",
            "to": ["to@example.com"],
            "subject": "Hello",
            "text_body": "Body",
        },
    )
    assert result["allowed"] is False
    assert "private-sent-copy" not in str(result)


def test_operation_check_withholds_unexpected_diagnostic(tmp_path, monkeypatch):
    runtime = _runtime(cache_dir=tmp_path)

    def fail(*_args, **_kwargs):
        raise RuntimeError("/srv/private-check-state")

    monkeypatch.setattr(runtime, "_resolve_context", fail)
    result = runtime.check_operation("send_email", {"account": "primary"})
    assert result["allowed"] is False
    assert "private-check-state" not in str(result)


@pytest.mark.parametrize(
    "failure,code",
    [
        (smtplib.SMTPAuthenticationError(535, b"private-auth"), "upstream_auth_failed"),
        (
            smtplib.SMTPSenderRefused(550, b"private-sender", "from@example.com"),
            "upstream_rejected",
        ),
        (smtplib.SMTPDataError(550, b"private-data"), "upstream_rejected"),
        (smtplib.SMTPResponseException(450, b"private-response"), "upstream_rejected"),
        (smtplib.SMTPNotSupportedError("private-capability"), "plugin_error"),
    ],
)
def test_definitive_rejection_allows_same_key_retry(tmp_path, failure, code):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    clients = iter([FailingSMTPClient(failure), FakeSMTPClient()])
    factory = RecordingSMTPClientFactory(lambda: next(clients))
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))
    plugin = SMTPServicePlugin()
    arguments = dict(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="stable",
    )
    with pytest.raises(OperationError) as caught:
        plugin.invoke_operation("send_email", arguments, context)
    assert caught.value.code == code
    assert caught.value.replay == "safe"
    assert "private-" not in str(caught.value.to_dict())
    result = plugin.invoke_operation("send_email", arguments, context)
    assert isinstance(result, dict) and result["ok"] is True
    assert result["idempotency_replayed"] is False
    assert len(factory.clients) == 2


@pytest.mark.parametrize("partial", [False, True])
def test_non_221_quit_preserves_submission_and_reservation(
    tmp_path, monkeypatch, partial
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin
    from arbiter_smtp.client import SMTPSubmissionClient

    submitted = []

    class QuitRejectingServer(smtplib.SMTP):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def ehlo(self, name=""):
            return 250, b"ok"

        def sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
            submitted.append(msg)
            return (
                {"refused@example.com": (550, b"private-recipient")} if partial else {}
            )

        def docmd(self, cmd, args=""):
            assert cmd.upper() == "QUIT"
            return 500, b"private-quit"

    monkeypatch.setattr("arbiter_smtp.client.smtplib.SMTP", QuitRejectingServer)
    runtime = SMTPRuntime(
        accounts={
            "primary": SMTPConfig(
                policy="bot", tls=MailTlsMode.none, authenticate=False
            )
        },
        policies={"bot": SMTPServicePolicyConfig()},
        smtp_client_factory=SMTPSubmissionClient,
        plugin_storage=PluginStorage(plugin_name="smtp", root=tmp_path),
    )
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))
    arguments = dict(
        account="primary",
        to=["accepted@example.com", "refused@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="stable",
    )
    if partial:
        with pytest.raises(OperationError) as caught:
            plugin.invoke_operation("send_email", arguments, context)
        assert caught.value.code == "partial_failure"
        assert caught.value.details["steps"] == {
            "smtp_submission": "partially_succeeded"
        }
        assert caught.value.replay == "unsafe"
        with pytest.raises(OperationError) as retried:
            plugin.invoke_operation("send_email", arguments, context)
        assert retried.value.domain_code == "idempotency_in_progress"
    else:
        first = plugin.invoke_operation("send_email", arguments, context)
        replayed = plugin.invoke_operation("send_email", arguments, context)
        assert isinstance(first, dict) and isinstance(replayed, dict)
        assert first["ok"] is True and replayed["idempotency_replayed"] is True
        assert replayed["message_id"] == first["message_id"]
    assert len(submitted) == 1


@pytest.mark.parametrize("stage", ["open", "reserve", "read"])
@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("/private/cache-secret"),
        sqlite3.OperationalError("private-database"),
    ],
)
def test_local_retry_storage_failure_has_plugin_classification(
    tmp_path, monkeypatch, stage, failure
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)

    def fail(*args, **kwargs):
        raise failure

    if stage == "open":
        monkeypatch.setattr(runtime, "_idempotency_store_factory", fail)
    else:
        store = runtime._idempotency_store(runtime._policies["bot"])
        if stage == "reserve":
            monkeypatch.setattr(store, "add_pending", fail)
        else:
            monkeypatch.setattr(store, "add_pending", lambda *a, **k: False)
            monkeypatch.setattr(store, "get", fail)
    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            dict(
                account="primary",
                to=["to@example.com"],
                subject="Hello",
                text_body="body",
                idempotency_key="stable",
            ),
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )
    assert caught.value.code == "plugin_error"
    assert caught.value.domain_code == "idempotency_storage_failed"
    assert caught.value.replay == "unknown"
    assert caught.value.transient == "unknown"
    assert "private-" not in str(caught.value.to_dict())
    assert factory.clients == []


@pytest.mark.parametrize(
    "raw_record",
    [
        '{"payload_hash":',
        '{"payload_hash": 42}',
        {"payload_hash": "private"},
    ],
    ids=["malformed-json", "invalid-json-shape", "invalid-stored-shape"],
)
def test_persisted_invalid_retry_record_has_plugin_classification(
    tmp_path: Path, raw_record: object
) -> None:
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    store = runtime._idempotency_store(runtime._policies["bot"])
    cache_key = "smtp:primary:stable"
    store._cache.set(cache_key, raw_record)

    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            dict(
                account="primary",
                to=["to@example.com"],
                subject="Hello",
                text_body="body",
                idempotency_key="stable",
            ),
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )

    assert caught.value.code == "plugin_error"
    assert caught.value.plugin == "smtp"
    assert caught.value.domain_code == "idempotency_storage_failed"
    assert caught.value.replay == "unknown"
    assert caught.value.transient == "unknown"
    assert "private" not in str(caught.value.to_dict())
    assert factory.clients == []
    assert store._cache.get(cache_key) == raw_record


@pytest.mark.parametrize(
    "failure",
    [PermissionError("private-cleanup"), sqlite3.OperationalError("private-cleanup")],
)
def test_local_reservation_cleanup_failure_has_plugin_classification(
    tmp_path, monkeypatch, failure
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(smtplib.SMTPAuthenticationError(535, b"private-auth"))
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    store = runtime._idempotency_store(runtime._policies["bot"])

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(store, "delete", fail)
    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            dict(
                account="primary",
                to=["to@example.com"],
                subject="Hello",
                text_body="body",
                idempotency_key="stable",
            ),
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )
    assert caught.value.code == "plugin_error"
    assert caught.value.domain_code == "idempotency_storage_failed"
    assert caught.value.replay == "unknown"
    assert caught.value.transient == "unknown"
    assert "private-" not in str(caught.value.to_dict())
    assert len(factory.clients) == 1


@pytest.mark.parametrize("field", ["subject", "to", "cc", "bcc"])
@pytest.mark.parametrize("newline", ["\r", "\n"])
def test_header_newlines_have_typed_validation_and_failed_preflight(
    tmp_path, field, newline
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    factory = RecordingSMTPClientFactory()
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    arguments = dict(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="header-test",
    )
    injected = f"private-header@example.com{newline}Bcc: other@example.com"
    arguments[field] = injected if field == "subject" else [injected]
    with pytest.raises(OperationError) as caught:
        SMTPServicePlugin().invoke_operation(
            "send_email",
            arguments,
            ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime})),
        )
    assert caught.value.code == "validation_error"
    assert caught.value.replay == "safe"
    assert "private-header" not in str(caught.value.to_dict())
    preflight = runtime.check_operation("send_email", arguments)
    assert preflight["allowed"] is False
    assert "private-header" not in str(preflight)
    assert factory.clients == []
    assert (
        runtime._idempotency_store(runtime._policies["bot"]).get(
            "smtp:primary:header-test"
        )
        is None
    )


@pytest.mark.parametrize("custom_subclass", [False, True])
def test_generic_protocol_failure_uses_fallback_and_retains_reservation(
    tmp_path, custom_subclass
):
    from arbiter_server.services import RuntimeRegistry, ServicePluginContext
    from arbiter_smtp import SMTPServicePlugin

    class CustomProtocolError(smtplib.SMTPException):
        pass

    error_type = CustomProtocolError if custom_subclass else smtplib.SMTPException
    factory = RecordingSMTPClientFactory(
        lambda: FailingSMTPClient(error_type("private-protocol"))
    )
    runtime = _runtime(cache_dir=tmp_path, factory=factory)
    plugin = SMTPServicePlugin()
    context = ServicePluginContext(runtimes=RuntimeRegistry({"smtp": runtime}))
    arguments = dict(
        account="primary",
        to=["to@example.com"],
        subject="Hello",
        text_body="body",
        idempotency_key="stable",
    )
    with pytest.raises(OperationError) as caught:
        plugin.invoke_operation("send_email", arguments, context)
    assert caught.value.code == "plugin_error"
    assert caught.value.domain_code == "protocol_failed"
    assert caught.value.transient == "unknown"
    assert caught.value.replay == "unknown"
    assert "private-protocol" not in str(caught.value.to_dict())
    with pytest.raises(OperationError) as retried:
        plugin.invoke_operation("send_email", arguments, context)
    assert retried.value.domain_code == "idempotency_in_progress"
    assert len(factory.clients) == 1
