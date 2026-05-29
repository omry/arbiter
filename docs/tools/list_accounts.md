# Tool: list_accounts

## Status

- Stage: `v1`
- Owner: `Agent Arbiter server`

## Purpose

Return the configured service accounts available to the caller, along with
lightweight metadata needed to choose an account for SMTP or IMAP operations.

## Intended usage

Use this when the caller needs to discover which accounts exist before
selecting one explicitly for `send_email` or an IMAP tool.

## Input shape

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {}
}
```

## Output shape

```json
{
  "accounts": {
    "smtp": {
      "primary": {
        "description": "Bot-owned account for automated email tasks",
        "policy": "bot",
        "enabled": true,
        "send": "allowed",
        "require_confirmation": false
      }
    },
    "imap": {
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
}
```

Return one object per active service. Each service owns its own account names,
so `arbiter.account.smtp.primary` and `arbiter.account.imap.primary` are
related only if the deployment chooses to name them the same way.

## Operation details

`list_accounts` is a discovery operation.

Expected behavior:

1. Ask each configured service runtime for its account summaries.
2. Return summaries grouped under `accounts.<service>`.
3. Do not expose credentials or raw transport configuration.

### SMTP

- include `enabled`
- include `send`
- include `require_confirmation`
- include the selected policy name

`send` is a one-state availability enum in the current SMTP summary:

- `allowed`: SMTP is configured and this account may be used for `send_email`

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
