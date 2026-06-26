---
title: Configure
---

An SMTP account describes how Arbiter connects to an SMTP server and what
sender identity it uses. The matching policy decides whether sending is allowed
and which recipients are permitted.

Bootstrap an account and starter policy:

```bash
arbiter-server bootstrap --plugin smtp --account bot
```

The account file contains deployment-owned connection settings and credentials:

```yaml title="arbiter/account/smtp/bot.yaml"
policy: bot_policy
host: ${oc.env:SMTP_BOT_ACCOUNT_HOST}
port: ${oc.env:SMTP_BOT_ACCOUNT_PORT,587}
authenticate: true
username: ${oc.env:SMTP_BOT_ACCOUNT_USERNAME}
password: ${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}
from_email: agent@example.com
from_name: Arbiter
tls: starttls
verify_peer: true
```

Generated config uses environment variables for deployment-specific connection
details. It is still your config: edit the YAML directly when a fixed value is
clearer for your deployment.

The policy file contains the sending guardrails:

```yaml title="arbiter/policy/smtp/bot_policy.yaml"
recipient_policy:
  allowed_domain_patterns:
    - example.com

limits:
  max_recipients_per_message: 10
  max_messages_per_minute: 5
```

## Account Fields

- `host`, `port`, `tls`, and `verify_peer` define the SMTP transport.
- `authenticate`, `username`, and `password` define authentication.
- `from_email` and `from_name` define the sender identity.
- `sent_copy.folder` optionally names the IMAP Sent folder to use for saved
  copies.

Agents do not provide or override these fields when sending mail.

## Policy Fields

- `recipient_policy`: allow or block recipients and recipient domains.
- `limits`: bound recipients per message and sends per minute.
- `idempotency`: control safe retry storage for repeated send attempts with the
  same caller-provided key.
- `sent_copy`: enable Sent-copy behavior and choose whether failures warn or
  fail.

After editing account and policy files, activate the account:

```bash
arbiter-server config activate --plugin smtp --account bot
```

If the account uses new environment variables, update the env file:

```bash
arbiter-server env bootstrap
```
