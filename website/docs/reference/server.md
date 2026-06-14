---
title: Arbiter Server reference
---

`arbiter-server` is the operator-facing command for configuring and running an
Arbiter server.

## Global options

Global options may appear before or after the subcommand.

```bash
arbiter-server [--config-dir DIR] [--config-name NAME] <command>
```

- `--config-dir DIR`: directory containing the root Hydra config. Defaults to
  `~/.arbiter`.
- `--config-name NAME`: root config file name without `.yaml`. Defaults to
  `arbiter-server`.

Commands that compose config also accept Hydra-style overrides after the
subcommand.

## serve

Run the Arbiter server.

```bash
arbiter-server serve [override...]
```

Examples:

```bash
arbiter-server serve
arbiter-server serve arbiter.server.bind.port=8025
arbiter-server --config-dir ./config.local serve
```

## config

Inspect, validate, activate, and deactivate server config.

```bash
arbiter-server config show [--resolve] [override...]
arbiter-server config check [--live] [override...]
arbiter-server config activate account <plugin> <name>
arbiter-server config deactivate account <plugin> <name>
```

- `config show`: print the composed config.
- `config show --resolve`: resolve OmegaConf interpolations before printing.
- `config check`: validate config and service runtime construction without
  serving.
- `config check --live`: also run configured account readiness checks using the
  current credentials.
- `config activate account`: add an account to the root defaults list. The
  account's referenced policy is activated as well.
- `config deactivate account`: remove an account from the root defaults list.
  The policy is removed only when no other active account still references it.

## bootstrap

Create editable config templates.

```bash
arbiter-server bootstrap arbiter [--force]
arbiter-server bootstrap plugin <plugin> account <name> [--force]
arbiter-server bootstrap plugin <plugin> policy <name> [--force]
```

- `bootstrap arbiter`: create the root server config and baseline server config.
- `bootstrap plugin ... account`: create a plugin-owned account template and,
  for the normal case, a matching starter policy.
- `bootstrap plugin ... policy`: create a plugin-owned policy template.
- To protect user config, bootstrap commands refuse to rewrite an existing file.
  Add `--force` only when you intentionally want to replace the target file.

## env

Inspect and bootstrap the env file referenced by server config.

```bash
arbiter-server env bootstrap [override...]
arbiter-server env check [override...]
```

- `env bootstrap`: create the configured env file if needed and add missing
  variables discovered from `${oc.env:...}` references.
- `env check`: verify every referenced environment variable is available from
  the env file or process environment.

## version

Print server and plugin runtime versions. When the command is running from a
source checkout, it also reports the current source commit and whether the
checkout has uncommitted changes.

```bash
arbiter-server version [--json]
```

- `version`: print the loaded Arbiter server version, server API line, source
  checkout state when available, and installed service plugin versions.
- `version --json`: print the same information as JSON.

## deploy

Create or update Docker deployment files from the installed `arbiter-server`
command. See [Deployment](../operate/deployment.md) for the Docker deployment
workflow, generated helper commands, package bundle management, and Linux
install.
For this release line, explicit deployment package pins use versions such as
`arbiter-server==0.9.0.dev2`.

## plugins

Inspect installed service plugins.

```bash
arbiter-server plugins list [--json]
```

- `plugins list`: print installed plugin names.
- `plugins list --json`: print server runtime version info, source checkout state
  when available, and plugin names, versions, and server API compatibility lines
  as JSON.
