# Tool: list_accounts

## Status

- Stage: `v1`
- Owner: `Mail Sentry server`

## Purpose

Return the configured accounts available to the caller, along with lightweight
metadata needed to choose an account for SMTP or IMAP operations.

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
  "accounts": [
    {
      "name": "primary",
      "description": "Bot-owned account for automated email tasks",
      "account_access_profile": "bot",
      "smtp": {
        "send": "allowed",
        "require_confirmation": false
      },
      "imap": {
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
  ]
}
```

Always return `smtp` and `imap` objects.

`smtp.send` is a two-state availability enum in the current contract:

- `allowed`: SMTP is configured and this account may be used for `send_email`
- `unavailable`: this account does not have SMTP configured

If `imap.enabled` is `false`, `imap.message` is omitted.

## Operation details

`list_accounts` is a discovery operation.

Expected behavior:

1. Read the configured account map.
2. Construct the defined response shape from the configured accounts.

For each account, return these base fields:

- stable account name
- human-readable description
- current `account_access_profile` name

Return protocol capabilities under `smtp` and `imap`.

### SMTP

- always include `smtp.send`
- always include `smtp.require_confirmation`
- `smtp.require_confirmation` reflects
  `mail.account_access_profiles.<profile>.services.smtp.require_confirmation`
  when the account has SMTP enabled; otherwise it is `false`

### IMAP

- always include `imap.enabled`
- when `imap.enabled` is `true`, return:
  - `imap.confirmation_required`
  - message capabilities under `imap.message`
  - flag capabilities under `imap.message.flags`

Message capabilities:

- `imap.message.read_allowed`
- `imap.message.move_allowed`
- `imap.message.delete_allowed`

#### Flag Exposure

`list_accounts` exposes the effective IMAP flag capabilities for the account.

Under `imap.message.flags`:

- all standard system flags are always returned, with their effective mode
- system flags may be `hidden`, `read_only`, or `read_write`
- a system flag may still be returned as `hidden` so callers know it will not
  appear in later tool-visible message data
- `imap.message.flags.user` contains all explicitly configured user flags, with
  their effective mode
- configured user flags may use `hidden`, `read_only`, or `read_write`
- configured user flags with `hidden` are redundant and are not returned
- unconfigured user flags are not returned and remain implicitly unavailable

## Policy checks

- Return all configured accounts under the current trusted-caller model.
- Do not expose credentials, transport configuration, recipient-policy
  configuration, audit configuration, or other sensitive internal settings.
- Do expose `account_access_profile`, `smtp.require_confirmation`, and
  `imap.confirmation_required` because callers use them for account selection
  and confirmation behavior.

## Audit behavior

- Emit normal debug logs for tool invocation and result handling.
- No special durable audit requirement is defined for account discovery in the
  current design.

## Errors

- `CONFIGURATION_ERROR` when configured accounts cannot be loaded or normalized
  correctly
- `INTERNAL_ERROR` for unexpected failures

## Out of scope

- Caller authentication and filtering results by caller identity
- Exposing raw transport settings or secrets

## Test checklist

- returns all configured accounts
- returns `smtp.require_confirmation` for SMTP-enabled accounts
- returns IMAP `confirmation_required` for IMAP-enabled accounts
- returns hierarchical protocol capabilities under `smtp` and `imap`
- returns `smtp.send` as `allowed` or `unavailable`
- omits `imap.message` when IMAP is not enabled on an account
- exposes all standard IMAP system flags with their effective mode
- exposes configured IMAP user flags with their effective mode
- reflects the configured `account_access_profile`
- does not expose transport, recipient policy, or audit configuration
