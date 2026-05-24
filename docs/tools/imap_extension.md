# Tool Family: Future IMAP Extension

## Status

- Stage: `stage 2`
- Owner: `Mail Sentry server`
- Rollout gate: begin IMAP only after the SMTP send flow is stable

## Purpose

Define the planned IMAP tool family and the shared constraints that apply once IMAP implementation begins.

## Planned tools

- `list_messages`
- `get_message`
- `search_messages`
- `move_message`
- `mark_message_read`
- `delete_message`

## Common input rules

- Every IMAP tool must take `account` as a mandatory input.
- That `account` must reference an account with IMAP enabled.
- IMAP tools may take `folder` explicitly or default to `mail.accounts.<account>.imap.default_folder` when omitted.

## Shared behavior constraints

- operations are scoped to a single selected account
- folder names are interpreted within that selected account only
- cross-account search is out of scope for the initial IMAP release
- cross-account moves are out of scope for the initial IMAP release

## IMAP-specific design constraints

- support write-capable IMAP operations from the first IMAP implementation
- model accounts and folders explicitly
- keep destructive actions opt-in and separately gated
- preserve stable message identifiers where possible, while accounting for IMAP UID and folder scoping realities

## Configuration notes

The IMAP config is organized under an account. Within an account, tools should refer to configured folder names rather than raw user-provided folder strings.

Each IMAP-enabled account should define at least:

- an `imap` config block
- a human-readable account description
- one account-level `account_access_profile` reference
- a `folders` mapping keyed by stable folder names

Each configured folder should define at least:

- a stable folder name via the map key
- an optional description

Access profiles establish default policy:

- `bot`: defaults come from `mail.account_access_profiles.bot`
- `personal`: defaults come from `mail.account_access_profiles.personal`

When IMAP is added, write-capable behavior should still be controlled by `account_access_profile` policy and any future guardrails for sensitive accounts.

Confirmed policy model:

- replace the coarse profile-level `read_only` flag with explicit IMAP policy gates for `read`, `search`, `move`, and `delete`
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

- apply durable IMAP audit behavior from `mail.account_access_profiles.<profile>.imap_audit`
- audit state-changing operations such as flag changes, message moves, and deletes by default
- always audit destructive operations such as delete when enabled
- support separate configuration for read-access and search-query auditing because those can generate much higher event volume

## Out of scope for the initial IMAP release

- cross-account message operations
- folder-specific policy overrides
- folder-specific audit overrides

## Follow-up needed before implementation

- write one tool document per IMAP operation
- decide what approval hook, if any, is required before connecting a personal account
