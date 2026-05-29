# Policies

## Purpose

Define cross-cutting safety, access, and logging rules shared across tools.

## Trust and access policy

The current design assumes the caller is trusted once connected to the MCP
server. Caller authentication between the bot and the MCP server is out of
scope for v1.

Current implications:

- `list_accounts` returns all configured accounts
- callers may explicitly select any configured account
- at this stage, policy enforcement is configuration-driven rather than caller-identity-driven
- Agent Arbiter is the authority boundary: the bot can access whatever Agent
  Arbiter exposes through configured accounts, enabled services, and policy
  objects
- account names and descriptions, including labels such as `personal`, are
  advisory context for caller behavior, not built-in enforcement tiers

The current implementation uses service-scoped policies under `arbiter`:

- SMTP accounts reference `arbiter.policy.smtp.<policy>` through
  `arbiter.account.smtp.<account>.policy`
- IMAP accounts reference `arbiter.policy.imap.<policy>` through
  `arbiter.account.imap.<account>.policy`

## Current Runtime Policies

- The caller may choose recipients, subject, and body.
- The caller must choose a configured account explicitly for `send_email`.
- The caller may not override SMTP transport settings.
- The caller may not override the `From` address.
- The caller may not override `Reply-To` in v1.
- The server validates basic recipient shape.
- The server enforces configured SMTP recipient policy and `max_recipients_per_message` before submission.
- The server enforces IMAP read/search/move/delete gates before IMAP operations.
- `mark_message_read` requires `read_write` access to the standard `seen` flag.
- `list_accounts` exposes SMTP `require_confirmation` and IMAP `confirmation_required` metadata for each account.
- IMAP operations are scoped to configured accounts and configured folders.
- Operational logging is v1 hardening work.
- Durable audit records are parked for post-v1.

## Logging policy

Operational logs are for troubleshooting and deployment visibility.

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

Operational logs should not include message bodies or secrets by default.

## Post-v1 Audit Policy

Durable audit storage is not part of v1. It is parked for post-v1 so v1 does
not ask operators to configure audit behavior that the runtime cannot honor.

The future durable audit log is for accountability, later review, and
sensitive-account governance.

The durable audit log should:

- be enabled by default
- retain records for `365` days by default
- store message metadata by default
- avoid storing message bodies by default
- record state-changing actions and policy-relevant decisions

Future protocol-specific audit policy:

- decide whether audit belongs under `arbiter.policy.<service>.<policy>` or a
  separate policy block
- define the final SMTP audit settings shape
- define the final IMAP audit settings shape
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

The config schema includes these SMTP safety controls:

- `arbiter.policy.smtp.<policy>.limits.max_messages_per_minute`
- `arbiter.policy.smtp.<policy>.limits.max_recipients_per_message`
- `arbiter.policy.smtp.<policy>.recipient_policy.allowed_recipients`
- `arbiter.policy.smtp.<policy>.recipient_policy.blocked_recipients`
- `arbiter.policy.smtp.<policy>.recipient_policy.allowed_domain_patterns`
- `arbiter.policy.smtp.<policy>.recipient_policy.blocked_domain_patterns`
- `arbiter.policy.smtp.<policy>.idempotency.expiration_days`

Current runtime status:

- recipient policy is enforced
- `max_recipients_per_message` is enforced
- `max_messages_per_minute` is enforced as a per-account, per-process rolling
  60-second limit
- startup rejects non-default `idempotency.expiration_days` because
  replay/conflict handling is not implemented yet

Caller confirmation policy is configured through:

- `arbiter.policy.smtp.<policy>.require_confirmation`
- `arbiter.policy.imap.<policy>.confirmation_required`

## Future boundary hardening

Before broadening deployment, revisit at least these questions:

- whether sending should be restricted to approved recipient domains or exact addresses
- whether first-contact messages should require a separate approval step
- whether sending should be restricted to known correspondents
- whether inbox access should start as read-only before any write or delete operations are allowed
- whether destructive IMAP operations should be disabled by default
- whether the bot-to-Agent Arbiter MCP connection needs caller authentication or
  authorization, such as a shared secret, bearer token, password, client
  certificate, or mTLS/PKI

## Current IMAP flag policy

The current IMAP policy model uses explicit protocol gates plus split IMAP flag
policy:

- keep coarse protocol gates for:
  - `arbiter.policy.imap.<policy>.allow_read`
  - `arbiter.policy.imap.<policy>.allow_search`
  - `arbiter.policy.imap.<policy>.allow_move`
  - `arbiter.policy.imap.<policy>.allow_delete`
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
- Treat account names and descriptions as advisory labels only.
- Put every must-enforce access decision in Agent Arbiter config.
- Fail closed on TLS validation errors.
- Avoid exposing raw protocol errors that may leak secrets.
- Enforce outbound rate limits to reduce abuse, loops, and accidental message storms.
- Keep durable audit data metadata-only by default and make retention configurable.
- Consider bot-to-Agent Arbiter authentication before allowing broader usage.
