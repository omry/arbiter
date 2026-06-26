from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy as email_policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
import hashlib
import json
import secrets
import smtplib
from time import monotonic
from typing import Callable, Protocol, cast

from hydra.core.config_store import ConfigStore

from arbiter_server.services import (
    CapabilityDescriptor,
    ConfigCheckError,
    ConfigCheckIssue,
    OperationDescriptor,
    ServicePluginContext,
    ServiceRuntimeContext,
)
from arbiter_server.storage import PluginStorage
from arbiter_server.version import distribution_version

from .config import (
    SMTPConfig,
    SMTPServicePolicyConfig,
    register_configs as register_smtp_configs,
)
from .idempotency import SMTPIdempotencyResult, SMTPIdempotencyStore
from .runtime_policy import _SMTPRuntimePolicyMixin

SERVER_API_VERSION = "0.9"


@dataclass(frozen=True)
class SendEmailResult:
    tool: str
    message_id: str
    recipient_count: int
    sent_copy: dict[str, object] | None = None
    idempotency_replayed: bool = False


@dataclass(frozen=True)
class SentCopyDestination:
    account: str
    folder: str


class SMTPSentCopyError(RuntimeError):
    def __init__(self, message: str, *, result: SendEmailResult) -> None:
        super().__init__(message)
        self.result = result


class SMTPClientProtocol(Protocol):
    def test_connection(self) -> None: ...

    def send(
        self,
        message_bytes: bytes,
        sender: str,
        recipients: list[str],
    ) -> None: ...


class SentMessageAppender(Protocol):
    def check_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination: ...

    def resolve_destination(
        self,
        *,
        account: str,
        folder: str | None,
    ) -> SentCopyDestination: ...

    def append_sent_message(
        self,
        *,
        account: str,
        folder: str,
        message_bytes: bytes,
    ) -> None: ...


SMTPClientFactory = Callable[[SMTPConfig], SMTPClientProtocol]
TimeProvider = Callable[[], float]
SMTPIdempotencyStoreFactory = Callable[[str], SMTPIdempotencyStore]


SEND_EMAIL_DESCRIPTION = (
    "Send a single email message through the configured SMTP submission server "
    "for the selected account. Use at least one recipient in to and at least "
    "one of text_body or html_body."
)


def _smtp_response_text(response: object) -> str:
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="replace")
    return str(response)


