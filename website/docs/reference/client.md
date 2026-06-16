---
title: Arbiter Client reference
---

`arbiter` is the client-facing command for agents and humans. It connects to an
Arbiter server and exposes discovery and operation execution.

Most users and agents should start with `info`.

Arbiter exposes a hierarchical discovery surface under `info`. Start with a
server and plugin orientation summary, then drill into the plugin or operation
needed for the task:

```bash
arbiter info
arbiter info plugin smtp
arbiter info op smtp send_email
```

## Global options

```bash
arbiter [--config-dir DIR] [--config-name NAME] <command>
```

- `--config-dir DIR`: client config directory. Defaults to `~/.arbiter`.
- `--config-name NAME`: client config file name without `.yaml`. Defaults to
  `arbiter-client`.
- `--version`: print the installed version.

The client reads the server URL from its config. The current config key is
`arbiter.url`; you can override it per command with a Hydra-style argument:

```bash
arbiter info arbiter.url=http://127.0.0.1:8075
```

When the server reports `deployment_scope=staged`, the client prints a small
heads-up on stderr so you know you are talking to a staged deployment.

Default client config:

```text
~/.arbiter/arbiter-client.yaml
```

Example:

```yaml
arbiter:
  url: http://127.0.0.1:8075
```

## bootstrap

Create the client config file.

```bash
arbiter bootstrap client [--force] [override...]
```

- `--force`: overwrite an existing client config.

Example:

```bash
arbiter bootstrap client arbiter.url=http://127.0.0.1:8075
```

## Common flow

```bash
arbiter info
arbiter info plugins
arbiter info plugin smtp
arbiter info op smtp send_email
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```

## info

Discover server identity, installed plugins, and operation schemas.

```bash
arbiter info [--yaml]
arbiter info plugins
arbiter info plugin <plugin>
arbiter info ops <plugin>
arbiter info op <plugin> <operation>
```

`info` prints JSON by default so agents and scripts can consume it directly.
For terminal reading, pipe it through `jq`:

```bash
arbiter info | jq
```

Use `--yaml` when you want readable YAML output instead:

```bash
arbiter info --yaml
```

- `info`: summarize the server URL, deployment scope, and installed plugins.
- `info plugins`: list installed plugins.
- `info plugin <plugin>`: describe one plugin and its operations.
- `info ops <plugin>`: list operations for one plugin.
- `info op <plugin> <operation>`: show one operation and its input schema.

## op

Run operations.

```bash
arbiter op run <operation-id> --args '<json-object>'
```

- `op run`: run one operation with JSON arguments.
- `operation` is an alias for `op`.

Example:

```bash
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```
