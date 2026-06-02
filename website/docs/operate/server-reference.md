---
title: Server Reference
---

`arbiter-server` is the operator-facing command for configuring and running the
Arbiter MCP server.

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

Run the MCP server.

```bash
arbiter-server serve [override...]
```

Examples:

```bash
arbiter-server serve
arbiter-server serve arbiter.server.port=8025
arbiter-server --config-dir ./config.local serve
```

## config

Inspect, validate, activate, and deactivate server config.

```bash
arbiter-server config show [--resolve] [override...]
arbiter-server config check [override...]
arbiter-server config activate account <plugin> <name>
arbiter-server config deactivate account <plugin> <name>
```

- `config show`: print the composed config.
- `config show --resolve`: resolve OmegaConf interpolations before printing.
- `config check`: validate config and service runtime construction without
  serving.
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

Print core and plugin runtime versions. When the command is running from a
source checkout, it also reports the current source commit and whether the
checkout has uncommitted changes.

```bash
arbiter-server version [--json]
```

- `version`: print the loaded Arbiter core version, core API line, source
  checkout state when available, and installed service plugin versions.
- `version --json`: print the same information as JSON.

## deploy

Create or update deployment files from the installed `arbiter-server` command.

```bash
arbiter-server deploy docker init [docker.dir=PATH] [docker.requirement=REQ ...]
arbiter-server deploy docker update [docker.dir=PATH] [docker.requirement=REQ ...]
```

- `deploy docker init`: write a local Docker deployment directory. Defaults to
  `./arbiter-docker`, refuses to overwrite existing managed files, and does not
  create config, start Docker, or run the server. When run from a local dev
  checkout, it seeds source-path requirements and a read-only source mount
  override.
- `deploy docker update`: refresh manifest-owned templates
  (`compose.yaml` and `arbiter-docker`) only when they are missing or still
  match the recorded manifest hash. Existing templates without manifest
  ownership or with local edits are skipped. It regenerates `docker.env`
  while preserving known and extra local values, and never rewrites an existing
  `requirements.txt`. If it
  creates missing local-checkout source requirements, it also creates the
  read-only source mount override when missing.
- `docker.dir=PATH`: deployment directory to create or update.
- `docker.requirement=REQ`: package requirement to seed into
  `requirements.txt` when it is created. Package requirements must be exact
  pins such as `arbiter-core==0.9.0.dev1`; absolute container paths are allowed
  for local source testing when a local Compose override mounts the source tree.
  May be repeated for explicit core and plugin pins.

The generated deployment directory includes `docker.env` for Compose/container
settings, a default `conf/` config directory, and its own `arbiter-docker`
helper for local operations such as `up`, `logs`, `restart`, `sync-env`, `info`,
`doctor`, and `install`. Use `arbiter-docker doctor --preinstall` to check a
prepared directory before promoting it to a Linux host with
`sudo ./arbiter-docker install --to /opt/arbiter --user arbiter`. The install
step copies the prepared directory, creates the dedicated user/group if
missing, sets ownership and modes, installs a root-managed systemd unit, and
does not add the `arbiter` user to the Docker group. Use
`arbiter-docker doctor --agent-user USER` to check common filesystem and Docker
socket mistakes for an agent identity. `doctor`, `up`, and `restart` also
reject unpinned package requirements. Arbiter config and `.env` are
supplied separately through the config tooling using the directory named by
`ARBITER_CONFIG_DIR` and `ARBITER_CONFIG_NAME` in `docker.env`.

## plugins

Inspect installed service plugins.

```bash
arbiter-server plugins list [--json]
```

- `plugins list`: print installed plugin names.
- `plugins list --json`: print core runtime version info, source checkout state
  when available, and plugin names, versions, and core API compatibility lines
  as JSON.
