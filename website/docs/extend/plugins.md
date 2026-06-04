---
title: Writing Plugins
---

Service plugins own service-specific config, runtime behavior, and operation
metadata.

For a copyable starting point, see
[`examples/plugins/echo`](https://github.com/omry/arbiter/tree/main/examples/plugins/echo).
It includes config schemas, bootstrap examples, runtime behavior, operation
metadata, entry-point wiring, and focused tests.

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
[project.entry-points."arbiter.services"]
echo = "arbiter_echo_example:plugin"
```

Use the plugin capability name as the entry-point key. Arbiter loads every
entry point in the `arbiter.services` group; it does not use the entry-point
key as the plugin id. If two installed plugins expose the same capability name,
server catalog construction fails with a duplicate capability error.

## Version contract

Plugins use compatibility-line versions. A plugin for Arbiter core
`0.9.x` should use a package version on the `0.9` line, such as
`0.9.0` or `0.9.1`, and declare the same core API line at runtime.
Prerelease versions such as `0.9.0.dev1` are useful for release validation but
are not the normal documentation target. The plugin runtime should derive
`version` from installed package metadata rather than duplicating the version
literal:

```python
from arbiter_core.version import distribution_version


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
  "arbiter-core>=0.9.0,<0.10.0",
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
