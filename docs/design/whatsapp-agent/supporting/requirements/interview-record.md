# Design interview record

This is the compact decision record for the first design round. It preserves decisions and unresolved work without turning the requirements document into a transcript.

## Round state

- Scope: isolated WhatsApp Agent Platform integration for Arbiter.
- Question allowance: at most eight questions for this round.
- Outcome: questioning closed after the named decisions were settled; no additional question was justified by the remaining allowance.
- Requirements approval: explicitly granted for the exact summary in `approval-summary.md`.
- Implementation authorization: not granted.

## Accepted decisions

| Decision | Result | Architectural effect |
| --- | --- | --- |
| Outbound boundary | Allow all Meta-supported outbound operations by default; Arbiter restrictions may be added later. | Keep provider creator-only enforcement and leave local recipient policy optional. |
| Policy surface | Expose explicit per-agent policy fields. | Make messages, statuses, media paths, and replay independently addressable. |
| Media policy | Separate ingress, download, egress, and deletion controls. | Media manager must evaluate four operations independently. |
| Defaults | Default-allow all Meta-supported operations. | No deny list or speculative restriction is required for the first implementation. |
| Token replacement | Operator supplies the replacement; plugin verifies and swaps transparently. | Credential activation must be atomic and failure-safe. |
| Replay | Fully allowed for now. | Support `offset=0` replay and deduplication without inventing a separate authorization boundary. |
| Status signals | Read receipts and typing indicators are independent from message permissions. | Status calls have their own policy fields. |

## Explicitly deferred

- Arbiter-side rate or burst limits. The provider manual documents per-agent limits, and no evidence of a gap was presented. Add only if an observed requirement appears.
- Additional replay restrictions. Add only if a distinct historical operation is introduced by the provider or by Arbiter storage.

## Unresolved engineering work

- Verify live account eligibility and regional/phone constraints.
- Verify live endpoint schemas and the exact sticker send/receive matrix.
- Verify media URL lifetime and deletion behavior.
- Select existing Arbiter storage primitives for cursors and deduplication.
- Complete independent security review before implementation readiness.

## Evidence

The accepted decisions are summarized in [requirements.md](../../requirements.md). The provider manual and other evidence files were intentionally not seeded into this restart. The final answers changed policy decomposition (status independence and replay treatment), but did not change the core trust boundary or the isolated-plugin architecture.
