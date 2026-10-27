# Portable identity enrollment: low-level design

Status: design candidate. This document specifies unimplemented work; it neither
creates trust nor authorizes implementation. The governing authorities are the
[requirements](requirements.md) (R04–R09, R11–R12) and
[HLD](high-level-design.md) (B1/B2/B3). Their reviewed bytes remain unchanged.
This closes the existing-portable-identity omission in milestone 2; it is not
milestone 5's repair of an existing enrollment. The old 22 delivery attempts
remain exhausted. The four proposed implementation PRs below need separate
implementation authorization after design review.

Source basis: merged main `25a8cf2196f36499ff92ce890f313901b51b4f2b`.
`prepared.py:prepare` currently generates every agent ID and portable leaf;
`enrollment_activation.py:activate` accepts only the local owner issuer;
`identity/provisional.go` always generates a fresh portable key. Existing Noise,
proofs, certificate profiles, approval, private publisher and exact receipt remain
the implementation foundations. No new cryptographic library or protocol service
is introduced.

## Observable journey and minimum solution

1. A client already enrolled at A selects its saved enrolled selector explicitly:
   `arbiter enrol url=https://B name=NAME identity=SOURCE_SELECTOR`.
2. B's operator has independently registered A's public owner-issuer certificate
   through trusted local administration. Public issuer registration is not a new
   secret handoff or an extra pairing fingerprint ceremony.
3. The client reads the portable key/certificate locally, generates fresh B access
   and recipient keys, selector and PSK, saves provisional state and advertises.
4. B's operator opens enrollment and approves the exact request and B grants using
   the existing single protected PSK handoff. Registered trust or a valid portable
   certificate never approves a request or grants an account automatically.
5. The existing Noise exchange proves the fresh access key and the selected
   portable private key over the exact advertisement and transcript. B keeps the
   original agent ID/portable certificate, issues only B access, and performs the
   same prepared-save/ready/conditional-commit/receipt/promotion sequence.

A need not be online. Its private access key never authenticates B and is never
sent to B. A's grants, denial, URL and access lifetime are not B authorization.
An existing B enrollment for this agent, including a denied or inactive one,
rejects this journey and offers owner-approved existing-enrollment repair. It must
not create another B identity to evade denial or overwrite accepted access.

Keep the portable key in each existing per-enrollment private bundle. Reuse the
native no-replace publisher and ownership checks. A separate global identity store,
key broker, account policy layer or reusable bearer capability is unnecessary.

## Registered owner trust

Extend the same private SQLite authority, under the existing instance owner:

| Column | Constraint and use |
| --- | --- |
| `issuer_sha256` | Primary key: lowercase hex SHA-256 of canonical DER, exactly 64 characters. |
| `owner_id` | Canonical UUIDv4 from the exact owner-role SAN. |
| `certificate_der` | Canonical owner profile DER, 1–4,096 bytes. Immutable for this fingerprint. |
| `active` | Integer boolean. Removal retains the row as an inactive tombstone. |
| `revision` | Positive monotonically increasing integer for effective trust changes. |

Register the initialized local owner issuer once. Repeated startup must not
reactivate an explicitly disabled row. Same active DER registration is idempotent;
removal and later reactivation advance revision. Same owner UUID may have multiple
explicitly registered issuer certificates: reuse of the operator recovery file
preserves owner UUID while independent server setup generates independent CA keys.
Select trust by exact registered certificate fingerprint, never by UUID alone.
The server cannot issue or renew a portable leaf under another server's key.

Add trusted local controls under the existing OS-authenticated dispatcher:
`owner-issuer register`, `owner-issuer list`, `owner-issuer export`, and
`owner-issuer remove`. Their CLI surface is
`arbiter-server authority owner-issuer ACTION --state-dir ROOT`.
Register takes `--certificate-file PATH`; export/remove take `--issuer-sha256 ID`.
The CLI reads one bounded public DER file and sends its bytes over the existing
64-KiB local control exchange. Export writes public DER to stdout. List paginates
32 summaries containing owner ID, fingerprint, active state and revision; export
returns one certificate, not an unbounded certificate list. No private key input,
network registration, arbitrary server-side file path or silent replacement.
An inactive/expired issuer cannot authorize new enrollment. Removing issuer trust
does not retroactively revoke an already activated B access credential; that is
B's explicit enrollment-denial authority.

