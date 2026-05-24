# Architecture

## V1 shape

The initial server is one MCP service with two tools:

- `list_accounts`
- `send_email`

Those tools should share the same:

- deployment-owned account configuration
- policy checks
- logging and audit behavior
- normalized error handling

The point of the implementation is not to build a generic mail framework. It is to provide a small, explicit server that can discover configured accounts and submit SMTP mail safely.

## Main responsibilities

- MCP handlers:
  accept tool calls, validate input, and return the documented response shapes
- Config loading:
  load configured accounts, sender identities, limits, recipient policy, and audit settings
- Send-email flow:
  resolve the selected account, apply policy checks, build the RFC 5322/MIME message, and submit it over SMTP
- Shared result handling:
  normalize failures into stable error codes and emit debug and audit records
- Future IMAP extension:
  add inbox and folder tools on top of the same account config and policy model rather than as a separate server

## Request lifecycle

1. MCP tool call received
2. Input validated against the tool contract
3. Selected account resolved from configuration
4. Policy and rate-limit checks applied
5. Service operation executed through the relevant transport adapter
6. Result normalized into the stable MCP response shape
7. Debug logs emitted, and durable audit records written when required by policy
8. Response returned

## Repository shape

```text
mail_sentry/
  README.md
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
  src/
    server.*
    config.*
    tools/
      list_accounts.*
      send_email.*
    services/
      mail_service.*
    transports/
      smtp_transport.*
      imap_transport.*   # added in stage 2
    policies/
      recipient_policy.*
      access_policy.*
  tests/
```

## Implementation notes

- Keep MCP tool handlers thin.
- Keep SMTP and future IMAP session handling out of tool handlers.
- Centralize policy evaluation, logging, audit, and error normalization.
- Implement one shared server that exposes multiple tools over the same config.
- Add IMAP by extending the same service rather than creating a separate server.
