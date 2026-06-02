---
title: Writing Plugins
---

Service plugins own service-specific config, runtime behavior, and operation
metadata.

## Responsibilities

A plugin provides:

- structured config schemas
- account and policy bootstrap examples
- runtime package metadata
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

Plugins use compatibility-line versions. A plugin for Arbiter core
`0.9.x` should use a package version on the `0.9` line, such as
`0.9.0.dev1`, `0.9.0`, or `0.9.1`, and declare the same core API line at
runtime. The plugin runtime should derive `version` from installed package
metadata rather than duplicating the version literal:

```python
from agent_arbiter.version import distribution_version


class ExampleServicePlugin:
    name = "example"
    version = distribution_version("arbiter-example", package_file=__file__)
    core_api_version = "0.9"
```

At plugin discovery and config registration time, Arbiter rejects plugins
whose `core_api_version` does not match the loaded core API line. It also
rejects plugin package versions that are not on that same `major.minor` line.

Package dependencies should express the same compatibility line:

```toml
dependencies = [
  "arbiter-core>=0.9.0.dev1,<0.10.0",
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
