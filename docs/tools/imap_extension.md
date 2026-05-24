# Tool Family: IMAP

## Status

- Stage: `implemented initial tool family`
- Owner: `Mail Sentry server`

## Purpose

Define the current IMAP tool family and the shared constraints that apply to IMAP operations.

## Tools

- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

## Common input rules

- Every IMAP tool takes `account` as a mandatory input.
- That `account` must reference an account with IMAP enabled.
- IMAP tools may take `folder` explicitly or default to `mail.accounts.<account>.imap.default_folder` when omitted.
- `message_id` values are IMAP UIDs returned by `list_messages` or `search_messages`; they are scoped to the selected account and folder.

## Shared behavior constraints

- operations are scoped to a single selected account
- folder names are interpreted within that selected account only
- folder names must be present in the selected account's configured `imap.folders` map
- cross-account search is out of scope
- cross-account moves are out of scope

## IMAP-specific design constraints

- model accounts and folders explicitly
- keep destructive actions opt-in and separately gated
- preserve stable message identifiers where possible, while accounting for IMAP UID and folder scoping realities

## Configuration notes

The IMAP config is organized under an account. Within an account, tools refer to configured folder names rather than arbitrary folder strings.

Each IMAP-enabled account should define at least:

- an `imap` config block
- a human-readable account description
- one account-level `account_access_profile` reference
- a `folders` mapping keyed by stable folder names

Each configured folder should define at least:

- a stable folder name via the map key
- an optional description

Access profiles establish policy. Account names such as `bot`, `personal`, or `alerts_readonly` are deployment-owned conventions, and accounts reference them through `account_access_profile`.

Write-capable behavior is controlled by `account_access_profile` policy and any client-side guardrails for sensitive accounts.

Confirmed policy model:

- explicit IMAP policy gates for `read`, `search`, `move`, and `delete`
- model IMAP flag access separately from content access
- split IMAP flag policy into:
  - `system_flags` for standard IMAP flags
  - `user_flags` for custom keywords
- use three flag modes for system flags:
  - `hidden`
  - `read_only`
  - `read_write`
- use three flag modes for configured user flags:
  - `hidden`
  - `read_only`
  - `read_write`
- `hidden` on a configured user flag is redundant and behaves like omitting that user flag from config
- default unspecified `system_flags` to `read_only`
- treat `mark_message_read` as a mutation of the standard `seen` system flag, requiring `read_write`
- treat bot-owned follow-up markers as explicit `user_flags` entries rather than implicit write access

## Audit behavior

The config includes IMAP audit settings under `mail.account_access_profiles.<profile>.imap_audit`, but durable audit storage is not implemented yet.

The target audit model is:

- apply durable IMAP audit behavior from `mail.account_access_profiles.<profile>.imap_audit`
- audit state-changing operations such as flag changes, message moves, and deletes by default
- always audit destructive operations such as delete when enabled
- support separate configuration for read-access and search-query auditing because those can generate much higher event volume

## Out of Scope

- cross-account message operations
- folder-specific policy overrides
- folder-specific audit overrides

## Follow-up

- write one tool document per IMAP operation
- decide what approval hook, if any, is required before connecting a personal account
- add OpenClaw wrapper coverage for IMAP tools if OpenClaw should use them before native MCP support exists
