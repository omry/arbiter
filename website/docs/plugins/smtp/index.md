---
title: SMTP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-smtp.svg?label=arbiter-smtp)](https://pypi.org/project/arbiter-smtp/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-smtp.svg?label=python)](https://pypi.org/project/arbiter-smtp/) [![Downloads](https://pepy.tech/badge/arbiter-smtp/month)](https://pepy.tech/project/arbiter-smtp)

SMTP lets Arbiter send email through configured SMTP accounts. Operators own
the SMTP host, credentials, sender identity, recipient policy, confirmation
requirements, rate limits, safe retry behavior, and optional Sent-copy
integration. Agents can discover and run the allowed send operation, but they
do not provide SMTP credentials or transport settings.

## Operations

| Operation | Use |
| --- | --- |
| `smtp:send_email` | Send one email message from a configured account. |

## Use

Inspect the plugin and the send operation:

```bash
arbiter info plugin smtp
arbiter info op smtp send_email
```

Inspect an account before using it:

```bash
arbiter info account smtp bot
```

Send a message:

```bash
arbiter op run smtp:send_email --args '{
  "account": "bot",
  "to": ["ops@example.com"],
  "subject": "Status",
  "text_body": "The job completed."
}'
```

## More

- [Configure](./configure.md): account and policy setup.
- [Behavior](./behavior.md): sent copies, safe retries, and policy effects.
- [Reference](./reference.md): schemas and detailed enforcement notes.
