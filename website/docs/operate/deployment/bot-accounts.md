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
arbiter-server --config-dir ./conf bootstrap plugin imap account bot
```

Edit the generated account and policy files before activating them:

- `conf/arbiter/account/smtp/bot.yaml`
- `conf/arbiter/policy/smtp/bot_policy.yaml`
- `conf/arbiter/account/imap/bot.yaml`
- `conf/arbiter/policy/imap/bot_policy.yaml`

Activate both accounts:

```bash
arbiter-server --config-dir ./conf config activate account smtp bot
arbiter-server --config-dir ./conf config activate account imap bot
```

Create or update the deployment env file, then fill in the credentials:

```bash
arbiter-server --config-dir ./conf env bootstrap
```

Then edit `conf/.env`.

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
