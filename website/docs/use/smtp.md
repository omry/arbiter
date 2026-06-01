---
title: SMTP
---

The SMTP capability currently exposes one operation:

```text
smtp:send_email
```

## Inspect

```bash
arbiter cap desc smtp
arbiter op desc smtp:send_email
```

The account summary includes:

- account description
- selected policy name
- whether sending is allowed
- whether caller confirmation is required

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
