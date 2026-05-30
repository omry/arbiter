# ADR 0001: Service Plugin Architecture

## Status

Proposed.

## Context

Agent Arbiter began as one MCP server for email operations. It currently exposes
SMTP and IMAP capabilities over a shared service-plugin configuration model.

The broader product direction is a platform for controlled agent access to
external services. Mail is the first domain, but the architecture should support
additional services such as CalDAV, CardDAV, and Sieve without forcing every
deployment to carry unused service configuration or runtime behavior.

`Agent Arbiter` is the platform name. The project has not had an initial release
yet, so package names, config keys, MCP tool names, and runtime identifiers are
still open to change before release.

## Decision

Use a service plugin architecture on the Python side.

Each service is represented by one plugin. SMTP and IMAP are separate plugin
distributions. Future services should follow the same model, without the core
treating any service plugin as built in.

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
- its own capability and operation descriptors
- service-specific API and policy semantics

Plugins receive only their own composed config node. A plugin should not receive
the whole application config by default.

## Configuration Model

Service activation is config-driven. Installed plugins are only available;
configured service account maps activate them.

Conceptually:

```yaml
arbiter:
  account:
    smtp:
      primary:
        policy: bot
        host: ${arbiter.etc.mailserver.host}
        port: ${arbiter.etc.mailserver.smtp_port}
        username: ${arbiter.etc.mailserver.username}
        password: ${arbiter.etc.mailserver.password}
        from_email: ${arbiter.etc.mailserver.username}

    imap:
      primary:
        policy: bot
        host: ${arbiter.etc.mailserver.host}
        port: ${arbiter.etc.mailserver.imap_port}
        username: ${arbiter.etc.mailserver.username}
        password: ${arbiter.etc.mailserver.password}
        default_folder: INBOX

  policy:
    smtp:
      bot:
        require_confirmation: false
    imap:
      bot:
        allow_read: true
        allow_search: true
```

Every non-empty key under `arbiter.account` is a configured service. The core
locates the plugin registered for that service key and passes a service runtime
config containing `arbiter.account.<service>` and `arbiter.policy.<service>` to
it.

Hydra composition should choose service defaults and variants. For example, a
deployment may compose a Google-specific SMTP schema into
`arbiter.account.smtp` without changing the service identity from `smtp`.

The `etc` node is weakly structured operator-owned configuration space. It is
intended for composition and interpolation material such as shared hostnames,
ports, usernames, secret references, and deployment constants.

Example:

```yaml
arbiter:
  etc:
    mailserver:
      host: mail.example.com
      username: agent@example.com
      password: ${secret_file:/run/secrets/mail_password}
      smtp_port: 587
      imap_port: 993
```

The core knows that `arbiter.etc` exists, but should not assign product
semantics to its contents. Typed service config remains under
`arbiter.account.*` and `arbiter.policy.*`.

## Plugin Discovery

Use Python package entry points for external plugin discovery.

Conceptually:

```toml
[project.entry-points."agent_arbiter.services"]
smtp = "agent_arbiter_smtp:plugin"
imap = "agent_arbiter_imap:plugin"
```

Namespace packages are not required for discovery. Plugin distributions can be
independent packages such as `agent-arbiter-smtp` with import packages such as
`agent_arbiter_smtp`.

The entry point group follows the package namespace.

Hydra config composition, not an explicit `provider` field, should select the
implementation variant for a service in the common case.

## Operator

The operator is the human or team deploying and configuring the server. The
operator decides which services and capabilities the agent can use through
configuration and policy.

## Implementation Staging

Because there is no released public contract yet, compatibility is not a
permanent constraint.

The first extraction stage introduced a plugin registration boundary:

- service plugins are discovered through entry points rather than a hard-coded
  central plugin list
- SMTP registration lives in the SMTP service plugin package
- IMAP registration lives in the IMAP service plugin package

The second extraction stage moved SMTP and IMAP operation behavior into
service-specific runtime objects. `AgentArbiterApp` remains only as a transitional
facade for account discovery and existing test helpers.

The third extraction stage introduced the first service-owned config shape.
The current shape makes accounts and policies service-scoped maps under
`arbiter`: `arbiter.account.smtp`, `arbiter.account.imap`,
`arbiter.policy.smtp`, and `arbiter.policy.imap`. The `arbiter.etc` node exists
as weakly structured operator-owned interpolation space, and configured account
maps now determine which installed plugins are activated.

Later stages should continue shrinking `AgentArbiterApp` before release.

## Consequences

This approach keeps the first refactor small while creating pressure toward the
broader platform shape.

It does not provide plugin isolation. External plugins run in-process, so
security boundaries must come from configuration, caller authentication,
deployment isolation, or a future subprocess model.
