# Receive updates and replay

The optional WhatsApp plugin currently supports account enrollment, credential
replacement, `whatsapp:receive_updates` and typed text `whatsapp:send_message`.
Artifact media upload is available. Media sending and downloading arrive in later slices. See [configuration](configure.md) for
setup, private storage and credential recovery.

All trusted local callers share the configured account policy and its normal
cursor. Account names do not isolate callers. Only the operator can change the
account configuration or its credential binding.

## Start normal polling

Call `whatsapp:receive_updates` with a configured `account`. The first normal
call requires an explicit nonnegative signed 64-bit integer `offset`. Use zero
to request retained backlog. An omitted initial offset returns
`validation_error` with `cursor_required` before any provider request.

```json
{"account": "creator", "offset": 0}
```

The result contains `updates`, the exact provider `next_offset`, and `replay`.
`updates` is a list of projected provider envelopes. Each envelope retains the
`object`, `entry` and `changes` structure. Its value contains contacts, inbound
messages and delivered/read statuses. A message has its ID, sender, timestamp,
type, typed payload and optional quoted context. Reactions are receive-only.
Unknown additive provider fields are discarded. The inner envelope has no
`next_offset`; use the result's continuation.

After the first successful normal poll, omit `offset` to use saved continuation:

```json
{"account": "creator"}
```

The plugin sends the exact cursor with provider `limit=100` and `timeout=15`.
It never derives a cursor from a message count or timestamp. Retain integer
precision in your client, including for values above JavaScript's exact-number
range. Offsets cannot exceed `9223372036854775807`.

An explicit normal offset must equal the saved cursor. A different value returns
`conflict` with `cursor_conflict` without contacting the provider. There is no
hidden cursor reset or guaranteed initial new-traffic-only mode.

## Empty responses and failures

A provider `204` produces an empty `updates` list and retains the requested
offset, including initial zero. Status-only pages are valid. Status receipts do
not create replyable inbound sources.

A provider `409` returns `conflict` with `poll_replaced`. The plugin does not
retry that poll inside the operation. Malformed JSON, invalid typed payloads,
unsupported message types, inconsistent source identities or a different provider
account fail without advancing continuation. A concurrent process that holds
the account poll lock causes a bounded conflict. Polling, enrollment and
credential replacement share that lock.

Provider calls use bounded response reads and a checked 30-second request budget.
OS DNS resolution can exceed the socket timeout; this API does not promise a
hard resolver deadline. Errors use fixed safe messages and omit credentials,
raw provider bodies and signed media URLs.

## Recover with explicit replay

Every replay requires an explicit offset, including zero:

```json
{"account": "creator", "offset": 0, "replay": true}
```

Replay returns its own `next_offset` and `replay: true`. Pass that returned cursor
with `replay: true` to continue the replay. Replay records observed inbound
sources but never replaces or initializes the normal cursor.

For example, normal polling can continue from 1287 while an explicit replay
returns 200. The next normal call still starts at 1287. Provider retention limits
how far back replay can recover data.

The plugin commits inbound source identities and the normal cursor in one SQLite
transaction before returning updates. If the caller loses the response after
commit, the next normal call continues from the committed cursor. Recover the
lost batch through explicit replay from the earlier request offset. There is no
local message backlog or exactly-once inbox. Message bodies are not stored in
SQLite. Keep the earlier request offset until your caller receives the response.

## Policy and withheld media

`updates: false` denies normal polling and replay before provider I/O or cursor
mutation. Preflight uses the same account policy and inspects committed readiness
without polling or changing continuation.

When `updates: true` and `media_ingress: false`, media messages remain visible
with their identity, sender, timestamp and type. The typed media object becomes
`{"withheld": true}`. It contains no usable media ID, caption, filename, MIME
type, digest or URL. Text, contacts, receipts, reactions and quoted message
context retain their documented projected fields. Malformed media remains an
error even when ingress is denied.

Policies default to allow for omitted known fields. Removing an explicit deny
restores that default on normal configuration activation or restart. Runtime
operation arguments cannot override policy.

## Durable state and capacity

