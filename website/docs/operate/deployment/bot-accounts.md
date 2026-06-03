---
title: SMTP And IMAP Bot Accounts
---

This example stages one `bot` account for SMTP sending and one `bot` account
for IMAP reading. It assumes the Docker staging directory already exists and
the deployment bundle includes `arbiter-smtp` and `arbiter-imap`.

Run commands from the directory where you created the staging directory:

```bash
cd arbiter-docker
arbiter-server --config-dir ./conf bootstrap arbiter
arbiter-server --config-dir ./conf bootstrap plugin smtp account bot
```

Edit the generated SMTP files before activating them:

- `conf/arbiter/account/smtp/bot.yaml`
- `conf/arbiter/policy/smtp/bot_policy.yaml`

Create the IMAP account file, creating parent directories as needed:

```yaml title="conf/arbiter/account/imap/bot.yaml"
# @package arbiter.account.imap.bot
defaults:
  - /arbiter/account/imap/schema@_here_
  - _self_

description: IMAP account for bot mailbox access.
policy: bot_policy

host: imap.example.com
port: 993
username: ${oc.env:IMAP_BOT_ACCOUNT_USERNAME}
password: ${oc.env:IMAP_BOT_ACCOUNT_PASSWORD}
tls: implicit
verify_peer: true
timeout_seconds: 30

default_folder: INBOX
folders:
  INBOX:
    description: Primary inbox.
```

Create the IMAP policy file, creating parent directories as needed:

```yaml title="conf/arbiter/policy/imap/bot_policy.yaml"
# @package arbiter.policy.imap.bot_policy
defaults:
  - /arbiter/policy/imap/schema@_here_
  - _self_

allow_read: true
allow_search: true
allow_move: false
allow_delete: false
confirmation_required: []
system_flags:
  seen: read_only
  flagged: read_only
  answered: read_only
  deleted: hidden
  draft: hidden
user_flags: {}
```

Activate both accounts:

```bash
arbiter-server --config-dir ./conf config activate account smtp bot
arbiter-server --config-dir ./conf config activate account imap bot
```

Create or update the deployment env file, then fill in the credentials:

```bash
./arbiter-docker sync-env
./arbiter-docker edit-env
```

Expected credential placeholders:

```dotenv title="conf/.env"
SMTP_BOT_ACCOUNT_USERNAME=
SMTP_BOT_ACCOUNT_PASSWORD=
IMAP_BOT_ACCOUNT_USERNAME=
IMAP_BOT_ACCOUNT_PASSWORD=
```

Validate the composed config before starting Docker:

```bash
arbiter-server --config-dir ./conf config check
```

For the full config model, including defaults lists, account activation, and
env-file behavior, see [Configuration Model](../configuration-model.md).
