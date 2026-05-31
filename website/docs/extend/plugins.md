---
title: Writing Plugins
---

Service plugins own service-specific config, runtime behavior, and operation
metadata.

## Responsibilities

A plugin provides:

- structured config schemas
- account and policy bootstrap examples
- runtime version metadata
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

## Version contract

Plugins use compatibility-line versions. A plugin for Agent Arbiter core
`0.8.x` should use a plugin version on the `0.8` line, such as `0.8.0` or
`0.8.1`, and declare the same core API line at runtime:

```python
class ExampleServicePlugin:
    name = "example"
    version = "0.8.0"
    core_api_version = "0.8"
```

At plugin discovery and config registration time, Agent Arbiter rejects plugins
whose `core_api_version` does not match the loaded core API line. It also
rejects plugin package versions that are not on that same `major.minor` line.

Package dependencies should express the same compatibility line:

```toml
dependencies = [
  "agent-arbiter-core>=0.8.0,<0.9.0",
]
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
