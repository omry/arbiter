---
title: Arbiter CLI Reference
---

`arbiter` is the client-facing command for agents and humans. It talks to an
Arbiter MCP server and exposes discovery, operation execution, and raw MCP
commands.

Most users and agents should start with `info`. The raw `mcp` commands are
available for inspection and debugging.

Arbiter exposes a hierarchical discovery surface under `info`. Start with a
server and account orientation summary, then drill into the plugin, account, or
operation needed for the task:

```bash
arbiter info
arbiter info plugin smtp
arbiter info account smtp bot
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

The client reads `arbiter.mcp_url` from its config. You can override it per
command with a Hydra-style argument:

```bash
arbiter info arbiter.mcp_url=http://127.0.0.1:8000/mcp
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
  mcp_url: http://127.0.0.1:8000/mcp
```

## bootstrap

Create the client config file.

```bash
arbiter bootstrap client [--force] [override...]
```

- `--force`: overwrite an existing client config.

Example:

```bash
arbiter bootstrap client arbiter.mcp_url=http://127.0.0.1:8000/mcp
```

## Common flow

```bash
arbiter info
arbiter info plugins
arbiter info plugin smtp
arbiter info accounts smtp
arbiter info account smtp bot
arbiter info op smtp send_email
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```

## info

Discover server identity, installed plugins, configured accounts, account
policy summaries, and operation schemas.

```bash
arbiter info [--yaml]
arbiter info plugins
arbiter info plugin <plugin>
arbiter info accounts <plugin>
arbiter info account <plugin> <account>
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

- `info`: summarize the server URL, deployment scope, installed plugins, and
  account descriptions/guidance.
- `info plugins`: list installed plugins.
- `info plugin <plugin>`: describe one plugin, its accounts, and its
  operations.
- `info accounts <plugin>`: list accounts for one plugin.
- `info account <plugin> <account>`: show one account plus its policy summary.
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

## mcp

Inspect and call raw MCP tools.

```bash
arbiter mcp [tools] [--json]
arbiter mcp call <tool-name> --args '<json-object>'
```

- `mcp` and `mcp tools`: list raw MCP tools.
- `mcp tools --json`: print full tool metadata as JSON.
- `mcp call`: call a raw MCP tool by name.