Use one atomic, owner-only upgrade of the existing authority schema before
serving enrollment. Support reading the previous schema during interrupted
publication reconciliation, then upgrade it once; reject unknown future schema
versions. Preserve all original credentials and exact enrollment-result bytes.
The migration creates this registry and the following unique binding table:
`agent_enrollments(agent_id PRIMARY KEY, enrollment_id UNIQUE NOT NULL)`.
Backfill it from each bounded stored prepared result in the same transaction;
malformed or duplicate historical bindings fail startup with state preserved.
Existing empty/unactivated databases need no synthetic enrollment. Migration is
an extension of current SQLite/version handling, not an ORM or migration framework.
The same `2.10.1` cut also changes the existing public fresh-v1 activation writer:
insert its `agent_enrollments` binding in the very transaction that inserts the
enrollment/result. Exact committed replay must find the same agent/enrollment
mapping; a missing/conflicting binding fails closed, never creates a second row.
The backfill and this writer change ship together before an upgraded server can
accept another fresh request. Thus v1 enrollments created during `2.10.1` or
`2.10.2`, including subsequently denied/inactive ones, cannot escape uniqueness.
The later v2 path extends this same writer; it does not install a second mapping
path, defer maintenance until reuse exposure or rely on repeating migration.

## Advertisement, custody and proof

New enrollment uses one version-2 public schema. Keep the endpoint, request
64-KiB cap, original ten-minute deadline, twenty pending requests, frame/handshake
limits and five distinct authenticated attempts. The JSON object has exactly:

| Field | Value |
| --- | --- |
| `version` | Integer `2`. |
| `selector`, `name` | Existing canonical selector and bounded display label. |
| `access_spki`, `portable_spki`, `recipient_spki` | Existing canonical key encodings and key separation. |
| `portable_identity` | `null` for fresh identity, or the exact object below. |

The reused-identity object has exactly `owner_id`, `agent_id`, `issuer_sha256`,
and `certificate_der` (canonical standard base64, decoded size ≤4,096 bytes).
It does not supply a trust root. The server selects the independently registered
root by fingerprint and owner ID. Reject duplicate, unknown, differently cased,
wrong-type, malformed, trailing or noncanonical fields. The portable leaf's
public key must equal `portable_spki`; identity UUIDs must be distinct from each
other and the target server/enrollment IDs under existing profile rules.

Anonymous registration performs bounded decoding only and retains these as
unverified claims. It cannot assert that possession has been proved. Before
approval, a bounded worker verifies the exact portable profile, current validity,
registered active issuer and proposed key. Approval retains issuer revision and
all exact request bindings; secret input is still protected and approval remains
immutable. The list/approval summary distinguishes a claimed portable identity
and certificate validation from the still-pending fresh possession proof.

The raw advertisement digest already enters the Noise prologue and both signing
proofs. Consequently it binds all version-2 identity fields. Keep those proof
algorithms/tags and their role separation; do not add an independent signature
scheme. A fresh `access` proof plus the original portable-key proof is mandatory.
The fresh mode still assigns a new agent only after owner approval.

New private provisional records use version 2 and retain a nullable source
identity containing exact owner DER, portable DER, owner/agent IDs and fingerprint.
Its bound is 24 KiB: two ≤4-KiB certificates, their base64 representation, existing
three bounded keys and metadata. Public advertisement stays under its existing
64-KiB cap. Prepared/enrolled wrapper bounds derive from the chosen private-record
version plus the existing maximum result representation. Version-1 private records
retain their original 8-KiB bound and exact advertisement reconstruction for old
receipt validation. No PSK is persisted.

Extract one strict bounded stored-record decoder from existing prepared loading.
Keep purpose-specific validation above it:

- `LoadPortableIdentity` accepts only the final `enrolled/` domain, validates native
  ownership/canonical complete state, owner/portable profiles at current time and
  portable private-key correspondence. It does not require A online, accept a
  prepared-only identity or substitute A's access key. Source bytes are unchanged.