Only supported processes sharing the same private state root coordinate polling.
Do not run independent active roots for one provider account. Sources are retained
across restart and policy changes. Duplicate sources must keep the same account,
message ID, participant, timestamp and type. A mismatch rolls back the whole page.

The root admits at most 250,000 source rows and has a 256 MiB database page
ceiling. These limits exclude rollback-journal and temporary-file headroom. At
source capacity, already-observed sources can still be checked. A page needing a
new source returns `resource_exhausted` without publishing its cursor. Storage
failure preserves prior safety records. Never delete source history or reset the
database while retaining an active binding; follow offline recovery and migration
instructions in [configuration](configure.md).


## Reply without duplicate submission

Automation must pass the observed inbound message ID as `reply_to_message_id`
and its exact sender as `participant_id`. The plugin records sources while
polling, including explicit replay. An unknown source, another participant or
an unobserved status receipt fails before submission. The optional quote
context does not establish a source. See the [text operation](reference.md).

A keyed reply binds that source to the complete normalized outbound payload,
including text, URL preview and quote context. Identical calls return the
retained result without submitting again. Changed payloads return
`conflict/reply_payload_conflict`; changing the key is not safe recovery.

An account send lock excludes simultaneous submissions across processes sharing
the root. A busy lock returns `conflict/reply_in_progress`; retry the same source
and payload after the current call finishes. The plugin commits a pending
reservation before starting HTTP submission. If the process dies after that
commit, the next holder of the send lock changes pending to unknown. It never
uses elapsed time or PID liveness as permission to resend.

If transport proves that no HTTP request submission began, the plugin can
remove the new reservation and return a safe-to-retry error. After possible
submission, a connection loss, malformed success or ambiguous provider failure
returns unknown and remains unknown on identical calls. A documented definitive
refusal is retained and replayed as the same bounded error. Neither a timeout
nor replaying the inbound page clears a reply reservation.

Acceptance followed by a storage failure returns `partial_failure` with known
delivery information and `retry.replay: unsafe`. Preserve that information and
do not submit again. The remaining pending record conservatively recovers to
unknown. Restart, credential replacement and policy changes preserve sources
and reply safety records. No local exactly-once delivery guarantee is made.

Omitting `reply_to_message_id` permits a proactive send without a durable source
reservation. Each call can submit again. An unknown proactive result is unsafe
to retry. Do not use proactive sends to bypass an uncertain keyed reply.

## Read receipts and typing

Read receipts require `read_receipts`. Starting typing requires both `typing`
and `read_receipts` because the provider combines those effects. Message-send
permission does not control these operations. Denied operations and explicit
typing cancellation submit nothing. Preflight checks permissions and readiness
without contacting the provider.

Each status call makes at most one provider request. A timeout or malformed
acknowledgement after possible submission has unsafe replay guidance: the
provider may already have applied the effect. The plugin never retries it
automatically and does not retain status outcomes as keyed message replies.
These operations do not change the update cursor or reply reservations.


## Media upload and artifact consumption

Upload checks the account policy, binding, declared type and known stored size
before consuming an artifact. Foreign URLs, mismatched IDs, bad nonces, expired
and already consumed capabilities return an unavailable error. Unknown types,
a declared/stored type mismatch and known oversize are rejected before
consumption or provider I/O. Preflight checks arguments, policy and readiness
without consuming or contacting the provider; it does not reserve an artifact.

The bounded reader shares the artifact store lock with HTTP consumption and
cleanup. It reads and verifies size and SHA-256 before committing the existing
one-time claim and access record. Only then does it return immutable bytes to
the plugin. A failed read or claim returns no bytes. Access-record publication
failure uses the store's existing claim rollback. Concurrent HTTP and plugin
consumers cannot both claim the same capability.

After consumption, local image validation or any provider failure requires a
fresh artifact. This includes a connection failure before provider submission.
The original capability remains consumed. Do not treat `retry.replay: unsafe`
as permission to reuse it or assume a timeout means that the provider created
nothing. The plugin submits at most one multipart request and never retries
upload automatically. A malformed or lost success response can leave an
unreported provider media object. Upload does not change polling or keyed reply
state. Artifact URLs, filenames and payloads are excluded from diagnostics.
