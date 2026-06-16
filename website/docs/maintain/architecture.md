---
title: Architecture
---

Arbiter is a policy-controlled service access runtime. Its capabilities
are loaded through service plugins, activated by config, and currently exposed
through native HTTP and CLI surfaces.

## Server responsibilities

- compose Hydra/OmegaConf config
- register structured config schemas
- discover installed service plugins
- activate configured services
- expose the current native HTTP and CLI access surfaces
- dispatch operation calls to service runtimes

## Plugin responsibilities

- own service schemas and bootstrap examples
- build service runtimes
- describe capabilities and operations
- validate service semantics and enforce service policy

## Canonical HTTP Surface

- `GET /_health_`
- `GET /api/v1/info`
- `GET /api/v1/plugins`
- `GET /api/v1/plugins/{plugin}/operations`
- `GET /api/v1/operations/{operation}`
- `POST /api/v1/operations/{operation}`

Service operations use ids such as `smtp:send_email` and
`imap:list_messages`.

## Repository shape

```text
server/   Arbiter server, plugin contracts
plugins/smtp/   SMTP service plugin
plugins/imap/   IMAP service plugin
docs/   source markdown notes
website/ Docusaurus website
deploy/ Docker deployment material
```
