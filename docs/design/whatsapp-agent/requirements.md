# WhatsApp Agent Platform integration

## Outcome and scope

Provide an optional, isolated Arbiter plugin for the WhatsApp Agent Platform. Agents use Arbiter's operation catalog. Arbiter keeps the provider credential private and applies operator-configured policy per WhatsApp provider account before each operation.

The owner clarified that "per-agent" means each configured WhatsApp provider agent/account. The plugin retains Arbiter's current trusted-local boundary: every caller that can reach the server can use its configured accounts and each account's policy. Account names are not caller authorization tiers. Runtime caller authentication and caller-specific grants belong to the separate authentication project. This plugin does not claim isolation between callers sharing an Arbiter server. See the [owner clarification](supporting/requirements/policy-boundary.md).

The plugin supports provider-documented updates, messages, read receipts, typing and media. It preserves the provider's creator-only outbound boundary. Allowing an operation does not add a provider capability or permit another recipient.

## Retained owner decisions

The [interview summary](supporting/requirements/approval-summary.md), [public-contract approval](supporting/high-level-design/contract-approval.md) and [enrollment approval](supporting/high-level-design/credential-enrollment-approval.md) record the selected scope. Their approved preimages remain unchanged. Earlier review stamps and agent-authored implementation claims supply no approval for this clean design.

- Default every defined, provider-supported operation policy to allowed.
- Expose explicit policy fields per configured WhatsApp provider account for updates, sending messages, read receipts, typing, media ingress, media download, media egress and media deletion.
- Normal polling and retained replay use the same updates permission. A replay selector adds no separate authorization boundary.
- Permit retained replay from offset zero. Keep normal continuation independent from replay continuation.
- Require automated replies to identify their originating inbound message. Suppress repeated submissions for that account and source across replay, concurrent callers and restart.
- Preserve proactive sends without a reply source. The plugin cannot infer whether unlabelled text is an automated reply; automation must use the source-keyed path.
- Keep read receipts and typing independent from message permission. Every actual provider side effect must be permitted: combined typing/read behavior requires both permissions.
- Keep media ingress, download, egress and deletion independently configurable. Download cannot bypass denied ingress; sending media requires both sending and egress permission.
- Verify an operator-supplied replacement credential against the enrolled provider identity before activation. Failed verification preserves the committed local credential.
- Establish the first binding through a fresh creator-chat challenge within local account registration. Unfinished setup preserves configuration and can restart with a fresh challenge.

## Administration and trust

Only the local server operator administers credentials, account bindings and policies through the existing configuration and CLI boundary. Agent operations cannot perform these actions. Provider data, messages, errors and media locators are untrusted and cannot confer administrative authority.

Keep credential bytes out of agent-visible data, command arguments and logs. Keep challenge text out of operator diagnostics; show the current challenge only in the operator terminal. Provider-retained challenges remain ordinary untrusted creator messages in polling and replay. Only the fresh challenge of the current enrollment attempt can establish a binding.

Reject unknown or unmapped operations and malformed policy before provider work. An omitted field for a known policy uses its approved allow default. An explicit deny must prevent its effect, including artifact consumption undertaken only for that effect.

## Observable behavior

### Updates and replies

The caller selects an explicit initial offset. Empty polls retain a known request offset. Normal continuation persists the provider's exact cursor. Replay returns a separate continuation without advancing normal polling. Fetch, decoding or storage failures must not lose the committed continuation.

Observing a message does not mark it replied to. Retained replay can return it again. Reply admission binds an observed source to its account, participant and outbound payload. Identical repeated calls return the stored outcome; a different payload for the same source conflicts.

Reserve durably before submission. An interrupted or uncertain submission must remain uncertain and must not cause an automatic resend. Report accepted delivery separately from unknown delivery. Provider acceptance followed by local recording failure must report partial failure with unsafe retry guidance. A failure proven to precede submission may release its reservation.

Retain source and reply state for every provider-supported replay of that source. Define crash recovery, multi-process exclusion, cleanup and storage-exhaustion behavior in the detailed design. Retained provider replay supplies recovery only while the provider retains the messages; this is not exactly-once update delivery.

### Media and statuses

Denied ingress retains the inbound message envelope but withholds usable media handles and metadata. Metadata results omit provider download URLs. Downloads validate the trusted destination, size and digest before returning an Arbiter artifact.

Upload accepts an existing same-store artifact capability, including one produced by another plugin. It cannot read arbitrary host paths or fetch an arbitrary input URL. Preserve existing capability, expiry and one-time consumption. A consumed input requires a fresh artifact after upload failure.

Only a confirmed provider deletion returns success. An indistinguishable unavailable or other-account media error cannot establish ownership or successful deletion. Unsupported typing cancellation returns a bounded unsupported result before transport.

### Enrollment and replacement

An unbound account is unavailable to agent operations. Registration uses a bounded read-only scan followed by a new unpredictable creator-chat challenge. Verification leaves normal polling state unchanged. Publish the verified identity and credential together. Reject conflicting local bindings and preserve cursor and reply state.

Cancellation, timeout and interruption before publication preserve account configuration and leave the account unbound. A new attempt requires a fresh challenge. After uncertain publication, inspect committed state before retrying; never overwrite an existing valid binding during recovery. Requests use one complete committed identity/credential snapshot.

## Acceptance and evidence

- Exercise policy decisions through the real operation entry points, including multi-effect denial and malformed configuration.
- Verify explicit initial offsets, empty/status-only pages, replay isolation, exact provider identities and safe decoding failures.
- Verify reply conflicts, repeated calls, process crashes on both sides of submission and recovery after uncertain outcomes without resending.
- Verify fresh enrollment, wrong-chat rejection, cancel/reopen behavior, concurrent aliases, interrupted publication and same-agent replacement.
- Verify artifact capability checks, concurrent HTTP/plugin consumption, provider origin checks, bounded reads, digest failures and deletion outcomes.
- Verify credential and diagnostic redaction, local permissions and durable publication on each supported platform.
- Review the complete design independently for security before implementation admission. Run integrated implementation and security acceptance before rollout.

External provider eligibility, schemas, media limits and lifetimes need evidence with explicit provenance. Historical observations are inputs to validate, not current guarantees. Small local probes can establish feasibility of selected repository primitives; implemented behavior still requires implementation acceptance.

## Deferred scope and authorization

Arbiter-side rate or burst limits remain deferred unless an observed requirement establishes a gap. Additional replay restrictions remain deferred unless a distinct historical operation introduces a new authorization boundary.

The design task includes requirements, architecture, engineering details and delivery slices. Product implementation, live account operations and deployment require their own authorization. The current owner separately authorized publishing the completed design as a PR and obtaining remote review.
