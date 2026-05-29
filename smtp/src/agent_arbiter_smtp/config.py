from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_arbiter.config import Policy
from hydra.core.config_store import ConfigStore


class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


@dataclass
class SMTPLimitsConfig:
    max_messages_per_minute: int | None = None
    max_recipients_per_message: int | None = None


@dataclass
class SMTPIdempotencyConfig:
    expiration_days: int = 7


@dataclass
class SMTPRecipientPolicyConfig:
    allowed_recipients: list[str] = field(default_factory=list)
    blocked_recipients: list[str] = field(default_factory=list)
    allowed_domain_patterns: list[str] = field(default_factory=list)
    blocked_domain_patterns: list[str] = field(default_factory=list)


@dataclass
class SMTPConfig(Policy):
    policy: str = "bot"
    description: str = ""
    host: str = "localhost"
    port: int = 587
    authenticate: bool = False
    username: str = ""
    password: str = ""
    from_email: str = "agent@example.com"
    from_name: str = "Agent Arbiter"
    tls: MailTlsMode = MailTlsMode.starttls
    verify_peer: bool = True
    timeout_seconds: float = 30.0


@dataclass
class SMTPServicePolicyConfig(Policy):
    require_confirmation: bool = False
    limits: SMTPLimitsConfig = field(default_factory=SMTPLimitsConfig)
    idempotency: SMTPIdempotencyConfig = field(default_factory=SMTPIdempotencyConfig)
    recipient_policy: SMTPRecipientPolicyConfig = field(
        default_factory=SMTPRecipientPolicyConfig
    )


SMTP_ACCOUNT_EXAMPLE = SMTPConfig(
    policy="bot_policy",
    description="SMTP account for (${.from_email})",
    host="smtp.example.com",
    port=587,
    authenticate=True,
    username="${oc.env:SMTP_USERNAME_BOT_ACCOUNT}",
    password="${oc.env:SMTP_PASSWORD_BOT_ACCOUNT}",
    from_email="agent@example.com",
    from_name="Agent Arbiter",
    tls=MailTlsMode.starttls,
    verify_peer=True,
    timeout_seconds=30.0,
)

SMTP_POLICY_EXAMPLE = SMTPServicePolicyConfig(
    require_confirmation=True,
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
        provider="agent-arbiter-smtp",
    )
    config_store.store(
        group="arbiter/account/smtp",
        name="example",
        node=SMTP_ACCOUNT_EXAMPLE,
        provider="agent-arbiter-smtp",
    )
    config_store.store(
        group="arbiter/policy/smtp",
        name="schema",
        node=SMTPServicePolicyConfig,
        provider="agent-arbiter-smtp",
    )
    config_store.store(
        group="arbiter/policy/smtp",
        name="example",
        node=SMTP_POLICY_EXAMPLE,
        provider="agent-arbiter-smtp",
    )
