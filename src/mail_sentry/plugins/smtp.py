from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from time import monotonic
from typing import Annotated, Callable, Protocol, cast

from pydantic import Field

from ..config import (
    AccountConfig,
    MailConfig,
    SMTPConfigLike,
    SMTPServiceConfig,
    SMTPServicePolicyConfig,
)
from ..services import ServicePluginContext, ServiceRuntimeContext, ToolServer


@dataclass(frozen=True)
class SendEmailResult:
    tool: str
    message_id: str
    recipient_count: int


class SMTPClientLike(Protocol):
    def send(
        self,
        message: EmailMessage,
        sender: str,
        recipients: list[str],
    ) -> None: ...


SMTPClientFactory = Callable[[SMTPConfigLike], SMTPClientLike]
TimeProvider = Callable[[], float]


class SMTPRuntime:
    service_name = "smtp"

    def __init__(
        self,
        mail_config: MailConfig,
        service_config: SMTPServiceConfig,
        smtp_client_factory: SMTPClientFactory,
        time_provider: TimeProvider = monotonic,
    ) -> None:
        self._mail_config = mail_config
        self._service_config = service_config
        self._smtp_client_factory = smtp_client_factory
        self._time_provider = time_provider
        self._attempt_timestamps: dict[str, list[float]] = {}

    def account_summary(
        self,
        account_name: str,
        account: AccountConfig,
        smtp_policy: SMTPServicePolicyConfig | None,
    ) -> dict[str, object]:
        smtp_enabled = account_name in self._service_config.accounts
        return {
            "enabled": smtp_enabled,
            "send": "allowed" if smtp_enabled else "unavailable",
            "require_confirmation": bool(
                smtp_enabled
                and smtp_policy is not None
                and smtp_policy.require_confirmation
            ),
        }

    def send_email(
        self,
        account: str,
        to: list[str],
        subject: str,
        text_body: str | None = None,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
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
        message["Message-ID"] = make_msgid(domain=self._sender_domain(smtp_config))

        if text_body:
            message.set_content(text_body)
            if html_body:
                message.add_alternative(html_body, subtype="html")
        else:
            message.set_content(html_body or "", subtype="html")

        envelope_recipients = recipients_to + recipients_cc + recipients_bcc
        self._enforce_policy(account, smtp_policy, envelope_recipients)
        self._consume_rate_limit(account, smtp_policy)
        smtp_client = self._smtp_client_factory(smtp_config)
        smtp_client.send(
            message,
            sender=smtp_config.from_email,
            recipients=envelope_recipients,
        )

        return SendEmailResult(
            tool="send_email",
            message_id=str(message["Message-ID"]),
            recipient_count=len(envelope_recipients),
        )

    def _resolve_context(
        self,
        account_name: str,
    ) -> tuple[SMTPConfigLike, SMTPServicePolicyConfig]:
        account = self._mail_config.accounts.get(account_name)
        if account is None:
            raise ValueError(f"send_email received an unknown account: {account_name}")

        smtp_config = self._service_config.accounts.get(account_name)
        if smtp_config is None:
            raise ValueError(
                f"send_email requires an SMTP-enabled account: {account_name}"
            )

        profile = self._mail_config.account_access_profiles[
            account.account_access_profile
        ]
        smtp_policy = profile.services.smtp
        if smtp_policy is None:
            raise ValueError(
                f"send_email requires an SMTP service policy for account: {account_name}"
            )

        return smtp_config, smtp_policy

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

    def _sender_domain(self, smtp_config: SMTPConfigLike) -> str:
        _, _, domain = smtp_config.from_email.partition("@")
        return domain or "localhost"

    def _enforce_policy(
        self,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
        recipients: list[str],
    ) -> None:
        max_recipients = smtp_policy.limits.max_recipients_per_message
        if max_recipients is not None and len(recipients) > max_recipients:
            raise ValueError(
                f"send_email exceeds max_recipients_per_message for account: {account_name}"
            )

        recipient_policy = smtp_policy.recipient_policy
        for recipient in recipients:
            normalized_recipient = recipient.strip().lower()
            _, _, domain = normalized_recipient.partition("@")
            if self._recipient_matches_list(
                normalized_recipient, recipient_policy.blocked_recipients
            ):
                raise ValueError(
                    f"send_email recipient is blocked by exact address policy: {recipient}"
                )
            if self._domain_matches_any_pattern(
                domain, recipient_policy.blocked_domain_patterns
            ):
                raise ValueError(
                    f"send_email recipient is blocked by domain policy: {recipient}"
                )

            has_allowlist = bool(
                recipient_policy.allowed_recipients
                or recipient_policy.allowed_domain_patterns
            )
            if has_allowlist and not (
                self._recipient_matches_list(
                    normalized_recipient, recipient_policy.allowed_recipients
                )
                or self._domain_matches_any_pattern(
                    domain, recipient_policy.allowed_domain_patterns
                )
            ):
                raise ValueError(
                    f"send_email recipient is not allowed by policy: {recipient}"
                )

    def _consume_rate_limit(
        self,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> None:
        max_messages = smtp_policy.limits.max_messages_per_minute
        if max_messages is None:
            return

        now = self._time_provider()
        window_start = now - 60.0
        active_attempts = [
            timestamp
            for timestamp in self._attempt_timestamps.get(account_name, [])
            if timestamp > window_start
        ]
        if len(active_attempts) >= max_messages:
            raise ValueError(
                f"send_email exceeds max_messages_per_minute for account: {account_name}"
            )

        active_attempts.append(now)
        self._attempt_timestamps[account_name] = active_attempts

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


RecipientList = Annotated[
    list[str],
    Field(
        description="JSON array of recipient email addresses.",
        examples=[["to@example.com"]],
    ),
]

OptionalRecipientList = Annotated[
    list[str] | None,
    Field(
        description="Optional JSON array of recipient email addresses.",
        examples=[["person@example.com"]],
    ),
]

AccountName = Annotated[
    str,
    Field(
        description=(
            "Configured account name returned by list_accounts. The selected account "
            "must have SMTP enabled."
        ),
        examples=["primary"],
        min_length=1,
    ),
]

SubjectLine = Annotated[
    str,
    Field(
        description="Email subject line.",
        examples=["Hello from MCP"],
        min_length=1,
    ),
]

TextBody = Annotated[
    str | None,
    Field(
        description="Optional plain-text body. Provide this or html_body.",
        examples=["Plain text message body."],
    ),
]

HtmlBody = Annotated[
    str | None,
    Field(
        description="Optional HTML body. Provide this or text_body.",
        examples=["<p>Hello from MCP</p>"],
    ),
]


class SMTPServicePlugin:
    name = "smtp"

    def build_runtime(
        self,
        config: object,
        context: ServiceRuntimeContext,
    ) -> object:
        from ..smtp import SMTPSubmissionClient

        smtp_client_factory = cast(
            SMTPClientFactory,
            context.dependencies.get("smtp_client_factory", SMTPSubmissionClient),
        )
        time_provider = cast(
            TimeProvider,
            context.dependencies.get("time_provider", monotonic),
        )
        return SMTPRuntime(
            cast(MailConfig, context.mail_config),
            cast(SMTPServiceConfig, config),
            smtp_client_factory=smtp_client_factory,
            time_provider=time_provider,
        )

    def register_tools(
        self,
        server: ToolServer,
        context: ServicePluginContext,
    ) -> None:
        runtime = context.runtimes.require(self.name, SMTPRuntime)

        @server.tool(
            description=(
                "Send a single email message through the configured SMTP submission "
                "server for the selected account. Use an account name returned by "
                "list_accounts, JSON arrays for to, cc, and bcc, at least one "
                "recipient in to, and at least one of text_body or html_body."
            )
        )
        def send_email(
            account: AccountName,
            to: RecipientList,
            subject: SubjectLine,
            text_body: TextBody = None,
            html_body: HtmlBody = None,
            cc: OptionalRecipientList = None,
            bcc: OptionalRecipientList = None,
        ) -> dict[str, object]:
            result = runtime.send_email(
                account=account,
                to=to,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
            )
            return {
                "ok": True,
                "message_id": result.message_id,
                "recipient_count": result.recipient_count,
            }


def plugin() -> SMTPServicePlugin:
    return SMTPServicePlugin()
