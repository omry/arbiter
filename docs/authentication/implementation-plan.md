---
plan_key: arbiter-authentication
slice_convention: hierarchical-v1
slice_inventory: {"executable": false, "path": "slices.json", "sha256": "995e7ed8c99dd2133188ba4ee5f8d9d5feaa1d175772f9dbba336755dedc533c"}
---

# Authentication: remaining implementation design

This is the detailed design for all remaining milestones **3–11**, based on
merged source `d52f8bc0acadc1de0dcb53fb1a96eb3e822e174a`.
[Requirements](requirements.md) remain exactly
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`.
The reviewed and owner-approved [HLD](high-level-design.md) is exactly
`5c0c6c2115b3129b7534cc924dcc9bf1f7f3a9be5175440e8e551f09361f6fbe`.
Those are the explicit governing design dependencies. This plan, its referenced
inventory and the current overview require affected review and final owner
approval. Neither design approval nor a planning state authorizes product work.

## Current boundary and navigation

Milestones 1–2, including portable enrollment `2.10.1–2.10.4`, are merged.
The original 21-PR enrollment stack consumed **22 attempts**, including its
unshipped parent attempt. That history, the subsequent four-slice correction,
old findings, original approval methods and receipts remain immutable. There is
no new allowance, campaign, enrollment or reservation in this design revision.

Use [overview](overview.md) for the map and [slices.json](slices.json) for the
**one canonical ordered inventory**. Its original `2`/`2.10` parents and four
children retain their exact intent, paths, dependencies and planning states;
new display ranges of zero mean no remaining work in those merged cuts, not
zero historical effort. The generated definition-only overview is not an
execution ledger. Future delivery must reconcile completed work through its
original receipts, not execute those four children again.

The [frozen prior plan](supporting/implementation-plan/historical-plan.txt),
[prior native LLD](supporting/implementation-plan/historical-native-lld.md) and
[original estimates](supporting/implementation-plan/estimates-before.json)
retain earlier text and IDs. Old stamps attest old bytes, not this revision.
The [estimate comparison](supporting/implementation-plan/estimate-comparison.md)
provides every old/new cut and parent rollup. None of that historical material
adds requirements to the current specification.

## Order, scope and size rules

The milestone order is 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Within milestone 3,
retain `3.1.1–3.1.4`, `3.2.1–3.2.2`, `3.3`, `3.4`, `3.6.1–3.6.2`,
`3.5.1–3.5.2`, `3.7`. The remaining child IDs and order stay as previously
issued. Exact leaf dependencies include the preceding leaf, so each prerequisite
result is complete before its consumer starts. A replaced parent retains its
aggregate responsibility; it is not an executable work item or extra reservation.
There are **51 remaining cuts**. This is a draft planning proposal, not an
approved PR-count allowance or guaranteed future diff.

Each leaf in `slices.json` names its complete ID, concrete title, consumed result,
scope, exact implementation/test/document paths, prerequisites and focused
completion check. Shared contracts and parent acceptance below also apply to
that leaf when first consumed. Public activation is deferred until its complete
boundary is safe; internal foundations always have an executable real consumer.
No empty framework, permissive placeholder, new policy engine or test-only
production authorization is permitted.

Display estimates mean **incremental final delivered nonblank physical lines**:
production and tests are separate; comment-only lines are excluded; physical
multiline statements and docstrings count. Shipped configuration, package data
and instructions count as production by role, not extension. Test-only helpers,
generators and integration drivers count as tests. Development design documents,
static fixture data, generated files, locks and dependencies are separate.
Handwritten CI is reported separately in the comparison and included in combined
ranges, not silently omitted from cost. No hour/day conversion is used.

The original **1,000 authored changed code/test-line maximum per PR** remains,
counting additions plus deletions across both languages, code motion and
handwritten fixtures. The approximate 600-line target is flexible. Final-content
estimates are not this edit-churn measure. The separate forecast envelope includes
replacement/motion/comment headroom; all current cuts fit. Recheck against the
actual predecessor before work. If evidence invalidates a cut, revise its affected
design and admission before implementation; do not remove tests or hide a split.
Relevant release fragments/help belong to their owning cuts. Development-only
planning changes do not require a published-package news fragment.

## Shared engineering contracts

Reuse `authority.py`, the existing private instance lock, SQLite authority DB,
`agent_enrollments`, `enrollments.prepared`, private file primitives and local
OS control. Keep original prepared bytes and opaque approval revision immutable.
Only the live revision is monotonic. Extend existing workers, carrier and HTTP
admission rather than add another executor, permission cache or operation engine.
Server source stays under `server/src/arbiter_server`; Go under its existing
`internal` packages. Proposed filenames identify ownership; do not create a
module solely because a slice has a name.

All new wire messages use version 1 with purpose, exact IDs, expected live
revision and bounded payload. Encode canonical UUIDv4 strings and unpadded
canonical Base64url for binary fields. Reject duplicate JSON fields, extra fields
unless an explicit versioned extension permits them, trailing input, wrong types,
non-finite numbers and oversize encodings before expensive work. JWS uses the
exact retained payload bytes, ES256 fixed-width 64-byte R||S and fixed purpose
`typ`; reject `jku`, `jwk`, algorithm substitution and unknown critical headers.
Registered verifier selection is independent of incoming headers. Bind public
key possession to a fresh 32-byte challenge, exact purpose, IDs, revision and
RFC9266 exporter. Comparison and commit recheck occur in the same live writer.

Every state mutation has a stable random operation ID and expected revision.
Exact completed retries return the original bounded result after verifying the
request/key context; conflicting reuse fails. Retain one latest result per
purpose/enrollment and the one active pending transition. Supersession retains
necessary history but cannot resurrect authority. Bounded authenticated receipt
retrieval, rather than replaying the original operation, resolves a lost result.
Ordinary plugin mutations and consuming GETs are never automatically repeated.

Keep Python 3.10–3.14, Go 1.24 and the current Linux/macOS/Windows x64/arm64
support matrix. Existing pins are `pyOpenSSL==26.2.0`, `cryptography>=42,<49`
and Gorilla WebSocket 1.5.3. Add binary X25519 age only; do not enable passphrase,
SSH, plugin or armored parsing through untrusted application data. Candidate
streaming backends are pyrage 1.3.0 and Go age 1.2.1 with existing compatible
transitive versions. Age 1.3.2 requires Go 1.25 and is not selected under the
current floor. Candidate versions are not a clean advisory/install verdict:
the owning first-use cut must verify reachable advisories, exact locks, supported
wheels/source builds and native execution. Failure blocks that cut; it does not
permit a platform drop, whole-file API or custom cipher format. See
[backend inspection and probe](supporting/implementation-plan/backend-evidence.md).

Reserve slots and byte/disk credits before crypto, copying or consuming GET.
Run blocking work off-loop through bounded existing owners. A canceled waiter
never releases actual thread/file/crypto ownership. For pyrage, use caller-owned
read/write adapters that bound every chunk, retain a sticky error even when the
binding swallows a final flush error, and record actual bytes/hashes. Check that
state, library result, finalization, flush/fsync and expected content before
publishing. The synthetic probe demonstrates why a return value alone is not
sufficient. Native progress/cancellation tests must demonstrate event-loop
responsiveness while the binding runs; no unbounded background queue is allowed.

## Milestone 3 — native transport, live grants and denial

The following contracts refine R10–R14 without changing the accepted public
interfaces. Ordinary native requests retain existing HTTP shapes and account
policy. Their anonymous predecessors close at the complete cutover.

### One ordinary channel and one selected enrollment

The outer carrier is a binary WebSocket at `/api/v1/native/api`. Ordinary inner
TLS is exactly TLS 1.3 with ALPN `arbiter-native-v1`; the existing fixed control
endpoint keeps `arbiter-control-v1` and never reaches ASGI. Pairing remains Noise.
Disable compression, TLS tickets/resumption, early data, HTTP/2, pipelining and
redirects. Use one HTTP/1.1 request per inner connection, then close. This is a
simple subset of the HLD's one-in-flight rule and needs no connection pool or
operation replay protocol.

Add the public selector setting `arbiter.enrollment` to existing client YAML and
the `enrollment=SELECTOR` override. It uses the existing canonical 22-character
selector, not a filename, key, URL or display name. Explicit override wins over
the config value. Missing selection gives enrollment/configuration guidance before
dial; never scan for a first identity or derive authority from URL. Enrollment
prints its final selector and this configuration guidance after durable promotion.
Changing `url=...` changes location only. Loading the selected record fixes the
expected server ID, remembered server authority and exact access key for this
logical request. Another server's record cannot authenticate a redirected URL.

Reuse the strict stored-record decoder from portable enrollment with a distinct
`LoadNativeIdentity` validator. Accept only final `enrolled/` native-owned state,
validate current server authority, exact access profile/IDs/key correspondence and
complete receipt/promotion. The portable leaf is historical enrollment evidence;
its later expiry is not ordinary access authority. Never accept portable proof,
prepared-only state or a lifecycle time exception as ordinary authentication.
Owner recovery and current/predecessor rotation remain milestones 4/5, not an
authentication retry added here.

Go's existing WebSocket `net.Conn` adapter carries `crypto/tls`. Verify the exact
remembered server authority, server-role SAN/ID, validity, key usage, EKU and
critical extensions before returning the stream. `GetClientCertificate` requires
the per-connection expected-server-verified marker before it supplies access
credentials. Do not put enrollment IDs, account names, application headers,
cookies, proxy authorization or nonces in the outer request. The dedicated native
outer dial may tolerate unverified carrier HTTPS; general/browser constructors
do not inherit that exception. Reject outer redirects and inherited proxy auth.

Return the verified inner connection from standard `http.Transport.DialTLSContext`.
Set `DisableKeepAlives`, disable HTTP/2 and proxy use, and reject all redirects in
the owning `http.Client`. Retain existing `TimeoutTransport` around that standard
transport; do not build a custom HTTP RoundTripper or duplicate phase timers.
The original connect budget covers outer TCP/HTTPS, upgrade and inner handshake.
`GotConn` means inner verification completed. API response time, plugin overrides
and download header/idle-progress time keep their current semantics. No phase,
reconnect, ping or wrapper restarts a logical deadline. No automatic retry after
request submission, uncertain result or consuming artifact GET.

### Shared bounded carrier and TLS stream

Extract only the common private carrier ownership/pump from `pairing_carrier.py`
and `control_tls.py`. Existing pairing/control behavior remains consumed by real
regression peers after extraction; fixed control framing is not generalized into
HTTP. Ordinary TLS adds a stream consumer on that same owner. Serialize every
memory-BIO operation in the existing bounded crypto pool, including handshake,
read, write, outgoing BIO drain and teardown. Handle partial writes, `WantRead`
and `WantWrite`; do not assume one send consumes one buffer. No OpenSSL private
API, TLS-record parser or new cryptographic dependency is needed.

Python ordinary TLS requires a client certificate. Verify its exact server-issued
access profile and remembered server IDs in the crypto worker. The resulting
immutable candidate identity is not yet an authorized caller: live enrollment,
accepted key, denial, inactivity and grants are checked through the coordinator
before ASGI admission. Portable and cross-server certificates fail even with a
generic valid clientAuth chain. No caller is reconstructed from HTTP headers,
outer TLS or WebSocket metadata. Expose only the server's selected ordinary leaf;
keep owner issuer and lifecycle-control trust selection separate.

The existing `arbiter.limits.native_transport` bounds remain:

| Owner/resource | Limit and release condition |
| --- | --- |
| Native carrier | 16 shared by pairing, fixed control and ordinary; reserve before upgrade, no wait queue. Release after stream cleanup and every actual crypto/plugin owner ends. |
| Handshake/crypto | Four concurrent bounded crypto workers; ordinary handshake uses the same gate, 10-second handshake deadline, 256-KiB cumulative ciphertext each direction, 32-KiB chain cap. |
| Pending native data | 256 KiB in each direction, including owned adapter, WebSocket, inner TLS and transport-pending copies. Reserve before a copy or feed; release only when its owner drains/discards. |
| WebSocket | Binary only, no compression, at most 64 KiB per message including fragmentation; existing byte/time control-frame limiter. |
| HTTP | 32-KiB header section, one request, 1-MiB configured operation-body ceiling; reserve actual operation work before pulling body data. |
| Request receipt | Server-owned absolute 30-second default from inner TLS completion through complete headers/body; configurable positive `arbiter.limits.native_transport.request_receive_timeout`. Fragments/pings never restart it. |
| Operation work | Existing configured eight actual blocking workers. A canceled waiter does not release a running worker or its carrier. |
| Client transfer | Existing four-per-process admission before GET/temp reservation; closed artifact paths do not create another transfer pool. |

Handshake totals and ordinary pending-data credits are different counters. Do not
retain the fixed control's lifetime `_control_tls_fed` counter for a long HTTP
stream or reset a cumulative counter and claim that buffers are bounded. Use one
moving credit ledger per direction, on the existing carrier owner. It accounts
for simultaneous parser fragments, queued WS chunks, BIO input/output, decrypted
pending data, header assembly and transient copies. One held WS message and one
active BIO call per carrier suffice; no background read-ahead queue is needed.

Feed/return TLS application chunks of at most 16 KiB. Drain outgoing BIO data
before offering more input or another application write. Because `pending()`
reports decrypted bytes rather than encrypted BIO occupancy, retain credits for
all fed ciphertext until the worker reaches `WantRead` with no pending plaintext;
then, only after the initial handshake completes, retain conservative space for
one incomplete TLS 1.3 record and its copy
(36 KiB total), in addition to still-owned adapter data. Disable post-handshake
client authentication and renegotiation; control/pairing keep their existing
cumulative limits. Never discount that
reserve while a partial record remains. If credits cannot be proved available,
pause input and drain; if the peer overruns the finite pending budget, abort
before another copy. An actual worker retains its input/output credit until its
completion callback, even after cancellation.

Use public `asyncio.Protocol`, `pause_reading`, `pause_writing` and transport
write-buffer APIs. Do not require `BufferedProtocol` at the Python 3.10 floor:
its outer SSL implementation still calls application `data_received`. Handle a
borrowed callback in bounded slices without retaining the full callback or feeding
wsproto past available credit. Reject over-budget delivery before copying it;
OS/kernel buffers and allocator capacity are not a claimed total-process heap
limit. The implementation must test actual pending-copy accounting under plain
and outer-TLS callbacks, fragmentation, coalescing and both slow directions.

The five-minute authenticated idle timer runs only with no admitted application
work or transfer progress. A long operation remains subject to its existing
operation timeout, not this idle timer. Control pings extend no deadline. Every
timeout/abort wakes blocked readers/writers; nothing retries an operation.
The independent request-receipt timer still runs while a worker waits for a body;
admission does not exempt that wait. Stop it only on complete bounded body receipt
or request termination. One absolute timer, rather than a second sliding idle
mechanism, bounds silent and drip-fed bodies. A smaller client budget may fail
first; raising a client budget does not extend the server's configured deadline.
After body completion this timer does not cut off an admitted long plugin call.

### HTTP/1.1 to the existing ASGI application

Reuse the installed HTTP parser (`h11` through the existing Uvicorn dependency)
for framing, not a new HTTP grammar. The private stream adapter builds one ASGI
scope with immutable `arbiter.caller`, verified server/agent/enrollment/key IDs and
the coordinator token. Only the server transport can construct that value. Reject
Upgrade, CONNECT, absolute-form targets, unsupported transfer encodings, ambiguous
Content-Length/Transfer-Encoding, trailers that change routing/authority, and a
second request. Preserve ordinary route paths, JSON envelopes and error codes.
The outer URL's Host and identity headers have no authorization role.

Parse a bounded complete header before choosing a route. Reserve the existing
worker and a provisional coordinator token before ASGI `receive` can supply body
data. A coalesced early body stays within pending carrier credit; excess closes
without invoking the plugin. Enforce both declared Content-Length and cumulative
decoded/chunked bytes; a missing length does not bypass the 1-MiB ceiling. Do not
emit `100 Continue` before reservation. Streaming responses use the same bounded
send/backpressure path; never concatenate an unbounded response.
If the receipt timer expires, stop ASGI body delivery, wake the existing bridge
with timeout and abort the carrier. The underlying worker remains reserved until
its actual completion callback. It must not reach schema/plugin dispatch after
timeout, even if a delayed final body fragment arrives. Missing body bytes never
receive a fresh deadline through another request or connection wrapper.

An operation ID selects a plugin before the body identifies its account. Before
body admission require a live enrollment with at least one grant for that plugin;
after bounded JSON decode require an explicit string `account`, check that exact
configured `(plugin, account)` grant, then use the existing schema validation and
account policy. There is no default first account or new operation allowlist.
Do not call plugin validation, account lookup or upstream work for a hidden
account. The existing IMAP/SMTP operations use this account argument; a synthetic
test plugin must use the same contract. Catalog-only requests use bounded work
with a token scoped to their returned visible accounts.

### Live authority and local administration

Keep exact `enrollments.prepared` bytes as immutable receipt history. Extend that
existing enrollment row, not a second denial/contact table, with `server_id`,
exact accepted access DER/SPKI fingerprint, `last_contact` and a
positive monotonic integer `live_revision`. Retain existing `denied` and
`inactive_after` as the single authoritative fields read by both ordinary access
and the fixed enrollment-receipt path. Existing opaque `revision` is immutable
approval provenance bound in the prepared result; never reinterpret it as a
monotonic live revision. Below, live token/revision checks mean `live_revision`.
Grant rows have unique `(enrollment_id, plugin, account)` and reference that same
enrollment. Do not add independently authoritative caches, mirror denial/cutoff
columns or mutate grants inside historical results.
Use portable enrollment's existing indexed `agent_enrollments` relation as the
sole agent-to-enrollment mapping; do not copy `agent_id` into `enrollments`.
Admission resolves the certificate's exact agent/enrollment pair through that
relation and the same enrollment row. Missing, conflicting or corrupt bindings
fail closed before ASGI admission. Migration validates the already backfilled
relation; fresh activation and exact replay retain its existing atomic writer.
No future key-version/rotation framework is implemented in this milestone.

One atomic startup schema upgrade backfills current key/grants and preserves each
existing denial, deadline and exact prepared/result. Initial `last_contact` is
derived from that enrollment's recorded activation/inactivity origin, not startup
time. Bound each parsed result, iterate rather than load all enrollments, reject
duplicate/malformed relationships and unknown future versions, and reuse the
existing stopped-instance/publication reconciliation rules. No serving mixed
old/new authority schemas. Fresh portable-enrollment activation populates these
columns and grants in its existing commit transaction; no interim activated row
lacks live authority. Existing receipt reads continue using the same row's exact
prepared result and live denied/inactive_after. Deny atomically blocks both paths;
authenticated ordinary contact refreshes their shared cutoff without changing
historical approval/result bytes. Receipt retrieval itself keeps its existing
contact semantics; incomplete retrieval never advances the cutoff.

Extend trusted `arbiter-server enrollment` controls with `grant add`, `grant remove`
and `deny`, as accepted in the HLD. Inputs are one exact enrollment ID and, for grant actions,
one configured plugin/account pair. Use bounded existing local-control messages
and peer credentials/Windows caller token. Add validates configured account and
does not clear enrollment denial/inactivity. Existing add/remove/deny is
idempotent; only an effective change advances live revision. Read summaries use
32-row keyset pages by enrollment ID and never expose private key material.
There is no remote grant endpoint, automatic regrant on password change, or
implicit different account when a configured name is repointed.

Authenticate contact only after strict mTLS and current accepted-key/live-state
verification. Atomically refresh `last_contact` and the existing 180-day cutoff
for an active enrollment; an already inactive or denied record cannot renew itself.
Malformed unauthenticated traffic or incomplete control exchange cannot refresh
contact. Rotation/grace-specific contact behavior is added by milestone 4.

### Coordinator, dispatch and denial linearization

Use one event-loop-owned coordinator with at most one fail-fast authority writer
reservation and live tokens bounded by the carrier/worker caps. Reuse the existing
local-control worker for its SQLite writes and existing crypto worker for
activation. A writer acquires its loop reservation, performs DB work off-loop,
then publishes commit or rollback on the loop. No event-loop SQLite, waiting
thread lock, new executor, unbounded writer queue or per-request global policy
snapshot. All writes that affect ordinary authority use this reservation; busy
writers fail safely rather than wait behind blocked plugin calls.

Read one bounded enrollment/grant snapshot in an existing worker, carrying the
coordinator's observed write epoch. Before issuing a token or accepting a new
account-specific read, the loop requires that epoch and exact live revision still
match and no writer barrier blocks the token. Stale/busy reads fail retryably
before dispatch, never retry an upstream operation. Already live tokens use the
coordinator's explicit validity/barrier state. On a committed unrelated change,
carry those bounded tokens forward to the new revision without widening their
account scope; affected tokens are invalidated. Do not reject unrelated running
output merely because a global epoch advanced. A read-only snapshot never
independently authorizes a request.

| Token transition | Owner and required check |
| --- | --- |
| Reserved | Real worker has started; live enrollment and plugin grant permit bounded body reads. It is not yet upstream dispatch. |
| Body complete | Parse only bounded JSON; choose the exact account and live revision, validate input through the existing schema. |
| Dispatched | At the already executing worker's plugin-entry boundary, bridge to the loop for the final exact enrollment/account/revision check. That successful handoff is the dispatch linearization point. The worker's next action calls the existing plugin; no queue, task spawning, await or alternative-account selection follows it. |
| Output | Before every response chunk and final ciphertext write, recheck the token after any wait/backpressure. |
| Canceled/waiter ended | Stop body/output and abort carrier; retain real worker, bridge, token and carrier ownership until actual work ends. |
| Actual worker finished | Existing pool `on_finished` callback retires worker ownership on the loop; release carrier only after all crypto/stream owners also finish. |

A deny/remove writer first places an affected-token barrier on admission, final
dispatch and output. It then performs the conditional SQLite transaction in the
existing admin worker. On commit, publish revision, invalidate affected tokens
on all connections, wake waiters and abort their carriers before reporting local
success. On rollback, remove the provisional barrier and permit old authority
again without replaying canceled or uncertain operations. A grant removal cancels
that account and catalog tokens whose visible output depends on it; enrollment
denial cancels all enrollment tokens. An unrelated grant does not widen or revoke
another token. Temporarily pausing admission for one authority writer is allowed;
unaffected in-flight plugin work is not canceled merely for a global epoch change.

The final plugin-entry handoff, not TLS acceptance or early body reservation,
orders dispatch against denial. Denial's barrier first means no later dispatch.
Handoff first means already dispatched work: OS scheduling or upstream completion
may occur after denial and cannot be undone. Report that boundary honestly; never
promise to undo mail effects. Waiting for eight blocked workers is not a condition
of committing denial or suppressing their output.

At output, drain TLS into bounded unsent buffers, await write readiness, then
check permission immediately before synchronous `transport.write`, with no await
between check and write. Do the same at plaintext/ASGI boundaries. Denial drops
unsent BIO/WS data and aborts raw transport without flushing close-notify or queued
application bytes. Bytes already handed to transport before the barrier cannot
be recalled. A late worker result cannot recreate caller authority or send a new
error after response/cancellation. `HTTPWork` waiter cleanup stops delivery but
does not discard its bridge/token until the pool's actual completion callback.

Fixed enrollment-receipt control uses this same coordinator. After its existing
proof and exact enrollment/result verification, return the observed write epoch
with the bounded result. Acquire an enrollment-scoped receipt token on the loop
only if the epoch and current denial/inactivity state still permit it. This token
does not grant ordinary account authority or change historical result bytes.
Retain token and carrier ownership through actual crypto/send completion.

The enrollment-deny barrier blocks new receipt admission and unsent receipt
output. Commit invalidates affected receipt tokens and aborts their carriers
without flushing queued application data. Rollback removes the provisional
barrier; unrelated enrollment or grant changes do not revoke receipt permission.
After every output wait/backpressure, check permission immediately before the
final synchronous raw ciphertext write through the same shared carrier owner,
with no await between check and write. The receipt's database snapshot alone is
not delivery permission. Bytes already handed to transport cannot be recalled.
Inactivity also blocks fresh receipt admission under the shared live state;
existing receipt contact semantics remain unchanged.

Ship the fixed-receipt guard with the first live enrollment-deny control in
`3.2.1`. That cut introduces the minimal shared coordinator with the real fixed
receipt as its consumer, including its final-write barrier and actual-owner
cleanup. `3.2.2` extends this same coordinator to ordinary grant/body/dispatch
work and owns ordinary grant add/remove controls. Do not expose live denial and
defer receipt output integration to `3.6` or public ordinary cutover. No second
coordinator, authority table, executor, protocol or production bypass is needed.

### Discovery, policy and artifact closure

Use the same token/grant projection for overview, plugin/accounts, operation/schema,
policy summaries, previews, test/info routes and direct invocation. Show a plugin
only with visible granted accounts; filter counts/examples/guidance as well as
names. Direct hidden/unknown account and policy lookup returns the same generic
not-found response before account-specific validation or upstream work. A policy
is visible only through a granted account using it; this does not create a policy
grant. Current account policy remains the only operation policy. Preserve grants
for deliberate repointing; password changes require no grant edit.

Before ordinary public cutover, supply a small closed `ArtifactStore` implementation
through the existing plugin dependency and `for_plugin` wrapper. Current concrete
type checks remain usable. It must not invoke the legacy constructor or any legacy
create/inspect/open/read/nonce method. Keep only inert limit properties needed by
schema responses; creation fails before payload staging with an authenticated
`artifact_unavailable` error. No bypass/config flag and no plaintext fallback.
Caller identity is fixed in ASGI scope and set through a scoped `ContextVar` in
the actual synchronous plugin worker, reset in `finally`; shared runtime context
is never mutated. The later encrypted store consumes this same caller boundary.

Also close descriptor projection and inline extraction before legacy lookup:
`_operation_artifacts`, `_native_artifact_from_descriptor` and
`_inline_artifact_payload` cannot turn a plugin-supplied old URL/path/nonce into
usable bytes or a descriptor. Close old metadata/HEAD/GET and raw artifact routes,
including guessed paths. A synthetic artifact-producing operation must fail
before any legacy-store entry, payload, metadata or nonce; inspect both a legacy
spy and actual filesystem. Report prior upstream effects without retrying them.

Under the existing exclusive startup owner, invalidate and incrementally remove
only server-managed ownerless legacy artifact index entries and their validated
files in the configured managed store. Do not instantiate its serving store,
follow links, scan mail or touch client files. Unknown/corrupt managed metadata
fails closed with operator guidance; no guessed ownership. Cleanup is bounded
per entry and repeatable across restart; deletion failure never re-enables serving.

### Cutover and acceptance

Private real peers exercise ordinary TLS/ASGI/coordinator paths before public
activation. No public bypass flag or test fixture creates product authority.
Build and exercise every Go discovery/schema/error/invocation/artifact constructor
with the selected native factory through private real peers first. Keep production
constructor selection on the existing path until the same cut switches it to the
native factory, closes ordinary anonymous API and legacy artifact routes, and
enables the complete reviewed ordinary channel. That cut is `3.5.2`; `3.5.1`
does not switch the product to an unavailable server route. After cutover there
is no anonymous fallback or bypass flag.
Only minimal health and bounded enrollment bootstrap remain anonymous. Missing
authority or projection fails closed. Fixed controls stay distinct. The first
installed ordinary prefix includes denial and closed-artifact acceptance; these
are not deferred to a final release checklist.

| ID | Required real acceptance, assigned to the owning cuts below |
| --- | --- |
| N01 | Python/Go strict mutual TLS; wrong server/ALPN/role/issuer/key/time, portable/cross-server/prepared-only state and forged proxy headers never dispatch. |
| N02 | Real outer relay observes no access identity, account, operation, body or secret; changed outer cert/URL and TCP passthrough preserve expected inner trust; wrong server fails before client credentials. |
| N03 | Exact moving pending-byte accounting, all framing/chain/handshake caps, slow bidirectional peers, fragmented/coalesced WS/TLS/HTTP and valid 1-MiB body; no lifetime-byte cap on ordinary streams. |
| N04 | Pre-body eight-worker admission, Content-Length/chunked limits and Expect behavior. Fill all eight with silent/drip-fed bodies; the absolute server deadline, delayed final fragment, denial and shutdown wake waits, prevent dispatch and recover capacity only on actual worker exit. Fragments/pings/reconnect wrappers do not extend it. Health/admin denial remain responsive. |
| N05 | Existing rows, exact receipts, opaque approval revision, denies, grants and original inactivity survive one-way upgrade/restart. Admission uses the existing canonical agent binding after old-row backfill, fresh activation and exact replay; missing/conflicting/corrupt relations fail closed without a second agent-ID copy. Ordinary access and receipt read one denial/cutoff authority; denial blocks both, and active contact refresh changes their shared cutoff without changing historical bytes. |
| N06 | Discovery/count/schema/policy/error/preview/direct paths reveal only granted accounts and use existing policies; account repoint and password renewal preserve grants. |
| N07 | Controlled real-socket deny/remove races at queued read, body, final dispatch and cross-connection output. Fixed receipts also race snapshot return, token admission, blocked output and final raw write against deny commit; cover rollback, unrelated enrollment/grant changes, disconnect and actual-owner cleanup. No post-barrier dispatch or unsent delivery; already handed-off bytes cannot be recalled. Receipt cases run with the first live deny control in 3.2.1. |
| N08 | Denial/disconnect with blocked upstream calls retains all eight workers and carrier owners until actual exit; no automatic retry or false promise to recall prior effects. |
| N09 | Actual artifact-producing plugin/Go request rejects before legacy store entry or filesystem/nonce/descriptor/inline publication; old routes fail, restart cleanup is confined and failure stays closed. |
| N10 | Every real CLI constructor, including error/tool/artifact paths, uses explicit selected enrollment and verified native factory after atomic client/server cutover; the preceding product prefix keeps its working route while private peers exercise the new factory. No general outer TLS exception, URL identity inference, redirect or uncertain-operation replay. |
| N11 | Existing connect/API/plugin/download idle budgets through real peers: slow connect, no early headers, long active call, ping-only traffic, slow progressing stream and cancellation. |
| N12 | Installed supported Python/Go/native OS/CPU prefix actually executes N01/N04/N07/N09/N10 plus focused relay/timeouts; CI required variables are present and tests do not silently skip. |

Reuse `test_native_http_protocol`, `test_artifact_http_admission` real-socket
fixtures and the existing Python/Go peer, installed-CLI and terminal drivers.
Own shared harness additions once; add targeted later cases. No live mail account,
full duplicated installed adversarial matrix or production prototype is authorized
by this design. Dependency/source inspection establishes API feasibility only;
native execution is implementation acceptance, not evidence already obtained.


## Milestone 4 — rotation, late completion and fallback

Extend the same live enrollment row with current credential version/DER/SPKI,
one predecessor version/DER and one pending rotation reference. A separate
`pending_rotation` row, keyed by enrollment, contains operation UUID, expected
live revision, current-key fingerprint, `prepared_at`, fixed `grace_until`,
candidate SPKI/certificate or null and phase. One conditional transaction creates
it on due authenticated contact **before** requesting the candidate. Contact
updates last-contact/inactivity only for a currently accepted, active key; it
never moves an existing grace. A candidate is bound once. Preparation, retries,
invalid candidates and partial controls cannot extend its deadlines.

The Go enrolled record becomes one private version-2 lifecycle record, atomically
replaced under its existing per-selector lock. It retains immutable enrollment
provenance separately from current/predecessor/pending keys and trusted-server
state. Migrate an exact valid version-1 record once; do not overwrite an unknown
version or delete the only durable recovery source on ambiguous publication.
Use existing POSIX custody checks/flush and Windows ACL/handle/WRITE_THROUGH
primitives. Save the candidate private key and exact certificate/result before
sending confirmation. After server activation, a lost receipt is recovered with
candidate possession and the recorded operation ID; the client never needs the
old certificate to remain unexpired merely to retrieve its completed result.

### Normal rotation exchange and operation ordering

Normal rotation is an internal authenticated HTTP control, not the restricted
late-completion carrier. Under the ordinary strict native channel add exactly
`POST /api/v1/lifecycle/rotation/prepare`, `/bind`, `/confirm` and `/result`.
Each opens a separate ordinary connection with exactly one request; each request
and reply is at most 32 KiB, uses the shared strict decoder, coordinator and
bounded existing worker, and never enters a plugin or consumes an artifact.
An active enrollment needs no account grant to maintain its own credentials.
It still needs its exact accepted current access key, ordinary certificate
validity, no denial and time before inactivity/grace cutoff.

The version-1 envelope carries purpose `arbiter-rotation-{phase}-v1`, server,
agent and enrollment IDs, operation UUID, expected live revision, pending UUID
(or null only on initial prepare), and the phase fields below. No public URL or
header supplies these identities. Prepare is an authentication-only preflight:
its reply is either not-due with current version/revision, or the exact pending
ID, immutable prepared/grace times, current-key fingerprint, phase, candidate
certificate/hash if bound, and a 32-byte server challenge. At first due prepare,
the writer creates that pending ID/challenge before asking for a candidate;
repeated prepare returns the same pending state and never extends time. Retain
the preparation revision as history. A later grant revision can return a fresh
current live-revision snapshot for the same pending ID; confirmation must sign
that current snapshot and the writer rechecks it. An actual key replacement,
denial or inactivity cannot be papered over by updating the snapshot.

Bind adds candidate SPKI and new-key signature over the envelope, stored challenge
and fresh exporter. The authenticated current key plus new possession permits
one candidate certificate, issued through the current server authority. Return
and retain that exact certificate/phase/result, without activating it. Same
operation/key retries return it; another key or pending ID fails. Confirm carries
the old-and-new signatures specified below, only after candidate custody is
saved. Result carries the same operation/pending IDs and fresh candidate-key
proof; before activation it reports only authenticated pending state, and after
activation it returns the exact committed receipt. A completed result requires
candidate possession through the fixed result control if ordinary current-key
TLS is no longer possible. That fixed control cannot prepare or bind a new key.

In 4.2, every selected client constructor runs one shared lifecycle preflight
under the existing per-selector lock before submitting a user request. It reads
state, prepares/resumes, saves a candidate before bind, saves the returned exact
certificate before confirm, and durably promotes only a verified receipt. A
fresh ordinary connection then submits the user request once with the resulting
credential. All exchanges share the invocation's existing absolute connect/API
budget; their connections, phase transitions and authentication-only retries do
not restart it. Do not serialize a user body, capability or consuming nonce into
any lifecycle exchange. No new background job or second HTTP/timer stack exists.

If prepare/bind is incomplete before grace, authenticated state may still prove
the current key usable; the next invocation resumes the same pending transition.
A network error is not such proof. If confirm may have committed, persist the
ambiguous phase and reconcile via result with the saved candidate before sending
any user work; never blindly confirm again or fall back to an unproven old key.
An exhausted budget fails this invocation without user-operation submission.
After grace, only the recorded late-completion journey can proceed. Save failure,
concurrent clients, writer busy/stale revision, denial and every lost reply get
real peer tests at their first owner, not only the final integration cut.

Confirmation binds purpose `arbiter-rotation-v1`, server/enrollment/agent IDs,
pending ID, expected revision, both key fingerprints, candidate certificate hash,
server challenge and exporter. Verify old and candidate signatures, then recheck
pending identity, grace/inactivity/denial and candidate correspondence under the
live writer. Commit new current, one predecessor and exact result together;
invalidate superseded sessions before returning success. A repeated completed
confirmation returns its original result only to the activated candidate proof.

After grace, old ordinary access is refused; the fixed control endpoint can
only complete the recorded pending rotation. Verify the stored issuer/signature,
exact DER/SPKI/IDs and possession. Relax **only certificate time** for this exact
old/candidate pair; require fresh channel binding, no denial and current time
before inactivity cutoff. Reissue a valid certificate for that same candidate
key without changing the pending ID or grace. The control may not prepare a
new rotation, issue another key or construct an ordinary caller. A completion
that misses inactivity requires the accepted owner repair journey.

Credential-status control authenticates the remembered server and the presented
current key before returning a purpose-bound cause. Only unambiguous rejection
of that current credential by this verified server permits one authentication-only
predecessor attempt. That attempt must satisfy ordinary validity, live accepted
key, grace, denial and inactivity checks. It occurs before submission, carries no
operation body or artifact nonce and does not restart logical timeouts. Discard
predecessor on expiry, completed superseding recovery or unenrollment. No receipt
cleanup deadline may shorten the required completion interval.

Ownership: 4.1 owns server pending state and ordinary prepare/bind/confirm/result
handlers with real private peers/clock fixtures; 4.2 owns the client replaceable
record, shared pre-operation constructor integration and activation; 4.3 owns restricted proofs/reissue; 4.4 owns
status/fallback; 4.5 extends their shared process/clock harness. Each earlier cut
has its own real peer and failures. Parent acceptance is the complete due → save
→ activate → lost receipt → expired-key completion journey, with simultaneous
denial, withholding, 30-day/24-hour/180-day boundaries and actual restarts.

## Milestone 5 — owner-authorized client recovery

Local `enrol repair` reuses bounded Noise pairing with an explicit existing
enrollment selected by OS-authorized administration. Bind its stable identity,
current live revision and unchanged grants/denial into approval. New enrollment
cannot smuggle that selector into its own approval. Commit replacement and
invalidate stale access/pending/completion sessions through the same coordinator.
An owner may replace credentials on a denied enrollment; it remains denied.

### Non-monopolizing request, offline authorization and access commit

`recover-request` generates replacement access keys in private client custody,
then obtains a **stateless** challenge cookie over the verified server's fixed
recovery control. Its version-1 public request binds purpose, request/operation
UUIDs, server/owner/agent/enrollment IDs, expected live revision, a random 32-byte
challenge, creation/expiry and exact replacement SPKI plus optional portable
binding. It expires after ten minutes and is at most 64 KiB including certificates
and proofs. A purpose-separated ES256 JWS with typ
`arbiter-recovery-challenge-v1` authenticates those exact bytes using the existing
server-held backup-origin signing key. This key cannot authorize recovery. The
offline owner file already pins that server's origin SPKI; the offline command
verifies this signature through that pinned key, never an incoming JWK/root.
No new operator key, trust store or secret export is introduced.

Request issuance allocates **no per-enrollment pending slot**, row or durable
candidate. It only uses the existing globally bounded carrier/crypto admission
while serving the request. Multiple proposed keys may obtain independent cookies;
one cannot exclude another. New-key possession bounds a usable request but is
not owner authority. Unknown enrollment gets the same bounded cookie-shaped reply
with non-authoritative dummy revision/identity values; only successful completion
confirms a real binding. Unknown, stale, invalid, inactive and denied diagnostic
failures reveal no accounts or specific private state. Transient busy is generic,
not a per-enrollment reservation. Expiry or stale revision requires a fresh public
request and corresponding authorization; retries never slide an existing expiry.

The existing offline `recovery authorize` command verifies cookie signature,
registered owner/server binding, strict purpose/schema and expiry. It displays
the exact identity/change, reads the private operator file and signs only these
exact bytes with typ `arbiter-owner-recovery-v1`. Private owner material never
leaves that machine. Completion carries this JWS, original signed cookie and a
fresh replacement-key/exporter signature. Under the live writer, independently
select the enrolled recovery verifier; recheck both signatures, current revision,
expiry, IDs and new key before creating any durable state. In one conditional
transaction, advance the live credential, consume the operation/cookie digest
and retain its exact result. First valid owner-authorized completion wins; another
cookie for the old revision is stale. Arbitrary unauthenticated requests cannot
create or overwrite this receipt. Preserve grants, denial and historical bytes;
owner-authorized replacement can repair expired/inactive access, but cannot clear
denial. At completed owner repair, set last-contact/inactivity from the actual
completion time under the unchanged180-day policy; incomplete or unauthenticated
traffic cannot refresh it. Replace current access and discard compromised
predecessor/pending authority. Stale rotations/completion sessions lose authority
atomically.

The activated replacement key can reconcile the exact result through fresh
possession after a lost reply without reapplying the mutation. After completion,
this receipt lookup does not depend on the ten-minute authorization expiry;
superseding recovery invalidates obsolete receipt authority. It never clears a
later denial. Use the existing latest-result retention and bounded decoder.
Tests continuously issue attacker-generated valid new-key requests while the
legitimate owner completes, and cover competing valid owner completions,
expiration, grant/deny races, restart and lost results.

### Portable signing and partial multi-server recovery

Portable replacement is **one key, separate server authorizations** through the
already accepted `identity replace-request` → `enrol recover-request
--portable-request` → offline `recovery authorize` → `enrol recover` journey.
The private client replacement record stores one new portable key and the public
binding: stable agent/owner IDs, original issuer certificate/SPKI fingerprints,
old portable certificate hash, new SPKI, operation UUID and new-key possession.
Each server's cookie and owner JWS covers that complete binding as well as its
own replacement access key. An owner signature is authorization, not an X.509
issuer signature. Neither ordinary enrolled access nor portable possession alone
can request a different portable key or another agent's certificate.

At the original issuing server, `recover-request` identifies signing eligibility
only if its **locally held owner-issuer private key** matches the original issuer
SPKI/owner ID and still-valid registered issuer certificate. A remotely registered
public issuer is never enough. The server's registered recovery verifier must be
the verifier for that owner; do not let this server's administrator authorize a
different owner's identity. There is no existing signing control to reuse:
5.4 adds this narrowly purpose-bound handler to the fixed recovery control and
live writer, within the existing public request/authorize/complete commands.

Completion verifies the independent owner JWS, replacement access/exporter and
replacement portable-key signatures, exact original agent/enrollment binding,
expected live/issuer revisions and original certificate provenance. Only a live
non-denied enrolled identity with that original legitimate issuer may receive
normal portable certification. Owner access repair remains allowed for a denied
identity but returns portable-pending without issuing a leaf or removing denial.
Prepare the exact normal-profile leaf for the new portable key, then atomically
commit access replacement, issued certificate/result and portable-binding
revision under the same writer; failure before commit activates neither. A
simultaneous denial/issuer removal/recovery makes the guarded transaction fail,
not a partial issue. Stable operation retries retrieve this exact certificate
only after new access **and portable** key possession; they cannot mint again.

The client verifies normal role/issuer/agent/key/time correspondence, privately
saves the returned certificate and receipt, then atomically publishes that same
portable key/certificate under its identity lock. Record a completed receipt
before selector publication so a crash/lost reply reconciles without another
owner authorization or replacement key. Per-server access records are promoted
only from their own exact verified results. No second enrollment approval occurs.

Other already-enrolled servers consume their own challenges and owner signatures
and replace only their local access credentials. They neither sign for the
original issuer nor trust a new issuer automatically. These recoveries may finish
before the original issuer is reachable: keep the public old certificate as
history and new portable credential explicitly pending; ordinary access does not
require a currently valid portable leaf. Once the original issuer's exact result
is saved, publish the shared portable certificate once and record each server's
independent completion. Retry only unfinished authentication/result exchanges,
never user operations. Fresh enrollment elsewhere waits for legitimate issuer
certification and that server's independent issuer-registration rule. An absent
original enrollment, unavailable/expired issuer, missing signer or wrong owner
leaves portable certification pending with restore/issuer-registration guidance;
never mint a replacement CA or attach the old leaf to a new key.

### Same-key issuer and portable renewal

Keep issuer validity 3,650 days, leaf365, five-minute backdate and renewal windows
366 days/30 days before expiry. In5.5, local authenticated lifecycle maintenance
renews an owner issuer using **that same locally held key**, owner ID and CA
profile while the current issuer is still valid. Sign the new self-signed root
and a bounded purpose `arbiter-owner-issuer-renewal-v1` statement over exact old
and new certificate hashes, unchanged SPKI/owner, issuer revision and operation
ID. Persist the new certificate, statement and result atomically before serving
it; a lost result returns identical bytes. This is certificate-time renewal,
not a new identity/key authorization or a general remote CA-issue interface.

An enrolled client fetches this public statement from the remembered issuing
server before supplying credentials to a changed certificate. Verify the actual
channel's unchanged authority/key and old trust, both exact certificate profiles,
same SPKI/owner, old certificate still valid locally, monotonic issuer revision
and the signed old→new binding before saving it. For another server's exact-
fingerprint public registry, forward this same bounded public statement through
an authenticated lifecycle renewal control. Its writer must find the exact old
active registered certificate, still valid **at that receiving server**, and
verify unchanged owner/SPKI/CA constraints, statement and self-signature. Retain
the old fingerprint as provenance and add the exact renewed-certificate alias
under the same trust identity/revision. Removal/denial of the original trust
blocks this transition; it cannot resurrect inactive trust, change issuer key,
register an unrelated CA or authorize new accounts. No incoming root authenticates
its own first registration. A missed validity window needs explicit legitimate
registration/restore guidance, not automatic expired-root acceptance.

For same-key portable leaf renewal, the original issuer's strict ordinary
`POST /api/v1/lifecycle/portable/renew` consumes a bounded version-1 request with
purpose `arbiter-portable-renew-v1`, exact server/owner/agent/enrollment IDs,
current issuer/leaf hashes, unchanged portable SPKI, expected live/issuer revisions,
operation ID and portable-key/exporter proof. Require accepted active access,
no denial/inactivity, recorded agent binding, matching locally held issuer key
and still-valid original portable leaf/registered issuer. Issue only the same
agent/key/profile with renewed dates; atomically save the exact result and
recorded portable-certificate revision. Wrong key, missed leaf validity or issuer
loss requires owner recovery, never access-only recertification. Client checks
and saves issuer continuity first, then the leaf, using existing private custody.
Lost result uses the same operation and both-key proof, not another issuance.
Renewal does not rotate access, change grants or make portability native authority.

Ownership:5.1 pairing repair;5.2 stateless request/JWS/offline authorizer;5.3 live
once-only commit/result;5.4 original-issuer signing and partial multi-server
client orchestration;5.5 same-key issuing/registry/leaf renewal. Focused tests in
each owning cut use the actual fixed/ordinary controls and durable client state.
The two-server parent journey preserves independent grants and denial, survives
attacker request floods, loses every result/save boundary, removes original
issuer availability and races renewal/removal/recovery without a second approval.

## Milestone 6 — server identity and certificate continuity

Store server transitions in the same authority DB, keyed by monotonic local
transition revision and operation UUID. This is local trust-history ordering,
not a migration epoch/fencing system. Each bounded signed record contains version,
purpose, durable server ID, prior authority SPKI/certificate hash, next authority
SPKI/certificate hash, revision, operation ID and reason. Staging verifies the
new key/certificate and immutable old state before publication. New authority
possession signs the same statement; planned transition also carries old authority
endorsement. Compromise transition instead requires the independent registered
owner signature on the exact replacement/challenge and cannot be authorized by
the old key alone. Retain at most 32 records per reply and page by exact prior
fingerprint/revision; retain history needed within the allowed client lifecycle.
Missing or conflicting history stops with repair guidance, not arbitrary roots.

The live local-control owner reserves the same coordinator writer, stages key
material privately and publishes active authority plus transition/result together.
The stopped owner uses the same exclusive instance lock; there is no concurrent
direct DB writer. Existing access leaves keep their exact accepted DER, live
revision and recorded issuing-authority verification until legitimate access
rotation. Historical issuer **public** keys verify those exact records only;
they cannot issue new accepted credentials or authorize the successor server.
Do not require all clients to rotate simultaneously or trust arbitrary leaves
signed by an old compromised CA.

Clients retrieve only bounded nonsecret transition material before sending access
credentials/capabilities. Verify the chain from remembered trust or independently
registered owner proof to the actual new channel key, including fresh new-key
exporter possession. Persist trust under the existing selector lock before any
ordinary credential is supplied. After adopting an owner-authorized compromise
transition, reject superseded old-authority transitions and forks. Before learning
an independent compromise repair, a client cannot magically diagnose compromise
of its still-trusted peer; this protocol does not claim retroactive detection.
A replayed record cannot roll client trust backwards. Failed save retains prior
trust and explicit repair guidance; no operation is submitted or replayed.

Same-key certificate renewal uses the existing issuer/leaf windows and purposes,
not a scheduled identity-key replacement. Verify the matching key/ID and store a
new certificate only through still-valid registered authority. Missed trust-root
validity needs independent owner recovery or explicit pairing; do not accept a
peer-supplied expired root's self-renewal. Outer HTTPS and URL changes are separate.

Ownership: 6.1 server publication, 6.2 client adoption, 6.3 independent compromise
repair, 6.4 same-key renewal. Parent acceptance runs online and long-offline
clients, lost result/restart, multiple replacements, missing/forked history,
missed validity and retained access/grants/denial without exposing credentials
to a substituted server.

## Milestone 7 — encrypted creator-bound server artifacts

Register one current X25519 recipient version per enrollment through live access.
The server encrypts a fresh 32-byte challenge to the proposed recipient; return
its hash through the authenticated request and bind both access signature and
new recipient proof to enrollment/version/revision/exporter. Only then replace
current recipient. Retain old private recipient keys locally and server storage
keys while referenced by unexpired artifacts; a bounded 24-hour artifact maximum
and no replacement while another retained predecessor is needed prevent unbounded
key history. Recovery may explicitly lose old transient artifacts; do not turn
access rotation into recipient deletion. Public keys never authorize service.

A synchronous plugin receives immutable creator/enrollment/account context from
the coordinator. It cannot nominate another creator. Reserve artifact count,
50-MiB plaintext and worst-case ciphertext/metadata disk before reading producer
bytes. Use binary age X25519 for exactly the registered client recipient and
server storage recipient; bound header parsing to 16 KiB and expected stanza
count/size before general recipient handling. No passphrase/plugin/KDF attacker
input. For up to two recipients, reserve at most plaintext + 64 KiB total payload
overhead, plus bounded encrypted metadata/index; verify this bound against the
selected backend before activation and fail closed on unexpected overhead.
Actual bytes, including incomplete staging and consumed files retained by readers,
count toward 1 GiB and 2,000 slots. Do not lower the 50-MiB plaintext limit.

Write ciphertext privately, hash measured ciphertext and plaintext incrementally,
check sticky backend errors/finalization, flush and publish atomically. Encrypt
metadata/content details separately for the same recipients. The private index
contains only artifact/enrollment/account/recipient-version references, nonce
verifier, consumption/deadlines, lengths/hashes, file/quota references needed for
authorization and cleanup. Metadata is not plaintext in an outer sidecar. Server
inspection decrypts under its retained storage key into a bounded reader; no
persistent plaintext payload is created. Cleanup holds physical reservation until
all actual readers/writers stop and the file is deleted.

The authenticated descriptor has version, durable server/agent/enrollment IDs,
origin account, artifact UUID, recipient version, age format identifier, plaintext
and ciphertext lengths/SHA256, expiry and capability. Hold it privately in client
memory; never learn trusted origin from incoming ciphertext metadata or public
HEAD. It is delivered only to the authenticated creator under native TLS.
Bound metadata/descriptors to 64 KiB. Untrusted cipher/header errors are generic;
no descriptor, nonce or content enters routine logs.

HEAD and descriptor reads recheck creator + account grant + nonce + live revision
without consumption. GET commits consumption under the existing store lock and
coordinator token **before** opening output. Crash/failure after that point stays
consumed. The same final-write barrier blocks further delivery on grant removal
or denial. A backup intentionally omits transient artifacts; restore cannot
resurrect their capabilities. Preserve ten-minute idle and 24-hour maximum TTL.
Only after encrypted writer, quotas and authorized GET pass do 7.5 activate the
plugin adapter. Legacy ownerless files/routes remain closed and confined cleanup
never scans mail, arbitrary state or client files.

Ownership: 7.1 recipient custody; 7.2 streaming encryption/backend; 7.3 quotas and
restart; 7.4 origin/consume; 7.5 all plugin/inline activation; 7.6 native contention
and packaging extension. Parent check covers both recipients, genuine 50-MiB
boundary, tamper/truncation, shared-account nonce theft, slow/revoked GET,
full-disk/orphan recovery and real mock SMTP/IMAP creation/inspection.

## Milestone 8 — four explicit client processing paths

Use the HLD commands exactly. `with-stdin` consumes a required literal string
`true` or `false` after `--stream`; do not use a parser whose Boolean flag leaves
`false` as an argv item. Reject omission, duplicates, mixed path flags, invalid
values and bad child argv **before consuming GET**. The `--` separator begins
child argv; child flags are not Arbiter flags. Use existing argv execution and
`{}` path substitution; no shell-string evaluation. Configured enrollment and
its explicit override apply to all four modes.

One process-wide four-transfer gate reserves worst-case staging/fallback before
GET. Reuse existing connect/header/idle-progress budgets and child stdout cap.
Child stdout/stderr handling remains bounded with concurrent pipe draining;
backpressure and cancellation must not leak a child or actual file/crypto owner.
No timeout or verification failure repeats consuming GET.

With `--stream true`, validate descriptor/server/key context first and decrypt
age chunks straight into child stdin. Hash/length/finalization are checked to EOF.
On failure, close input/cancel the child under existing policy, return nonzero
and disclose possible already-performed effects. This is authenticated chunk
processing, not complete origin verification before first input. No plaintext
input file exists, although the selected tool can write its own output.

With `--stream false`, download once into reserved private ciphertext staging,
then establish the immutable read-only handle or bounded snapshot described below.
Fully verify that exact immutable source: cipher hash/length, age decryption into
a discard/hash sink, origin, plaintext hash/length and finalization. Only after
those checks pass may the child start and receive a second decryption of that
same immutable source. Before the verification pass, on POSIX, close all writable
handles, reopen and verify the same inode read-only,
unlink its pathname and retain only the read-only descriptor; on Windows use
private ACL and exclusive sharing after closing writable aliases. If the platform
cannot establish stable bytes, copy into one reserved immutable byte snapshot
before the verification pass. Do not verify a mutable file and snapshot afterward.
Keep the exact descriptor/key/version in memory through both passes. Four maximum
50-MiB snapshots plus bounded overhead are reserved before GET. Path replacement,
a late writable alias or a reopened filename cannot switch verified bytes.

`with-temp --allow-plaintext-temp` writes private plaintext staging and verifies
fully before giving the pathname to the child. Remove it after actual child/file
owners finish; crash cleanup is best effort, not secure erase. `save-decrypted`
uses private same-filesystem staging, full verification and native atomic
**no-clobber** publication; a preexisting final path, symlink or publication race
fails without overwriting. On POSIX use link/no-replace publication and directory
flush; on Windows use the existing no-replace WRITE_THROUGH adapter. No ambiguous
`save` alias. Explicit bounded `get --stdout` remains immediate output with honest
final failure, not a fifth verified-before-start promise.

Ownership: 8.1 strict parser/immediate pipe; 8.2.1 encrypted temp/immutability;
8.3 plaintext temp; 8.4 persistent export; 8.5 installed process composition.
The original 8.2 remains replaced history; 8.2.1 retains its paths, acceptance
and estimates with the corrected immutable-source order. Future 8.3 depends on
8.2.1. No milestone 8 work is selected by the current milestone 3 delivery.
Parent check runs real path-only/stdin tools for all choices, wrong descriptor,
truncation/EOF, path aliases, child failure/signals, four-transfer saturation,
rotation/denial and crash orphan cleanup on the supported native platforms.

## Milestone 9 — complete consistent application backup

Extend `generations.py`; its existing initial-authority DB selector is not a
complete backup implementation. A saved configuration generation contains the
selected effective application configuration, configured policies and materialized
actual secrets. Resolve only configured `oc.env` and `secret_file` references;
never dump environment or recursively copy an arbitrary host tree. Bound reads,
use private regular-file no-follow handles, compare identity/stat and content on
both ends of capture, and retry only the pre-publication capture on changed
sources. Fail if an application-required resolver/source cannot be captured.
Normalize saved secret references into private generation-relative files or
resolved protected configuration fields, so restore never depends on the old
host's secret path. Preserve nonsecret host-dependent application settings for
ordinary operator edits; OS/service/proxy/deployment wiring remains excluded.

Publish saved configuration only after schema/policy/secret validation under the
same writer barrier as authority changes. Runtime reload and its saved-generation
reference change together before new requests see it; failed reload keeps old
runtime and saved state. Export captures that reference while holding the writer
barrier, takes a SQLite `backup` snapshot including its keys/accepted credentials/
contact/deadlines/grants/completion history, then releases the barrier before
hashing/encrypting the immutable private snapshot. There is no snapshot made by
copying a live SQLite file or pairing independently chosen config/authority times.
Read-only export remains available during maintenance.

One admin job owns snapshot, staging/disk credits and output until actual cleanup.
Private source snapshots may contain plaintext server secrets; they remain in
server-owned protected temporary generation storage and are removed afterward.
Only the **encrypted bundle** is published to the external job destination. The
external job gets the public recipient; the owner file's private signer/decryptor
is never required for export and never bundled.

Use one uncompressed record grammar inside binary age. Prefix is fixed magic
`ARBITER-BACKUP-1\n`, a big-endian u32 manifest length, exact manifest bytes and
bounded fixed-width ES256 signature; then files in manifest order, each u64 length
and exact bytes, and a fixed terminator followed by age-authenticated EOF.
The signed manifest identifies format, owner/server IDs, snapshot UTC time,
schema/config generation, registered backup-origin key ID, explicit omissions,
and sorted unique relative path/size/SHA256 entries. Signature verifies exact
manifest bytes. Incoming key IDs only select among the external file's already
registered verifiers; the bundle may not supply trust. No general archive reader,
compression, links, devices, absolute paths, duplicate/case-colliding paths,
traversal, Windows reserved/device/alternate-stream names or platform-ambiguous
separator/normalization is permitted. Use a fixed ASCII managed-path grammar.

Limits: one admin job, 4 GiB total plaintext records, 10,000 entries, 64 MiB per
entry and 1 MiB manifest. Reject before allocation/stream growth; length counters
use checked 64-bit arithmetic. Age header is bounded to the selected one-recipient
profile. Validate all hashes, signature and authenticated EOF; reject extra bytes.
The 64-MiB per-entry bound applies to the captured DB as well; an overlarge saved
application fails export honestly instead of silently omitting rows. Reserve disk
for immutable snapshot, encrypted staging and retained final output first.

Export writes a private same-directory temporary output, checks all backend
sticky errors/length/finalization, flushes and atomically replaces the explicitly
selected bundle path. It never follows an arbitrary destination symlink. A failed
job leaves the last good file intact; lost local command receipt is resolved by
that operation's recorded result/output hash, not blindly replacing it again.
The external scheduler owns startup/cadence/off-host storage/retention/alerts;
document verified SSH invocation and nonzero failure reporting, without cloud
integration, a built-in scheduler or a first-enrollment backup gate.

Ownership: 9.1 complete saved config/secrets; 9.2 coherent SQLite snapshot;
9.3 format/strict shared verifier; 9.4 local encrypted publication; 9.5 external
job and round-trip/concurrency extension. Parent acceptance changes grants,
rotation and configured file secrets while exporting, verifies a coherent
restorable result through external trust, and tests final-write/full-disk/
cancellation with exact omissions and last-good preservation.

## Milestone 10 — offline full restore and latest undo

The CLI loads recovery/restore primitives before application configuration/plugin
startup. Broken ordinary startup must not prevent restore. Acquire the existing
exclusive instance lock before reading mutable state; concurrent server start,
restore and undo fail busy. Validate safe parents/ACLs and reserve disk for staged
new generation, active state and retained undo. Never use hard links between
mutable active state and an immutable recovery snapshot.

Decrypt into a new private same-filesystem generation. Bound every record before
writing; accept only allowlisted managed files. Verify complete signed manifest,
all hashes and age EOF against the separate operator file, certificate/key/role
correspondence, DB integrity/schema and configuration/policy validity before any
active reference changes. Restore runs no plugin/network/mail operation. Incomplete
staging is unreferenced and removed under the stopped owner on restart. Unknown
schema or required migration outside the supported version range fails before
activation. No target-configuration merge or saved deadline reset.

Upgrade the existing control selector to one bounded record:
`{version:2, active:UUID, undo:UUID|null, publication:UUID}`. All three references
are published in **one** durable control replacement after referenced files and
generation directories are flushed. Record publication intent and exact target
hash using the existing restart reconciliation; recovery accepts only the exact
old complete or new complete control. A malformed/missing referenced generation
fails closed. POSIX uses same-filesystem replace plus directory flush; Windows
uses the existing WRITE_THROUGH publication adapter with private handles/ACLs.
Native process-termination tests, not mocked rename success, prove each boundary.

On successful restore, the stopped previous active generation becomes the latest
immutable undo snapshot; the staged restored generation becomes active. Only
then may unreferenced older undo data be removed. Failure before committed control
publication preserves both prior active and prior undo references. Saved contact,
expiry, denial and grants reflect the selected snapshot; warnings explain stale
restored authorization and later excluded changes.

Undo after use clones that immutable pre-restore snapshot into **another fresh
active generation**, validates and flushes it, then changes only active/publication
while retaining the same undo UUID. Running the server after undo cannot mutate
that retained point. Repeated undo after more changes restores that same point;
it does not toggle into redo. Next successful restore replaces the point;
`undo-discard` atomically clears the reference before deleting only its now
unreferenced data. No expiry, hard post-use cutoff, acceptance phase or redo
history is introduced.

Before restore/undo/discard print snapshot time, excluded later changes, restored
credentials/grants/denials, lost post-restore local edits and irreversible upstream
effects. Explicit invocation selects the action without another mandatory approval
ceremony. Ordinary host-specific configuration edits precede startup. Clients
retain identity but may need bounded predecessor authentication or explicit owner
repair; diagnostics must not claim knowledge of unseen newer client state.

Ownership: 10.1 offline decoder/trust; 10.2 lock/staging; 10.3 active+undo durable
publication; 10.4 repeatable undo/discard; 10.5 native kill/client composition.
Parent acceptance covers empty/existing host, malformed bundles, failed restore
preserving an existing undo, after-use repeated undo, disk/ACL failure and process
termination around every flush/intent/control switch on supported platforms.

## Milestone 11 — cooperative migration and authenticated handoff

Maintenance begin takes the existing live writer barrier, commits persistent
ordinary-dispatch/admin-mutation inhibition, invalidates affected tokens and
drains/cancels actual owners. Return completion only after real cleanup. Restart
reads this persistent inhibition before route admission. Export/status and explicit
maintenance end remain available; failed capture/destination startup does not
automatically clear maintenance. Fixed controls that mutate credentials are also
inhibited. The operator controls the one writable installation; no epoch, takeover,
cluster state or automatic fencing is added.

The runbook is: begin maintenance → export final coherent bundle → keep source
disabled → restore stopped destination → edit ordinary host settings → start only
destination in restored maintenance mode → run `maintenance end` locally on the
destination only → verify paired clients. Destination verification must prove that
ordinary dispatch stays inhibited before that explicit step and becomes available
after it, while the source remains disabled. Rollback after destination use can lose later
state; show the same stale-restore warnings and never restart source automatically.
Use isolated synthetic staging on its separate port when implementing the runbook;
no live account or installation migration is authorized here.

`handoff` configures a fixed destination URL on the disabled source. Its service
loads only the saved source identity/transition evidence and the immutable URL;
no ordinary API, enrollment, grant, rotation, receipt-write or recovery mutation
handler is registered. A bounded fixed control authenticates the remembered source
and returns that URL before operation submission. The client then verifies the
destination's same durable ID/current authorized key chain, persists URL under
its existing custody owner and only then starts ordinary credential negotiation.
No access certificate, account, capability or operation body is forwarded by the
handoff. Later destination key replacement uses its legitimate chain/owner proof
and needs no edit to the old fixed URL. Invalid/unavailable old URL gives explicit
configuration guidance; no arbitrary redirect or uncertain operation replay.

Ownership: 11.1 maintenance/drain/runbook; 11.2 fixed handoff/client adoption;
11.3 multi-client and whole-auth installed staging/matrix. Parent acceptance
migrates several clients with different grants, pending/completed rotations and
receipts; tests wrong destination, later replacement, offline old URL, failed
URL save/lost control result and post-use undo. Source remains non-mutating.

## Integrated acceptance, reviews and delivery boundary

| Governing parent outcomes | First owning work and integrated completion |
| --- | --- |
| R01–R03 | Preserve merged setup/enrollment; each new CLI stays lightweight and owner-separated; 11.3 checks the complete installation without external identity service, daemon or browser/provider framework. |
| R04–R06 | 3.2.1 retains identities/bindings; 5.1–5.5 own exact client repair and issuer authority; 6.3 owns independent server compromise repair; 11.3 checks two-server custody/grants/denials. |
| R07–R09 | Existing merged pairing/result evidence stays valid; 3.1.1/3.2.1 regress shared carrier/receipt, 5.1 tests repair without a second identity; 11.3 includes restart/lost-result fresh and reused enrollment. |
| R10–R12 | 3.1.1–3.1.4 transport/admission, 3.5.2 safe cutover, 3.7 real proxy/native budgets; each new control/backend owns its aggregate bounds and actual cleanup. |
| R13–R14 | 3.2.1–3.6.2 live grant/admission/output linearization, 3.5.2 installed cutover; 7.4/7.5 compose creator/account artifact access; 11.3 checks full cross-connection denial. |
| R15–R17 | 4.1–4.5 fixed lifecycle/fallback; 5.5/6.4 renewal; 6.1–6.3 server continuity; 10.5 and 11.3 cover stale restored clients and no uncertain replay. |
| R18–R20 | 7.1–7.6 encrypted origin/store/quota and 8.1–8.5 all four paths; 8.5/11.3 exercise native real-child behavior, cleanup and partial-effect truth. |
| R21–R22 | 9.1–9.5 useful complete signed encrypted snapshot/external job, verified round trip into 10.1; 11.3 checks ongoing post-enrollment/config-change backup. |
| R23–R24 | 10.1–10.5 stopped full restore, failure preservation, immutable latest undo after use and warnings; 11.3 checks multiple stale client histories. |
| R25 | 11.1–11.3 cooperative maintenance, final capture, stopped destination and non-mutating verified handoff across destination replacement. |

Each cut owns focused positive/negative/failure tests and real Python/Go prefix
integration before submission. Reuse `test_native_http_protocol`,
`test_artifact_http_admission`, installed enrollment/terminal drivers and custody
fixtures; add each shared harness once in its first consumer. Later closure cuts
extend cross-area scenarios; they do not postpone an earlier boundary's first test.
Use mock upstream SMTP/IMAP accounts only. New CLI/config/format/platform behavior
gets help, applicable package fragment and skill guidance in its owning cut.

During later authorized delivery, run applicable Python/Go tests, lint/type checks,
Go vet and required native CI. Use repository-local runners and Black with one
worker. Every final candidate needs current general, actual DBB, fresh independent
history-aware review and the applicable remote PR cycle/checks; retain immutable
original findings/responses/reports and cumulative limits. A correction/restack
invalidates affected evidence rather than inheriting the earlier head's approval.
No design review or synthetic probe here claims those implementation checks ran.

Deliver one connected stack per milestone. Complete each child review cycle in
order, then require full parent and completed-prefix integration on every final
head before presenting the stack for operator merge. No automatic merge. Verify
main after an authorized merge before starting the next milestone. A typed
checkpoint must separate planning authority from product ownership; no plan file
or sidecar is silently added to a product slice. Current design migration requires
fresh affected review/admission before any future delivery uses it.

The final 11.3 gate runs complete setup/enrollment at two isolated servers,
authenticated policy/grants/denial, clocks/recovery/renewal/server replacement,
all four encrypted artifact paths, concurrent export/offline restore/repeated undo,
and maintenance/handoff with real previously paired clients. Run full supported
Python/Go/OS/CPU install and execution matrices, contention and failure peers,
required checks, isolated staging, dependency/release metadata and docs/help/skill
consistency. Unavailable tests remain unverified and block the corresponding
publication claim. Completing a milestone is not package release, deployment,
live-account authorization or permission to implement another scope.
