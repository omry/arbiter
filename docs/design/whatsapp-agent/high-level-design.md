# WhatsApp Agent Platform architecture

This architecture implements the [requirements](requirements.md) through one optional `arbiter-whatsapp` service plugin. The public behavior follows the original [contract approval](supporting/high-level-design/contract-approval.md) and [enrollment approval](supporting/high-level-design/credential-enrollment-approval.md). The [source inspection](supporting/high-level-design/source-notes.md) records existing foundations and missing interfaces.

## Components and authority

| Component | Responsibility and boundary |
| --- | --- |
| Existing Arbiter configuration | Resolve configured account selection and typed account policy. Agents cannot administer them. |
| WhatsApp plugin | Register eight operations, validate arguments and policy, project public results and map safe errors. |
| Provider transport | Construct fixed HTTPS destinations, attach private credentials, bound requests/responses and validate provider schemas. |
| Plugin-private bindings | Keep provider identity and active credential in one protected atomic record per stable credential reference. |
| Plugin-private state | Persist normal cursors, observed inbound identities and reply reservations/results. Coordinate supported processes sharing the same state root. |
| Local operator adapter | Perform first enrollment and verified replacement through the existing server CLI. It is absent from the agent operation catalog. |
| Existing artifact store | Own capability validation, expiry and consumption. A small new bounded read method supports outbound uploads. |

Register the package through the existing `arbiter.services` entry point as `whatsapp`. Reuse injected `plugin_storage`, `artifact_store`, dataclass operation validation and `OperationError`. Extend the existing nested schema generator and local CLI dispatch only where the approved object and credential contracts require them. These additions do not exist yet.

The provider enforces creator-only messaging. Arbiter binds operations to an enrolled provider identity and preserves opaque participant IDs. A token, provider response, participant message or artifact locator cannot change policy or obtain operator authority. One deployment and private state root own each provider agent; separate active roots cannot coordinate polling or deduplication.

Policy is per configured WhatsApp provider account, as the owner [explicitly clarified](supporting/requirements/policy-boundary.md). Current Arbiter supports trusted local callers and does not authenticate ordinary runtime API callers or enforce their stored enrollment grants. Every reachable caller shares access to all configured accounts and their permitted operations. Discovery and artifact access retain the existing server/capability boundary. Account names do not isolate callers. Keep this deployment trusted and local; caller authentication and caller-specific grants remain in the separate authentication project.

## Configuration and policy

```yaml
services:
  whatsapp:
    accounts:
      creator:
        credential: whatsapp/creator
        policy: default
    policies:
      default:
        updates: true
        send_messages: true
        read_receipts: true
        typing: true
        media_ingress: true
        media_download: true
        media_egress: true
        media_delete: true
```

The credential value is an opaque plugin-owned reference, never a token or arbitrary host path. Existing configuration composition resolves accounts and policies. Each account selects its own resolved policy, shared by all callers that can reach this server. Missing known fields default to `true`; invalid references or non-boolean policy values fail configuration validation. Unknown/unmapped operations fail before provider work.

Every effect requires all listed permissions. The same evaluator serves invocation and the existing optional operation-check hook. A preflight check performs no provider I/O, artifact consumption, reservation or cursor update.

| Effect | Required fields |
| --- | --- |
| Normal polling or replay | `updates` |
| Include usable inbound media references | `updates`, `media_ingress` |
| Read media metadata | `media_ingress` |
| Download bytes into an artifact | `media_ingress`, `media_download` |
| Upload an existing artifact | `media_egress` |
| Send text | `send_messages` |
| Send media | `send_messages`, `media_egress` |
| Mark read | `read_receipts` |
| Start typing, including its provider read effect | `typing`, `read_receipts` |
| Delete provider media | `media_delete` |

An explicit deny prevents the operation before transport or local artifact consumption. Removing an explicit field restores the allow default on the next normal configuration activation/restart. Agent arguments cannot override or reset policy. Allow defaults do not fabricate unsupported provider capabilities.

## Public operation contract

```text
receive_updates(account: string, offset: integer | null = null,
                replay: boolean = false)
  -> {updates: [Update], next_offset: integer, replay: boolean}

send_message(account: string, participant_id: string, message: Message,
             reply_to_message_id: string | null = null)
  -> {delivery: "accepted", provider_message_id: string,
      idempotency_replayed: boolean}
   | {delivery: "unknown", provider_message_id: null,
      idempotency_replayed: boolean}

mark_read(account: string, message_id: string) -> {status: "accepted"}
typing(account: string, message_id: string, active: boolean)
  -> {status: "accepted"}

ArtifactInput = {id: string, url: string}
MediaMetadata = {media_id: string, media_type: string,
                 size: integer, sha256: string}
upload_media(account: string, content: ArtifactInput, media_type: string)
  -> {media_id: string}
get_media(account: string, media_id: string) -> MediaMetadata
download_media(account: string, media_id: string)
  -> {artifact: ArtifactDescriptor}
delete_media(account: string, media_id: string) -> {status: "deleted"}
```

