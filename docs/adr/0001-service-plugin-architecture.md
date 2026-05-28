# ADR 0001: Service Plugin Architecture

## Status

Proposed.

## Context

Agent Arbiter began as one MCP server for mail operations. It currently exposes
SMTP and IMAP tools over a shared mail-shaped configuration model.

The broader product direction is a platform for controlled agent access to
external services. Mail is the first domain, but the architecture should support
additional services such as CalDAV, CardDAV, and Sieve without forcing every
deployment to carry unused service configuration or runtime behavior.

`Agent Arbiter` is the current platform name. `Oversight` remains a possible
future rename if the package name becomes available. The project has not had an
initial release yet, so package names, config keys, MCP tool names, and runtime
identifiers are still open to change before release.

## Decision

Use a service plugin architecture on the Python side.

Each service is represented by one plugin. SMTP and IMAP should become separate
plugin distributions. Future services should follow the same model, without the
core treating any service plugin as built in.

The core platform is responsible for:

- composing configuration with Hydra and OmegaConf
- registering Structured Config schemas
- discovering installed service plugins
- activating configured services
- bootstrapping the MCP server
- providing narrow runtime context to plugins

Each service plugin is responsible for:

- its own Structured Config schema
- validation of its own config node
- service-specific runtime construction
- its own MCP tools
- service-specific API and policy semantics

Plugins receive only their own composed config node. A plugin should not receive
the whole application config by default.

## Configuration Model

Service activation is config-driven. Installed plugins are only available;
configured service nodes activate them.

Conceptually:

```yaml
services:
  smtp:
    accounts:
      primary:
        host: ${etc.mailserver.host}
        port: ${etc.mailserver.smtp_port}
        username: ${etc.mailserver.username}
        password: ${etc.mailserver.password}
        from_email: ${etc.mailserver.username}

  imap:
    accounts:
      primary:
        host: ${etc.mailserver.host}
        port: ${etc.mailserver.imap_port}
        username: ${etc.mailserver.username}
        password: ${etc.mailserver.password}
        default_folder: INBOX
```

Every key under `services` is a configured service. The core locates the plugin
registered for that service key and passes `services.<service>` to it.

Hydra composition should choose service defaults and variants. For example, a
deployment may compose a Google-specific SMTP schema into `services.smtp`
without changing the service identity from `smtp`.

The `etc` node is weakly structured operator-owned configuration space. It is
intended for composition and interpolation material such as shared hostnames,
ports, usernames, secret references, and deployment constants.

Example:

```yaml
etc:
  mailserver:
    host: mail.example.com
    username: agent@example.com
    password: ${secret_file:/run/secrets/mail_password}
    smtp_port: 587
    imap_port: 993
```

The core knows that `etc` exists, but should not assign product semantics to
its contents. Typed service config remains under `services.*`.

## Plugin Discovery

Use Python package entry points for external plugin discovery.

Conceptually:

```toml
[project.entry-points."agent_arbiter.services"]
smtp = "agent_arbiter.plugins.smtp:plugin"
imap = "agent_arbiter.plugins.imap:plugin"
```

Namespace packages are not required for discovery. Plugin distributions can be
independent packages such as `agent-arbiter-smtp` with import packages such as
`agent_arbiter_smtp`.

The entry point group follows the current package namespace. A future rename can
move the group to `oversight.services`.

Hydra config composition, not an explicit `provider` field, should select the
implementation variant for a service in the common case.

## Operator

The operator is the human or team deploying and configuring the server. The
operator decides which services and capabilities the agent can use through
configuration and policy.

## Implementation Staging

Because there is no released public contract yet, compatibility is not a
permanent constraint. During refactoring, temporary compatibility can still be
useful as a staging tool because it keeps tests focused and reduces the number
of simultaneous moving parts.

The first extraction stage introduced a plugin registration boundary:

- service plugins are discovered through entry points rather than a hard-coded
  central plugin list
- SMTP MCP registration lives in a temporary in-tree SMTP plugin module
- IMAP MCP registration lives in a temporary in-tree IMAP plugin module

The second extraction stage moved SMTP and IMAP operation behavior into
service-specific runtime objects. `AgentArbiterApp` remains only as a transitional
facade for account discovery and existing test helpers.

The third extraction stage introduced the first `services.*` config shape.
Account metadata remains under `mail.accounts`, service-owned account transport
config moved under `services.smtp.accounts` and `services.imap.accounts`, `etc`
exists as weakly structured operator-owned interpolation space, and configured
service nodes now determine which installed plugins are activated.

Later stages should continue shrinking `AgentArbiterApp`, decide whether shared
access profiles remain the policy home, and perform any chosen rename before
release.

## Consequences

This approach keeps the first refactor small while creating pressure toward the
broader platform shape.

It does not provide plugin isolation. External plugins run in-process, so
security boundaries must come from configuration, caller authentication,
deployment isolation, or a future subprocess model.
