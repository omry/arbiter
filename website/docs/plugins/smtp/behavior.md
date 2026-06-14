---
title: Behavior
---

SMTP sending is policy-gated before Arbiter opens an SMTP transaction. The
caller selects a configured account and message payload; Arbiter supplies the
transport, credentials, sender identity, recipient checks, rate limits, and
safe retry handling from deployment config.

## Sent Copies

By default, SMTP attempts to save a copy of successfully submitted messages to
IMAP Sent mail when the destination can be resolved unambiguously. The SMTP
account id is reused as the IMAP account id. For example,
`smtp:send_email(account="personal")` only considers `imap.personal`.

Arbiter serializes the message once and uses those same bytes for SMTP DATA and
the IMAP append, so the Sent copy matches what was submitted to the SMTP server.

Normal configuration is to mark exactly one folder on the matching IMAP account
as `kind: SENT`:

```yaml
folders:
  Sent:
    description: Sent mail.
    kind: SENT
```

If there is no matching IMAP account, no `kind: SENT` folder, or multiple
`kind: SENT` folders, the sent copy is skipped unless a folder override is set
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

## Safe Retries

Sending email is not reversible, so retries need a way to avoid accidentally
sending the same message twice. Arbiter does this with a caller-provided retry
key. This is the behavior the config calls `idempotency`.

Provide `idempotency_key` when retrying a send. The SMTP plugin stores keyed
records in its plugin data directory and replays a successful result for the
same key and payload until the retry record expires.

When a caller retries with the same `idempotency_key` and payload after a
sent-copy failure, Arbiter does not submit the message through SMTP again. It
reuses the stored submitted message bytes and retries only the IMAP Sent copy.
Until that retry succeeds or the record expires, the keyed retry record can
contain the submitted message bytes.

When `idempotency.cache_dir` is `null`, Arbiter stores SMTP idempotency records
under the SMTP plugin's server-managed writable data directory. If set,
`cache_dir` is a plugin-relative subdirectory, not an arbitrary filesystem path.
