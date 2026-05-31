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
- no caller override for SMTP host, TLS, credentials, sender identity, or
  `Reply-To`

SMTP idempotency config is reserved for future runtime work. The server fails
closed if unsupported idempotency settings are configured.