`Message` contains a type and exactly one corresponding typed payload for text, image, audio, video, document or sticker. Text uses `text.body`. Media payloads use an uploaded media `id`; only provider-supported caption/filename fields are accepted. Optional provider quote context is separate from the local `reply_to_message_id` admission key. Reactions are receive-only. The provider's size, format and text limits are enforced before submission where locally knowable. Nested object schemas must be described and validated through the real catalog.

`updates` preserves the documented provider entry/change structure, projected to known fields. Validate the bound provider identity before committing any cursor or source. Malformed/unsupported envelopes fail without advancing continuation. Under ingress denial, preserve message identity, participant, timestamp and type, but replace its media object with an explicit withheld marker and omit usable media handles, captions and metadata. Status-only batches are valid.

## Polling and reply journey

Before a normal cursor exists, require an explicit nonnegative signed-64-bit offset. Zero selects retained backlog. An omitted initial offset returns `validation_error`/`cursor_required` without transport. There is no promised gap-free initial new-traffic-only mode.

After initialization, an omitted normal offset uses the stored continuation. An explicit normal offset must equal it or return `conflict`/`cursor_conflict`. Replay always requires an explicit offset, including zero, and returns its own continuation without changing normal polling.

Serialize provider polling per enrolled account across supported processes. A valid normal batch commits the exact next cursor and newly observed source identities together before returning. A `204` returns an empty batch and retains the request offset, including an initial zero. A `409`, invalid response or failed transaction leaves committed state unchanged. A replay records observed sources but never replaces the normal cursor. A response lost after commit can be recovered through explicit replay while provider retention permits it.

```text
receive_updates("creator", offset=0)
  -> {updates: [...], next_offset: 1287, replay: false}
receive_updates("creator", offset=0, replay=true)
  -> {updates: [...], next_offset: 200, replay: true}
receive_updates("creator")
  # continues normal polling from 1287
send_message("creator", "user:123",
             {type: "text", text: {body: "Hello"}},
             reply_to_message_id="wamid.inbound-1")
  -> {delivery: "accepted", provider_message_id: "wamid.sent-1",
      idempotency_replayed: false}
# An identical source-keyed call returns that result with idempotency_replayed=true.
```

An observed source identifies the enrolled account, inbound message ID and participant. It is not a handled marker or a copy of the message backlog. A keyed reply must match that observed source. Bind its reservation to the participant and canonical outbound payload. Unknown or mismatched sources fail before transport; changed payloads conflict.

Commit a reservation before submitting once. Concurrent same-source requests cannot both submit. A live pending reservation returns `reply_in_progress`. Store acceptance and its provider ID before returning. A lost response or abandoned reservation becomes unknown and is never automatically resent. Identical calls replay the retained result or error; changed payloads return `reply_payload_conflict`.

A failure proven to precede submission may release its reservation. A definitive provider refusal is retained as a bounded error; it does not justify a second submission. Acceptance followed by local result-storage failure returns partial failure with known delivery information and unsafe retry guidance. An unkeyed proactive send remains allowed, but an uncertain result is unsafe to replay and automation cannot use that path to bypass keyed deduplication.

Source and reply records must outlive all provider-supported replay of that source. LLD owns concrete storage, exclusion, retention and recovery proof. It must not rely on unverified expiry or unstable clock assumptions to erase a reply record. Storage exhaustion preserves prior state and prevents effects whose safety requires a new durable write.

## Status and media journeys

`mark_read` authorizes only the read effect. `typing(active=true)` authorizes the documented combined read/typing request and therefore checks both permissions. `typing(active=false)` returns non-transient `upstream_rejected`/`typing_reset_unsupported` without transport. Provider timeout/reply behavior clears the indicator; the plugin does not claim cancellation.

For upload, validate policy, supported MIME type and known size before consuming the artifact. The new bounded `PluginArtifactStore.read_once` method resolves a same-store ID/URL capability without a network fetch. It shares the store's capability, expiry, root locking and one-time consumption with HTTP reads. Return immutable bytes and metadata, never an unprotected filesystem path. Normalize invalid, missing, expired and consumed capabilities to one unavailable result. A failed provider upload after consumption requires a fresh artifact.

