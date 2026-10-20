# Authentication and mutual enrollment

Reading status: this earlier requirements/design synthesis is retained as
non-normative input to high-level design. The [requirements baseline](requirements.md)
is the current requirements surface. The owner selected separate requirements,
high-level-design and sliced-plan review/stamp stages. B1–B5 below describe later
design/evidence work, not automatic blockers to the requirements-level stamp.
The historical candidate text and DBB assessment below remain preserved as context.

Status, 2026-10-20: interview questioning is closed. Actual DBB reviewed the
architecture repairs, **not a complete implementation-ready protocol or release
security approval**. No authentication implementation is authorized by this
record. Read the [second-review brief](reviews/repair-brief.md)
for the current review scope.

Post-review update, 2026-10-20: candidate repairs for findings 1–5 are specified
in the [security contracts](security-contracts.md).
The owner's operational disposition of finding 6 appears below. The
[first DBB report](reviews/dbb-initial.md) identifies the
earlier reviewed bytes; its frozen inputs remain preserved. The completed
[re-review](reviews/dbb-repairs.md) and targeted
[follow-up](reviews/dbb-followup.md) resolve findings 1–5
at design level; finding 6 is the accepted operational limitation. The follow-up
identifies the exact reviewed revision. Only this status text and the readiness
footer changed afterward; the reviewed snapshot remains preserved. No repair is
an implemented security fix.

Approved behavior is labeled **Agreed**. Mechanisms labeled **Candidate** remain
proposals. **B1–B5** are finite design blockers, not approved deferrals. The
[coverage and evidence index](evidence.md) maps all
104 recorded decisions; byte-preserved originals are optional historical evidence.

## Scope

**Agreed.** One trusted owner operates Arbiter and supplies upstream passwords.
Deliver Arbiter-managed identity (AMI) first, using X.509 credentials. Setup needs
only Arbiter tooling and embedded libraries; no external identity account,
separate identity application or independently operated client daemon is required.
Hermes and OpenClaw should be able to use that tooling; native external-identity
reuse is deferred. Identity must not depend on a central external authority.

The owner initiates enrollment from the client and explicitly approves it through
trusted server administration. Existing clients must recognize the same logical
server after host, URL, HTTPS-certificate or proxy changes. Private installations
must not require public DNS, Internet exposure, Certbot or a public certificate.

Account grants, client recovery, artifact encryption, application backup/restore,
restore undo and planned migration remain in scope. Consolidation does not move
these approved requirements into future work. Browser administration is a future
interface whose trust boundary must fit this architecture. It must show pending
enrollments and let the owner approve or reject them; this does not move UI
delivery into the initial CLI scope. Initial administration uses local CLI or
SSH. Delivery slices and final command/configuration spellings
are not selected here. (D2–D9, D11, D14–D22, D33–D42, D59, D61–D68, D79–D82)

**Already deferred:** multi-owner hosting and protection from an untrusted
operator; confidential execution; external identity-provider implementations;
short typable pairing codes; independently revocable installations under one
agent identity; narrower helper permissions; sandbox/broker integration; passkey
recovery; remote backup-export API; and clusters or multi-endpoint redundancy.
Future providers must retain owner-authorized recovery, portability and transport
independence. No speculative provider framework is required now.

## Actors, trust and credentials

| Actor or boundary | Authority and limitations |
| --- | --- |
| Owner/operator | Approves identities, enrollment, account grants and recovery through trusted administration; currently also supplies upstream secrets. |
| Arbiter server | Holds upstream credentials, enforces account access and existing plugin policies, and can inspect upstream plaintext. |
| Native client and helpers | Hold client credentials; helpers share the parent's identity and permissions and cannot be revoked independently. Agents cannot create durable identities without owner approval. |
| Forwarding proxy | Must not gain native API plaintext or credentials. It can observe routing/timing and deny service. Browser TLS termination has a different trust boundary below. |
| External backup job | May request encrypted export through local/SSH administration and store ciphertext; receives no private recovery key or ordinary-agent export grant. |

**Agreed known risk.** Lightweight mode permits the agent to read and copy its
client private keys. Put `NEVER SHARE THIS` at the top of the key file; that is
guidance, not protection from prompt injection or impersonation. Do not expose
owner/recovery keys to agents. Once a supported secure mode exists, doctor should
warn about lightweight custody and explain the alternative. The intended future
mitigation is a sandboxed agent calling a trusted broker that holds its key,
likely through Reploy controlled sessions. The broker must authenticate its
caller and cannot be an unrestricted signing/operation service on the host.
Neither that isolation nor a warning proves that an allowed request reflects
human intent. No secure broker mode is implemented. (D23–D32, D73)

