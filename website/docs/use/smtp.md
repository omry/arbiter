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


class SMTPSentCopyFailureMode(str, Enum):
    warn = "warn"
    fail = "fail"


@dataclass
class SMTPSentCopyAccountConfig:
    folder: str | None = None


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
    cache_dir: str | None = None


@dataclass
class SMTPRecipientPolicyConfig:
    allowed_recipients: list[str] = field(default_factory=list)
    blocked_recipients: list[str] = field(default_factory=list)
    allowed_domain_patterns: list[str] = field(default_factory=list)
    blocked_domain_patterns: list[str] = field(default_factory=list)


@dataclass
class SMTPSentCopyPolicyConfig:
    enabled: bool = True
    on_failure: SMTPSentCopyFailureMode = SMTPSentCopyFailureMode.warn


@dataclass
class SMTPServicePolicyConfig(Policy):
    require_confirmation: bool = False
    limits: SMTPLimitsConfig = field(default_factory=SMTPLimitsConfig)
    idempotency: SMTPIdempotencyConfig = field(default_factory=SMTPIdempotencyConfig)
    recipient_policy: SMTPRecipientPolicyConfig = field(
        default_factory=SMTPRecipientPolicyConfig
    )
    sent_copy: SMTPSentCopyPolicyConfig = field(
        default_factory=SMTPSentCopyPolicyConfig
    )
```

</details>

When `cache_dir` is `null`, Arbiter stores SMTP idempotency records under the
SMTP plugin's server-managed writable data directory. If set, `cache_dir` is a
plugin-relative subdirectory, not an arbitrary filesystem path. Account tests
validate idempotency storage by writing and deleting a short-lived readiness
record, so permission problems are reported before keyed sends attempt SMTP
delivery.

## Sent Copies

By default, SMTP attempts to save a copy of successfully submitted messages to
IMAP Sent mail when the destination can be resolved unambiguously. The SMTP
account id is reused as the IMAP account id. For example,
`smtp:send_email(account="personal")` only considers `imap.personal`.
Arbiter serializes the message once and uses those same bytes for SMTP DATA and
the IMAP append, so the Sent copy matches what was submitted to the SMTP server.

Normal configuration is to mark exactly one folder on the matching IMAP account
as `kind: sent`:

```yaml
folders:
  Sent:
    description: Sent mail.
    kind: sent
```

If there is no matching IMAP account, no `kind: sent` folder, or multiple
`kind: sent` folders, the sent copy is skipped unless a folder override is set
on the SMTP account:

```yaml
sent_copy:
  folder: "Sent Messages"
```

SMTP policy controls whether sent-copy is attempted and how failures are
reported:

```yaml
sent_copy:
  enabled: true
  on_failure: warn  # warn | fail
```

With `on_failure: warn`, SMTP submission success remains success and the
operation result includes `sent_copy.status: failed` or `skipped`. With
`on_failure: fail`, Arbiter fails before SMTP submission when the destination
cannot be resolved. If SMTP succeeds but IMAP append fails, the operation fails
with an explicit error; the email has already been submitted and cannot be
unsent.

When a caller retries with the same `idempotency_key` and payload after a
sent-copy failure, Arbiter does not submit the message through SMTP again. It
reuses the stored submitted message bytes and retries only the IMAP Sent copy.
Until that retry succeeds or the idempotency record expires, the keyed
idempotency record can contain the submitted message bytes.

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
- sent-copy idempotency, so a replay does not resend SMTP and either avoids a
  second Sent append or retries a previously failed Sent append
- no caller override for SMTP host, TLS, credentials, sender identity, or
  `Reply-To`

Provide `idempotency_key` when retrying a send. The SMTP plugin stores keyed
records in its plugin data directory and replays a successful result for the
same key and payload until the idempotency record expires.
