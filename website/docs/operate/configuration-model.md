---
title: Configuration Model
---

Arbiter treats configuration as the deployment authority. Operators
declare which services exist, which accounts are available, which policies
apply, and where credentials are referenced. Agents consume the approved MCP
surface that results from that configuration.

## Security note

Agents should interact with Arbiter only through approved surfaces, such
as MCP or the Arbiter CLI. They must not control the config directory or Agent
Arbiter deployment, because changing those inputs lets them circumvent the
policy they are supposed to be constrained by.

It is recommended to deny agents direct access to protected services and
credentials, typically by sandboxing the agent. For the full trust model, see
[Security Model](security.md).

## Example config directory

Server config lives in `~/.arbiter` by default. Use `--config-dir <dir>` for a
different deployment directory, such as a test fixture or container mount.

An example server config directory looks like this:

This can look like a lot at first glance, but operators do not need to create
the structure by hand. `arbiter-server bootstrap`, `arbiter-server config
activate`, and `arbiter-server env bootstrap` create the scaffold, activate
accounts, and maintain the local env file.

```text
~/.arbiter
├── arbiter
│   ├── account
│   │   ├── imap
│   │   │   ├── bot.yaml        # IMAP account owned by the bot
│   │   │   └── personal.yaml   # owner IMAP account
│   │   └── smtp
│   │       ├── bot.yaml        # SMTP account owned by the bot
│   │       └── personal.yaml   # owner SMTP account
│   ├── policy
│   │   ├── imap
│   │   │   ├── bot.yaml        # bot policy with full IMAP access
│   │   │   └── personal.yaml   # restricted access to owner IMAP
│   │   └── smtp
│   │       ├── bot.yaml        # bot policy with full SMTP access
│   │       └── personal.yaml   # restricted access to owner SMTP
│   └── server.yaml             # MCP server settings
├── arbiter-server.yaml         # root server composition config
└── .env                        # local environment-backed values
```

The config directory is normal files on disk. It should be owned by the
deployment operator, not by the agent using the service.

## Basic model

`arbiter-server.yaml` is the default root server config. It selects the active
server settings, accounts, and policies. Operators normally change the active
set with `arbiter-server config activate` and `arbiter-server config
deactivate`, not by editing the root file by hand.

Use `--config-name foo` when the root config file is named `foo.yaml`:

```bash
arbiter-server --config-name foo config show
```

Plugin-owned account and policy files live under
`arbiter/account/<plugin>/` and `arbiter/policy/<plugin>/`. Plugins define the
schema for those files, so SMTP, IMAP, and future plugins can each own their
service-specific config shape.

At startup, `arbiter-server serve` composes the active config, validates it
against server and plugin schemas, and only then exposes tools.

## Command-line overrides

Commands that compose config accept Hydra/OmegaConf-style overrides. Use
`config show` without overrides when you want to inspect the configured state:

```bash
arbiter-server config show
```

`config show` and `config check` can also accept optional overrides when you
want to preview or validate a temporary change for this invocation, without
changing the underlying config files.

Use an override with `serve` when you want a one-off runtime change without
editing the config files:

```bash
arbiter-server serve arbiter.server.bind.port=8025
```

That command starts the server on port `8025` for that process only.

Hydra also supports config group overrides. Arbiter uses config groups for
server settings, accounts, and policies, but normal account activation should go
through `arbiter-server config activate` so the root composition remains
consistent.

## Accounts

An account describes how a plugin reaches an upstream service. Account config
usually includes connection settings, credential references, display metadata,
and the policy name used for that account.

Accounts are deployment-owned. Agents may select exposed accounts through the
MCP surface, but they should not receive the service credentials behind those
accounts.

## Policies

A policy describes what the plugin should allow for an account. Policies are
plugin-specific because each service has different meaningful controls. An SMTP
policy might limit recipients or require confirmation; an IMAP policy might
limit readable folders or message operations.

Policies are cross-cutting config: they are separate files, but accounts refer
to them by name. Bootstrap creates a matching policy for each new account, but
multiple accounts can share one policy when that is useful.

## Scaffolding flow

Server config commands use `~/.arbiter` by default. Pass `--config-dir <dir>`
when you want to target a different config directory:

```bash
arbiter-server --config-dir ./config.local bootstrap arbiter
```

For a normal deployment, start by bootstrapping the root server scaffold once:

```bash
arbiter-server bootstrap arbiter
```

To protect user config, all bootstrap commands refuse to rewrite an existing
file unless `--force` is added.

After the root scaffold exists, bootstrap plugin-owned objects as needed:

```bash
arbiter-server bootstrap plugin smtp account bot
arbiter-server bootstrap plugin imap account personal
```

Creating a config file does not make it active. Activate accounts after editing
the generated templates:

```bash
arbiter-server config activate account smtp bot
arbiter-server config activate account imap personal
```

Use `arbiter-server config show` to inspect the composed result and
`arbiter-server config check` before serving.

## Env file management

Generated account configs should reference credentials through
`${oc.env:...}` rather than storing secrets directly in YAML. Arbiter can
help maintain the local env file named by server config:

```yaml title="~/.arbiter/arbiter-server.yaml"
arbiter:
  env_file: .env
```

Relative env file paths are resolved from the config directory. By default that
means `~/.arbiter/.env`; use `--config-dir <dir>` before the subcommand to use
a different config root.

```bash
arbiter-server env bootstrap
arbiter-server env check
```

When `arbiter.env_file` is set, `arbiter-server` loads that dotenv file into
its own process before composing config. This does not require Docker or an
external wrapper. Existing process environment variables take precedence over
values from the env file.

Unlike config bootstrap, `env bootstrap` is meant to be re-run. It reads the
existing env file, keeps existing values, keeps unrelated variables, and adds
any missing variables discovered in the composed config. If no env file is
configured yet, it adds `arbiter.env_file: .env` to the root config and creates
that file. `env check` verifies that all referenced variables are available
before the server starts.

The generated env file depends on the config that is active when the command
runs. For example, a composed config with SMTP and IMAP bot accounts might
produce entries like:

```dotenv title="~/.arbiter/.env"
# arbiter-imap
IMAP_BOT_ACCOUNT_USERNAME=
IMAP_BOT_ACCOUNT_PASSWORD=

# arbiter-smtp
SMTP_BOT_ACCOUNT_USERNAME=
SMTP_BOT_ACCOUNT_PASSWORD=

# miscellaneous
EXTRA_LOCAL_VALUE=keep-me
```

Different active accounts, policies, plugins, or command-line overrides can
require different environment variables.
