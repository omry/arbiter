# Proposed HLD contract amendment

Status: approved historical summary. The owner approved this amendment in [contract-approval.md](contract-approval.md), and the selected rules are incorporated into the current HLD. The exact approved preimage is retained at [approved-preimages/contract-amendment.md](approved-preimages/contract-amendment.md).

The isolated `whatsapp` plugin, creator-only provider boundary, eight operation names, default-allow policy fields, shared `updates` permission for retained replay, operator-only credentials, and downloaded-media artifacts remain selected. No new requirements interview or question allowance is proposed.

## 1. Updates and replay

```text
receive_updates(account: string,
                offset: integer | null = null,
                replay: boolean = false)
    -> {updates: [Update], next_offset: integer, replay: boolean}
```

Rules:

- Before a normal continuation cursor exists, the caller must provide an explicit nonnegative starting offset. `offset=0` selects the retained backlog. An omitted initial offset returns `validation_error`, domain `cursor_required`, without a provider call. This replaces the ambiguous initial head behavior; it does not silently select historical replies for the caller.
- Once initialized, `offset=null, replay=false` uses the saved continuation. An explicit normal offset must match that saved continuation; otherwise return `conflict`, domain `cursor_conflict`, without a provider call. Arbitrary historical offsets use replay mode.
- `replay=true` requires an explicit nonnegative offset, including zero. It uses the same `updates` permission. It returns the replay batch's `next_offset` but never replaces the normal continuation cursor. A caller can continue a replay by passing that returned offset with `replay=true` again.
- A valid normal response persists the provider's exact signed-64-bit `next_offset`. A 204 returns an empty batch and reuses the explicit or saved request offset. There is always a known integer in that case. A 409 or retryable fetch failure retains that request offset.
- Polling may return the same inbound message more than once. Observing or returning a message does not mark it replied to and does not suppress it from a later retained replay.
- If a response is lost after cursor persistence, explicit retained replay recovers messages while the provider still retains them. There is no new local inbox or acknowledgement operation. This is not a promise of exactly-once update delivery, nor recovery after provider expiry/deletion.

Examples:

```text
receive_updates(account="creator", offset=0)
    -> {updates: [...], next_offset: 1287, replay: false}
receive_updates(account="creator")
    -> {updates: [], next_offset: 1287, replay: false}  # provider 204
receive_updates(account="creator", offset=0, replay=true)
    -> {updates: [...], next_offset: 200, replay: true}
receive_updates(account="creator")
    # normal polling still starts from 1287, not the replay position 200
```

The API deliberately does not offer a gap-free initial "new traffic only" mode until the provider exposes a usable head cursor or equivalent evidence. Repeated offset-less provider polls are not a substitute: the retained manual says they can skip traffic between requests.

## 2. Messages and duplicate replies

```text
send_message(account: string,
             participant_id: string,
             message: Message,
             reply_to_message_id: string | null = null)
    -> {delivery: "accepted", provider_message_id: string,
        idempotency_replayed: boolean}
     | {delivery: "unknown", provider_message_id: null,
        idempotency_replayed: boolean}
```

Automated replies must supply the originating inbound `messages[].id` as `reply_to_message_id`. The plugin uses `(account, inbound_message_id)` as the reply identity and binds it to the participant and canonical outbound payload. This is an Arbiter deduplication input, not a claim that the provider supports a quoted-reply field. Proactive outbound messages remain permitted with `null`, under the existing creator-only and message-policy boundaries.

The source ID must identify an inbound message previously observed for this account and participant. Persist that source identity with normal cursor advancement; do not mark it replied to. An unknown or mismatched source returns a validation error before transport. Source records contain identity/participant/retention metadata, not a second copy of the provider's message backlog.

The plugin cannot infer whether unlabelled prose is a reply. The enforceable duplicate-reply guarantee applies to the source-keyed reply path; automation must use it. This condition is part of the requested contract decision, not an implementation assumption hidden from the owner.

Before sending a keyed reply, persist a reservation. Concurrent same-source calls cannot both submit. Repeating the same source and payload returns a stored successful result without another provider send. Reusing that source with another participant or payload returns `conflict`, domain `reply_payload_conflict`.

