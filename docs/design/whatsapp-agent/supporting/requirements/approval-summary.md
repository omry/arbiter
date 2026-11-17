# Requirements approval summary

## Approved outcome

Build an isolated Arbiter plugin for the WhatsApp Agent Platform. Arbiter keeps the provider credential inside the plugin and exposes provider-supported operations through explicit per-agent policy fields with allow-all defaults.

## Approved scope

- Preserve the provider creator-only outbound boundary.
- Keep message, status, media direction, and retained-update replay policy decisions independently addressable.
- Support retained-update replay, including `offset=0`, with cursor persistence and deduplication before automated replies.
- Verify operator-supplied replacement credentials before activation and keep the active credential when verification fails.
- Keep the credential out of agent-visible data and logs.

## Deferred and unresolved work

- Arbiter-side rate or burst limits remain deferred unless evidence establishes a gap.
- Additional replay restrictions remain deferred unless the provider or Arbiter storage introduces a distinct historical operation.
- Live account eligibility and regional or phone constraints, endpoint schemas including sticker behavior, media URL lifetime and deletion behavior, cursor and deduplication storage primitives, and the independent security review remain open.

## Approval boundary

This approval covers the requirements in `../../requirements.md`. It does not approve HLD or LLD details, implementation, publication, deployment, account operation, or security compliance claims.
