from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from arbiter_server.config import Policy
from hydra.core.config_store import ConfigStore


class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


class SMTPSentCopyFailureMode(str, Enum):
    warn = "warn"
    fail = "fail"


@dataclass
class SMTPLimitsConfig:
    max_messages_per_minute: int | None = None
    max_recipients_per_message: int | None = None


@dataclass
class SMTPIdempotencyConfig:
    expiration_days: int = 7
    cache_dir: str | None = None


@dataclass
class SMTPRecipientPolicyConfig:
    allowed_recipients: list[str] = field(default_factory=list)
    blocked_recipients: list[str] = field(default_factory=list)
    allowed_domain_patterns: list[str] = field(default_factory=list)
    blocked_domain_patterns: list[str] = field(default_factory=list)


@dataclass
class SMTPSentCopyAccountConfig:
    folder: str | None = None


@dataclass
class SMTPSentCopyPolicyConfig:
    enabled: bool = True
    on_failure: SMTPSentCopyFailureMode = SMTPSentCopyFailureMode.warn


@dataclass
class SMTPConfig(Policy):
    policy: str = "bot"
    description: str = ""
    guidance: str = ""
    host: str = "localhost"
    port: int = 587
    authenticate: bool = False
    username: str = ""
    password: str = ""
    from_email: str = "agent@example.com"
    from_name: str = "Arbiter"
    tls: MailTlsMode = MailTlsMode.starttls
    verify_peer: bool = True
    timeout_seconds: float = 30.0
    sent_copy: SMTPSentCopyAccountConfig = field(
        default_factory=SMTPSentCopyAccountConfig
    )


@dataclass
class SMTPServicePolicyConfig(Policy):
    limits: SMTPLimitsConfig = field(default_factory=SMTPLimitsConfig)
    idempotency: SMTPIdempotencyConfig = field(default_factory=SMTPIdempotencyConfig)
    recipient_policy: SMTPRecipientPolicyConfig = field(
        default_factory=SMTPRecipientPolicyConfig
    )
    sent_copy: SMTPSentCopyPolicyConfig = field(
        default_factory=SMTPSentCopyPolicyConfig
    )


SMTP_ACCOUNT_EXAMPLE = SMTPConfig(
    policy="bot_policy",
    description="SMTP account for (${.from_email})",
    host="smtp.example.com",
    port=587,
    authenticate=True,
    username="${oc.env:SMTP_BOT_ACCOUNT_USERNAME}",
    password="${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}",
    from_email="agent@example.com",
    from_name="Arbiter",
    tls=MailTlsMode.starttls,
    verify_peer=True,
    timeout_seconds=30.0,
)

SMTP_POLICY_EXAMPLE = SMTPServicePolicyConfig(
    limits=SMTPLimitsConfig(
        max_messages_per_minute=30,
        max_recipients_per_message=10,
    ),
    recipient_policy=SMTPRecipientPolicyConfig(
        allowed_domain_patterns=[],
    ),
)


def register_configs(config_store: ConfigStore) -> None:
    config_store.store(
        group="arbiter/account/smtp",
        name="schema",
        node=SMTPConfig,
        provider="arbiter-smtp",
    )
    config_store.store(
        group="arbiter/account/smtp",
        name="example",
        node=SMTP_ACCOUNT_EXAMPLE,
        provider="arbiter-smtp",
    )
    config_store.store(
        group="arbiter/policy/smtp",
        name="schema",
        node=SMTPServicePolicyConfig,
        provider="arbiter-smtp",
    )
    config_store.store(
        group="arbiter/policy/smtp",
        name="example",
        node=SMTP_POLICY_EXAMPLE,
        provider="arbiter-smtp",
    )
