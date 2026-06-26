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
arbiter-server serve arbiter.server.bind.port=8075
arbiter-server --config-dir ./config.local serve
```

## config

Inspect, validate, activate, and deactivate server config.

```bash
arbiter-server config show [--resolve] [--package PATH [--value]] [override...]
arbiter-server config check [--live] [override...]
arbiter-server config activate --plugin <plugin> --account <name>
arbiter-server config activate --plugins <plugin[,plugin...]> --account <name>
arbiter-server config deactivate --plugin <plugin> --account <name>
arbiter-server config deactivate --plugins <plugin[,plugin...]> --account <name>
```

- `config show`: print the composed config.
- `config show --resolve`: resolve OmegaConf interpolations before printing.
- `config show --package PATH`: print only one config subtree or scalar.
- `config show --package PATH --value`: print the selected scalar value without
  YAML formatting.
- `config check`: validate config and service runtime construction without
  serving.
- `config check --live`: also run configured account readiness checks using the
  current credentials.
- `config activate`: add an account to the root defaults list. The
  account's referenced policy is activated as well. Use comma-separated plugin
  names to activate the same account name for several plugins in one command,
  for example `config activate --plugins imap,smtp --account bot`.
- `config deactivate`: remove an account from the root defaults list.
  The policy is removed only when no other active account still references it.

## bootstrap

Create editable config templates.

```bash
arbiter-server bootstrap --server [--force]
arbiter-server bootstrap --plugin <plugin> [--account <name>] [--force]
arbiter-server bootstrap --plugins <plugin[,plugin...]> [--account <name>] [--force]
arbiter-server bootstrap --plugin <plugin> --policy <name> [--force]
arbiter-server bootstrap --plugins <plugin[,plugin...]> --policy <name> [--force]
```

- `bootstrap --server`: create the root server config and baseline server
  config.
- `bootstrap --plugin ... --account`: create a plugin-owned account template
  and, for the normal case, a matching starter policy. If `--account` is
  omitted, the account name defaults to `default`. Use comma-separated plugin
  names with `--plugins` to create the same account name for several plugins in
  one command, for example `bootstrap --plugins imap,smtp --account bot`.
- `bootstrap --plugin ... --policy`: create a plugin-owned policy template.
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

## plugins

Inspect installed service plugins.

```bash
arbiter-server plugins list [--json]
```

- `plugins list`: print installed plugin names.
- `plugins list --json`: print server runtime version info, source checkout state
  when available, and plugin names, versions, and server API compatibility lines
  as JSON.
