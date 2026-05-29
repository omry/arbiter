# Agent Arbiter Playground

This directory contains a checked-in, no-secrets Hydra config for running a
local Agent Arbiter server during development.

The default playground starts a streamable HTTP MCP server with no configured
mail accounts. That keeps the first run safe: only the core `list_accounts`
tool is available, and it returns an empty account set.

Activate the repository virtualenv before running these commands.

From the repository root:

```bash
arbiter-server config check --config-path "$PWD/playground" --config-name config
arbiter-server serve --config-path "$PWD/playground" --config-name config
```

From this directory:

```bash
arbiter-server config check --config-path "$PWD" --config-name config
arbiter-server serve --config-path "$PWD" --config-name config
```

In another terminal, point the client at the playground server:

```bash
arbiter --url http://127.0.0.1:8025/mcp tools list
arbiter --url http://127.0.0.1:8025/mcp accounts list
```

Keep real credentials out of this directory. For local account experiments,
prefer environment-variable interpolation in a separate untracked config file
or a throwaway config under `/tmp`.
