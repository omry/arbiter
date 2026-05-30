# Config Bootstrapping

## Purpose

Create deployment-owned Agent Arbiter config files from canonical templates.

Agent Arbiter does not ship a runnable service config. Operators create a Hydra
config directory, edit the generated files, then run the server against that
directory.

## Config directory

By default, `arbiter-server` uses `~/.arbiter` as its config directory and
`arbiter-server.yaml` as its root config. For repository-local development, pass
an explicit scratch directory:

```bash
arbiter-server --config-dir "$PWD/config.local" bootstrap arbiter
```

`config.local/` is intended as repo-local scratchspace for development and is
ignored by source control. Production deployments should use a deployment-owned
config directory instead.

## Main config

Create the main Agent Arbiter config:

```bash
arbiter-server --config-dir "$PWD/config.local" bootstrap arbiter
```

This writes the root config:

```text
config.local/arbiter-server.yaml
```

The root config is intentionally only a defaults list:

```yaml
defaults:
  # Agent Arbiter composes this config at startup from the defaults below.
  # Inspect the composed config with:
  #   arbiter-server --config-dir <dir> --config-name arbiter-server config show
  # Override composed values with Hydra overrides, for example:
  #   arbiter-server --config-dir <dir> serve arbiter.server.port=8025
  # Optionally load a config-dir-relative dotenv file before composition:
  #   arbiter:
  #     env_file: local.env
  - arbiter: server
  - _self_
```

Bootstrap also writes the default server option:

```text
config.local/arbiter/server.yaml
```

The server option owns the default streamable HTTP server values and discovery
preview caps. Operators can edit that file or override individual values from
the command line.

Use `--config-name` to write a different main config file name:

```bash
arbiter-server --config-dir "$PWD/config.local" --config-name local bootstrap arbiter
```

That writes `config.local/local.yaml` while still writing the server option to
`config.local/arbiter/server.yaml`.

## Plugin objects

Plugins own their account and policy templates. The SMTP plugin currently
provides account and policy bootstrap examples.

Create an SMTP account option and its matching default policy:

```bash
arbiter-server --config-dir "$PWD/config.local" bootstrap plugin smtp account personal_account
```

This writes both files:

```text
config.local/arbiter/account/smtp/personal_account.yaml
config.local/arbiter/policy/smtp/personal_account_policy.yaml
```

Edit both files, then activate the account:

```bash
arbiter-server --config-dir "$PWD/config.local" config activate account smtp personal_account
```

Activation updates the main config defaults list and also activates the policy
named by the account's `policy` field.

Create an additional SMTP policy option:

```bash
arbiter-server --config-dir "$PWD/config.local" bootstrap plugin smtp policy readonly
```

This writes:

```text
config.local/arbiter/policy/smtp/readonly.yaml
```

and prints the matching defaults entry:

```yaml
defaults:
  - arbiter/policy:
    - smtp/readonly
```

Plugins that do not provide a bootstrap example fail explicitly instead of
generating a guessed shape.

## Compose generated objects

Generated plugin objects are Hydra group options. To enable them, add the
printed entries to the main config defaults:

```yaml
defaults:
  - arbiter: server
  - arbiter/account:
    - smtp/personal_account
  - arbiter/policy:
    - smtp/personal_account_policy
    - smtp/readonly
  - _self_
```

The selected files package themselves into the composed `arbiter.account` and
`arbiter.policy` trees. For example, `arbiter/account/smtp/personal_account.yaml`
is composed as `arbiter.account.smtp.personal_account`.

Generated account and policy files also extend their plugin-owned structured
schema before applying local values:

```yaml
# @package arbiter.account.smtp.personal_account
defaults:
  - schema@_here_
  - _self_
```

Each account must reference a policy in the same service namespace:

```yaml
policy: readonly
```

You can also let the CLI update the main defaults list for an account:

```bash
arbiter-server --config-dir "$PWD/config.local" config activate account smtp personal_account
```

Activating an account also activates the policy named by the account's
`policy` field. The CLI first looks for a policy file with that name, then for
a policy file matching the account name; either file is composed under the
account's `policy` value.

Deactivate an account when you want to remove it from composition:

```bash
arbiter-server --config-dir "$PWD/config.local" config deactivate account smtp personal_account
```

Deactivating an account removes its policy entry only when no other active
account in the same service still references that policy.

## Editing generated files

The generated files are starting points. Edit them before running a real
service:

- set account descriptions that make account choice clear
- set host, port, TLS, and sender identity
- narrow recipient or mailbox access policies
- replace credentials with deployment-specific interpolation

Credentials should stay outside source control. Use OmegaConf environment
interpolation such as `${oc.env:SMTP_PERSONAL_ACCOUNT_PASSWORD}` or a deployment
secret mechanism. For local runs, `arbiter.env_file` can load a dotenv-style
file before Hydra composes the config; existing process environment values take
precedence.

## Validate and inspect

Validate the config before serving:

```bash
arbiter-server --config-dir "$PWD/config.local" config check
```

Use a local env file when you want Arbiter to populate process environment
variables before composition:

```yaml
arbiter:
  env_file: local.env
```

Relative env file paths are resolved from `--config-dir`.

Bootstrap or validate the env file from the composed config:

```bash
arbiter-server --config-dir "$PWD/config.local" env bootstrap
arbiter-server --config-dir "$PWD/config.local" env check
```

`env bootstrap` rebuilds the configured env file. It keeps existing assignments,
adds missing `${oc.env:...}` references, sorts plugin blocks by name, and places
existing variables that are not referenced by the config under
`# miscellaneous`. If `arbiter.env_file` is not configured yet, it adds
`arbiter.env_file: .env` to the root config first.

`config check` validates a runnable server config. A freshly bootstrapped config
with no accounts is intentionally not runnable yet; add at least one service
account and policy before expecting this command or `serve` to pass.

Show the composed Hydra job config:

```bash
arbiter-server --config-dir "$PWD/config.local" config show
```

Resolve interpolations while showing the composed config:

```bash
arbiter-server --config-dir "$PWD/config.local" config show --resolve
```

Run the server with the same explicit config directory:

```bash
arbiter-server --config-dir "$PWD/config.local" serve
```

## Overwrites

Bootstrap commands refuse to overwrite existing files by default. Use `--force`
only when replacing the target file is intentional:

```bash
arbiter-server --config-dir "$PWD/config.local" bootstrap plugin smtp account personal_account --force
```