def _format_smtp_exception(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            f"SMTP authentication failed ({exc.smtp_code}): "
            f"{_smtp_response_text(exc.smtp_error)}"
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"SMTP error {exc.smtp_code}: {_smtp_response_text(exc.smtp_error)}"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        detail = str(exc)
        return (
            "SMTP server disconnected"
            if not detail
            else f"SMTP server disconnected: {detail}"
        )
    return str(exc)


def _smtp_live_auth_error(smtp_config: SMTPConfig) -> str | None:
    if not smtp_config.authenticate:
        return None
    has_username = bool(smtp_config.username.strip())
    has_password = bool(smtp_config.password)
    if has_username and not has_password:
        return "SMTP account missing password for live authentication check"
    if has_password and not has_username:
        return "SMTP account missing username for live authentication check"
    if not has_username and not has_password:
        return (
            "SMTP account missing username and password for live authentication check"
        )
    return None


@dataclass(frozen=True)
class SendEmailInput:
    account: str = field(
        metadata={"description": "Configured SMTP account name."},
    )
    to: list[str] = field(
        metadata={"description": "Primary recipient email addresses."},
    )
    subject: str = field(metadata={"description": "Email subject line."})
    text_body: str | None = field(
        default=None,
        metadata={"description": "Plain text body."},
    )
    html_body: str | None = field(
        default=None,
        metadata={"description": "HTML body."},
    )
    cc: list[str] | None = field(
        default=None,
        metadata={"description": "CC recipient email addresses."},
    )
    bcc: list[str] | None = field(
        default=None,
        metadata={"description": "BCC recipient email addresses."},
    )
    idempotency_key: str | None = field(
        default=None,
        metadata={"description": "Optional caller-supplied key for retry-safe dedupe."},
    )


class SMTPRuntime(_SMTPRuntimePolicyMixin):
    service_name = "smtp"

    def __init__(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        smtp_client_factory: SMTPClientFactory,
        time_provider: TimeProvider = monotonic,
        idempotency_store_factory: SMTPIdempotencyStoreFactory = SMTPIdempotencyStore,
        plugin_storage: PluginStorage | None = None,
        sent_message_appender: SentMessageAppender | None = None,
    ) -> None:
        self._accounts = cast(Mapping[str, SMTPConfig], accounts)
        self._policies = cast(
            Mapping[str, SMTPServicePolicyConfig],
            policies,
        )
        self._smtp_client_factory = smtp_client_factory
        self._time_provider = time_provider
        self._idempotency_store_factory = idempotency_store_factory
        self._plugin_storage = plugin_storage
        self._sent_message_appender = sent_message_appender
        self._idempotency_stores: dict[str, SMTPIdempotencyStore] = {}
        self._attempt_timestamps: dict[str, list[float]] = {}
        self._validate_config()

    def configure_sent_message_appender(
        self,
        sent_message_appender: SentMessageAppender,
    ) -> None:
        self._sent_message_appender = sent_message_appender

    def account_summaries(self) -> dict[str, object]:
        return {
            account_name: {
                "description": account.description,
                "guidance": account.guidance,
                "policy": account.policy,
                "enabled": True,
                "send": "allowed",
            }
            for account_name, account in sorted(self._accounts.items())
        }

    def test_accounts(
        self,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        results: dict[str, object] = {}
        for account_name, smtp_config in sorted(self._accounts.items()):
            if progress is not None:
                progress(account_name)
            smtp_policy = self._policies[smtp_config.policy]
            stage = "connect_auth_noop_idempotency"
            if auth_error := _smtp_live_auth_error(smtp_config):
                results[account_name] = {
                    "status": "failed",
                    "stage": "configuration",
                    "error_type": "ValueError",
                    "message": auth_error,
                }
                continue
            strict_sent_copy = self._sent_copy_requires_readiness_check(smtp_policy)
            try:
                self._smtp_client_factory(smtp_config).test_connection()
                self._test_idempotency_storage(smtp_policy)
                if strict_sent_copy:
                    stage = "connect_auth_noop_idempotency_sent_copy"
                    self._test_sent_copy_destination(
                        account=account_name,
                        smtp_config=smtp_config,
                        smtp_policy=smtp_policy,
                    )
            except Exception as exc:
                results[account_name] = {
                    "status": "failed",
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "message": _format_smtp_exception(exc),
                }
                continue
            checks = ["connect", "ehlo", "noop"]
            if smtp_config.tls.value != "none":
                checks.append("tls")
            if smtp_config.authenticate:
                checks.append("authenticate")
            checks.append("idempotency_storage")
            if strict_sent_copy:
                checks.append("sent_copy_destination")
            results[account_name] = {
                "status": "ok",
                "stage": stage,
                "checks": checks,
                "delivery": "skipped",
                "reason": "read-only SMTP account test does not send mail",
            }
        return results

    def send_email(
        self,
        account: str,
        to: list[str],
        subject: str,
        text_body: str | None = None,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> SendEmailResult:
        smtp_config, smtp_policy = self._resolve_context(account)
        recipients_to = self._normalize_recipients("to", to)
        recipients_cc = self._normalize_recipients("cc", cc or [])
        recipients_bcc = self._normalize_recipients("bcc", bcc or [])

        if not text_body and not html_body:
            raise ValueError("send_email requires text_body or html_body")

        normalized_subject = subject.strip()
        if not normalized_subject:
            raise ValueError("send_email requires a non-empty subject")

        sender = formataddr((smtp_config.from_name, smtp_config.from_email))
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipients_to)
        if recipients_cc:
            message["Cc"] = ", ".join(recipients_cc)
        message["Subject"] = normalized_subject
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Message-ID"] = make_msgid(domain=self._sender_domain(smtp_config))

        if text_body:
            message.set_content(text_body)
            if html_body:
                message.add_alternative(html_body, subtype="html")
        else:
            message.set_content(html_body or "", subtype="html")
        message_bytes = message.as_bytes(policy=email_policy.SMTP)

        envelope_recipients = recipients_to + recipients_cc + recipients_bcc
        self._enforce_policy(account, smtp_policy, envelope_recipients)

        normalized_idempotency_key = self._normalize_idempotency_key(idempotency_key)
        payload_hash: str | None = None
        cache_key: str | None = None
        if normalized_idempotency_key is not None:
            payload_hash = self._idempotency_payload_hash(
                account=account,
                policy=smtp_config.policy,
                sender=smtp_config.from_email,
                sender_name=smtp_config.from_name,
                to=recipients_to,
                cc=recipients_cc,
                bcc=recipients_bcc,
                subject=normalized_subject,
                text_body=text_body,
                html_body=html_body,
            )
            cache_key = self._idempotency_cache_key(
                account=account,
                idempotency_key=normalized_idempotency_key,
            )
            replayed_result = self._reserve_or_replay_idempotency(
                smtp_policy,
                account=account,
                smtp_config=smtp_config,
                cache_key=cache_key,
                payload_hash=payload_hash,
            )
            if replayed_result is not None:
                return replayed_result

        sent_copy_destination: SentCopyDestination | None = None
        sent_copy_result: dict[str, object] | None = None
        try:
            sent_copy_destination, sent_copy_result = (
                self._resolve_sent_copy_destination(
                    account=account,
                    smtp_config=smtp_config,
                    smtp_policy=smtp_policy,
                    enforce_required=False,
                )
            )
            sent_copy_decision = self._sent_copy_preflight_decision(
                smtp_policy=smtp_policy,
                result=sent_copy_result,
            ).evaluate()
            if not sent_copy_decision.allowed:
                raise RuntimeError(
                    sent_copy_decision.why_not
                    or "send_email sent-copy preflight failed"
                )
            self._consume_rate_limit(account, smtp_policy)
            smtp_client = self._smtp_client_factory(smtp_config)
            smtp_client.send(
                message_bytes,
                sender=smtp_config.from_email,
                recipients=envelope_recipients,
            )
        except Exception as exc:
            if cache_key is not None and self._should_clear_idempotency_reservation(
                exc,
                envelope_recipients=envelope_recipients,
            ):
                self._idempotency_store(smtp_policy).delete(cache_key)
            raise

        if sent_copy_result is None and sent_copy_destination is not None:
            sent_copy_result = self._append_sent_copy(
                destination=sent_copy_destination,
                message_bytes=message_bytes,
            )
        if sent_copy_result is None:
            sent_copy_result = self._sent_copy_outcome(
                "skipped",
                account=account,
                reason="sent copy destination was not resolved",
            )

        result = SendEmailResult(
            tool="send_email",
            message_id=str(message["Message-ID"]),
            recipient_count=len(envelope_recipients),
            sent_copy=sent_copy_result,
        )
        if cache_key is not None and payload_hash is not None:
            self._idempotency_store(smtp_policy).store_success(
                cache_key,
                payload_hash=payload_hash,
                result=SMTPIdempotencyResult(
                    message_id=result.message_id,
                    recipient_count=result.recipient_count,
                    sent_copy=result.sent_copy,
                    sent_copy_message_bytes=self._sent_copy_retry_message_bytes(
                        sent_copy_result,
                        message_bytes,
                    ),
                ),
                expire_seconds=self._idempotency_expire_seconds(smtp_policy),
            )
        self._raise_if_submitted_sent_copy_required(result, smtp_policy)
        return result

    def check_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        operation_id = f"smtp:{operation}"
        if operation != "send_email":
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": f"unknown SMTP operation: {operation}",
            }
        account = cast(str, arguments.get("account"))
        try:
            smtp_config, smtp_policy = self._resolve_context(account)
            recipients_to = self._normalize_recipients(
                "to", cast(list[str], arguments.get("to"))
            )
            recipients_cc = self._normalize_recipients(
                "cc", cast(list[str] | None, arguments.get("cc")) or []
            )
            recipients_bcc = self._normalize_recipients(
                "bcc", cast(list[str] | None, arguments.get("bcc")) or []
            )
            text_body = cast(str | None, arguments.get("text_body"))
            html_body = cast(str | None, arguments.get("html_body"))
            if not text_body and not html_body:
                raise ValueError("send_email requires text_body or html_body")
            normalized_subject = cast(str, arguments.get("subject")).strip()
            if not normalized_subject:
                raise ValueError("send_email requires a non-empty subject")

            recipients = recipients_to + recipients_cc + recipients_bcc
            send_decision = self._send_policy_decision(
                account_name=account,
                smtp_policy=smtp_policy,
                recipients=recipients,
            ).evaluate()
            if not send_decision.allowed:
                return self._decision_check_result(operation_id, send_decision)

            sent_copy_result = self._check_sent_copy_destination(
                account=account,
                smtp_config=smtp_config,
                smtp_policy=smtp_policy,
            )
            return self._check_send_decision(
                operation_id=operation_id,
                account=account,
                smtp_policy=smtp_policy,
                recipients=recipients,
                sent_copy_result=sent_copy_result,
            )
        except Exception as exc:
            return {
                "operation": operation_id,
                "allowed": False,
                "why_not": str(exc),
            }

    def _resolve_context(
        self,
        account_name: str,
    ) -> tuple[SMTPConfig, SMTPServicePolicyConfig]:
        smtp_config = self._accounts.get(account_name)
        if smtp_config is None:
            raise ValueError(
                f"send_email requires an SMTP-enabled account: {account_name}"
            )

        smtp_policy = self._policies.get(smtp_config.policy)
        if smtp_policy is None:
            raise ValueError(
                f"send_email account references an unknown SMTP policy: {account_name}"
            )

        return smtp_config, smtp_policy

    def _check_sent_copy_destination(
        self,
        *,
        account: str,
        smtp_config: SMTPConfig,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> dict[str, object] | None:
        if not smtp_policy.sent_copy.enabled:
            return self._sent_copy_outcome("disabled", account=account)

        folder_override = self._normalize_sent_copy_folder(smtp_config.sent_copy.folder)
        if self._sent_message_appender is None:
            return self._sent_copy_outcome(
                "skipped",
                account=account,
                reason="IMAP sent-copy appender is not configured",
            )

        try:
            destination = self._sent_message_appender.check_destination(
                account=account,
                folder=folder_override,
            )
        except Exception as exc:
            return self._sent_copy_outcome(
                "skipped",
                account=account,
                reason=str(exc),
                error_type=type(exc).__name__,
            )

        return self._sent_copy_outcome(
            "resolved",
            account=destination.account,
            folder=destination.folder,
        )

    def _resolve_sent_copy_destination(
        self,
        *,
        account: str,
        smtp_config: SMTPConfig,
        smtp_policy: SMTPServicePolicyConfig,
        enforce_required: bool = True,
    ) -> tuple[SentCopyDestination | None, dict[str, object] | None]:
        if not smtp_policy.sent_copy.enabled:
            return None, self._sent_copy_outcome("disabled", account=account)

        folder_override = self._normalize_sent_copy_folder(smtp_config.sent_copy.folder)
        if self._sent_message_appender is None:
            result = self._sent_copy_outcome(
                "skipped",
                account=account,
                reason="IMAP sent-copy appender is not configured",
            )
            if enforce_required:
                self._raise_if_sent_copy_required(result, smtp_policy)
            return None, result

        try:
            destination = self._sent_message_appender.resolve_destination(
                account=account,
                folder=folder_override,
            )
        except Exception as exc:
            result = self._sent_copy_outcome(
                "skipped",
                account=account,
                reason=str(exc),
                error_type=type(exc).__name__,
            )
            if enforce_required:
                self._raise_if_sent_copy_required(result, smtp_policy)
            return None, result

        return destination, None

    def _append_sent_copy(
        self,
        *,
        destination: SentCopyDestination,
        message_bytes: bytes,
    ) -> dict[str, object]:
        if self._sent_message_appender is None:
            return self._sent_copy_outcome(
                "skipped",
                account=destination.account,
                folder=destination.folder,
                reason="IMAP sent-copy appender is not configured",
            )
        try:
            self._sent_message_appender.append_sent_message(
                account=destination.account,
                folder=destination.folder,
                message_bytes=message_bytes,
            )
        except Exception as exc:
            return self._sent_copy_outcome(
                "failed",
                account=destination.account,
                folder=destination.folder,
                reason=str(exc),
                error_type=type(exc).__name__,
            )
        return self._sent_copy_outcome(
            "saved",
            account=destination.account,
            folder=destination.folder,
        )

    def _raise_if_sent_copy_required(
        self,
        result: dict[str, object],
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        if smtp_policy.sent_copy.on_failure.value != "fail":
            return
        raise RuntimeError(f"send_email sent-copy preflight failed: {result['reason']}")

    def _normalize_sent_copy_folder(self, folder: str | None) -> str | None:
        if folder is None:
            return None
        normalized = folder.strip()
        if not normalized:
            raise ValueError("SMTP sent_copy.folder must be non-empty when configured")
        return normalized

    def _sent_copy_outcome(
        self,
        status: str,
        *,
        account: str | None = None,
        folder: str | None = None,
        reason: str | None = None,
        error_type: str | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {"status": status}
        if account is not None:
            result["account"] = account
        if folder is not None:
            result["folder"] = folder
        if reason is not None:
            result["reason"] = reason
        if error_type is not None:
            result["error_type"] = error_type
        return result

    def _sent_copy_retry_message_bytes(
        self,
        sent_copy_result: dict[str, object],
        message_bytes: bytes,
    ) -> bytes | None:
        if sent_copy_result.get("status") in {"failed", "skipped"}:
            return message_bytes
        return None

    def _sent_copy_needs_idempotent_retry(
        self,
        result: SMTPIdempotencyResult,
    ) -> bool:
        if result.sent_copy_message_bytes is None:
            return False
        if result.sent_copy is None:
            return True
        return result.sent_copy.get("status") in {"failed", "skipped"}

    def _retry_sent_copy_from_idempotency(
        self,
        *,
        account: str,
        smtp_config: SMTPConfig,
        smtp_policy: SMTPServicePolicyConfig,
        result: SMTPIdempotencyResult,
    ) -> SendEmailResult:
        sent_copy_result = result.sent_copy
        if result.sent_copy_message_bytes is not None:
            destination, resolved_result = self._resolve_sent_copy_destination(
                account=account,
                smtp_config=smtp_config,
                smtp_policy=smtp_policy,
                enforce_required=False,
            )
            sent_copy_result = resolved_result
            if destination is not None:
                sent_copy_result = self._append_sent_copy(
                    destination=destination,
                    message_bytes=result.sent_copy_message_bytes,
                )
        if sent_copy_result is None:
            sent_copy_result = self._sent_copy_outcome(
                "skipped",
                account=account,
                reason="sent copy destination was not resolved",
            )
        replayed_result = SendEmailResult(
            tool="send_email",
            message_id=result.message_id,
            recipient_count=result.recipient_count,
            sent_copy=sent_copy_result,
            idempotency_replayed=True,
        )
        self._raise_if_submitted_sent_copy_required(replayed_result, smtp_policy)
        return replayed_result

    def _raise_if_submitted_sent_copy_required(
        self,
        result: SendEmailResult,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        if smtp_policy.sent_copy.on_failure.value != "fail":
            return
        sent_copy_result = result.sent_copy or {}
        if sent_copy_result.get("status") in {"saved", "disabled"}:
            return
        reason = sent_copy_result.get("reason")
        suffix = f": {reason}" if reason else ""
        raise SMTPSentCopyError(
            "send_email submitted the SMTP message but failed to save "
            f"a sent copy{suffix}",
            result=result,
        )

    def _validate_config(self) -> None:
        for account_name, smtp_config in sorted(self._accounts.items()):
            if smtp_config.policy not in self._policies:
                raise ValueError(
                    "SMTP account references an unknown policy: "
                    f"{account_name} -> {smtp_config.policy}"
                )
            if (
                smtp_config.sent_copy.folder is not None
                and not smtp_config.sent_copy.folder.strip()
            ):
                raise ValueError(
                    f"SMTP sent_copy.folder must be non-empty: {account_name}"
                )
        for policy_name, smtp_policy in sorted(self._policies.items()):
            if smtp_policy.idempotency.expiration_days <= 0:
                raise ValueError(
                    "SMTP idempotency expiration_days must be positive: "
                    f"{policy_name}"
                )
            cache_dir = smtp_policy.idempotency.cache_dir
            if cache_dir is not None and not cache_dir.strip():
                raise ValueError(
                    f"SMTP idempotency cache_dir must be non-empty: {policy_name}"
                )
            if cache_dir is not None and self._plugin_storage is not None:
                self._plugin_storage.path(cache_dir.strip())

    def _normalize_recipients(
        self,
        field_name: str,
        recipients: list[str],
    ) -> list[str]:
        normalized = [
            recipient.strip() for recipient in recipients if recipient.strip()
        ]
        if field_name == "to" and not normalized:
            raise ValueError("send_email requires at least one recipient in to")

        for recipient in normalized:
            if "@" not in recipient:
                raise ValueError(f"send_email received an invalid {field_name} address")

        return normalized

    def _sender_domain(self, smtp_config: SMTPConfig) -> str:
        _, _, domain = smtp_config.from_email.partition("@")
        return domain or "localhost"

    def _policy_now(self) -> float:
        return self._time_provider()

    def _rate_limit_attempt_timestamps(self) -> dict[str, list[float]]:
        return self._attempt_timestamps

    def _enforce_policy(
        self,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
        recipients: list[str],
    ) -> None:
        self._send_policy_decision(
            account_name=account_name,
            smtp_policy=smtp_policy,
            recipients=recipients,
        ).evaluate().require_allowed()

    def _consume_rate_limit(
        self,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        self._rate_limit_decision(
            account_name, smtp_policy
        ).evaluate().require_allowed()
        max_messages = smtp_policy.limits.max_messages_per_minute
        if max_messages is None:
            return

        now = self._policy_now()
        window_start = now - 60.0
        attempt_timestamps = self._rate_limit_attempt_timestamps()
        active_attempts = [
            timestamp
            for timestamp in attempt_timestamps.get(account_name, [])
            if timestamp > window_start
        ]
        if len(active_attempts) >= max_messages:
            raise ValueError(
                f"send_email exceeds max_messages_per_minute for account: {account_name}"
            )

        active_attempts.append(now)
        attempt_timestamps[account_name] = active_attempts

    def _recipient_matches_list(
        self,
        recipient: str,
        configured_recipients: list[str],
    ) -> bool:
        normalized = recipient.lower()
        return any(
            normalized == value.strip().lower() for value in configured_recipients
        )

    def _domain_matches_any_pattern(self, domain: str, patterns: list[str]) -> bool:
        normalized_domain = domain.lower()
        for pattern in patterns:
            normalized_pattern = pattern.strip().lower()
            if normalized_pattern.startswith("*."):
                suffix = normalized_pattern[2:]
                if normalized_domain.endswith(f".{suffix}"):
                    return True
                continue
            if normalized_domain == normalized_pattern:
                return True
        return False

    def _normalize_idempotency_key(self, idempotency_key: str | None) -> str | None:
        if idempotency_key is None:
            return None
        normalized = idempotency_key.strip()
        if not normalized:
            raise ValueError("send_email idempotency_key must be non-empty")
        return normalized

    def _idempotency_payload_hash(
        self,
        *,
        account: str,
        policy: str,
        sender: str,
        sender_name: str,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        text_body: str | None,
        html_body: str | None,
    ) -> str:
        payload = {
            "account": account,
            "policy": policy,
            "sender": sender,
            "sender_name": sender_name,
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "text_body": text_body,
            "html_body": html_body,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def _idempotency_cache_key(self, *, account: str, idempotency_key: str) -> str:
        return f"smtp:{account}:{idempotency_key}"

    def _reserve_or_replay_idempotency(
        self,
        smtp_policy: SMTPServicePolicyConfig,
        *,
        account: str,
        smtp_config: SMTPConfig,
        cache_key: str,
        payload_hash: str,
    ) -> SendEmailResult | None:
        store = self._idempotency_store(smtp_policy)
        expire_seconds = self._idempotency_expire_seconds(smtp_policy)
        if store.add_pending(
            cache_key,
            payload_hash=payload_hash,
            expire_seconds=expire_seconds,
        ):
            return None

        record = store.get(cache_key)
        if record is None:
            if store.add_pending(
                cache_key,
                payload_hash=payload_hash,
                expire_seconds=expire_seconds,
            ):
                return None
            record = store.get(cache_key)
        if record is None:
            raise ValueError("send_email idempotency cache reservation failed")
        if record.payload_hash != payload_hash:
            raise ValueError(
                "send_email idempotency_key was reused with a different payload"
            )
        if record.result is None:
            raise ValueError("send_email idempotency_key is already in progress")
        if self._sent_copy_needs_idempotent_retry(record.result):
            replayed_result = self._retry_sent_copy_from_idempotency(
                account=account,
                smtp_config=smtp_config,
                smtp_policy=smtp_policy,
                result=record.result,
            )
            store.store_success(
                cache_key,
                payload_hash=payload_hash,
                result=SMTPIdempotencyResult(
                    message_id=replayed_result.message_id,
                    recipient_count=replayed_result.recipient_count,
                    sent_copy=replayed_result.sent_copy,
                    sent_copy_message_bytes=self._sent_copy_retry_message_bytes(
                        replayed_result.sent_copy or {},
                        record.result.sent_copy_message_bytes or b"",
                    ),
                ),
                expire_seconds=expire_seconds,
            )
            return replayed_result
        return SendEmailResult(
            tool="send_email",
            message_id=record.result.message_id,
            recipient_count=record.result.recipient_count,
            sent_copy=record.result.sent_copy,
            idempotency_replayed=True,
        )

    def _idempotency_store(
        self,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> SMTPIdempotencyStore:
        cache_dir = self._idempotency_cache_dir(smtp_policy)
        store = self._idempotency_stores.get(cache_dir)
        if store is None:
            store = self._idempotency_store_factory(cache_dir)
            self._idempotency_stores[cache_dir] = store
        return store

    def _test_idempotency_storage(
        self,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        store = self._idempotency_store(smtp_policy)
        key = f"__arbiter_readiness__:{secrets.token_urlsafe(16)}"
        payload_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        try:
            added = store.add_pending(
                key,
                payload_hash=payload_hash,
                expire_seconds=60,
            )
            if not added:
                raise ValueError("SMTP idempotency cache readiness key collided")
            record = store.get(key)
            if record is None or record.payload_hash != payload_hash:
                raise ValueError("SMTP idempotency cache readiness read failed")
        finally:
            store.delete(key)

    def _sent_copy_requires_readiness_check(
        self,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> bool:
        return (
            smtp_policy.sent_copy.enabled
            and smtp_policy.sent_copy.on_failure.value == "fail"
        )

    def _test_sent_copy_destination(
        self,
        *,
        account: str,
        smtp_config: SMTPConfig,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        self._resolve_sent_copy_destination(
            account=account,
            smtp_config=smtp_config,
            smtp_policy=smtp_policy,
            enforce_required=True,
        )

    def _idempotency_cache_dir(self, smtp_policy: SMTPServicePolicyConfig) -> str:
        configured_cache_dir = smtp_policy.idempotency.cache_dir
        if configured_cache_dir is not None:
            if self._plugin_storage is not None:
                return str(self._plugin_storage.path(configured_cache_dir.strip()))
            return configured_cache_dir.strip()
        if self._plugin_storage is None:
            raise ValueError(
                "SMTP idempotency cache_dir is required when plugin storage is unavailable"
            )
        return str(self._plugin_storage.path("idempotency"))

    def _idempotency_expire_seconds(
        self,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> int:
        return smtp_policy.idempotency.expiration_days * 24 * 60 * 60

    def _should_clear_idempotency_reservation(
        self,
        exc: Exception,
        *,
        envelope_recipients: list[str],
    ) -> bool:
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            refused_recipients = {
                recipient.strip().lower() for recipient in exc.recipients
            }
            attempted_recipients = {
                recipient.strip().lower() for recipient in envelope_recipients
            }
            return attempted_recipients <= refused_recipients
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return False
        return True


def _smtp_account_bootstrap_template(
    *,
    name: str,
    policy_name: str,
    env_suffix: str,
) -> str:
    return f"""# @package arbiter.account.smtp.{name}
defaults:
  # Extend the plugin-owned structured schema, then override values below.
  - schema@_here_
  - _self_

# Human-facing summary shown by account listing tools.
description: SMTP account for (${{.from_email}})

# Operator guidance shown to agents during discovery.
guidance: ""

# Matching policy generated alongside this account.
policy: {policy_name}

# SMTP submission endpoint.
host: ${{oc.env:SMTP_{env_suffix}_HOST}}
port: ${{oc.env:SMTP_{env_suffix}_PORT,587}}

# Set to false for unauthenticated local relays.
authenticate: true

# Credentials are read from the Arbiter process environment.
username: ${{oc.env:SMTP_{env_suffix}_USERNAME}}
password: ${{oc.env:SMTP_{env_suffix}_PASSWORD}}

# Sender identity used in message headers.
from_email: agent@example.com
from_name: Arbiter

# TLS mode: starttls, implicit, or none.
tls: starttls
verify_peer: true
timeout_seconds: 30

# Optional override for the IMAP sent-copy folder used after successful sends.
# Leave null to infer the only kind=SENT folder on the matching IMAP account.
sent_copy:
  folder: null
"""


def _smtp_policy_bootstrap_template(*, name: str) -> str:
    return f"""# @package arbiter.policy.smtp.{name}
defaults:
  # Extend the plugin-owned structured schema, then override values below.
  - schema@_here_
  - _self_

# Basic send-rate limits. Use null to disable a limit.
limits:
  max_messages_per_minute: 30
  max_recipients_per_message: 10

# Dedupe window for repeated send attempts.
idempotency:
  expiration_days: 7
  # Optional plugin-relative subdirectory. Defaults to the SMTP plugin's
  # private writable space under idempotency/.
  cache_dir: null

# Empty lists do not restrict recipients. Add entries to enforce allow/block rules.
recipient_policy:
  allowed_recipients: []
  blocked_recipients: []
  allowed_domain_patterns: []
  blocked_domain_patterns: []

# Save submitted messages to the matching IMAP account's Sent folder when one
# can be resolved. on_failure=warn keeps SMTP success even if IMAP append fails;
# on_failure=fail treats missing sent-copy audit as an operation failure.
sent_copy:
  enabled: true
  on_failure: warn
"""


class SMTPServicePlugin:
    name = "smtp"
    version = distribution_version("arbiter-smtp", package_file=__file__)
    server_api_version = SERVER_API_VERSION

    def register_configs(self, config_store: ConfigStore) -> None:
        register_smtp_configs(config_store)

    def bootstrap_config(self, *, kind: str, name: str) -> object | None:
        if kind == "account":
            env_suffix = name.upper().replace("-", "_")
            if not env_suffix.endswith("_ACCOUNT"):
                env_suffix = f"{env_suffix}_ACCOUNT"
            return _smtp_account_bootstrap_template(
                name=name,
                policy_name=f"{name}_policy",
                env_suffix=env_suffix,
            )
        if kind == "policy":
            return _smtp_policy_bootstrap_template(name=name)
        return None

    def build_runtime(
        self,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
        context: ServiceRuntimeContext,
    ) -> object:
        from .client import SMTPSubmissionClient

        smtp_client_factory = cast(
            SMTPClientFactory,
            context.dependencies.get("smtp_client_factory", SMTPSubmissionClient),
        )
        time_provider = cast(
            TimeProvider,
            context.dependencies.get("time_provider", monotonic),
        )
        return SMTPRuntime(
            accounts=accounts,
            policies=policies,
            smtp_client_factory=smtp_client_factory,
            time_provider=time_provider,
            plugin_storage=cast(
                PluginStorage | None, context.dependencies.get("plugin_storage")
            ),
            sent_message_appender=cast(
                SentMessageAppender | None,
                context.dependencies.get("sent_message_appender"),
            ),
        )

    def check_config(
        self,
        *,
        accounts: Mapping[str, object],
        policies: Mapping[str, object],
    ) -> None:
        smtp_accounts = cast(Mapping[str, SMTPConfig], accounts)
        smtp_policies = cast(Mapping[str, SMTPServicePolicyConfig], policies)
        errors: list[ConfigCheckIssue] = []
        for account_name, smtp_config in sorted(smtp_accounts.items()):
            if smtp_config.policy not in smtp_policies:
                continue
            if (
                smtp_config.sent_copy.folder is not None
                and not smtp_config.sent_copy.folder.strip()
            ):
                errors.append(
                    ConfigCheckIssue(
                        message="SMTP sent_copy.folder must be non-empty",
                        account=account_name,
                        policy=smtp_config.policy,
                    )
                )
        for policy_name, smtp_policy in sorted(smtp_policies.items()):
            if smtp_policy.idempotency.expiration_days <= 0:
                errors.append(
                    ConfigCheckIssue(
                        message="SMTP idempotency expiration_days must be positive",
                        policy=policy_name,
                    )
                )
            cache_dir = smtp_policy.idempotency.cache_dir
            if cache_dir is not None and not cache_dir.strip():
                errors.append(
                    ConfigCheckIssue(
                        message="SMTP idempotency cache_dir must be non-empty",
                        policy=policy_name,
                    )
                )
        if errors:
            raise ConfigCheckError(errors)

    def describe_capability(
        self,
        context: ServicePluginContext,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name=self.name,
            description="Send email through configured SMTP accounts.",
        )

    def describe_operations(
        self,
        context: ServicePluginContext,
    ) -> tuple[OperationDescriptor, ...]:
        return (
            OperationDescriptor(
                name="send_email",
                description=SEND_EMAIL_DESCRIPTION,
                input_schema=SendEmailInput,
            ),
        )

    def invoke_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
        context: ServicePluginContext,
    ) -> object:
        if operation != "send_email":
            raise ValueError(f"unknown SMTP operation: {operation}")

        runtime = context.runtimes.require(self.name, SMTPRuntime)
        result = runtime.send_email(
            account=cast(str, arguments.get("account")),
            to=cast(list[str], arguments.get("to")),
            subject=cast(str, arguments.get("subject")),
            text_body=cast(str | None, arguments.get("text_body")),
            html_body=cast(str | None, arguments.get("html_body")),
            cc=cast(list[str] | None, arguments.get("cc")),
            bcc=cast(list[str] | None, arguments.get("bcc")),
            idempotency_key=cast(str | None, arguments.get("idempotency_key")),
        )
        return {
            "ok": True,
            "message_id": result.message_id,
            "recipient_count": result.recipient_count,
            "sent_copy": result.sent_copy,
            "idempotency_replayed": result.idempotency_replayed,
        }

    def check_operation(
        self,
        operation: str,
        arguments: Mapping[str, object],
        context: ServicePluginContext,
    ) -> dict[str, object]:
        runtime = context.runtimes.require(self.name, SMTPRuntime)
        return runtime.check_operation(operation, arguments)


def plugin() -> SMTPServicePlugin:
    return SMTPServicePlugin()
