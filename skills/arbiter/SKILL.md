---
name: arbiter
description: >-
  Use when an agent needs to work with Arbiter: discover what an Arbiter MCP
  server exposes, choose a capability, inspect accounts or operation schemas,
  run approved operations, or reason about Arbiter without direct access to
  service credentials.
---

# Arbiter

Use incremental discovery. Start with the smallest cheap query, then inspect
only the capability or operation needed for the task.

## First Discovery Step

When the MCP URL is known, get the capability index:

```bash
arbiter arbiter.mcp_url=http://127.0.0.1:8025/mcp cap format='{id}: {desc} ({num_accts} accounts)'
```

This answers: "What service surfaces are available here?"

Treat `version_info` as a connection and identity check, not as discovery. It
is useful before discovery when you need to confirm which Arbiter server you
are talking to.

## Discovery Sequence

1. List capabilities with `arbiter ... cap`.
2. Pick one capability and inspect it with `arbiter ... cap desc <capability>`.
3. Pick one operation and inspect its schema with
   `arbiter ... op desc <capability>:<operation>`.
4. Run an operation only after the capability, account context, and input schema
   are understood.

## Guardrails

- Do not ask for full operation schemas before the capability index unless the
  user already chose a capability.
- Do not request or expose upstream service credentials. Arbiter owns those.
- Quote shell arguments that contain braces or brackets, especially in zsh.
