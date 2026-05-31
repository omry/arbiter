---
title: Writing Plugins
---

Service plugins own service-specific config, runtime behavior, and operation
metadata.

## Responsibilities

A plugin provides:

- structured config schemas
- account and policy bootstrap examples
- runtime construction
- capability descriptor
- operation descriptors and schemas
- operation invocation
- service-specific policy checks

## Entry point

Plugins are discovered through Python package entry points:

```toml
[project.entry-points."agent_arbiter.services"]
smtp = "agent_arbiter_smtp:plugin"
```

## Runtime boundary

Core passes only service-owned config to the plugin:

- `arbiter.account.<service>`
- `arbiter.policy.<service>`

The plugin should not need the full application config for ordinary operation.

## Operation ids

Operation ids are formed by the core from capability and operation names:

```text
<capability>:<operation>
```

For example:

```text
smtp:send_email
imap:list_messages
```

Plugins name operations within their capability; the core validates common
operation-id syntax and dispatches to the selected plugin.
