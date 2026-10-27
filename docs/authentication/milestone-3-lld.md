# Native API, grants and live denial: low-level design

Status: design candidate; no product implementation or delivery authorization.
This refines [requirements](requirements.md) R10–R14 and [HLD](high-level-design.md)
B2/B3. It consumes the reviewed [portable enrollment LLD](portable-enrollment-lld.md).
Those three documents are its explicit design dependencies. Requirements/HLD bytes
remain unchanged. The original milestone-3 seven-PR forecast is historical; the
already sized proposal below replaces its cuts, not its approved product scope.

Source basis: merged main `25a8cf2196f36499ff92ce890f313901b51b4f2b` plus the
unimplemented portable-enrollment contract. Current `ControlTLS` is a fixed
64-KiB exchange, not an ordinary stream. `PairingProtocol` owns carrier bounds;
`BlockingOperationPool` already retains actual threads after waiter cancellation;
`HTTPWork` supplies a worker/loop bridge. `main.py` still exposes anonymous API
and legacy artifact routes. Go `configuredHTTPClient` still constructs ordinary
HTTP clients. This document specifies changes to those paths, not a second API,
transport daemon, policy engine or authorization cache.

## One ordinary channel and one selected enrollment

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

## Shared bounded carrier and TLS stream

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

## HTTP/1.1 to the existing ASGI application

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

## Live authority and local administration

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

Extend trusted `arbiter-server authority` controls with `grant add`, `grant remove`
and `enrollment deny`. Inputs are one exact enrollment ID and, for grant actions,
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

## Coordinator, dispatch and denial linearization

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

## Discovery, policy and artifact closure

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

## Cutover and acceptance

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

## Already sized implementation proposal

Numbers are handwritten added + deleted production, tests/fixtures and CI,
including code motion, shared helper ownership and review-correction headroom.
Docs/news/lock data are separate. These bottom-up ranges replace the historical
3350–5840 code/test forecast; they are estimates for review, not measured future
diffs, approved delivery counts or a dynamic-splitting allowance. Each upper bound
fits the existing 1000-line cap before implementation starts. If source evidence
invalidates a cut, return to design rather than hide a late split in execution.

| ID | Complete consumed result, scope and focused checks | Production | Tests/fixtures | CI | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| `3.1.1` | Shared bounded carrier/lease/TLS pump extracted and consumed by existing pairing/control and real streaming Python/Go peers. Own shared streaming harness. N03/N04; no ordinary public route. | 320–440 | 300–420 | 0–5 | 620–865 |
| `3.1.2` | Strict Python ordinary mTLS and immutable candidate identity consumed by real peer; live admission still private. N01 and handshake limits. | 170–240 | 310–420 | 0–5 | 480–665 |
| `3.1.3` | Go strict native identity/dial plus standard HTTP transport, exercised against real Python; reuse stored decoder and existing timeout wrapper. N01/N02/N11. | 190–260 | 300–410 | 0–5 | 490–675 |
| `3.1.4` | One-request bounded HTTP/ASGI stream with immutable scope, real body/response peers and no public cutover. N03/N04, parser/body admission. | 230–320 | 350–470 | 0–5 | 580–795 |
| `3.2.1` | Live row extension/atomic backfill and OS enrollment-deny control with minimal shared coordinator consumed by the real fixed receipt. Guard receipt admission and final output, retain actual owners and test N07 receipt races/rollback/cleanup. Use the existing agent binding without a row copy; validate old/fresh/replay and corrupt relations. Extend activation once; preserve original receipt. N05. | 230–330 | 420–610 | 0–5 | 650–945 |
| `3.2.2` | Extend the same coordinator/writer reservation to ordinary authority and actual-worker callbacks, consumed by private real ASGI peer. Own OS grant add/remove controls. N04/N05/N06/N07 dispatch, busy/rollback and no stale snapshot authorization. | 220–300 | 330–430 | 0–5 | 550–735 |
| `3.3` | All discovery/direct-account/policy projections use exact live grants with current schemas and private peer. N06 including negative information leaks. | 160–230 | 320–440 | 0–5 | 480–675 |
| `3.4` | Real authorized plugin dispatch with caller context, closed concrete artifact dependency, descriptor/inline closure and confined startup invalidation. N09 with spy plus filesystem; reuse shared harness. | 230–320 | 350–460 | 0–5 | 580–785 |
| `3.6.1` | Denial closes all affected body/dispatch paths across actual sockets; actual blocked work retains slots/carriers. N07/N08, cutoff/rollback and admin responsiveness. | 190–270 | 350–470 | 0–5 | 540–745 |
| `3.6.2` | Permission after backpressure and final ciphertext-write boundary, abort without flushing revoked output. N03/N07/N08 streaming/late-worker cases. | 150–220 | 330–450 | 0–5 | 480–675 |
| `3.5.1` | Explicit selector/config/help and every CLI constructor exercises the verified native factory through complete private real peers; production selection stays on the existing working route. N10/N11. | 130–180 | 320–410 | 0–5 | 450–595 |
| `3.5.2` | Atomic production CLI constructor switch, public ordinary enablement and anonymous API/artifact closure, with no fallback; first installed authenticated prefix executes denial and closed-artifact journey. N01/N04/N07/N09/N10/N12. | 110–190 | 380–510 | 0–5 | 490–705 |
| `3.7` | Focused installed relay/outer-cert/passthrough and timeout composition, native prefix/packaging closure; add no deferred core mechanism. N02/N11/N12 and all parent N01–N12. | 20–60 | 380–480 | 0–10 | 400–550 |

Order follows the displayed rows; existing 3.1/3.2/3.5/3.6 parents retain their
identities and acceptance. `2.10.4` precedes `3.1.1`; `3.6.2` precedes public
cutover. Each source/peer foundation has an executable current consumer, not an
empty interface. 3.1.1 owns shared carrier/harness code motion; later cuts own
only incremental scenarios. 3.5.2 owns the first installed ordinary journey;
3.7 extends it, not creates a second harness. The revised cut ranges move
20–40 production and 30–50 focused regression lines from
`3.5.1` into `3.5.2` for their atomic production switch; they are not counted twice.
Receipt/coordinator ownership moves a net 20–30 production and 20–30 test lines
from `3.2.2` into `3.2.1`; ordinary grant controls move the other way. Existing
fixed-control real-socket fixtures support 100–150 additional receipt-race test
lines in `3.2.1`. These are estimates, counted once; later ordinary and installed
coverage extends the shared harness rather than repeating the receipt matrix.
The proposed 13 PRs and aggregate range require separate implementation
authorization. No milestone-2 allowance is
reused and no milestone-3 product work begins as part of this design PR.
Aggregate: 2,350–3,360 production, 4,440–5,980 tests/fixtures and 0–70 handwritten
CI lines, or 6,790–9,410 total. The larger test share includes real streaming,
revocation/worker-lifetime and cross-language composition at their owning cuts.

Primary API references supporting the adapter choice:
[Python 3.10 protocol API](https://docs.python.org/3.10/library/asyncio-protocol.html),
[Python 3.10 SSL protocol source](https://github.com/python/cpython/blob/v3.10.19/Lib/asyncio/sslproto.py),
[pyOpenSSL memory-BIO API](https://pyopenssl.org/en/stable/api/ssl.html).
The implementation must validate the installed pinned versions and supported Go
floor rather than treat newer local library/source inspection as native proof.