- Existing receipt loading validates current target-server/access authority and
  authenticates the current receipt peer. Stored portable evidence is historical:
  validate its full profile/chain at issuance, rather than impose its later
  expiration as an unrelated receipt deadline. This is not a new-enrollment time
  exemption or ordinary TLS exception. A not-yet-valid source leaf is rejected.
- Ordinary access loading belongs to the forthcoming milestone 3 LLD, a future
  consumer rather than a governing input to this enrollment specification.
  Parsing one stored bundle must not equate these purposes.

`NewProvisionalFromIdentity` reuses only the validated portable key/certificate.
The target access and recipient keys remain independently generated. The existing
one enrollment owner spans source read, cleanup, save, handoff and promotion.
`identity=...` cannot combine with `resume=...`; a missing/invalid source fails
before registration or cleanup. Normal fresh enrollment omits `identity`.

## Prepared result and single activation

Preserve the existing nine-field prepared-result layout and exact receipt bytes.
For reuse, the fixed owner/agent IDs identify the selected portable issuer and
agent, not B's local owner issuer. Field 2 is that registered owner certificate;
field 3 is the original portable certificate unchanged. B supplies its own server
authority/leaf, fresh B access leaf, recipient and recovery public metadata as
before. The owner ID is never evidence that B holds the portable issuer key.

Before prepare, verify the approved issuer revision and current portable validity.
Before ready, Go compares the returned owner/agent IDs, portable leaf and issuer
DER byte-for-byte with the selected local source in addition to existing result
validation. B access must bind the original agent, B server, fresh enrollment and
fresh access key. Same-server source selection is rejected with repair guidance.

At activation, in one existing `BEGIN IMMEDIATE` transaction:

1. Recheck exact approval, original request deadline, ready proof, proposed keys,
   current accounts and issuer fingerprint/owner/active/revision.
2. Verify the portable profile at current time. Fresh mode requires the current
   local issuer and newly issued portable leaf; reuse requires the exact approved
   registered issuer and original leaf. Do not compare a reused issuer with B's
   unrelated local owner credential.
3. Insert the unique agent/enrollment binding and the existing enrollment/result
   row together. An existing binding rejects even if its enrollment is denied or
   inactive. Same committed selector/enrollment/result replay may only read its
   unchanged result; it does not create another binding.
4. Consume approval at the existing live commit decision and commit. A concurrent
   issuer removal that commits first defeats activation; activation committed
   first remains a valid B enrollment. No event-loop disk/crypto or registry lock
   spans database work. SQLite's existing fail-fast writer exclusion suffices.

Ready still follows complete private flush. The existing server-authenticated
receipt uses only B's registered access key and exporter/fresh nonce; no PSK,
portable credential or source-server assertion can retrieve another enrollment.
B denial/inactivity still prevents receipt retrieval. Changing B grants later
must not change the exact historical prepared result. Resume/promotion, failed
fsync, torn no-replace publication and original-expiry behavior remain intact.

There is no published v1/v2 enrollment compatibility service. At final cutover,
all newly generated registrations use v2 and the public registration parser accepts
v2 only; old clients get a format error without an identity reset. Keep only the
v1 saved-state/result decoder needed to recover prior committed enrollment.
The earlier implementation slices below exercise the new codecs and activation
through real private peers while public registration stays on its existing safe
fresh path. The final slice switches the product path atomically.

## Complete acceptance

