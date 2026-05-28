# Architecture

## Current Shape

The server is one MCP service exposing SMTP and IMAP tools over one deployment-owned configuration model:

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
That direction keeps the current public mail surface compatible for the next
refactor while moving SMTP and IMAP toward separate first-party service plugins.

## Main responsibilities

- MCP handlers:
  accept tool calls, validate input, and return the documented response shapes
- Config loading:
  load configured accounts, sender identities, limits, and recipient policy
- Send-email flow:
  resolve the selected account, apply policy checks, build the RFC 5322/MIME message, and submit it over SMTP
- IMAP flow:
  resolve the selected account and configured folder, apply read/search/move/delete/flag policy, and execute the operation over IMAP
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
mail-sentry/
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
    mail_sentry/
      app.py       # application/service layer and policy checks
      config.py    # dataclass config schema and validation
      main.py      # FastMCP server and tool registration
      smtp.py      # SMTP transport adapter
      imap.py      # IMAP transport adapter
  tests/
```

## Implementation notes

- Keep MCP tool handlers thin.
- Keep SMTP and IMAP session handling out of tool handlers.
- Keep account/folder access policy in the application layer rather than transport adapters.
- Centralize logging and error normalization when those hardening pieces are implemented.
- Keep durable audit storage and audit policy configuration as post-v1 work.
- Implement one shared server that exposes multiple tools over the same config.
