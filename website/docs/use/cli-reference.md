---
title: Arbiter CLI Reference
---

`arbiter` is the client-facing command for agents and humans. It talks to an
Arbiter MCP server and exposes capability, account, operation, and raw
MCP commands.

Most users should start with `cap`, `accounts`, and `op`. The raw `mcp`
commands are available for inspection and debugging.

Arbiter keeps MCP tool count small by exposing a hierarchical discovery
surface. Start broad with capabilities, then drill into accounts and operation
schemas before running an operation:

```bash
arbiter cap
arbiter cap desc smtp
arbiter accounts desc smtp bot
arbiter op desc smtp:send_email
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
arbiter cap arbiter.mcp_url=http://127.0.0.1:8000/mcp
```

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
arbiter cap
arbiter cap desc smtp
arbiter accounts
arbiter accounts desc smtp bot
arbiter op desc smtp:send_email
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```

## cap

Discover capability names and descriptions.

```bash
arbiter cap [list] [--json]
arbiter cap desc [capability]
```

- `cap` and `cap list`: list capability names.
- `cap list --json`: print capability names as JSON.
- `cap desc`: describe all capabilities with bounded summaries.
- `cap desc <capability>`: describe one capability.
- `capabilities` is an alias for `cap`.
- `describe` is an alias for `desc`.

Bounded summaries include capability descriptions, account counts, operation
counts, and limited previews. Operators configure preview limits under
`arbiter.discovery`.

## accounts

Inspect configured accounts exposed through capabilities.

```bash
arbiter accounts [list] [--json]
arbiter accounts desc <capability> [account]
```

- `accounts` and `accounts list`: list accounts grouped by capability.
- `accounts list --json`: print account names as JSON.
- `accounts desc <capability>`: describe accounts for one capability.
- `accounts desc <capability> <account>`: describe one account.
- `describe` is an alias for `desc`.

## op

Inspect and run operations.

```bash
arbiter op desc <operation-id>
arbiter op run <operation-id> --args '<json-object>'
```

- `op desc`: describe one operation, such as `smtp:send_email`.
- `op run`: run one operation with JSON arguments.
- `operation` is an alias for `op`.
- `describe` is an alias for `desc`.

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