For download, obtain fresh provider metadata, retain its signed URL only for that operation, validate fixed HTTPS provider destinations and the exact media path before attaching credentials, and reject redirects. Bound bytes, verify metadata size and SHA-256, then create an Arbiter artifact. `get_media` omits provider URLs. Unknown, expired, deleted and other-account media may share `media_unavailable`; return `deleted` only after provider confirmation. Deleting a resource is not a promise to remove previously delivered messages.

## Operator enrollment and credential replacement

Initial enrollment belongs to existing account registration: bootstrap/reuse the selected account configuration, then explicitly enroll during activation. The LLD selects the exact CLI shape and optional server-to-plugin hook. Preview, readiness checks and ordinary configuration selection do not secretly enroll an account. Enrollment and replacement never appear in the agent catalog.

The local operator supplies a bounded secret on stdin, with hidden terminal input when interactive. Use the same resolved account and state root as the server. An existing valid binding reports enrollment complete; replacement is a separate operator action:

```text
replace_whatsapp_credential(account: string, replacement: SecretInput)
  -> {status: "active" | "unchanged", verification: "passed" | "failed"}
```

For first enrollment, hold the account poll lock and inspect local state before provider I/O. Scan retained updates read-only from zero until the first empty response within a time/request budget. Keep the temporary cursor in memory. Any observed identity must agree; stop when it already belongs to a local binding. Display a new unpredictable `Arbiter enrollment` challenge only after the scan completes. Accept only a fresh exact creator-text match from the intended chat with a consistent provider identity.

After proof, hold a root-wide binding lock only for local validation/publication. Reject duplicate references or provider identities. Prepare and validate the private state store before atomically publishing identity and credential in one protected record. That publication makes the account available. If active bindings exist, missing/inconsistent state fails closed rather than recreating lost reply history.

Cancel, timeout or interruption before publication preserves configuration and leaves the account unbound. Reopening setup accepts a fresh secret and starts a new challenge. A second invocation cannot take over an active attempt. Near uncertain publication, inspect the record and state: a valid commit means complete, no commit permits a fresh attempt only after local consistency is established, and inconsistent state requires operator recovery. Never overwrite an existing valid binding during recovery.

Replacement holds the account poll lock, proves that the candidate reaches the already-bound provider identity through bounded read-only verification, and leaves normal continuation unchanged. Failed or inconclusive proof preserves the committed local bytes. Publish the verified token with the unchanged identity using protected same-volume atomic replacement and platform durability checks. Requests read one complete committed snapshot; in-flight requests may complete using their earlier snapshot. Provider regeneration can invalidate an old token even while its old bytes remain locally committed.

Operator logs omit secrets, challenge text and provider identifiers. The current terminal prompt displays the challenge. Provider-retained challenges remain ordinary untrusted updates under the normal polling/replay policy and cannot authorize another attempt. Automated callers can recognize their prefix and skip replies.

## Failures, compatibility and migration

Validate arguments, binding readiness and all policy effects before provider writes. Keep raw provider bodies, credentials, media URLs and artifact capabilities out of diagnostics. Map failures to existing bounded `OperationError` codes with truthful transient and replay guidance. Retry only safe reads within one operation budget; never automatically retry ambiguous writes.

Reuse current server worker bounds rather than adding unbounded transport threads. LLD specifies deadlines, response limits, provider fixtures and destination checks. DNS and platform storage behavior require explicit acceptance rather than a promise unsupported by the runtime.

The optional package must declare a server dependency that includes its nested-schema, credential-dispatch and artifact-read additions. Existing plugins remain compatible because the new local hook is optional and existing scalar/list schemas retain their behavior. No agent-authentication change is introduced.

A state-root migration stops the old owner and moves bindings, cursors and replies together before the new owner starts. Do not run independent active roots for one provider agent or reset state while keeping an active credential.

## Alternatives and remaining engineering work

Direct provider access would expose credentials and bypass Arbiter policy. One permission would lose the selected independent effects. Provider replay plus source-keyed reply admission is the approved smaller alternative to a local inbox/ack protocol. A plugin-local protected credential adapter avoids a general secret service or new network administration endpoint. A bounded artifact read reuses existing capabilities and avoids changing uploads to base64 request bodies.

LLD must resolve the concrete schemas, state transactions and locks, safe record retention, atomic credential publication on supported platforms, bounded artifact read/cleanup ordering, transport limits and current provider evidence. These can invalidate an adapter or storage choice and must be resolved before affected implementation admission. Actual plugin, platform and integrated security behavior remains implementation acceptance before rollout. A clean HLD review alone establishes neither implementation readiness nor a passed live-provider check.
