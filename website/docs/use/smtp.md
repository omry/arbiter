---
title: SMTP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-smtp.svg?label=arbiter-smtp)](https://pypi.org/project/arbiter-smtp/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-smtp.svg?label=python)](https://pypi.org/project/arbiter-smtp/) [![Downloads](https://pepy.tech/badge/arbiter-smtp/month)](https://pepy.tech/project/arbiter-smtp)

The SMTP capability currently exposes one operation:

```text
smtp:send_email
```

## Inspect

```bash
arbiter info plugin smtp
arbiter info op smtp send_email
```

The account summary includes:

- account description
- selected policy name
- whether sending is allowed
- whether caller confirmation is required

## Config schema

<details>
<summary>Dataclasses</summary>

Hydra validates SMTP account and policy config against these schemas during
config composition, including values supplied through command-line overrides.

SMTP account config is registered as `arbiter/account/smtp/schema`:

```python
class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


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
    from_name: str = "Arbiter"
    tls: MailTlsMode = MailTlsMode.starttls
    verify_peer: bool = True
    timeout_seconds: float = 30.0
```

SMTP policy config is registered as `arbiter/policy/smtp/schema`:

```python
@dataclass
class SMTPLimitsConfig:
    max_messages_per_minute: int | None = None
    max_recipients_per_message: int | None = None


@dataclass
class SMTPIdempotencyConfig:
    expiration_days: int = 7
    cache_dir: str = ".arbiter/smtp-idempotency"


@dataclass
class SMTPRecipientPolicyConfig:
    allowed_recipients: list[str] = field(default_factory=list)
    blocked_recipients: list[str] = field(default_factory=list)
    allowed_domain_patterns: list[str] = field(default_factory=list)
    blocked_domain_patterns: list[str] = field(default_factory=list)


@dataclass
class SMTPServicePolicyConfig(Policy):
    require_confirmation: bool = False
    limits: SMTPLimitsConfig = field(default_factory=SMTPLimitsConfig)
    idempotency: SMTPIdempotencyConfig = field(default_factory=SMTPIdempotencyConfig)
    recipient_policy: SMTPRecipientPolicyConfig = field(
        default_factory=SMTPRecipientPolicyConfig
    )
```

</details>

## Run

```bash
arbiter op run smtp:send_email --args '{
  "account": "bot",
  "to": ["ops@example.com"],
  "subject": "Status",
  "text_body": "The job completed."
}'
```

## Policy checks

The server enforces:

- account existence
- configured recipient policy
- `max_recipients_per_message`
- per-account per-process message rate limit
- keyed idempotency replay/conflict handling
- no caller override for SMTP host, TLS, credentials, sender identity, or
  `Reply-To`

Provide `idempotency_key` when retrying a send. The SMTP plugin stores
successful keyed results in the policy's persistent cache and replays the same
result for the same key and payload until the idempotency record expires.