An active reservation returns `conflict`, domain `reply_in_progress`. After an interrupted send, a lost response, or an uncertain submission, its outcome remains unknown and the plugin does not automatically send again. A subsequent call for the same source returns the retained unknown outcome. A proven pre-submission failure may release the reservation; an ambiguous failure may not. A crash after reservation but before transport can therefore require explicit human recovery even if no message was sent. That is the cost of suppressing duplicate submissions without provider idempotency.

If the provider accepted a message but recording its result fails, use the existing `OperationError` partial-failure envelope with the known delivery information and unsafe retry guidance. Do not release the reservation or claim a safely retryable failure.

Reply records must outlive every provider-supported replay of their source message. The LLD must select the concrete store, multi-process exclusion, record retention and recovery rules and prove them with concurrency/crash probes before claiming readiness. The existing SMTP reserve/result pattern is the comparison point; it is not already a WhatsApp reply implementation.

Example:

```text
send_message("creator", "user:123", {type: "text", text: "Hello"},
             reply_to_message_id="wamid.inbound-1")
    -> {delivery: "accepted", provider_message_id: "wamid.sent-1",
        idempotency_replayed: false}
# Replay returns the same inbound message; the identical reply call yields:
    -> {delivery: "accepted", provider_message_id: "wamid.sent-1",
        idempotency_replayed: true}
```

For an unkeyed proactive send, a transport ambiguity returns `delivery="unknown"`, `provider_message_id=null` and unsafe retry guidance. The plugin never blindly resubmits it.

## 3. Read receipts and typing

```text
mark_read(account: string, message_id: string)
    -> {status: "accepted"}
typing(account: string, message_id: string, active: boolean)
    -> {status: "accepted"}
```

The two operation names and their policy fields remain. Neither requires `send_messages` or `updates` permission. Every actual side effect must be authorized:

- `mark_read` requires `read_receipts`.
- Under the retained provider contract, `typing(active=true)` also marks the message read. It therefore requires both `typing` and `read_receipts`. If either is denied, reject before the provider call.
- `typing(active=false)` returns a bounded unsupported-capability error without a provider call (`upstream_rejected`, domain `typing_reset_unsupported`, non-transient, replay safe). It never reports that an indicator was cancelled.
- The indicator expires through the provider's documented reply/timeout behavior. Sending a null or omitted typing indicator is not treated as cancellation.

This changes the prior promise of an explicit typing reset. If live evidence later establishes independent typing or cancellation, that behavior must be reconciled with the approved contract before enablement; it is not presumed here.

## 4. Media inputs, outputs and permissions

```text
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

`ArtifactInput` takes the ID and URL from an existing Arbiter artifact descriptor. The proposal adds a narrow read-once adapter to the existing `PluginArtifactStore`; it does not assume that adapter already exists. The adapter resolves only a validated local artifact ID and its URL capability, checks the ID/URL match, and uses the existing store's expiry, nonce and one-time-consumption rules. It does not fetch arbitrary URLs or accept host file paths. This is an explicit small server-interface addition and must be owned and estimated in the foundation slice.

The proposal supports valid artifacts already present in the same Arbiter store, including artifacts produced by another plugin when the caller holds their capability. It does not create an agent-to-server file-upload service. A consumed artifact cannot be reused for another upload attempt. Validate policy, provider-supported type and known size before consuming it; once consumed, a failed provider upload still requires a fresh artifact for retry. The LLD must define safe read lifetime, cleanup exclusion and platform behavior using the existing store rather than returning an unprotected path to an asynchronously deleted file.

Permissions are explicit:

| Behavior | Required policy |
| --- | --- |
| Poll updates | `updates` |
| Expose inbound media references in updates | `updates` and `media_ingress` |
| Fetch agent-visible metadata | `media_ingress` |
| Return downloaded media bytes as an artifact | `media_ingress` and `media_download` |
| Upload outbound media | `media_egress` |
| Send a message containing media | `send_messages` and `media_egress` |
| Delete provider media | `media_delete` |

When `media_ingress` is denied, an otherwise allowed update poll retains the message ID, participant and timestamp but replaces the media payload/reference with an explicit withheld-media marker. It does not return a usable media ID or provider URL. Download permission does not bypass an ingress denial.

`get_media` returns sanitized metadata without the provider URL. `download_media` validates trusted provider download origins and redirects before attaching a credential, enforces provider and Arbiter size limits, checks the digest, and creates the existing Arbiter artifact result.

Only a confirmed provider delete success returns `deleted`. The prior `already_absent` success arm is removed. The documented ambiguous error for an unknown, expired, deleted or other-agent ID remains a bounded `upstream_rejected`/`media_unavailable` error. The plugin does not infer ownership or absence from that error. Provider expiry, media URL lifetime and sticker limits still need live evidence.

## 5. Operator credential replacement

The operator-only action retains its result contract:

```text
replace_whatsapp_credential(account: string, replacement: SecretInput)
    -> {status: "active" | "unchanged", verification: "passed" | "failed"}
