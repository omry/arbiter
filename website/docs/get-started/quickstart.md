---
title: Quickstart
---

This quickstart uses the default config directory, `~/.arbiter`, bootstraps one
SMTP account, and shows the composed config.

## Install

Create a virtualenv and install Arbiter from pip. The virtualenv keeps
the server, client, and plugin packages isolated from your system Python.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install arbiter-suite
```

:::note

The PyPI package `arbiter` is unrelated to Arbiter. Install
`arbiter-suite` for the default Arbiter bundle, or install packages such
as `arbiter-server`, `arbiter-smtp`, and `arbiter-imap` explicitly.

:::

Use the virtualenv commands directly, or activate it before running examples.

## Security boundary

:::warning

Arbiter only helps if agents cannot change the deployment and cannot
reach the protected service another way. Keep config, credentials, plugins, and
startup scripts operator-owned, and do not give agents direct service
access.

Before production use, read [Security Model](../operate/security.md) and
[Configuration Model](../operate/configuration-model.md).

:::

## Create config

Arbiter uses `~/.arbiter` by default. To keep config somewhere else, add
`--config-dir <dir>` before the subcommand, for example
`arbiter-server --config-dir ./config.local config show`.

Arbiter config is built with Hydra and OmegaConf. For how the defaults
list, config groups, schemas, interpolation, and command-line overrides fit
together, see [Configuration Model](../operate/configuration-model.md).

Bootstrap the Arbiter server config once per installation. This creates the root
composition scaffold and the default server config. Plugin-owned account and
policy config is bootstrapped separately when you configure that plugin for the
first time.

```bash
arbiter-server bootstrap arbiter
# wrote ~/.arbiter/arbiter-server.yaml
# wrote ~/.arbiter/arbiter/server.yaml
```

Add an SMTP account template and its matching policy:

```bash
arbiter-server bootstrap plugin smtp account bot
# wrote ~/.arbiter/arbiter/account/smtp/bot.yaml
# wrote ~/.arbiter/arbiter/policy/smtp/bot_policy.yaml
```

Edit the generated files:

```text
~/.arbiter/arbiter/account/smtp/bot.yaml
~/.arbiter/arbiter/policy/smtp/bot_policy.yaml
```

<details>
<summary>Show generated config shape</summary>

The root config is mostly a Hydra defaults list:

```yaml title="~/.arbiter/arbiter-server.yaml"
defaults:
  - arbiter: server
  - arbiter/account:
    - smtp/bot
  - arbiter/policy:
    - smtp/bot_policy
  - _self_
```

The account file extends the plugin-owned schema and uses OmegaConf
interpolation for secrets:

```yaml title="~/.arbiter/arbiter/account/smtp/bot.yaml"
defaults:
  - /arbiter/account/smtp/schema@_here_
  - _self_

policy: bot_policy
host: smtp.example.com
port: 587
authenticate: true
username: ${oc.env:SMTP_BOT_ACCOUNT_USERNAME}
password: ${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}
from_email: agent@example.com
from_name: Arbiter
tls: starttls
verify_peer: true
timeout_seconds: 30
```

</details>

After editing, include the account in the composed config:

```bash
arbiter-server config activate account smtp bot
# updated ~/.arbiter/arbiter-server.yaml
```

Inspect the composed config:

```bash
arbiter-server config show
```

```yaml
arbiter:
  server:
    name: arbiter
    transport: streamable-http
    bind:
      host: 127.0.0.1
      port: 8000
      path: /mcp
  account:
    smtp:
      bot:
        policy: bot_policy
        host: smtp.example.com
        username: ${oc.env:SMTP_BOT_ACCOUNT_USERNAME}
        password: ${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}
  policy:
    smtp:
      bot_policy:
        require_confirmation: true
```

## Environment values

Generated account configs keep secrets out of YAML by referencing environment
variables with `${oc.env:...}`. Arbiter does not own those secrets, but it
can help you prepare and check the local env file named by the config.

Use `env bootstrap` when the config changed and you want the local env file to
catch up:

```bash
arbiter-server env bootstrap
```

This scans the composed config for `${oc.env:...}` references, creates the
configured env file if needed, and adds any missing variables without replacing
existing values. After it runs, open the env file and fill in the blank secret
values.

Use `env check` before starting the server, or after editing the env file:

```bash
arbiter-server env check
```

This verifies that every `${oc.env:...}` reference in the composed config is
available from either the env file or the process environment. If anything is
missing, it prints the variable names and the plugin block they came from.

## Run

Validate, then start the server:

```bash
arbiter-server config check
arbiter-server serve
```

In another terminal, point the client at the MCP endpoint:

```bash
arbiter info arbiter.mcp_url=http://127.0.0.1:8000/mcp
arbiter info plugin smtp arbiter.mcp_url=http://127.0.0.1:8000/mcp
```