| ID | Required evidence and failure direction |
| --- | --- |
| P01 | Real A/B Python servers and Go client preserve portable key/cert/agent ID; B gets distinct access/enrollment/recipient state. A can be offline. |
| P02 | Same owner UUID with distinct CA keys succeeds only after exact B registration of A issuer. Unknown, inactive, mismatched or expired issuer/leaf fails. |
| P03 | Valid certificate without original private key, changed source ID/key/cert/hash, wrong PSK, replayed or changed transcript never reaches ready/activation. |
| P04 | Window, pending capacity, original deadlines, five authenticated attempts and duplicate-owner accounting remain unchanged for fresh and reused identity. |
| P05 | Existing active/denied/inactive B binding rejects new reuse; include real fresh-v1 enrollments committed on both intermediate `2.10.1` and `2.10.2` prefixes before later reuse. A denial alone grants no B authority and clears no B denial. Source access leaf never authenticates B. |
| P06 | Trust removal/re-add and approval/ready/commit races cannot revive an approval bound to an older issuer revision. Rollback preserves binding/result consistency. |
| P07 | Crash/lost successful or lost final reply, historical prepared expiry, portable expiry after commit, restart and promotion failure recover exactly one B result using B access. |
| P08 | POSIX/Darwin/Windows custody, source file replacement, private-public field leaks, failed save and old committed v1 receipt fixtures retain existing safety. |
| P09 | One-way upgrade preserves real old prepared/result/denial data, detects duplicate/malformed bindings, and fails interrupted migration atomically. From the first upgraded prefix, every new fresh-v1 activation and exact replay maintains one matching binding/result atomically; failed insertion rolls back both. |
| P10 | Installed fresh and reused journeys execute in existing native CI; old-wire rejection is actionable and does not delete valid saved identities. |

Broad malformed-input matrices belong in unit/source-peer tests. Use one compact
installed two-server journey plus recovery on the existing terminal driver; do
not clone the entire adversarial matrix at installation level. No live accounts.

## Implementation-ready cut proposed for review

Counts are handwritten additions + deletions, including code motion, helpers,
fixtures and review-correction headroom. Docs/news/lock data are separate. These
are bottom-up engineering estimates from the named existing files, not measured
future changes or a guarantee. Each upper bound includes its local negative and
integration work and fits the existing 1,000-line hard limit. No later dynamic
split is assumed to make an overlarge row fit.

| ID | Independently consumed result and ownership | Production | Tests/fixtures | CI | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| `2.10.1` | Real OS-local issuer controls, atomic schema/backfill and same-transaction binding maintenance in the existing fresh-v1 activation writer. Extend authority/control/admin/activation; P02/P06/P09 plus post-upgrade activation/replay/rollback. | 260–370 | 350–490 | 0–5 | 610–865 |
| `2.10.2` | Go native source loading and v2 private/public codecs consumed by an executable Python/Go peer. Extend existing decoders/publisher; public reuse remains unreachable. P03/P08 and old receipt decoding. | 210–300 | 280–390 | 0–5 | 490–695 |
| `2.10.3` | B prepare/proof/v2 activation extends the existing binding writer, with historical receipt validation and a real A/B/Go peer. P01/P03/P05/P06/P07 includes prior-prefix v1 active/denied/inactive states; public v2 remains closed. | 230–310 | 320–430 | 0–5 | 550–745 |
| `2.10.4` | Wire explicit source selection and v2-only public registration into real CLI pairing; installed A/B and fresh regression journeys plus migration/help/news closure. P04/P08/P10 and all parent P01–P10. | 150–240 | 350–460 | 0–10 | 500–710 |

Order is `2.10.1 -> 2.10.2 -> 2.10.3 -> 2.10.4`, then milestone 3.
Existing 2.1–2.9 delivery identities and their approvals are preserved. The new
parent `2.10` owns P01–P10; it is a proposed corrective scope, not a reset of
milestone 2's exhausted implementation allowance. Estimated total: 2,150–3,015
lines including handwritten CI. The existing native CI matrix and terminal,
authority, activation and peer fixtures are reused; 2.10.2 owns source/codec
harness additions, 2.10.3 owns two-server composition, and 2.10.4 adds only the
installed variants. No external dependency adoption or platform drop is needed.
The first-cut writer ownership reallocates 20–40 production lines from 2.10.3
to 2.10.1; 50–70 additional test lines cover intermediate activation integrity.
Aggregate production remains 850–1,220; tests are 1,300–1,770 and CI 0–25.
These are revised estimates, not measured future diffs. Core uniqueness tests run
where the first upgraded writer exists; later composition uses its existing P05
budget to test reuse of those real intermediate states.