```

Proposed operator adapter:

```text
arbiter-whatsapp credential replace --account creator --token-stdin
```

The command runs under the existing server operator identity and resolved account/storage configuration. Standard input supplies the token; command-line arguments and results do not contain it. Agent operations cannot invoke this control-plane adapter.

Use plugin-owned private credential files under `PluginStorage`, selected by an opaque account reference. A bounded provider verification runs before an atomic file replacement. New requests read the committed credential snapshot; requests already in flight may complete with their previously acquired snapshot. The replacement path must not print secret values or create an agent-visible credential endpoint. No new shared secret-management service or control-plane transport is proposed.

This is a proposed plugin-specific adapter, not an existing Arbiter credential facility. The LLD must resolve the exact verification request, private permissions, concurrent replacement, crash durability and runtime reload checks before readiness. Verification must not send a message, emit a status, or consume updates; any read-only update verification must coordinate polling and leave continuation state unchanged.

Failure preserves Arbiter's previously committed credential bytes. Arbiter cannot make an old token valid again if provider-side regeneration already invalidated it. Evidence and operator documentation must distinguish those two facts.

## 6. Dependency and review repair

Before final LLD acceptance and implementation admission, resolve provider eligibility, the status/media schema conflicts, concrete credential and artifact interfaces, the storage/reply state design and independent design security review. Live account use remains separately authorized; this amendment grants none.

Implementation-specific crash, concurrency and redaction tests and the final integrated security review remain delivery acceptance work. They cannot be prerequisites for permission to write the implementation that those tests exercise. Reassign final integration slice 5 to its actual implementation/test responsibilities and move design evidence out of its dependency on slices 1–4. Re-estimate the concrete adapter work once the contract decision is retained.

## Alternatives and limits

- Keeping the old signatures would leave the provider-side effects, missing reply association and impossible results unresolved.
- A local inbox/acknowledgement protocol would improve automatic recovery of lost poll responses but add a second delivery system. Explicit retained replay plus reply-keyed deduplication is the smaller proposal and remains bounded by provider retention/deletion.
- Raising read permission implicitly during typing would violate the accepted independent policy controls. Requiring permission for both actual effects is explicit and keeps both controls effective.
- Base64 media input avoids an artifact-read adapter but changes the selected artifact input and is constrained by Arbiter's default 1 MiB request-body limit (`server/src/arbiter_server/limits.py`). The proposed narrow adapter reuses existing artifact capabilities and consumption instead.
- A generic credential service or a new administrative network endpoint is unnecessary for the proposed plugin-local file replacement.

## Evidence and approval boundary

Source evidence: the retained [provider manual](../implementation-plan/whatsapp-agent-developer-manual.txt), especially pages 15–17 and 23; `server/src/arbiter_server/artifacts.py` (`PluginArtifactStore`, `ArtifactStore.open_once` and `inspect`); `server/src/arbiter_server/errors.py`; `server/src/arbiter_server/storage.py`; and `plugins/smtp/src/arbiter_smtp/__init__.py` (idempotent reservation/result handling).

The manual is provisional evidence. No live-provider check, implementation, security approval or final design stamp is claimed. Approval of this amendment would select these proposed public rules and the narrow interface additions. It would not accept an unreviewed LLD, resolve the open evidence gates, or authorize delivery.
