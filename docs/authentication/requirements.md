# Authentication and mutual enrollment requirements

Stage: requirements baseline, 2026-10-20. This document specifies the intended
behavior and acceptance conditions for the first authentication delivery. It
does not select a complete architecture, wire protocol, implementation plan or
production dependency. An initial requirements stamp attests this level only.

The owner approved this sequence: requirements and stamp; high-level design and
stamp; implementation plan with slices and stamp. Actual DBB must review the
exact final bytes of each new or modified document before it is stamped. A later
content change requires DBB re-review of the affected change before restamping.
Nothing here authorizes product implementation or release.
(D68 and the owner's subsequent staged-delivery and DBB-before-stamp directions.)

## Authority and reading scope

The requirements below are the current baseline. D references identify original
owner decisions through the [provenance index](evidence.md), including later
supersessions. The mixed historical interview and earlier synthesis are source
evidence, not additional documents whose every statement is normative. Later
direct owner decisions take precedence over superseded proposals.
In particular, D78 supersedes D60's earlier acceptance of proxy-readable native
traffic; the current requirement is end-to-end native confidentiality.

The [earlier synthesis](design.md) and [candidate security contracts](security-contracts.md)
are inputs to high-level design. Their library choices, wire formats, storage
engines, certificate profiles, browser-session mechanisms and crypto envelopes
are candidates unless explicitly retained as requirements here. Prior DBB
reports assess those candidates, not this new requirements baseline. The
requirements-level acceptance scenarios below are obligations for later
validation, not claims that tests or implementation already exist.

## Scope and trust

**R01 — single trusted owner and lightweight setup.** One operator owns the
installation and supplies upstream passwords. Deliver Arbiter-managed identity
(AMI) first, using standard X.509 identity mechanisms and established
cryptographic protocols. Initial setup requires only Arbiter tooling and embedded
libraries, without a central external identity service, separate identity app or
separately operated client daemon. Hermes and OpenClaw can use Arbiter tooling;
native reuse of their identities is future work. (D2, D10–D11, D18–D22, D33, D41–D42)

**R02 — trusted server, limited agent custody.** The operator/server can read
upstream secrets and plaintext. Lightweight mode permits agents to read their
own client keys; agent-side compromise can therefore copy those keys and make
otherwise permitted requests. Client key files carry `NEVER SHARE THIS` guidance,
which is not a security boundary. Owner/recovery secrets must remain unavailable
to agents. Once a supported secure custody mode exists, doctor warns about
lightweight custody and describes the alternative. Transient helpers share the
parent's identity and grants; independent helper revocation/narrowing is deferred.
(D23–D32, D73)

**R03 — scope boundaries.** First delivery includes CLI administration, enrollment
after setup, account grants, key lifecycle/recovery, artifact protection and client
processing, application backup/restore/undo and planned migration. Future browser
administration must fit these trust requirements and show pending enrollments
with approve/reject actions, but browser UI delivery is not part of the initial
CLI implementation. Multi-owner hosting, protection from an untrusted operator,
confidential execution, external identity-provider implementations, short pairing
codes, independently revocable installations under one identity, narrower helper
permissions, sandbox/broker integration, passkey recovery, remote backup export
and clustering/redundancy remain out of scope. No speculative provider framework
is required; future providers must preserve the required ownership, recovery,
portability and transport independence. (D1, D4, D20, D27–D32, D59, D73, D95)

Acceptance: a private installation can complete initial and later client setup
without an external identity account or public infrastructure. The operator is
not promised protection from themselves or prompt-injection-proof client custody.
Helpers use existing grants; no unapproved helper/provider/UI features are needed
to accept the initial CLI delivery.

## Identity and owner authority

**R04 — durable identities and separate authority.** Keep owner, server, agent,
enrollment, key versions and account grants distinct. Names, URLs, fingerprints
and certificate serials are not durable identity. Register owner public trust
before accepting an ownership assertion; a name or requester-provided root is
not proof. Fresh proof of the relevant private key is required. AMI permits the
owner-backed server to certify owner-approved client identities without a manual
CSR handoff. A client may generate provisional keys but may not create its own
owner-approved durable identity. (D3, D10, D13, D17, D23–D26, D33–D34, D103)

**R05 — portable identity and per-server access.** Preserve the owner-certified
portable identity direction whose role separation DBB accepted: ownership proof
can identify an agent during enrollment elsewhere, but cannot itself grant service
access, automatically enroll it or replace an existing server-local access key.
Each server enrollment uses a distinct client-generated access key. Trusting an
owner or recognizing an agent never creates account grants. Another server's
credential must not authenticate or repair this enrollment. (D7–D9, D24, D72, D103)

**R06 — owner-authorized recovery.** The owner alone can authorize identity/key
recovery. Verified replacement preserves durable identity, existing enrollments
and grants without another per-server operator approval, while preserving all
suspension/revocation state. Owner authorization itself supplies that approval;
portable identity possession alone does not. Simple local repair uses the
approved pairing flow for the existing enrollment. Server administration does
not confer authority over another owner's identity. Recovery after server-key
compromise must not rely only on that compromised key. The protected recovery
material can authorize live credential recovery as well as decrypt backups; its
labeling and documentation must state both powers and theft consequences. Keep
one operator-managed recovery file; exact internal key roles and formats are
high-level-design work. (D23–D25, D44, D64, D96, D101, D103–D104; post-review owner acceptance)

Acceptance: a copied display name, unregistered owner root or credential for
another server cannot gain access or replace a key. Owner-approved recovery
preserves current grants and denials without a second approval ceremony. A
stolen portable identity alone cannot authorize replacement or clear a denial.

## Enrollment and completion

**R07 — owner-initiated mutual trust.** The owner starts enrollment at the client,
which generates provisional keys and a copyable secret. The operator opens a
bounded server enrollment window and approves the exact pending request through
trusted local/SSH administration, supplying the client secret and selecting
account grants. Normally closed enrollment explains how to request opening,
without a remote unauthenticated open action. Unverified host/name labels remain
untrusted display data. The client receives the result directly; no owner return
bundle or second fingerprint-copy step is required. (D5–D9, D12–D16, D33–D38)

**R08 — secure bounded pairing.** The client secret is 32 random bytes, displayed
as 43 unpadded Base64url characters, with public request selector/version framing
separate. Do not send the secret as a password over the unknown connection.
Use an established high-entropy secret-authenticated exchange to bind roles,
approved request, proposed keys and durable server trust, with mutual confirmation
and private-key possession proof before activation. Lifetime is ten minutes from
request creation. Cap each request at five distinct admitted exchanges; charge
only after proof of the pairing secret, deduplicate identical authenticated
replays, and limit unauthenticated resources separately. Five failed/abandoned
admitted exchanges require fresh approval. The configurable uncommitted-request
default is twenty, including approved/in-progress requests, with immediate excess
rejection and no unbounded queue. This does not cap enrolled clients or promise
fairness under connection spam. Reconnection or reopening the CLI resets none
of these bounds; approval does not require a persistent operator connection.
(D69–D71 as revised by D77; approved post-review attempt-accounting correction)

**R09 — durable, single completion.** Client trust/keys must survive before
server commitment becomes effective. Commit identity, credential, grants and
completion state coherently; a retry or lost reply must not create a second
identity, grant or activation. Recover committed results after restart through
authenticated, idempotent reconciliation. Incomplete requests and provisional
resources must expire/clean up without leaking unbounded junk. Secret loss may
invalidate an uncommitted request, not undo a committed enrollment. Concurrent
approval, expiry, denial or key changes must be resolved before commitment.
Pairing secrets stay out of logs, backups, arguments, environment and shell history.
(D36, D38, D69–D71, D76–D77; reviewed security constraints)

Acceptance: normal enrollment needs one client-to-operator secret handoff and
one exact owner approval, without a second manual trust transfer. Wrong-secret
or anonymous traffic cannot spend that request's five attempts. Replays,
parallel requests, deadline boundaries, abandoned requests, crash and lost final
reply preserve one bounded authorization result and clean incomplete state.

## Transport and administration

**R10 — independent native trust and confidentiality.** Native operations,
discovery and artifact access authenticate the enrolled client and remembered
server and encrypt directly between client and Arbiter. A forwarding proxy must
not gain native plaintext, keys or artifact capabilities. The durable server
identity survives host, URL, outer HTTPS certificate and proxy changes. Verify
the expected server before sending credentials/capabilities; identity/channel
failure must not fall back to ordinary unauthenticated HTTP or proxy identity
headers. Proxy denial of service and traffic metadata are outside this guarantee.
Private setup requires no public DNS, Internet exposure, Certbot, CA installation
or public certificate. (D8, D39–D40, D47–D48, D55, D78–D80)

**R11 — administration is separate authority.** Initial administration uses an
authenticated local OS boundary or verified SSH, not merely a loopback address.
Future private browser access uses an operator-authorized loopback UI, with
verified loopback-bound SSH forwarding for remote use. HTTP is permitted only
on those private loopback legs. Require explicit bounded browser-session
authorization, Host/Origin/CSRF defenses and isolation from other local services
and persistent browser state; neither source address, port nor SSH alone grants
admin authority. Public browser administration uses browser HTTPS trust, separate
from native identity. Record TCP passthrough with HTTPS at Arbiter as DBB's
recommended public recipe; Apache termination is a trusted alternative, not
mandatory dual support. Browser trust must not impose public infrastructure on
private native setup. (D4, D39, D81–D82; DBB browser-boundary findings)

**R12 — bounded work and truthful diagnostics.** Bound sockets, incomplete
handshakes, cryptographic work, buffering, control traffic, temporary storage and
worker admission, with backpressure and ownership retained until actual cleanup.
Preserve existing connection budgets and download idle-progress semantics;
control pings are not artifact progress. Select exact transport limits in design;
U53's proposed 64-connection default is not approved. Diagnostics distinguish
unverified server identity from verified client rejection and identify specific
causes only when authenticated evidence supports them. Never repair by clearing
denials, guessing the cause, exposing keys or replaying uncertain operations.
(D76, D97–D98; reviewed resource and diagnostic constraints)

Acceptance: proxy substitution, changed outer certificate and wrong durable
server are distinguishable. Proxy changes preserve valid native trust without
exposing native secrets. An unrelated local service or old browser state cannot
obtain admin authority. Overload, disconnect and cancellation release bounded
resources only after their real owners finish; failures provide honest guidance.

## Account access and revocation

**R13 — grants precede existing policies.** Only minimal health and bounded
enrollment bootstrap are anonymous. Ordinary discovery/catalog/schema and direct
lookup require authentication and hide ungranted account names, descriptions,
settings and guidance. Verify the client and its configured plugin/account
grant, then use that account's existing configured policy and upstream flow.
Do not add a second client policy, operation allowlist or policy intersection.
The owner can change grants after enrollment. Grants follow configured account
names, including deliberately repointed accounts; use a new name for a separate
boundary. Password renewal needs no regranting. (D7, D9, D54–D55, D57–D59, D102)

**R14 — live denial and no uncertain replay.** Removal/revocation rejects new
access for the affected account grant or enrollment and terminates the affected
client requests/download delivery across
connections. Synchronize work admission with denial so a race cannot start new
authorized work after revocation. Already delivered data and committed upstream
effects cannot be recalled. Continuing blocking work retains its slot until it
actually exits. Rotation, reconnect, restore repair and migration do not replay
uncertain mutations or consuming artifact GETs. (D56, D93–D98)

Acceptance: direct invocation cannot bypass hidden discovery or account grants.
Policy behavior remains the current account-policy flow. Grant changes affect
existing connections and prevent further delivery, while already completed mail
effects are reported accurately rather than promised undone.

## Key lifecycle and continuity

**R15 — on-contact rotation and fixed grace.** Default key age for due rotation
is thirty days. Prepare rotation on authenticated client connection, not through
a required background service. Preparation starts one configurable completion
grace, default twenty-four hours. Only client verification, durable save and
new-key possession confirmation activate the replacement. Until grace ends,
missing confirmation alone does not prohibit current-key ordinary use. After
grace, prohibit old-key ordinary operations but permit only completion of that
same pending rotation. Retries, withheld candidates and continued old-key use
cannot extend the grace. These intervals are not certificate lifetimes.
(D43–D46, D74–D75)

**R16 — restricted completion and inactivity.** Preserve unattended completion
when required keys/state survive, even after old/candidate certificate expiry,
without weakening ordinary credential validation. Neither receipt cleanup nor
certificate expiry may impose an earlier mandatory re-pairing deadline. The
default inactivity cutoff is 180 days since authenticated client contact, not
arbitrary TCP traffic; cutoff disables ordinary access and unattended rotation
until owner-approved pairing for the existing enrollment. Incomplete restricted
exchanges must not keep moving either deadline. Denial/owner repair supersedes
stale rotation or completion work. (D44, D74–D75; DBB restricted-completion finding)

**R17 — bounded rollback assistance and server replacement.** Retain current plus
one immediate predecessor access credential per server, separately from a pending
candidate. Try current first; allow one authentication-only predecessor attempt
after unambiguous credential rejection by the same verified server. Do not use
it after an unverified peer, explicit denial/revocation, timeout or uncertain
operation result. It cannot override validity, inactivity, grace or server
revocation. Replace predecessor on completed rotation; discard on expiry,
superseding owner recovery or unenrollment. Missing/older history may require
owner-approved repair. Support operator-triggered planned server identity-key
replacement with verified automatic client adoption; no periodic server-root
schedule or requirement to replace it merely for migration/HTTPS renewal.
A compromised server key alone cannot authorize legitimate recovery.
(D96–D98, D104)

Acceptance: loss of rotation confirmation neither activates an unsaved key nor
resets grace. At grace expiry only the exact pending completion is reachable;
at inactivity cutoff owner repair is required. Revoked credentials cannot renew
themselves. Stale restore/undo can trigger bounded fallback or explicit owner
repair without changing enduring identities, inventing a diagnosis or replaying
upstream work.

## Artifacts and client processing

**R18 — owner-bound artifacts and encryption.** Metadata, HEAD and GET require
the creating authenticated client identity and nonce; sharing an upstream account
or copying a URL grants no access. Helpers sharing that identity remain usable.
HEAD is non-consuming; GET is one-time and a failed stream does not reopen its
nonce. Preserve expiry. Encrypt retained server copies while allowing Arbiter to
decrypt/inspect them and later rescan upstream content within retention. Encrypt
downloads/saved encrypted copies for the authorized client. Encryption alone
does not prove origin: bind artifact, creator, recipient and expected content to
the authenticated server context. No prompt-injection detection feature or
long-term encrypted-archive recovery is added. (D48–D51; DBB artifact-origin finding)

**R19 — preserve four client choices.** Support immediate authenticated plaintext
stdin streaming, verified encrypted temporary download followed by plaintext
stdin, explicit plaintext temporary pathname for path-only tools, and deliberate
persistent plaintext export named `artifact save-decrypted`. No universal
processing default or compatibility alias is selected. Immediate streaming can
deliver a valid but incomplete prefix and irreversible tool effects; report final
failure honestly. Verified modes complete origin, content and finalization checks
before tool startup or final export, with stable verified bytes through processing.
Only ciphertext is staged in encrypted-temp mode. Plaintext-temp needs an explicit
flag and cleanup. Discourage routine agent use of persistent plaintext export;
cleanup is not crash-proof secure deletion or protection from the same OS user's
full access. Client docs and operation help explain storage, startup delay,
partial delivery and plaintext-file tradeoffs. (D52–D53, D83–D85; DBB stable-byte clarification)

**R20 — limits and legacy upgrade.** Preserve 50 MiB plaintext per artifact,
2,000 stored artifacts, 1 GiB physical artifact storage, ten-minute idle and
twenty-four-hour maximum retention. Count encryption/metadata overhead against
physical storage without reducing the plaintext cap. During the authentication
upgrade discard only server-managed legacy ownerless artifacts and invalidate
their links. Do not guess ownership, retain nonce-only access, touch upstream
mail/unrelated state/client files or recreate downloads by replaying work.
(D52–D53, D66, D83–D85; existing approved resource limits)

Acceptance: another enrolled identity cannot use a stolen artifact URL/nonce.
HEAD does not consume; one GET does, including failure. Validly encrypted but
substituted content fails origin verification. Verified modes start no tool on
failed preverification; streaming keeps its explicitly partial contract. Storage
and cancellation remain bounded, with legacy cleanup confined to the stated scope.

## Backup, restore, undo and migration

**R21 — useful ongoing encrypted backup.** Arrange backup from startup through an
external operator job invoking trusted local/SSH export. The job owns cadence,
off-host durable storage, retention and failure reporting; Arbiter owns a complete
consistent encrypted application bundle to its configured public recipient.
No internal scheduler, Dropbox integration, synchronous backup of every mutation,
first-enrollment backup gate or ordinary-agent export grant is required.
Remote API export is deferred. Keep private recovery material outside server,
agents and synced bundle storage, with trusted public origin-verification data
in the same protected operator file. Loss of all recovery copies makes bundles
unrecoverable. Authenticating a bundle must not depend on a key nominated only
by that incoming bundle. (D61–D64, D86–D88, D101; reviewed recovery constraints)

**R22 — backup contents and exclusions.** Include durable server/issuer identity,
server-held keys, enrolled public credentials and accepted-key state, grants,
policies, account configuration and actual configured upstream secrets, including
configured file-backed sources. Exclude client private keys, arbitrary environment
dumps, host images, OS/Reploy/Apache/DNS setup, upstream mail, client files, active
sessions and uncommitted pairing secrets. Export one coherent saved state and
publish only a complete validated bundle; failure preserves the last good export.
(D61, D67, D87, D101)

**R23 — offline full restore.** Restore must work when Arbiter will not start.
Validate privately with the target stopped and restore one coherent saved
application configuration/secret/authority state, without silently merging target
settings. Different-host settings use ordinary edits before startup, not a
restore-specific wizard. Warn of snapshot date and excluded later changes;
stale restore may reinstate old grants/revocations and deadlines. Do not claim
freshness or reset timers to extend authority. No mandatory permissions review,
paused-access mode or extra activation approval is imposed. Without usable
backup, initialize a new server identity and explicitly pair/grant existing
clients again; surviving durable client identities need not be recreated.
(D61, D65, D89, D96)

**R24 — restore undo after use.** Keep the latest coherent pre-restore state,
without automatic expiry, until the next successful restore or explicit discard.
A failed restore preserves the existing undo point. Allow offline undo of the
particular restore even after startup/use, with very prominent warnings about
lost later local configuration, enrollment, key and authorization changes.
Do not add a hard cutoff, mandatory acceptance step, silent merge or redo history.
Undo cannot reverse upstream effects; affected clients may need owner-approved
repair of their existing enrollment, retaining restored grants and denials.
(D90–D91, D96, D99–D100)

**R25 — cooperative migration with a maintenance window.** Preserve identity,
enrollments, grants and secrets through a final coherent bundle and one active
destination. Document stopping/disabling ordinary source service before final
capture, restoring with the destination stopped and keeping the source disabled
after cutover. Assume cooperative servers and rare migration; the procedure is
the accepted control, not automated prevention of duplicate writable copies.
No client authority-generation counter, signed takeover, persistent retirement,
cluster synchronization or zero-downtime guarantee is required. Preserve the
approved authenticated old-URL handoff direction without treating it as an
automatic takeover protocol: any source handoff must be non-mutating, work
despite later destination key rotation, and require the same durable destination
identity before credentials/nonces or URL adoption. Do not replay uncertain
operations. Post-use fallback can be stale; local undo is not automatic old-host
restart. Older restore/undo remains permitted with its warnings. (D92–D100;
owner's post-review procedural-migration disposition)

Acceptance: backup after enrollment captures those enrollments without changing
them. Tampered/untrusted or incomplete bundles cannot activate mixed state.
Valid older bundles remain intentionally restorable with warnings. Failed restore
preserves undo; undo after use warns and restores coherent prior state. Migration
with already-paired clients preserves trust and requires no generation mechanism
or unapproved cluster service; clients never send secrets to an unverified
destination or repeat an uncertain consuming operation.

## Acceptance boundaries and next stages

A requirements review checks that these outcomes, constraints, defaults, risks
and acceptance conditions are coherent, traceable and sufficient to begin
high-level design. It must not obtain a clean verdict by deleting an unmet
requirement or by claiming an untested mechanism already works. Accepted risks
are the trusted operator, lightweight client-key exposure, partial streaming,
irreversible upstream effects, stale-backup rollback and procedural migration.

The former B1–B5 groups are high-level-design work areas, not proof that
requirements are incomplete or implementation is ready:

| Area | Decisions and evidence for the next stage |
| --- | --- |
| B1 — pairing | Exact authenticated exchange, transcript/key proofs, durable activation, receipt retrieval and bounded replay/crash/overload behavior. |
| B2 — identity/lifecycle | Certificate/issuer/identity/purpose profiles, owner recovery, key validity and transitions, predecessor durability and Python/Go verification. |
| B3 — transport/admin | Native encrypted proxy-independent transport, restricted completion, admission/timeout ownership, local control boundary, numeric bounds and future browser trust/compatibility. |
| B4 — artifacts | Encryption/envelope/backend, authenticated origin, key persistence, stable verified bytes, physical accounting, streaming and Python/Go/platform evidence. |
| B5 — recovery | Cross-file consistency, authenticated encrypted framing, offline restore/undo activation, local admin portability and documented migration/URL handoff. |

Carry the reviewed security invariants into those designs. Candidate Noise,
TLS-over-WebSocket, age, JWS, SQLite, issuer encodings, extra internal signing keys,
fresh browser-origin strategy and helper libraries are not selected by this
requirements stamp. Their feasibility and maintenance cost must be evaluated at
the relevant design stage. Retain explicit already-approved choices such as AMI,
X.509, long-secret pairing, per-server access keys and the numerical defaults above.
The following implementation plan must then define coherent independently
reviewable/testable slices and cross-area integration checks. Neither later
stage is produced or approved by stamping this requirements baseline.
