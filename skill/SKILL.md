---
name: arbiter
description: >-
  Use when an agent needs to work with Arbiter: discover what an Arbiter server
  exposes, choose a capability, inspect operation schemas, run approved
  operations, or reason about Arbiter without direct access to service
  credentials.
---

# Arbiter

Use incremental discovery. Start with `info server` for server orientation,
then inspect only the plugin, account, or operation needed for the task.
Note: client binary is inside bin/arbiter[.exe] near this skill file.

## First Discovery Step

When the URL is known, get the server orientation summary:

```bash
arbiter arbiter.url=http://127.0.0.1:8075 info server
```

This answers: "Which Arbiter server is this?"

Discovery commands print JSON by default so agents and scripts can consume
them directly. If you need to read output in a terminal, pipe it to `jq` once
rather than changing the discovery flow:

```bash
arbiter arbiter.url=http://127.0.0.1:8075 plugins | jq
```

## Discovery Sequence

1. Start with `arbiter ... info server`.
2. List plugins with `arbiter ... plugins` if the service list is needed.
3. Inspect one plugin with `arbiter ... plugins <plugin>`.
4. Inspect operation schemas with `arbiter ... op desc <plugin>:<operation>`.
5. Run an operation only after the plugin context, operation purpose, and input
   schema are understood.

## Guardrails

- Treat plugin summaries and operation descriptions as the primary discovery
  path.
- Do not ask for full operation schemas before choosing a relevant plugin.
- Do not request or expose upstream service credentials. Arbiter owns those.
- Use YAML only for human reading, such as
  `arbiter ... info server --yaml`.
