---
title: Writing Plugins
---

An Arbiter plugin is a Python package that teaches Arbiter how to use one
service area, such as a mail provider, database, ticket system, or internal
API. The plugin owns the service-specific configuration, builds the runtime
client for that service, and describes the operations that agents can discover
and call through Arbiter.

For a copyable starting point, see
[`examples/plugins/echo`](https://github.com/omry/arbiter/tree/main/examples/plugins/echo).
It includes config schemas, bootstrap examples, runtime behavior, operation
metadata, entry-point wiring, and focused tests. Copy the structure, but rename
the package, import module, entry point, and capability before using it as a
real plugin.

## What You Build

A useful plugin usually has these pieces:

- a Python distribution package
- an `arbiter.services` entry point
- account and policy config schemas
- bootstrap templates for new accounts and policies
- a runtime object that talks to the service
- service access enforcement according to the configured policy
- operation descriptions and input schemas
- operation invocation code
- optional config checks and account tests

The server handles shared concerns such as loading plugins, composing config,
validating operation inputs, dispatching calls, and reporting plugin metadata.
The plugin should stay focused on the service it owns.

## 1. Choose Names And Package Metadata

Use one short capability name for the service, such as `smtp`, `imap`, or
`echo`. That name becomes the plugin id shown to users and the prefix for
operation ids.

The example service uses `echo` as its capability name, so the entry-point
label in the package metadata is also `echo`:

Plugins are discovered through Python package entry points:

```toml
[project.entry-points."arbiter.services"]
echo = "arbiter_echo_example:plugin"
```

The entry point target must return a plugin object that implements
`ServicePlugin`. Using `ServicePlugin` in the class and factory signatures gives
static type checkers a clear contract. Arbiter still validates the object
structurally at runtime; it does not require inheritance.

Arbiter loads every installed entry point in the `arbiter.services` group. The
entry-point key is a packaging label; Arbiter uses the plugin object's `name`
as the capability id. If two installed plugins expose the same capability name,
server startup fails with a duplicate capability error.

Plugins use compatibility-line versions. A plugin for Arbiter server `0.9.x`
should use a package version on the `0.9` line, such as `0.9.0` or `0.9.1`,
and the plugin object returned by the entry point must declare the same server
API line. In practice, the metadata and typed factory sit together:

```python
from arbiter_server.services import ServicePlugin
from arbiter_server.version import distribution_version


class ExampleServicePlugin(ServicePlugin):
    name = "example"
    version = distribution_version("arbiter-example", package_file=__file__)
    server_api_version = "0.9"

    # Implement the ServicePlugin methods here.


def plugin() -> ServicePlugin:
    return ExampleServicePlugin()
```

Declare the same compatibility line in package dependencies:

```toml
dependencies = [
  "arbiter-server>=0.9.0,<0.10.0",
]
```

At plugin discovery and config registration time, Arbiter rejects plugins whose
`server_api_version` does not match the loaded server API line. It also rejects
plugin package versions that are not on that same `major.minor` line.

## 2. Define Account And Policy Config

Accounts identify configured service connections. Policies describe what those
accounts are allowed to do. Keep them separate: bootstrapping commonly creates
one policy per account, but operators can point multiple accounts at the same
policy when that is useful.

Register plugin-owned schemas under the service-specific config groups:

```python
def register_configs(config_store: ConfigStore) -> None:
    config_store.store(
        group="arbiter/account/echo",
        name="schema",
        node=EchoConfig,
        provider="arbiter-echo-example",
    )
    config_store.store(
        group="arbiter/policy/echo",
        name="schema",
        node=EchoPolicyConfig,
        provider="arbiter-echo-example",
    )
```

The server passes only service-owned config to the plugin:

- `arbiter.account.<service>`
- `arbiter.policy.<service>`

The plugin should not need the full application config for ordinary operation.

Provide bootstrap templates for the config files operators create most often:

```python
def bootstrap_config(self, *, kind: str, name: str) -> object | None:
    if kind == "account":
        return _account_template(name=name, policy_name=f"{name}_policy")
    if kind == "policy":
        return _policy_template(name=f"{name}_policy")
    return None
```

## 3. Build The Runtime

The runtime is the service implementation. It receives the already-composed
accounts and policies for this plugin, validates relationships such as policy
references, and owns the code that talks to the external service.

```python
def build_runtime(
    self,
    accounts: Mapping[str, object],
    policies: Mapping[str, object],
    context: ServiceRuntimeContext,
) -> object:
    return EchoRuntime(accounts=accounts, policies=policies)
```

Use `ServiceRuntimeContext.dependencies` for shared server-provided helpers
such as storage, artifact handling, client factories, or test doubles. Keep
credentials and service-specific rules in plugin config and policy, not in
operation arguments.

## 4. Describe The Agent Surface

The capability description tells users what the plugin does:

```python
def describe_capability(self, context: ServicePluginContext) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=self.name,
        description="Echo messages through configured example accounts.",
    )
```

Each operation has a short name, a description, and a JSON Schema input schema:

```python
OperationDescriptor(
    name="echo_message",
    description="Return a policy-checked echo response for the selected account.",
    input_schema={
        "type": "object",
        "properties": {
            "account": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["account", "message"],
        "additionalProperties": False,
    },
)
```

Arbiter forms operation ids from the capability and operation name:

```text
<capability>:<operation>
```

For example:

```text
smtp:send_email
imap:list_messages
```

Keep operation inputs about the requested action. Do not expose transport
details, credentials, or policy toggles as operation parameters.

## 5. Invoke Operations

Before invocation, Arbiter validates arguments against the operation's input
schema. The plugin then dispatches the operation to its runtime and enforces
service-specific policy:

```python
def invoke_operation(
    self,
    operation: str,
    arguments: Mapping[str, object],
    context: ServicePluginContext,
) -> object:
    if operation != "echo_message":
        raise ValueError(f"unknown echo operation: {operation}")

    runtime = context.runtimes.require(self.name, EchoRuntime)
    return runtime.echo_message(
        account=cast(str, arguments["account"]),
        message=cast(str, arguments["message"]),
    )
```

Return small structured results with the information the caller needs. Keep
service clients, connection pools, and temporary state inside the runtime.

## 6. Add Operator Feedback

Config checks help operators catch mistakes before they start using the
deployment. Implement `check_config` when the plugin can validate references,
required settings, policy shape, or service-specific invariants from local
config alone.

Runtime account summaries and account tests improve discovery and deployment
checks:

- `account_summaries()` returns user-facing account details.
- `test_accounts()` verifies configured accounts. For some services this is a
  local validation; for others it may be a live connection check.

Use warnings for questionable but allowed config. Raise a config-check error
for config that cannot work.

## 7. Test The Plugin

Use the echo plugin tests as the basic pattern. Cover:

- runtime policy enforcement
- config validation and unknown policy references
- operation discovery and input schema
- operation invocation through the catalog
- account summaries and account tests, if implemented
- bootstrap output for account and policy files
- error messages for common operator mistakes

Prefer focused unit tests for plugin logic. Add live service tests only where a
real service boundary must be verified.

## Author Checklist

Before publishing or installing a plugin, confirm that:

- the distribution package installs cleanly
- the `arbiter.services` entry point returns a plugin object
- `name`, `version`, and `server_api_version` are correct
- config schemas register under the plugin's account and policy groups
- bootstrap creates useful account and policy examples
- operations have clear descriptions and strict input schemas
- policy is enforced in runtime behavior, not only in docs
- config checks catch the mistakes operators are likely to make
- tests exercise discovery, invocation, policy, and failure paths