Keep enduring identities, enrollment records, key versions and grants distinct.
A display name, URL, current key fingerprint or certificate serial is not the
enduring identity. The exact standard identity/issuer profile remains **B2**.

| Key role | Agreed authority or candidate separation |
| --- | --- |
| Owner issuer | Trusted AMI environment represents the owner and certifies durable agent identities; never give its private key to agents or another independent server for portable verification. |
| Server identity authority | Establishes the durable server trust remembered through pairing; independent of outer HTTPS certificates. |
| Portable agent identity | **Provisionally accepted, subject to DBB (D103):** separate owner-certified client-held identity key proves ownership during enrollment elsewhere. It grants no service access, automatic enrollment or existing-enrollment key replacement. |
| Per-server access | **Agreed:** client generates a distinct authentication key for each server enrollment; another server's credential cannot authenticate or replace it. |
| Artifact decryption | **Candidate:** separate client recipient and server-storage keys; authentication rotation must not discard decryption keys needed for active artifacts. |
| Backup recovery | **Agreed:** operator holds private decryption material outside server, agent and bundle-sync storage; server has its public recipient. The same protected recovery file also retains trusted public verification metadata. |
| Backup signing | **Candidate:** separate server-held signing key authenticates bundle origin; not the private operator decryption key. |

Owner public trust registration and fresh agent ownership proof must precede
accepting an existing identity. A familiar owner name or a root supplied by the
requester is insufficient. Verifying ownership never creates grants. Owner-only
identity recovery must preserve existing identities/grants without clearing
suspension or revocation. Current server-local credential repair still requires
its owner's explicit approval; portable identity proof alone cannot replace a
local access key. Exact cross-server continuity evidence remains **B2**.
(D17, D23–D25, D32–D34, D72–D73, D101, D103)

