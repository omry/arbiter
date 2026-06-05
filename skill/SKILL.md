---
name: arbiter
description: >-
  Use when an agent needs to work with Arbiter: discover what an Arbiter MCP
  server exposes, choose a capability, inspect accounts or operation schemas,
  run approved operations, or reason about Arbiter without direct access to
  service credentials.
---

# Arbiter

Use incremental discovery. Start with `info` for orientation, then inspect only
the plugin, account, or operation needed for the task.
Note: client binary is inside bin/arbiter[.exe] near this skill file.

## First Discovery Step

When the MCP URL is known, get the server and account orientation summary:

```bash
arbiter arbiter.mcp_url=http://127.0.0.1:8025/mcp info
```

This answers: "Which Arbiter server is this, which plugins are installed, and
which accounts are available for what?"

`info` prints JSON by default so agents and scripts can consume it directly. If
you need to read it in a terminal, pipe it to `jq` once rather than changing the
discovery flow:

```bash
arbiter arbiter.mcp_url=http://127.0.0.1:8025/mcp info | jq
```

## Discovery Sequence

1. Start with `arbiter ... info`.
2. List plugins with `arbiter ... info plugins` if only the service list is
   needed.
3. Inspect one plugin with `arbiter ... info plugin <plugin>`.
4. Inspect account intent and policy with
   `arbiter ... info account <plugin> <account>`.
5. Inspect operation schemas with `arbiter ... info op <plugin> <operation>`.
6. Run an operation only after the plugin, account context, policy summary, and
   input schema are understood.

## Guardrails

- Treat account descriptions and guidance as the primary place for
  user/operator intent.
- Do not ask for full operation schemas before choosing a relevant plugin and
  account.
- Do not request or expose upstream service credentials. Arbiter owns those.
- Use YAML only for human reading: `arbiter ... info --yaml`.
