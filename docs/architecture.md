# Architecture

## Current Shape

The server is one MCP service whose current SMTP and IMAP capabilities are
loaded through service plugins and activated by `arbiter.account.<service>`
configuration:

- `list_accounts`
- `send_email`
- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

Those tools should share the same:

- deployment-owned account configuration
- policy checks
- operational logging once implemented
- normalized error handling once the error model is implemented

The point of the implementation is not to build a generic mail framework. It is to provide a small, explicit server that can discover configured accounts, submit SMTP mail, and operate on configured IMAP folders safely.

The planned platform direction is captured in
[ADR 0001: Service Plugin Architecture](adr/0001-service-plugin-architecture.md).
SMTP and IMAP now register through the plugin boundary, own service-specific
runtime objects, and receive service-owned account and policy config from
`arbiter.account.<service>` and `arbiter.policy.<service>`. They live in
independently installable service-plugin projects so the core has no built-in
service status.

Agent-facing skills are intentionally not part of this package split yet. A
future skill integration should sit above the arbiter surface and compose
across installed services rather than belonging to `core/`, `smtp/`, or `imap/`.

## Main responsibilities

- MCP handlers:
  accept tool calls, validate input, and return the documented response shapes
- Config loading:
  load service-owned account config from `arbiter.account.*`, reusable policy
  config from `arbiter.policy.*`, and operator interpolation values from
  `arbiter.etc`; before Hydra composes config, core initializes all installed
  service plugins through their `register_configs` hook
- Config examples:
  let service plugins register canonical account and policy examples in their
  own Hydra ConfigStore groups beside `schema`
- Send-email flow:
  resolve the selected account, apply policy checks, build the RFC 5322/MIME message, and submit it through the SMTP runtime
- IMAP flow:
  resolve the selected account and configured folder, apply read/search/move/delete/flag policy, and execute the operation through the IMAP runtime
- Shared result handling:
  normalize failures into stable error codes and emit operational logs once those hardening pieces are implemented

## Request lifecycle

1. MCP tool call received
2. Input validated against the tool contract
3. Selected account resolved from configuration
4. Access-policy checks applied
5. Service operation executed through the relevant transport adapter
6. Tool result assembled for the MCP response
7. V1 hardening: normalized errors and operational logs
8. Response returned

## Repository shape

```text
agent-arbiter/
  README.md
  pyproject.toml     # shared test/type/format tool config, not a package
  noxfile.py         # repo-level orchestration across packages
  Dockerfile         # composed runnable product image
  core/
    pyproject.toml
    src/
      agent_arbiter/
        app.py       # account discovery facade
        config.py    # dataclass config schema and validation
        main.py      # FastMCP server bootstrap and core tool registration
        services.py  # service plugin contract and runtime registry
    tests/
  smtp/
    pyproject.toml
    src/
      agent_arbiter_smtp/
        __init__.py  # SMTP service plugin and runtime
        client.py    # SMTP transport adapter
    tests/
  imap/
    pyproject.toml
    src/
      agent_arbiter_imap/
        __init__.py  # IMAP service plugin and runtime
        client.py    # IMAP transport adapter
    tests/
  deploy/
    compose.yaml
    config.yaml
    readonly-imap/
  docs/
    overview.md
    architecture.md
    config.md
    policies.md
    errors.md
    tools/
      list_accounts.md
      send_email.md
      imap_extension.md
```

## Implementation notes

- Keep MCP tool handlers thin.
- Keep SMTP and IMAP session handling out of tool handlers.
- Keep account/folder access policy in service runtimes rather than transport adapters.
- Centralize logging and error normalization when those hardening pieces are implemented.
- Keep durable audit storage and audit policy configuration as post-v1 work.
- Keep repo-level integration adapters out of service packages unless they
  become distributable plugin packages themselves.
