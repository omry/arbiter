# Capability Discovery: Account Summaries

## Status

- Stage: `v1`
- Owner: `Agent Arbiter server`

## Purpose

Document the account-summary payload returned by `describe_caps` and
`describe_cap`, along with lightweight metadata needed to choose an account for
SMTP or IMAP operations.

## Intended usage

Use this when the caller needs to discover which accounts exist before
selecting one explicitly for `smtp:send_email` or an IMAP operation.

## Input shape

`describe_caps` accepts optional preview limits:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "account_preview_limit": {
      "type": "integer",
      "minimum": 0,
      "description": "Maximum account names to preview per capability"
    },
    "operation_preview_limit": {
      "type": "integer",
      "minimum": 0,
      "description": "Maximum operation names to preview per capability"
    }
  }
}
```

`describe_cap` takes one capability name:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["capability"],
  "properties": {
    "capability": {
      "type": "string",
      "description": "Capability name returned by list_caps"
    }
  }
}
```

## Output shape

`describe_caps` returns bounded capability summaries:

```json
{
  "capabilities": [
    {
      "id": "smtp",
      "description": "Send email through configured SMTP accounts.",
      "account_count": 1,
      "accounts": ["primary"],
      "accounts_truncated": false,
      "operation_count": 1,
      "operations": ["send_email"],
      "operations_truncated": false
    },
    {
      "id": "imap",
      "description": "Read and manage mail through configured IMAP accounts.",
      "account_count": 1,
      "accounts": ["primary"],
      "accounts_truncated": false,
      "operation_count": 6,
      "operations": [
        "delete_message",
        "get_message",
        "list_messages",
        "mark_message_read",
        "move_message",
        "search_messages"
      ],
      "operations_truncated": false
    }
  ]
}
```

Callers may pass `account_preview_limit` and `operation_preview_limit` to request
a smaller or larger preview. The server clamps those values to
`arbiter.discovery.max_account_preview_limit` and
`arbiter.discovery.max_operation_preview_limit`. A `*_truncated` field is `true`
when the returned preview is smaller than the full set.

`describe_cap` returns the detailed account map for one capability:

```json
{
  "id": "smtp",
  "description": "Send email through configured SMTP accounts.",
  "accounts": {
    "primary": {
      "description": "Bot-owned account for automated email tasks",
      "policy": "bot",
      "enabled": true,
      "send": "allowed",
      "require_confirmation": false
    }
  },
  "operations": [
    {
      "id": "smtp:send_email",
      "name": "send_email",
      "description": "Send a single email message through the configured SMTP submission server for the selected account."
    }
  ]
}
```

For IMAP, the `accounts` map includes IMAP-specific message and flag
capabilities:

```json
{
  "id": "imap",
  "accounts": {
    "primary": {
      "description": "Bot inbox",
      "policy": "bot",
      "enabled": true,
      "confirmation_required": ["delete"],
      "message": {
        "read_allowed": true,
        "move_allowed": true,
        "delete_allowed": true,
        "flags": {
          "seen": "read_only",
          "flagged": "read_write",
          "answered": "read_only",
          "deleted": "hidden",
          "draft": "hidden",
          "user": {
            "bot.followed_up": "read_write"
          }
        }
      }
    }
  }
}
```

The `arbiter accounts list` CLI command derives a compact view from
`describe_caps`, while `arbiter accounts desc <capability> [account]` uses
`describe_cap`.

Each service owns its own account names, so `arbiter.account.smtp.primary` and
`arbiter.account.imap.primary` are related only if the deployment chooses to
name them the same way.

## Operation details

Account summaries are discovery data returned by capability-description tools.

Expected behavior:

1. Ask each configured service runtime for its account summaries.
2. Return summaries grouped under the described capability.
3. Do not expose credentials or raw transport configuration.

### SMTP

- include `enabled`
- include `send`
- include `require_confirmation`
- include the selected policy name

`send` is a one-state availability enum in the current SMTP summary:

- `allowed`: SMTP is configured and this account may be used for
  `smtp:send_email`

### IMAP

- include `enabled`
- include the selected policy name
- include `confirmation_required`
- include message capabilities under `message`
- include flag capabilities under `message.flags`

## Policy checks

- Return all configured accounts under the current trusted-caller model.
- Do not expose credentials, transport configuration, recipient-policy
  configuration, audit configuration, or other sensitive internal settings.
- Do expose policy names, SMTP `require_confirmation`, and IMAP
  `confirmation_required` because callers use them for account selection and
  confirmation behavior.

## Test checklist

- returns configured accounts grouped by service
- returns SMTP `require_confirmation`
- returns IMAP `confirmation_required`
- exposes all standard IMAP system flags with their effective mode
- exposes configured IMAP user flags with their effective mode
- does not expose credentials or raw transport settings
