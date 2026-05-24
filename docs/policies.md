# Policies

## Purpose

Define cross-cutting safety, access, logging, and audit rules shared across tools.

## Trust and access policy

The current design assumes the caller is trusted once connected to the MCP server. Caller authentication between the bot and the MCP server is out of scope for now.

Current implications:

- `list_accounts` returns all configured accounts
- callers may explicitly select any configured account
- at this stage, policy enforcement is configuration-driven rather than caller-identity-driven

## Current Runtime Policies

- The caller may choose recipients, subject, and body.
- The caller must choose a configured account explicitly for `send_email`.
- The caller may not override SMTP transport settings.
- The caller may not override the `From` address.
- The caller may not override `Reply-To` in v1.
- The server validates basic recipient shape, but configured recipient-count limits are not enforced yet.
- The server enforces `allow_smtp_send` before SMTP submission.
- The server enforces IMAP read/search/move/delete gates before IMAP operations.
- `mark_message_read` requires `read_write` access to the standard `seen` flag.
- IMAP operations are scoped to configured accounts and configured folders.
- Operational debug logs and durable audit records are still design contracts.

## Audit policy

The server distinguishes between operational debug logs and a durable audit log.

### Debug logs

Debug logs are for troubleshooting and operational visibility.

The server should emit structured debug logs for:

- tool invocation start
- validation failure
- SMTP connection attempt
- SMTP submission result
- unexpected exception

Recommended debug-log fields:

- timestamp
- tool name
- recipient counts
- recipient domains
- generated message id
- error code
- retryable

Debug logs should have shorter retention than the audit log and should not include message bodies or secrets by default.

### Durable audit log

The durable audit log is for accountability, later review, and sensitive-account governance.

The durable audit log should:

- be enabled by default
- retain records for `365` days by default
- store message metadata by default
- avoid storing message bodies by default
- record state-changing actions and policy-relevant decisions

Protocol-specific audit policy:

- audit behavior is configured under `mail.account_access_profiles`
- SMTP audit settings are read from `mail.account_access_profiles.<profile>.smtp_audit`
- IMAP audit settings are read from `mail.account_access_profiles.<profile>.imap_audit`
- there is no per-account audit override in the current design
- IMAP state-changing operations such as flag changes, message moves, and deletes should generate durable audit records by default
- destructive IMAP operations such as delete should always produce durable audit records when the operation is enabled
- IMAP read access and search-query auditing may be configured separately because they can generate much higher event volume

Recommended audit fields:

- timestamp
- tool name
- account name when applicable
- folder name when applicable
- configured account access profile when applicable
- caller identity when available
- idempotency key when provided
- generated message id
- target recipient counts
- target recipient domains
- policy decision
- result status
- error code when applicable

For IMAP mutation operations, audit records should also include:

- target message identifier
- source folder when applicable
- destination folder when applicable
- previous and new state for flag changes when applicable

The durable audit log should be treated as a distinct storage and retention concern rather than a long-retained copy of ordinary debug logs.

## Rate limits and safety limits

The config schema includes these SMTP safety controls, but runtime enforcement remains open work:

- `mail.accounts.<account>.smtp.limits.max_messages_per_minute`
- `mail.accounts.<account>.smtp.limits.max_recipients_per_message`
- `mail.accounts.<account>.smtp.recipient_policy.allowed_domains`
- `mail.accounts.<account>.smtp.recipient_policy.blocked_domains`

## Future policy tightening

Before supporting a personal inbox, revisit at least these questions:

- whether sending should be restricted to approved recipient domains or exact addresses
- whether first-contact messages should require a separate approval step
- whether sending should be restricted to known correspondents
- whether inbox access should start as read-only before any write or delete operations are allowed
- what audit trail is required for message access, message sending, and destructive folder actions
- whether destructive IMAP operations should be disabled by default
- what approval hook is required before supporting a personal inbox

## Confirmed: split IMAP flag policy

The current `account_access_profile.read_only` model is replaced by explicit protocol policy and split IMAP flag policy.

The accepted replacement is:

- keep coarse protocol gates for:
  - `allow_smtp_send`
  - `imap.allow_read`
  - `imap.allow_search`
  - `imap.allow_move`
  - `imap.allow_delete`
- replace coarse IMAP write gating with two flag-policy groups:
  - `system_flags`
  - `user_flags`

Shared flag modes:

- `hidden`: do not expose the flag in tool-visible responses and do not allow mutation
- `read_only`: expose the flag in tool-visible responses but do not allow mutation
- `read_write`: expose the flag and allow mutation

Default behavior:

- unspecified `system_flags` default to `read_only`
- `user_flags` are opt-in and require explicit configuration

Why split the flag policy:

- standard IMAP system flags have stable semantics and are useful to the bot even when mutation is not allowed
- custom user flags should stay opt-in because they may encode operator-specific or client-specific workflows
- the bot may eventually use user flags such as a follow-up keyword, but that should require explicit configuration

Standard `system_flags` keys:

- `seen`
- `flagged`
- `answered`
- `deleted`
- `draft`

`user_flags` behavior:

- keys are literal custom keyword strings
- only listed keywords are visible to tools
- only listed keywords may be mutated

IMAP tools follow these rules:

- content read/search permissions come from the coarse IMAP policy, not from flag policy
- hidden flags should be omitted from tool-visible message responses
- user-flag defaults should remain deny-by-default to avoid leaking workflow-specific state

## Security considerations

- Store credentials outside source control.
- Treat personal account access as a separate trust tier.
- Fail closed on TLS validation errors.
- Avoid exposing raw protocol errors that may leak secrets.
- Enforce outbound rate limits to reduce abuse, loops, and accidental message storms.
- Keep durable audit data metadata-only by default and make retention configurable.
- Consider additional per-call safety checks before allowing broader usage.
