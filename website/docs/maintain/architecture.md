---
title: Architecture
---

Arbiter is a policy-controlled service access runtime. Its capabilities
are loaded through service plugins, activated by config, and currently exposed
through MCP and CLI surfaces.

## Core responsibilities

- compose Hydra/OmegaConf config
- register structured config schemas
- discover installed service plugins
- activate configured services
- expose the current MCP and CLI access surfaces
- dispatch `run_op` calls to service runtimes

## Plugin responsibilities

- own service schemas and bootstrap examples
- build service runtimes
- describe capabilities and operations
- validate service semantics and enforce service policy

## Canonical MCP surface

- `info`
- `version_info`
- `run_op`

Service operations use ids such as `smtp:send_email` and
`imap:list_messages`.

## Repository shape

```text
core/   Arbiter server, client CLI, plugin contracts
smtp/   SMTP service plugin
imap/   IMAP service plugin
docs/   source markdown notes
website/ Docusaurus website
deploy/ Docker deployment material
```