**Candidate recovery contract ([H1](security-contracts.md#h1--owner-recovery-and-credential-roles)).**
Register an independent owner recovery verifier through trusted administration;
the private signer stays in the existing external operator recovery file, never
on the server or agent. A fresh owner-signed statement binds one server,
enrollment, expected key version, challenge and exact replacement key. The
replacement key proves possession; one conditional transaction consumes the
event, invalidates old sessions/rotations and preserves grants and denials.
That signature is the owner's approval under D24, without a second approval or
new grant ceremony at each server. Portable identity alone has no replacement
authority. H1 specifies custody, replay and compromise-recovery boundaries;
the exact standard profile and interoperability evidence remain **B2**.
The candidate recovery file therefore carries live recovery authority as well
as backup decryption. Its labeling/setup documentation must disclose that theft
can authorize replacement keys at registered servers; keeping one file does not
reduce that authority or make it an ordinary agent credential.

## Enrollment and incomplete attempts

**Agreed journey.** Enrollment is available after setup as well as for the first
client. New enrollment requests are normally closed; the operator opens a bounded
window. A closed endpoint explains how to ask the operator to open it, without
providing an unauthenticated remote-open action. (D5–D6, D14–D16, D35–D38)

1. On the owner's instruction, the client starts enrollment for a URL, generates
   provisional keys and a public request selector, and displays one copyable
   pairing value. Client generation precedes owner-approved durable identity
   creation. Unverified names/host labels are display data, not proof.
2. The owner supplies that value through trusted local/SSH server administration,
   approves the exact pending request and selects account grants. Use protected
   input, not arguments, environment variables, shell history or logs.
3. An established authenticated exchange uses the secret to bind protocol roles,
   request/key context and durable server trust. The secret is not sent as a
   password over the unknown connection. Both peers confirm the exchange and
   prove possession of the relevant private keys before trust is activated.
4. The client durably retains provisional server trust and its keys before final
   commitment. The server atomically commits identity, credential, grants and
   recoverable completion state, then returns the result directly to the client.
5. Lost completion replies are recovered idempotently using the approved key and
   remembered server trust. Do not issue duplicate identities or add grants again.
   The owner does not copy a return bundle or perform a second fingerprint check.

| Agreed enrollment bound | Value and interpretation |
| --- | --- |
| Pairing secret | 32 random bytes; 43 unpadded Base64url characters, plus public selector/version framing. Short-code PAKE is deferred. |
| Lifetime | Ten minutes from request creation; reconnecting or reopening the admin CLI does not reset it. |
| Attempts | Five admitted failed/abandoned exchanges per request; exhaustion needs fresh approval. |
| Uncommitted requests | Operator-configurable default 20, including approved/in-progress requests; reject excess without a queue. This does not cap enrolled clients or guarantee fairness under spam. |

**Candidate mechanism.** Use exactly
`Noise_NNpsk0_25519_ChaChaPoly_SHA256`, client initiator/server responder, with an
explicit decoded 32-byte PSK and no downgrade. Bind request context in the
prologue, carry credential material after the handshake and require explicit
confirmation. Enforce 65,535-byte wire and 65,519-byte transport-plaintext limits
outside the Python wrapper. Serialize each direction; cache only the bounded
result of an accepted retransmission, not reusable mutable handshake state.

**Candidate state ownership.** The server alone mutates a private transactional
SQLite authorization store. Pending state progresses through pending, approved,
exchanging, confirmed and committed, or terminates as expired/rejected/exhausted.
Reserve bounded crypto resources before starting crypto, but charge one of the
five semantic attempts only after a PSK-authenticated first flight. Deduplicate
an identical authenticated replay as the same exchange; unauthenticated junk
cannot consume a specific request's attempts. The approved accounting adjustment
and its candidate replay/admission contract are specified in
[H4](security-contracts.md#h4--pairing-resource-admission-and-semantic-attempts).
Conditionally recheck approval, expiry, account validity and key binding at commit. Pre-signing cannot authorize
a credential whose commit fails. Revocation takes precedence over receipt retrieval.
PSKs remain only in bounded live pairing contexts, excluded from logs/backups;
restart invalidates uncommitted contexts whose secrets are lost. Committed
results survive restart. Client and server clean abandoned provisional resources.

A private Unix-domain control socket is a candidate local admin transport;
Windows needs an equivalent authenticated local boundary. Loopback TCP alone is
not admin authentication. Complete wire/proof/retrieval and storage profiles are
**B1/B2/B5**. (D36, D69–D71 as revised by D77, D76)

## Native transport and browser administration

**Agreed.** Authenticate and encrypt native client–Arbiter traffic independently
of the forwarding proxy and outer HTTPS certificate. Native operations, discovery
and artifact paths all use the same authentication boundary. No ordinary API
fallback after an identity or tunnel failure. (D40, D47–D48, D55, D78–D80)

**Candidate topology:**

```text
Native CLI -- WSS carrier, optionally through proxy -- Arbiter listener
             [ HTTP/1.1 inside mutually authenticated TLS 1.3 ]
                         terminates only at Arbiter
                                      |
                       shared authorization/application/store

Browser ---- ordinary HTTPS / trusted TLS terminator ---- admin surface
Private browser ---- loopback HTTP + verified SSH tunnel ---- loopback UI
```

The candidate outer carrier sends only routing/upgrade data, not client
certificates, account names, artifact nonces or admin cookies. It has no cookie
jar or inherited authorization headers. To preserve the approved self-signed
setup and proxy/certificate independence, outer chain/hostname validation is not
the native identity gate in this candidate. Any such exception belongs only to
a dedicated carrier that returns a verified inner stream, never a general HTTP
client. Inner server validation remains mandatory. Do not broaden it into
scheme fallback, an expired inner credential exception or trusting arbitrary
roots. Strict framing/admission still applies to a malicious outer peer.

The ordinary inner verifier checks chain, identity, purpose and live accepted enrollment
state. Start without TLS early data or session resumption. Propagate immutable
verified caller context into the existing ASGI application; do not trust proxy
identity headers. Reuse shared admission, artifact storage and blocking workers.
Account-access checks run on every dispatch, not just at handshake. Route all
client download/tool/export HTTP construction through this transport as well.

Bound sockets, incomplete handshakes, crypto work, buffering and control traffic;
apply backpressure and preserve slot ownership until real cleanup. Eight plugin
workers alone do not bound tunnels. Keep the existing separate connection budget
across outer connect/upgrade/inner handshake, and download idle-progress rules;
a WebSocket ping is not artifact progress. **64 open native connections is an
unanswered proposal (U53), not an approved default or a review prerequisite.**
The adapter, its bounds and expiry handling remain **B3**.

**Agreed browser boundary.** Future browser administration uses ordinary browser
trust, not native pairing keys. Private access is an operator-authorized
loopback UI, remotely reached through verified, loopback-bound SSH forwarding;
local HTTP is allowed only on those loopback legs. The session still needs
operator authorization, Host/Origin/CSRF defenses, bounded lifetime and isolation
from other localhost services. Cookies are not port-isolated; no local-source-address
admin exemption is implied. **Candidate session scheme:** trusted CLI first
reserves the listener/forward and issues a single-use fragment bootstrap for a
fresh unpredictable `.localhost` origin, isolating persistent state from old
local services. The browser retains its bounded
bearer and CSRF token only in origin-scoped memory, with strict Host/Origin and
request checks. The complete candidate contract and private SSH boundary are
in [H5](security-contracts.md#h5--private-browser-administration).
This does not add browser delivery to initial CLI scope. (D39, D81)

For public administration, DBB recommends TCP passthrough with HTTPS at Arbiter
as the primary deployment recipe. Apache HTTPS termination is an explicitly
trusted alternative; supporting both is not an initial delivery requirement. A terminator
serving the UI is trusted with admin plaintext, sessions and delivered code;
backend HTTPS does not remove that trust. Arbiter cannot reliably forbid or
detect every upstream TLS topology. Certbot is optional; native trust must not
follow certificate renewal. Browser login/UI delivery and dual-topology support
are not silently added to implementation scope. The recommendation is recorded;
the private session contract and deployment/transport evidence still need review
and validation under **B3**. (D82)

## Account grants and revocation

**Agreed.** Only minimal health and bounded pairing bootstrap are anonymous.
General catalogs/schemas require authentication. Discovery and direct lookup must
hide ungranted account names, descriptions, settings and guidance consistently.

For an operation: verify the client and right to the configured plugin/account,
then preserve the existing flow: resolve that account, load its configured
`policy`, enforce it, and perform the upstream operation. No extra client policy,
operation allowlist, policy intersection or client-selected policy is introduced.
The owner can add/remove account grants after enrollment. Grants follow the
configured plugin/account name: repointing `imap/personal` carries its existing
grants; use a new name for a separate boundary. Password renewal needs no regrant.
(D7, D9, D54–D55, D57–D59, D102)

On revocation, terminate that client's active requests/downloads and reject new
ones. Track verified identity and work across all connections, not source IP.
Synchronize dispatch/registration with access changes. Already committed upstream
actions and delivered data cannot be recalled; continuing blocking work retains
its slot until it actually exits. Access-key rotation or transport recovery must
not replay uncertain operations or consuming GETs. (D56)

## Client key rotation, recovery and server replacement

**Agreed.** Each server enrollment has its own access key. The owner alone
approves lost-key/compromise recovery using pairing for the existing enrollment;
preserve identity and saved grants without clearing local denials. Copies of a
shared lightweight credential remain indistinguishable. (D23–D25, D43–D46, D72–D73)

| Client lifecycle event | Required behavior |
| --- | --- |
| Key age reaches the default 30-day threshold | Rotate on the next successfully authenticated connection, without fresh owner approval. This is not a certificate lifetime. |
| Server prepares due rotation | Persist one obligation and start a fixed configurable 24-hour grace immediately; withholding the new key cannot postpone it. |
| Candidate received | Client verifies and durably saves it, then confirms possession; only confirmation activates it. |
| Before grace expiry | Missing confirmation alone does not prohibit ordinary use of the currently accepted credential. |
| After grace expiry | Old-key ordinary operations fail; only authenticated completion of this same pending rotation remains. No countdown reset on retry/reconnect. |
| Late completion | May finish unattended before inactivity cutoff when required keys/state survive; receipt cleanup or certificate expiry must not silently impose an earlier re-pairing deadline. Ordinary access still requires valid credentials. |
| Default 180-day inactivity cutoff since authenticated contact | Disable access and unattended rotation; require owner-approved pairing for the existing enrollment. Arbitrary TCP traffic does not renew activity. |

**Candidate durability.** Conditional database transitions bind expected key and
state version to one pending rotation; no database transaction waits on the
client/network. Owner recovery or revocation wins over stale rotation completion.
After activation, new ordinary requests use the new key; admitted requests may
finish under existing budgets/access checks. Keep sufficient activation state
for lost-reply reconciliation. Helpers serialize shared local key updates.
**Candidate restricted completion ([H2](security-contracts.md#h2--restricted-late-rotation-channel)).**
Use a separate server-authenticated inner TLS control channel with old-key and
candidate-key possession proofs bound to that connection and exact pending
rotation. Its narrow verifier may relax only the old certificate's time check
for that transition, while enforcing all other profile, state, denial and
inactivity checks. It exposes no HTTP/ASGI caller or ordinary operation. Close
it after completion, then use new-key ordinary mTLS. Incomplete exchanges never
extend either deadline. H2 requires compatibility and negative-route tests;
those remain unverified **B2/B3** evidence, not a general expiry bypass. (D74–D75)

**Agreed rollback assistance.** Keep current access credential plus one immediate
completed predecessor per server, separately from one pending candidate. Current
first; after an unambiguous authentication rejection from the same verified
server, permit one authentication-only predecessor attempt. Not after an unverified
peer, explicit denial/revocation, network timeout or uncertain operation result.
Retain the actual accepted predecessor on completed rotation, and discard it on
expiry or superseding owner recovery or unenrollment. Do not downgrade preferred
local state or reset server deadlines. Older/missing history can still require owner-approved
repair; diagnostics must distinguish server verification from client rejection
and report only causes supported by verified evidence. (D96–D98)

**Agreed server-key policy.** Support operator-triggered planned identity-key
replacement with verified automatic client adoption. Keep scheduled periodic
replacement out of scope; migration or HTTPS renewal alone does not require it.
A compromised old key cannot independently authenticate legitimate recovery.
**Candidate:** standard X.509 transition certificates, staged server activation,
new-key possession and atomic client trust-record update before acknowledgement.
Retain bounded public transition evidence for supported offline clients; missed
multiple replacements and stale-root restore must be handled explicitly. No
server-root fallback follows from the access-key predecessor rule. Transition
history/retirement and independent owner recovery remain **B2**. (D104)

## Artifacts and client processing

**Agreed.** Metadata/HEAD/GET require the creating client's authenticated identity
and nonce; another client with the same upstream account or copied URL gains no
access. Helpers sharing the identity remain compatible. Preserve non-consuming
HEAD, one-time GET, expiry and no nonce reopening after a failed stream. Encrypt
retained server copies with server-controlled decryption, and downloads/saved
copies to the client. Keep server plaintext inspection and later rescanning
possible without extending retention or claiming a prompt-injection defense.
(D48–D51)

**Candidate:** age v1/X25519, separate storage/delivery encryption, authenticated
recipient registration and version binding. Keep sensitive metadata encrypted;
a minimal private index retains owner, nonce, consumption, deadlines and storage
accounting. Bind expected artifact identity, recipient version, length and hash
to the authenticated download context. Check decrypted metadata, length/digest
and cryptographic finalization; age decryption alone does not prove sender origin.
Authorization/ownership checks precede decryption and GET consumption. Hold keys
through active processing; neither auth rekey nor identity repair recovers a lost
artifact decryption key. No long-term encrypted-archive recovery feature is added.
The complete envelope, streaming backend and key persistence remain **B4**.

**Candidate origin contract ([H3](security-contracts.md#h3--artifact-origin-and-processing)).**
Obtain the expected descriptor from the verified inner channel and retain it in
protected client process state through verification/processing; never trust
origin metadata supplied only inside ciphertext. Verified modes compare the
complete plaintext length/hash and cryptographic finalization before tool
startup or final export. Transient encrypted staging retains the descriptor
and same open file; restart does not turn a leftover file into trusted input.
Immediate streaming retains its explicitly partial guarantee. No independent
encrypted-archive signature scheme is added to these live/transient modes.

| Client-selected processing path | Guarantee and tradeoff |
| --- | --- |
| Immediate plaintext stdin | Stream authenticated chunks without disk; later truncation can leave the tool with a valid but incomplete prefix and irreversible effects. Report final failure honestly. |
| Verified encrypted download, then stdin | Buffer only ciphertext, verify completion before tool startup, then decrypt into stdin. Requires temp space, delayed startup and potentially a second decryption pass. |
| Plaintext temporary pathname | Require an explicit flag for path-only tools; complete verification before starting the tool and clean up after processing. |
| Persistent plaintext export | Explicit `artifact save-decrypted`, replacing the proposed `save` name; discourage routine agent use. |

No processing default or compatibility alias is selected. Client documentation
and operation help must explain these tradeoffs. Private temp-file cleanup is
not crash-proof secure deletion or protection from other processes sharing the
OS user. Preserve existing limits:
50 MiB plaintext/artifact, 2,000 artifacts, 1 GiB physical artifact storage,
ten-minute idle and 24-hour maximum retention. Count ciphertext/metadata overhead
against physical storage without reducing the plaintext cap. Crypto/file work
must respect bounded admission, cancellation ownership and event-loop health.
(D52–D53, D83–D85)

During the authentication upgrade, discard only server-managed legacy ownerless
artifacts and invalidate their links; do not guess ownership, preserve nonce-only
access, touch upstream mail/unrelated plugin state/client files, or replay work
to recreate downloads. No deletion has been performed. (D66)

## Backup, restore, undo and migration

**Agreed.** Arrange ongoing backup from startup using an external operator job
requesting a consistent encrypted application bundle through trusted local/SSH
administration. The job owns cadence, off-host storage, retention and failure
reporting; Arbiter owns complete consistent export to its configured recipient.
No internal scheduler, Dropbox integration, synchronous backup of every mutation
or first-enrollment backup gate is selected. (D61–D64, D86–D88)

Include durable server/issuer identity and server-held keys, enrollment public
credentials/accepted-key state, grants, policies, account configuration and actual
configured upstream credentials. This does not back up client private keys.
Exclude host
images, OS/Reploy/Apache/DNS configuration, upstream mail, client files, active
sessions and uncommitted pairing secrets. Sources can use file-backed `oc.env`
or `secret_file`; capture configured values, not an arbitrary environment dump.
Keep private recovery material outside server, agent and bundle-sync storage.
The single operator recovery file includes trusted public server-verification
metadata as well as its private decryption key. Loss of all copies makes bundles
unrecoverable. Without a usable backup, create a new server identity and explicitly
pair/grant existing clients again. Surviving durable client identities need not
be recreated merely because the server was lost. (D61–D67, D101)

**Candidate export/restore mechanism:** consistent SQLite snapshot plus pinned
configuration/key/secret generations; bounded capture and crypto outside the event
loop; an authenticated manifest inside an age-encrypted bundle. Candidate JWS
ES256 signatures bind snapshot purpose, server, date, component names, lengths
and hashes. Verify against the independently trusted recovery record, never a
key nominated by the incoming bundle. Fix algorithms and reject duplicate fields,
unsupported schemas, traversal, links and executable restore hooks. Public-key
encryption alone does not authenticate the sender or prove a backup is latest.
Publish only a fully finalized, checked export; preserve the last good bundle
on failure. Exact bundle framing, snapshot/activation and portability are **B5**.

| Recovery operation | Agreed behavior |
| --- | --- |
| Restore | Work offline even when Arbiter will not start. Validate privately, keep the target stopped during activation, restore one coherent saved configuration/secret/authority generation, and warn of the snapshot date and lost later changes. No mandatory permission review, paused-access mode or separate activation approval. |
| Different host | Restore full saved Arbiter settings; edit URL/ports/paths normally before startup. Do not silently merge target settings or add a restore wizard. |
| Stale snapshot | Can reinstate older grants/revocations and deadlines. Do not claim reconstruction of newer state or reset timers to extend authority. |
| Undo | Preserve only the latest coherent pre-restore state, with no automatic expiry; replace it after the next successful restore or explicit discard. A failed restore preserves the existing undo point. |
| Undo after server use | Still permitted offline, with a very prominent warning that subsequent local changes will be lost. No hard cutoff; upstream email effects are not reversed, and clients may need credential repair. |

For planned migration, approve a maintenance window, quiesce/drain the source,
take a final coherent bundle, and activate one writable authority at the
destination. **Candidate authenticated URL handoff:** the old source becomes
handoff-only; a client verifies the same durable destination identity before
sending credentials/nonces or adopting its URL. Handoff cannot depend solely on
the source's stale accepted client key. Never replay uncertain mutations/GETs or
run independently writable copies. Pre-cutover fallback differs from rollback
after destination activity; local undo is not an automatic source-host fallback.
Shared-state clustering and zero-downtime guarantees remain deferred.
(D89–D100)

**Agreed post-review disposition — DBB finding 6 (2026-10-20).** Assume
cooperative servers and infrequent migration. Use a documented operator procedure
to maintain one active server, rather than client-enforced authority generations,
a signed takeover protocol or persistent server-retirement machinery:

1. For planned migration, stop ordinary service on the source and prevent its
   automatic restart before taking the final coherent bundle.
2. Restore the bundle with the destination stopped; adjust host-specific settings
   and start the destination during the agreed maintenance window.
3. Keep ordinary service on the source disabled after cutover. Any old-URL handoff
   must not serve operations or mutate authorization state.
4. For restore or undo, keep the target stopped while replacing state, then start
   only the selected installation. Follow the existing snapshot-date and
   post-use undo warnings; account for changes lost since the selected snapshot
   and repair affected client credentials as needed.

This is an accepted operational control, not an automatic guarantee against two
copies running. Stale authorization state remains the accepted restore risk
described above. Do not reject an approved older restore or undo using a
client-side generation counter. Authenticated URL handoff remains a candidate
convenience, not an automated authority-transfer requirement. This disposition
does not implement or verify the procedure, close the other B5 mechanisms, or
claim renewed DBB approval.

## Finite blockers and review boundary

The following groups replace the open-ended question frontier. They remain real
engineering/design gaps; listing them does not satisfy them or approve deferral.

| ID | Unresolved mechanism | Evidence required before implementation readiness |
| --- | --- | --- |
| B1 — pairing binding | Exact selector/wire/context encoding, separate key-possession proofs, durable activation/receipt retrieval and bounded replay/restart handling. | Complete sequence/state contract; lost-reply, conflicting-step, replay, crash and overload cases; maintained-library disposition and adapter limits. |
| B2 — identity and lifecycle | Standard issuer/stable-ID/purpose/validity profile; owner registration/continuity; accepted-key rules; client predecessor durability; server-key transition/history/retirement and compromise recovery. | Python/Go profile verification, role separation, current/old/pending races, offline/expiry/rollback and owner-repair traces without changing approved grace/inactivity behavior. |
| B3 — channel and administration | TLS-over-WebSocket/ASGI adapter, expired-credential restricted completion, immutable caller context, revocation and numeric transport bounds; private browser session and one public topology recommendation. | Composed channel/proxy/streaming/disconnect/admission checks; no outer-to-inner trust leakage or auth bypass; workable admin session boundary. |
| B4 — artifact cryptography | Final encryption/envelope/backend choice, trusted metadata/recipient binding, decryption-key updates, physical accounting and bounded streaming. | Python/Go interoperability, GIL/slow I/O/finalization failures, truncation in every processing mode and supported packaging; no false sender-authentication claim. |
| B5 — durable recovery | Complete cross-file snapshot ownership, signed bundle framing/limits, local admin transport on Windows and portable client/server persistence, restore/undo activation and migration handoff. | Forged/decryptable and stale bundles, crash boundaries, helper contention, lost completion, cleanup and paired-client migration/undo scenarios. |

No fresh owner decision is required merely to prepare this review. U53 and other
unselected tuning values remain proposals. DBB should first assess the trust
boundaries and whether these five blocker groups conceal a contradiction or
unnecessary complexity, then prioritize the minimum concrete mechanisms needed.
If evidence requires changing agreed behavior or significant maintenance scope,
return that specific tradeoff to the owner. Do not restart an unbounded interview.

## Evidence and readiness

The current checkout has HTTPS/self-signed or supplied certificates, account
policies and nonce-protected artifacts; it lacks this caller-authentication and
per-client grant layer. The client has separate operation/download request paths
and loopback TLS exceptions. These are source observations, not deployment tests.
See [source and experiment evidence](evidence.md).

An isolated Noise Python/Go POC established compatibility for the tested profile
on Linux and native Windows ARM64, plus exact-vector and Go 1.24-floor probes.
It did not test this complete enrollment protocol, native tunnel, artifact crypto,
backup/restore or independent security. The Python wrapper is old and has a
reproduced size-boundary defect; its bounded-adapter mitigation remains proposed.
Artifact/backend packaging and Python responsiveness concerns remain unverified.
No full-platform compatibility or production-dependency adoption is claimed.

Readiness: **questioning closed; repair contracts reviewed by actual DBB;
complete protocol, implementation and release readiness not established**.
Findings 1–5 are resolved at design level and finding 6 is accepted under the
documented cooperative-server procedure. B1–B5's remaining profile, adapter,
backend, interoperability and failure evidence must still be produced. These
are design repairs and an operational risk disposition, not implemented fixes.
The original AWD interview remains interrupted; this package does not rewrite
its pins/counters, answer U53 or authorize product work.
