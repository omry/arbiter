---
plan_key: arbiter-authentication
slice_convention: hierarchical-v1
---

# Authentication and mutual enrollment: implementation plan

Stage: implementation-plan candidate, 2026-10-20. Governing documents are the
[requirements](requirements.md), SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`,
and [HLD](high-level-design.md), SHA-256
`b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`.
Their exact reviewed stamps are required dependencies of this plan's stamp.
Neither governing document changes in this stage. This plan authorizes no
implementation, release, live-account operation or deployment by itself.
Actual DBB must approve the exact final plan before its SWE design stamp.

## Delivery identities and rules

`arbiter-authentication` owns this plan. Its numbered slices are `1` through `11`;
references below use `arbiter-authentication/1`, for example. Under
`hierarchical-v1`, an approved later split of `4` becomes `4.1`, `4.2`, etc.; never
renumber another slice or reuse a completed identity. The parent retains the
aggregate acceptance obligation. A future execution ledger records exact PR URL,
revision, dependencies, checks, reviews and state for each canonical identity;
there are no PRs, code changes or completed implementation slices yet.

Each numbered slice is one intended PR with one observable behavior, its own
negative/failure tests, documentation and applicable package fragments. Work
serially in the table order. Complete local review, checks, submission and the
approved remote review cycle for the current PR before advancing; approval binds
the final PR head, with restacking invalidating affected evidence as appropriate.
This document does not launch those cycles. If a slice becomes too large to
review, split it under its identity before implementation rather than hiding
another feature or introducing a scaffolding-only PR.

Use existing towncrier release surfaces: `server/news/` for core/native-client
integration behavior, `plugins/imap/news/` or `plugins/smtp/news/` for changed plugin
contracts, and `meta/arbiter-suite/news/` only for bundle dependency changes.
Current release tooling associates the native client with the server release
surface; do not invent an unconfigured `client/news/` directory. Internal planning
documents themselves do not need a user-facing release fragment.

All intermediate revisions are development work, not publishable authentication
support. Use isolated synthetic servers/accounts/state for prefix integration.
Do not add an anonymous compatibility mode, bypass switch, dummy production
identity, second policy layer or temporary protocol that must later be removed.
Product activation of the complete upgrade requires all eleven slices and the
combined gate below. Existing public API exposure is not declared fixed merely
because setup/enrollment exists. Once ordinary native authentication is cut over
in slice 3, anonymous legacy operation/artifact paths stay closed; an incomplete
artifact path must fail safely rather than emit an ownerless/plaintext substitute.
Missing authority state fails closed and directs the operator to trusted setup.

## Ordered PR queue

Dependencies below are direct semantic prerequisites. Serial delivery additionally
requires the preceding PR to finish its review cycle. Final integration covers
every combination, not just each slice in isolation.

| Slice | Observable result | Direct dependencies | Governing coverage |
| --- | --- | --- | --- |
| 1 | Operator initializes durable authority and uses authenticated local control. | None | R01–R06, R11–R12; HLD shared/B2/B3 |
| 2 | Owner pairs a real Go client with one secret handoff and durable single activation. | 1 | R04–R09, R10–R12; HLD B1/B2/B3 |
| 3 | Enrolled clients discover/invoke only granted accounts; live removal stops affected delivery. | 1, 2 | R10–R14; HLD shared/B2/B3 |
| 4 | Due client keys rotate, reconcile lost replies and complete safely after grace/expiry. | 1, 2, 3 | R15–R17; HLD B2/B3 |
| 5 | Owner repairs client identity/access without losing grants or clearing denials. | 1, 2, 3, 4 | R04–R06, R16–R17; HLD B2 |
| 6 | Planned or independently authorized server-key replacement preserves durable trust. | 1, 2, 4, 5 | R06, R10, R17; HLD B2/B3 |
| 7 | Server retains and delivers encrypted creator-bound artifacts with existing quotas. | 1, 2, 3 | R14, R18, R20; HLD B4 |
| 8 | Client offers all four artifact processing modes with honest verification/cleanup behavior. | 3, 4, 7 | R18–R20; HLD B4 |
| 9 | External local/SSH jobs obtain coherent, signed encrypted application backups. | 1, 3, 4, 5, 6, 7 | R21–R22; HLD B5 |
| 10 | Operator restores offline and can undo the latest restore, including after use. | 4, 5, 6, 9 | R23–R24; HLD B5 |
| 11 | Operator migrates paired clients during maintenance with verified URL handoff. | 3, 4, 6, 9, 10 | R10, R14, R17, R25; HLD B5 |

## Shared implementation contracts

### Code ownership and schemas

Add cohesive server identity/authority/transport/control modules beneath
`server/src/arbiter_server/`; add Go identity/state/carrier code beneath
`client/go-cli/internal/`. The exact module decomposition follows existing code
style; do not create a plugin/provider framework. `main.py` remains the ASGI
composition point, with immutable verified caller context and shared admission.
Existing `http_work.py`, `blocking.py`, artifact storage and account policies keep
their behavior except for the expressly planned identity/encryption checks.

Each slice commits its versioned schemas and shared positive/negative Python/Go
fixtures with the implementation that consumes them. Put synthetic protocol
fixtures in a clearly test-only shared location; fixed test keys never initialize
product trust. Schemas reject duplicate identities/JSON fields, unknown critical
fields, invalid lengths/encodings and trailing input. Sign/verify exact bytes;
ES256 is fixed-width `R || S`, not an ASN.1 signature or a reserialized payload.
No unspecified schema field may silently grant authority or select an algorithm.

Slice 1 fixes X.509/SAN URI/purpose/key-usage profiles, role trust stores, key-file
and recovery-file formats, authority row/revision semantics, and certificate
validity/renewal schedule specifications. Slice 5 owns owner-issuer and portable
certificate renewal; slice 6 owns server-authority and inner server-leaf renewal,
including trust-expiry recovery. Routine certificate renewal preserves the
appropriate key and durable IDs; it is not periodic server identity-key replacement.
Slice 2 fixes
Noise prologue/transcript/messages, pending/receipt state and inner-carrier ALPN.
Slice 4 fixes rotation/control proofs and acceptance state. Slice 5 fixes owner
recovery JWS and fresh challenge consumption. Slice 6 fixes server transition
proofs/history. Slice 7 fixes the authenticated artifact descriptor/age profile.
Slice 9 fixes backup records/manifest/signature and coherent snapshot semantics.
Slice 10 fixes durable active/undo publication and offline lock behavior.
Fixtures are reviewable parts of those PRs, not later unspecified integration work.

### Dependencies, platforms and defaults

Keep Python 3.10–3.14 and Go 1.24 as current compatibility floors; preserve the
existing Linux/macOS/Windows x64/arm64 CI matrix. Before adding each dependency,
select an exact tested compatible version set, review advisories/reachable uses,
record wheel/source-build needs and run its affected native matrix. The fourteen
Linux HLD probes are evidence of feasibility, not production dependency approval.
In particular, do not install pyOpenSSL26.3/cryptography49 under the current
`cryptography<49` constraint; evaluate a supported compatible set. Age's Rust
binding and the older Python Noise wrapper require explicit packaging/boundary
tests. Source builds may need build tools but must not create a separately
operated runtime service. Failure blocks the affected slice/release; changing
supported platforms or the HLD mechanism requires alignment, not silent narrowing.

Use structured named config fields owned by their domain. Carry HLD defaults
unchanged: 16 total carriers, 4 handshakes, 10-second handshake/control budgets,
256 KiB buffers and total handshake bytes per direction, 32 KiB headers/cert
chain, 64 KiB controls/WebSocket messages, no compression, five-minute idle with
active-operation exceptions, four client transfers and one administrative job.
Retain the Noise wire/plaintext limits, twenty pending requests, five authenticated
attempts, ten-minute enrollment lifetime, 30-day rotation, 24-hour fixed grace,
180-day inactivity and 365-day access certificates. Do not turn U53 into a
64-carrier default. Preserve existing worker/body/artifact limits and separate
connect/API/download-idle semantics. Validate types/ranges and expose effective
nonsecret settings through authenticated diagnostics.

For every public input, enforce aggregate byte/work limits before expensive
decode/crypto or allocation, including fragmentation, age headers, certificate
chains and backup manifests. Library convenience APIs are not admission control.
Reject overload without an unbounded queue; retain ownership until actual cleanup.

### Test layers and review evidence

Each PR has focused server/Go unit tests and a real Python-server/Go-client
prefix integration test for its behavior. Include real loopback sockets, a test
TLS-terminating WebSocket relay, restartable temporary directories and deterministic
clock/fault barriers. Isolated mock upstream SMTP/IMAP services supply fixtures;
no personal accounts or external mail effects. Library fixtures alone do not
prove application authorization, resource ownership or crash consistency.

Run relevant regressions, repository lint/type checks and required CI checks for
that PR. Commands include repo-local pytest targets, `.venv/bin/nox -s lint`
(Black uses one worker), and `go test ./...`/`go vet ./...` in `client/go-cli`.
Use existing supported test runners instead of bypassing their setup. The final
combined gate runs the full Python suite, server/Go integration and complete
supported matrix. Unavailable checks remain unverified and block relevant claims.

Before each PR is treated as complete, review its final code locally, resolve
findings, run its checks, checkpoint, submit and finish its remote PR review
cycle. Use actual DBB for security-sensitive changes with exact reviewed revision
evidence; if the optional reviewer workflow feature is unavailable, keep explicit
DBB orchestration. Changes after approval need appropriate re-review. No silent
reviewer substitution. This plan does not authorize automatic merging/deployment.

## Slice 1 — operator authority and local control

Deliver a complete operator setup/status journey: create durable owner/server IDs,
issuer and role-separated keys, external operator recovery file/public trust and
private SQLite authority state; inspect/manage it through authenticated Unix
socket or Windows named-pipe control. Recovery material stays outside running
server/agent/job storage. Status reports setup state without printing private keys.
An ordinary loopback connection cannot administer the instance. Initialize only
through trusted OS/SSH tooling; repeat setup cannot overwrite existing authority.

Scope includes OS ownership/ACL validation, private atomic file publication,
single-writer instance lock, exact credential profiles and shared Python/Go profile
fixtures. Keys/profile functions are exercised by real initialization and local
control, not a standalone abstraction PR. Register owner public trust before use.
Agent `NEVER SHARE THIS` custody and recovery-file combined powers are documented.

Acceptance: restart preserves IDs/trust; injected file/DB failures leave either
complete setup or safely recoverable unpublished staging; concurrent init/start
cannot create two writers. Wrong OS user, permissive ACL, unregistered owner,
wrong EKU/issuer/role/SAN, duplicate IDs and generic valid certs fail. Owner/recovery
private material never appears in server state, logs, argv, env or ordinary APIs.
Test native POSIX and Windows control boundaries. Add setup/trust documentation,
operator command help and an `arbiter-server` news fragment.

## Slice 2 — pairing a real client and reconciling activation

Deliver `enrol` from a real Go CLI against a real initialized Python server:
generate provisional per-server/portable/recipient keys and 32-byte secret; closed
server explains opening; trusted operator opens a bounded window, lists/selects
request, supplies protected PSK and approves exact keys/grants once. Existing
portable identity requires registered issuer/fresh possession but no automatic
access. Client receives prepared trust/certs directly and durably saves before
ready proof; one authority transaction activates and returns its recorded receipt.

Implement production Noise framing and bounded WebSocket adapter together with
the minimal inner server-authenticated TLS receipt/status control required to
recover committed activation without PSK. Python/Go fixtures exercise exact
transcript/key/role/request bindings. This slice includes the real encrypted
transport dependency and platform checks; it does not defer a lost-reply path to
an unspecified future carrier. Ordinary operation dispatch is slice 3.

Acceptance: one secret handoff/approval, no return bundle or extra fingerprint
step; anonymous/wrong-PSK traffic cannot spend five attempts; identical authenticated
replays share one owner; five distinct failed/abandoned exchanges exhaust without
reset. Enforce original ten-minute deadline and twenty pending records across
reconnect/open/approval races. Fault/crash tests before ready, after client flush,
before/after commit and after lost reply prove one durable identity/grant/result.
Restart invalidates uncommitted PSKs only; denial beats stale completion. Abandoned
client/server state cleans within bounds. Add enrollment/recovery diagnostics,
custody guidance and fragments for server and native client packaging as applicable.

## Slice 3 — native API, account grants and live denial

Route every ordinary CLI discovery/schema/invocation/download constructor through
the expected-server-verified inner TLS 1.3 transport, including errors and tool
paths. Preserve API shape and existing policies after `(plugin, account)` grants.
Reject anonymous/proxy-header callers and hide ungranted account information from
discovery and direct routes. Provide local grant add/remove and enrollment denial.
Creator/account caller context is mandatory for subsequent artifact work.

Implement shared coordinator admission and output cancellation across connections;
ordinary TLS checks exact live enrollment/accepted key, certificate and role.
Remove anonymous legacy API/artifact routing in the same cutover, invalidate
server-managed ownerless artifacts without guessing ownership or touching mail/
client files. No artifact storage fallback may bypass caller context. Keep
setup/health/bootstrap separate, and missing authority fails closed.

Until slice 7 installs the encrypted store, slice 3 must replace the plugin-facing
artifact creation/inspection dependencies with an explicitly closed adapter.
Reject artifact creation before calling the legacy store or creating a payload
file; never delegate to it, return a usable descriptor or expose nonce-only access.
This is an implementation-readiness boundary, not a client policy/allowlist or
operator bypass flag. Errors report any already-completed upstream effect honestly
and never trigger automatic operation replay. Slice 7 replaces this closed adapter
with the encrypted creator-bound adapter through the same caller-context boundary.

Acceptance: verified enrolled Go client invokes a granted synthetic operation;
portable/cross-server credentials cannot. Denial/grant removal wins races against
queued admission, request bodies, upstream dispatch and ongoing output; retained
blocking work keeps its slot until exit. Password renewal does not require grants;
repointed names keep deliberate grants. Test substituted server, changed outer
cert/proxy, malicious headers/redirects, byte/control limits, slow/chunked bodies
and unchanged timeout budgets; no native secret reaches the relay. Add account
grant/transport/proxy documentation, server/client fragments and skill guidance
where discovery/enrollment instructions change.

The slice-3 prefix test must also invoke an artifact-producing operation through
the real authenticated Python/Go path before slice 7. Assert the closed adapter
rejects before legacy store entry/payload publication, no plaintext or ownerless
file/metadata/nonce/usable descriptor is created, no legacy route can read one,
and cancellation/repeated failure release bounded staging and worker ownership.
Use a legacy-store spy plus filesystem inspection, including restart cleanup;
the final no-publication gate alone is not this acceptance evidence.

## Slice 4 — rotation, late completion and bounded fallback

Deliver on-contact due rotation, fixed grace, durable candidate save/confirmation
and lost-response reconciliation. A pending deadline is recorded before asking
for candidate; withholding/retry/old ordinary use cannot move it. Current,
predecessor and pending candidate form coherent client state. Use the separate
fixed control carrier to finish the exact rotation after grace or cert expiry;
ordinary mTLS stays strict. Control proofs bind both stored keys, fresh challenge,
TLS exporter, identities, purpose and pending revision. Reissue only that same
candidate certificate; no general expired-credential exception.

Acceptance: test 30-day/24-hour/180-day boundaries, old/candidate expiration,
candidate-withholding, disconnect and restart at every save/commit/receipt point.
Ordinary access remains until grace, then only pending completion is possible;
incomplete controls cannot refresh inactivity. Revocation/owner repair wins every
activation race. Receipt survives needed expiry/reconciliation intervals. One
predecessor authentication attempt requires unambiguous authenticated rejection
by the same verified server; never after denial/timeout/unverified peer or operation
submission, and never replay uncertain mutation/GET. Test across both languages
with real controlled clocks/sockets and malicious control transcripts. Add lifecycle
configuration/help, repair guidance and server/client fragments.

## Slice 5 — owner-authorized client recovery

Deliver trusted owner tooling to authorize a fresh per-server access key or
portable-key repair for an existing durable identity. Consume a server-specific,
fresh challenged ES256 authorization and replacement-key possession proof in one
conditional authority transaction. Signature supplies approval; no second remote
operator approval or grant recreation. Preserve suspensions/revocations. Local
pairing repair of the existing enrollment remains the simple alternative.

Acceptance: two isolated servers register the same owner trust but distinct access
keys; owner tooling produces per-server proofs and repairs both without sharing
issuer private keys or trusting another server's credential. Portable possession
alone fails. Wrong algorithm/key/purpose/server/revision/expiry, replay, concurrent
recovery/rotation/denial and lost response fail or reconcile exactly once. Revoked
enrollment remains revoked after repair. Owner/recovery file stays outside agent
and server custody; missing proof offers explicit repair, not identity reset.
Add recovery-file theft consequences, CLI journey and server/client fragments.

This slice also implements owner-issuer and portable-leaf certificate renewal
using slice 1's selected schedules, registered signer and live-state checks while
preserving owner/agent IDs and purpose separation. Controlled-clock tests cover
before-expiry renewal, signer/key mismatch, denial and renewal races, rejection
of portable credentials on ordinary TLS, restart and missed-renewal diagnostics.
Certificate renewal must not become an automatic access-key replacement or grant.

## Slice 6 — server identity replacement

Deliver operator-triggered planned authority replacement with old endorsement,
new-key possession, stable server ID and durable client adoption/transition
history. Separately deliver compromise repair using the independently registered
external owner verifier, never compromised server key alone. URL/HTTPS renewal
does not trigger identity rotation. Private/public-key roles remain distinct.

Acceptance: online and offline clients recognize legitimate replacement, including
lost transition response/restart and later key changes. Wrong ID, missing history,
expired/untrusted chain or endorsement by compromised key alone cannot install new
trust; missing independent authority gives explicit owner pairing guidance. Client
never sends credentials/capabilities before verifying the expected server or
authorized repair. Neither path clears enrollment denial or duplicates grants.
Test installed-client state and preserved history with real Python/Go peers.
Document planned versus compromised-key procedure and add server/client fragments.

This slice separately implements server-authority and inner-server-leaf certificate
renewal under slice 1's validity/schedule profiles, without scheduled identity-key
rotation. Test authorized before-expiry renewal with stable server ID/key,
appropriate issuer/live-state checks and clients retaining remembered trust.
Controlled-clock negative tests reject peer-provided renewal of an expired server
trust anchor; missing the trust-validity deadline requires the independently
authorized owner recovery path (or explicit pairing when that trust is missing).
Test expiry/restart/renewal races and ensure HTTPS renewal/URL changes do not replace
native identity or silently extend authority. Keep these tests distinct from
access-key rotation and planned server-key replacement.

## Slice 7 — encrypted server artifacts and ownership

Deliver server-side age encryption for the creator's registered recipient and
server storage recipient, authenticated origin descriptors and ciphertext delivery
through the native API. Thread immutable caller/account context into all plugin
artifact creation, including inline native descriptors; a plugin cannot choose
another creator. Keep server-readable inspection possible without adding scanning.
Recipient registration needs authenticated proof/new-key challenge and retained
key history is bounded by active artifact lifetime; auth rotation cannot discard
needed recipient keys. No persistent plaintext staging is introduced as a shortcut.

Acceptance: creator plus nonce plus live enrollment/account grant is required for
metadata/HEAD/GET; shared account, copied URL or revoked grant fails. HEAD is
non-consuming; GET atomically consumes before open, including failed streams.
Test nonce/consume/revoke/store-lock races, slow writer/disconnect, restart, orphan
cleanup and quota reservations through actual cleanup. Preserve 50 MiB plaintext,
2,000 artifacts, 1 GiB physical, ten-minute idle and 24-hour maximum; count all
crypto/metadata/staging overhead without lowering plaintext cap. Mock IMAP/SMTP
flows exercise real artifact adapters. Legacy cleanup is idempotent and confined
to server-managed ownerless files. Add storage/ownership docs and affected server/
plugin fragments; server encryption is not whole client-processing completion.

## Slice 8 — four client artifact paths

Deliver immediate plaintext stdin, fully verified encrypted-temp-to-stdin,
explicit plaintext-temp pathname and deliberate `artifact save-decrypted`.
Authenticated descriptor and key remain private process state; downloaded metadata
cannot nominate origin. Verified modes finish digest/length/age finalization and
source checks before tool launch/publication. Keep stable ciphertext between
passes through proven platform handles or the HLD's bounded immutable snapshot.
Do not select a universal mode/default or preserve an ambiguous `save` alias.

Acceptance: tamper, truncation, wrong origin/creator/recipient and changed staging
path cannot start a tool in verified modes. Immediate stdin may deliver valid
prefix, but reports final failure honestly. Test POSIX writable-alias removal,
Windows sharing/ACL constraints, fallback reservation, four-transfer saturation,
full disk, child failure, signals/cancellation and crash cleanup. No orphan file
resumes a consumed GET; plaintext temp/export require explicit action and no
partial final export. Access/recipient rotation and grant denial during download
compose with slices 3/4/7. Explain delay/storage/partial-effects/plaintext exposure
in both client reference and operation help, discourage persistent agent exports,
and add native-client fragments and affected skill updates.

## Slice 9 — useful consistent encrypted export

Deliver local/SSH export usable by an external startup/periodic job: capture one
saved config/policy/actual-secret/authority generation, briefly fence mutations,
take coherent SQLite snapshot, then stream signed manifest/records through age to
the registered external recipient. Capture configured secret-file values without
arbitrary environment dumps; changed source during capture aborts before publish.
Exclude the HLD omissions, especially sessions, PSKs, client private keys and
transient artifact payloads. Operator file/public metadata and encrypted bundle
are clearly distinct; server/job has no private recovery signer/decryptor.

Acceptance: enroll/grant/revoke/rotate/config reload during export yields one
coherent snapshot. Secret-source changes, output failure/cancellation/disk full,
oversize/header/manifest abuse and crash preserve the previous complete export;
never expose partial final bundle or plaintext staging. One admin job at a time,
4 GiB total plaintext/10,000 entries/64 MiB per entry/1 MiB manifest and bounded
encrypted header/overhead parsing are enforced before growth. Verify exact signed
manifest/file digests using external trusted metadata in a round-trip harness,
not a signer supplied by the bundle. Include job examples for durable storage and
failure reporting without adding scheduler/cloud integration/first-enrollment
gate. Add backup CLI/docs and an arbiter-server fragment.

## Slice 10 — offline restore and latest undo

Deliver offline restore while the server is broken/stopped, using the external
operator file to decrypt/authenticate a complete bundle privately. Verify strict
records/manifest/origin/hashes/key correspondence/config before publishing a fresh
saved generation. Use exclusive stopped-instance lock and one durable transaction
for active/undo references; failed restore preserves current state and previous
undo point. Preserve saved deadlines, not restore-time authority extensions.

Deliver offline undo of that latest successful restore, without expiry or a
post-use hard cutoff; explicit discard or next successful restore replaces it.
Warn very prominently about snapshot date, lost post-snapshot/post-restore local
changes, restored grants/revocations and upstream effects that cannot be undone.
No silent merge, restore wizard, redo history, paused-access mode or acceptance
ceremony. Ordinary different-host edits remain available before start.

Acceptance: real process termination/fault injection before/after flush and each
reference update leaves an old or new coherent generation on native POSIX/Windows.
Reject malformed/duplicate/traversing/link/device/oversized records and wrong
external verifier; no partial activation. Restore to an empty host and over an
existing instance; test old snapshots, client current/predecessor divergence,
pairing repair, revocation preservation within the selected snapshot, repeated
restore failure, undo after use and explicit discard. Show clear repair diagnostics
without claiming to know unseen client history. Add full restore/undo examples,
host-configuration exclusions and arbiter-server fragment.

## Slice 11 — cooperative migration and handoff

Deliver maintenance/migration tooling and runbook: disable source ordinary work
and state-changing admin, drain/cancel real owners, export final snapshot, restore
stopped destination, edit host settings, start destination only and keep source
disabled. Optional source handoff is a fixed authenticated URL response using
saved source identity, without ordinary dispatch/enrollment/grant/rotation writes.
Client obtains URL before operation submission, verifies the same durable identity
and legitimate current key-transition chain at destination, then stores the URL.
No HTTP redirect or repair replays uncertain mutation/consuming GET.

Acceptance: multiple previously paired clients migrate with distinct grants,
pending/completed rotations and receipt state. Test destination identity mismatch,
later destination key replacement without changing old handoff URL, offline/old
URL failure, lost response and explicit URL edits. Clients send no credentials/
nonces to an unverified destination. Source mode cannot mutate authority. Post-use
undo/fallback warns of stale state and never automatically starts the old host.
The operator, not a new epoch/fencing/cluster mechanism, prevents two writable
copies. Run actual Reploy staging on its separate port using synthetic state;
no live installation migration is authorized by this plan. Add maintenance/handoff
docs, client diagnostics and affected server/client fragments.

## Combined acceptance and publication gate

Every slice runs prefix integration; do not defer first Python/Go composition,
platform packaging or cancellation testing until this gate. Once all eleven PRs
have approved final heads and required checks, run the complete synthetic journey:

1. Fresh private setup with no public CA/DNS or external identity account; enroll
   first and additional clients with one handoff/approval, restart and reconcile.
2. Authenticated discovery/direct invocation of mock SMTP/IMAP accounts with
   distinct grants, existing policies, grant change/denial and concurrent output.
3. Due rotation, fixed grace, expired-certificate control completion, inactivity,
   predecessor fallback, owner client repair, owner-issuer/portable/server-authority/
   server-leaf certificate renewal and server-key replacement, including refused
   expired peer-driven trust renewal and explicit owner recovery after missed validity.
4. Encrypted artifacts through all four modes with origin/recipient checks,
   failed consuming GET, full limits, quota contention and real cancellation.
5. Concurrent-state export, offline restore, failed restore preserving undo,
   after-use undo/repair, maintenance migration and non-mutating URL handoff.

Run combined contention/cancellation/deadline/cleanup checks so carrier, crypto,
HTTP/body, plugin worker, file/quota and admin ownership cannot each pass while
their composition leaks work. Run all Python tests and Go tests/vet, lint/type
checks, client/server builds/install smoke, supported Python/OS/CPU matrix and
Reploy staging regressions. Public proxy tests include TLS termination with an
untrusted inspecting relay, changed outer cert, and TCP passthrough; future
browser administration remains unimplemented, with its documented trust boundary.

Published help/docs/skill, package dependency metadata, release notes and upgrade
guidance must agree with the final implementation. Existing pre-auth clients get
actionable enrollment guidance, not an unauthenticated fallback. Do not require
live accounts to prove safety or mark unavailable checks as passed. Remaining
failures require fixes/re-review or an explicit requirements/HLD decision; no
scope reduction, platform drop or readiness claim is hidden in release wording.

Plan approval means this is an acceptable order and verification obligation,
not that any slice is implemented or has a PR. Execution begins only on separate
authorization. Reviews and exact-byte stamp evidence are stored outside this
document so recording its verdict does not invalidate the reviewed plan.
