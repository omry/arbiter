---
title: Reference
---

## Config Schemas

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
    limits: SMTPLimitsConfig = field(default_factory=SMTPLimitsConfig)
    idempotency: SMTPIdempotencyConfig = field(default_factory=SMTPIdempotencyConfig)
    recipient_policy: SMTPRecipientPolicyConfig = field(
        default_factory=SMTPRecipientPolicyConfig
    )
    sent_copy: SMTPSentCopyPolicyConfig = field(
        default_factory=SMTPSentCopyPolicyConfig
    )
```

## Enforcement

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

## Sent-Copy Reference

Sent-copy saves a submitted SMTP message into a matching IMAP Sent folder. The
message is submitted through SMTP first, then appended through IMAP.

`sent_copy.enabled` lives on the SMTP policy and controls whether Sent-copy is
attempted. `sent_copy.on_failure` also lives on the policy: `warn` keeps SMTP
submission success even when Sent-copy cannot be saved, while `fail` reports
the Sent-copy failure as an operation failure. `sent_copy.folder` lives on the
SMTP account and can name an explicit IMAP folder for the Sent copy.

When `sent_copy.folder` is set, Arbiter uses that folder on the IMAP account
with the same account id. Without an override, Arbiter uses the matching IMAP
account only when exactly one folder has `kind: SENT`. If there is no matching
IMAP account, no Sent folder, or multiple Sent folders, Sent-copy is skipped or
failed according to `sent_copy.on_failure`.

The operation result reports Sent-copy as `saved`, `skipped`, `failed`, or
`disabled`. A retry with the same `idempotency_key` and payload does not submit
the SMTP message again. If the previous SMTP submission succeeded but Sent-copy
failed or was skipped, the retry can retry only the IMAP Sent-copy step. While
that retry is possible, the retry record can contain the submitted message
bytes.

## Config Checks

| Command | What it checks | Contacts SMTP? |
| --- | --- | --- |
| `arbiter-server config check` | Static SMTP account and policy validity. | No |
| `arbiter-server config check --live` | Static checks plus configured account readiness. | Yes |

Static SMTP checks:

| Check | Failure |
| --- | --- |
| `sent_copy.folder` is non-empty when configured | `SMTP sent_copy.folder must be non-empty` |
| `idempotency.expiration_days` is positive | `SMTP idempotency expiration_days must be positive` |
| `idempotency.cache_dir` is non-empty when configured | `SMTP idempotency cache_dir must be non-empty` |

Live SMTP checks:

| Check | Notes |
| --- | --- |
| `connect` | Opens the configured SMTP connection. |
| `ehlo` | Verifies SMTP greeting/capability exchange. |
| `noop` | Verifies the authenticated session can issue a read-only command. |
| `tls` | Verifies the configured TLS mode as part of connection setup. |
| `idempotency_storage` | Writes, reads, and deletes a short-lived retry-readiness record. |
| `sent_copy_destination` | Runs only when `sent_copy.enabled: true` and `sent_copy.on_failure: fail`; verifies the required Sent-copy destination can be resolved. |

The live check is read-only for email delivery: it does not send mail.
