# Authentication: candidate security contracts after DBB review

Prepared 2026-10-20 for a second actual DBB review. These contracts repair the
mechanism gaps in findings 1–5 of the [first review](reviews/dbb-initial.md).
They elaborate the [agreed design](design.md); they do not
authorize implementation or claim tested protocol interoperability. Candidate
key/format choices remain subject to review. B1–B5 still require their listed
implementation evidence. Finding 6 uses the owner's documented operational
procedure, not an authority-generation protocol.

## H1 — owner recovery and credential roles

### Trust provision and custody

The owner issuer, durable server identity, portable agent identity, per-server
access, artifact recipient and backup encryption/signing are distinct purposes.
Portable identity is a non-CA X.509 leaf: `digitalSignature` plus a dedicated
portable-identity EKU, without `clientAuth`, `serverAuth`, `keyCertSign` or
`anyExtendedKeyUsage`. Access certificates use a different key and exact
server/enrollment binding. Verifiers check both key usage and EKU, not just a
valid chain. Exact OID/subject encoding and Python/Go profile tests remain B2.
See [RFC 5280 §4.2.1.12](https://www.rfc-editor.org/rfc/rfc5280.html#section-4.2.1.12).

**Candidate independent recovery authority.** Trusted operator tooling generates
a purpose-separated ES256 recovery-signing key and keeps its private material
inside the existing protected operator recovery file, alongside the separate
backup-decryption material and trusted public verification data. This is one
operator-managed file, not another external service or agent credential. It
extends the candidate file schema; it does not reuse the backup-decryption key
for signing. The server receives only its public recovery verifier. Neither the
server-held AMI issuer nor the agent-held portable identity can sign recovery
authorizations. Losing all external recovery material still requires explicit
new trust establishment; a compromised server key cannot nominate its successor.

Custody consequence: this file now holds both backup-decryption and live
owner-recovery authority. Theft can authorize attacker-held replacement keys at
registered servers, subject to the recovery checks and preserved denials; it is
not merely disclosure of old backups. Setup, recovery-file labeling and operator
documentation must state both powers. Keep the file outside agents, servers and
bundle-sync storage. Unattended backup jobs receive no private signer. This
candidate is a later role-separated schema extension, not a claim that D101's
public-metadata addition already approved another private key.

Trusted local/SSH setup registers that owner public verifier before it can be
used. Initial pairing binds the verifier, owner identity and durable server
identity into the client's authenticated trust record. Another independent
server must register the same owner trust through its trusted administration;
a requester-supplied owner root never registers itself. Signing authority and
ordinary access remain separate even when the human owner/operator is the same.

### Recovery transaction

1. The recovering client creates a fresh per-server access key. A bounded
   recovery request identifies the existing client/enrollment and expected old
   credential version. The server records a fresh challenge and request ID;
   restart/expiry invalidates an uncommitted challenge, not existing grants.
2. Through trusted operator tooling, the owner reviews and signs a versioned,
   purpose-tagged recovery statement. It binds owner, durable server identity,
   durable client identity, enrollment ID, request/challenge, expected credential
   version, exact replacement public key and expiration. A portable-identity
   replacement additionally binds the old/new portable key and ownership chain.
   Cross-server recovery uses a statement for each affected server, which tooling
   can prepare in one owner-authorized operation; it is not a service credential.
3. The replacement client proves possession of the exact new key over that
   statement and the current verified recovery channel. The server checks its
   previously registered owner verifier, purpose, request, expiry, key bindings
   and current state. Receiving a new certificate/root is not recovery proof.
4. One conditional transaction consumes the challenge, replaces the expected key,
   records the event/result and invalidates old sessions/pending rotations. It
   preserves enduring identity, existing grants and all suspension/revocation
   state. Repeating the same event with the same key returns its recorded result;
   conflicting, superseded or consumed events cannot replace a later key.

The owner's signature is the explicit approval required by D24: there is no
second approval at each server and no repeat account-grant ceremony. This does
not authorize a portable identity key alone to replace access keys. Recovery of
a suspended/revoked enrollment never makes it usable; lifting denial is a
separate existing administrative operation. Current local owner-approved pairing
remains the simple repair path, including after stale restore/undo.
For that one server, trusted local approval itself authorizes replacement; the
owner need not use the external signer merely to repeat D44's local pairing
flow. An externally verifiable statement is needed for automatic continuity at
other servers or independent repair of remembered server trust. It is not an
additional step imposed on every enrollment or local credential repair.

Use a fixed JWS signature profile with purpose in the protected header and
payload; select the registered verifier independently of untrusted payload keys.
Verify the signed bytes, reject duplicate/unknown critical fields, fixed-algorithm
violations and ambiguous identities. The schema and cross-language fixtures are
required before adoption. See [JWS validation](https://www.rfc-editor.org/rfc/rfc7515.html#section-5.2).

Server-identity compromise requires restoring a trusted host through local/SSH
administration, then an owner-signed, server-specific old/new identity-key repair
statement and new-key possession proof. Clients use the independent owner
verifier retained at pairing, not an endorsement by the compromised key alone.
This is distinct from routine planned server-key replacement. Missing independent
trust requires explicit owner-mediated re-pairing; never learn a recovery root
from the server being repaired. The complete transition-history profile remains B2.

## H2 — restricted late-rotation channel

Ordinary native HTTP remains behind mutually authenticated TLS with full client
certificate validity and live enrollment checks. Do not weaken that verifier.
Use a distinct, bounded rotation-completion carrier whose inner TLS authenticates
the server to the client's existing durable trust but does not request a client
certificate. It dispatches a fixed control exchange, not HTTP/ASGI or plugin code.
The untrusted outer WebSocket still carries no credentials before inner server
verification. No redirects, resumption, early data or ordinary-operation fallback.

The server issues a fresh connection-local nonce. Client proof covers protocol
version/purpose, server and enrollment identity, pending rotation ID/version,
stored old-key fingerprint, candidate-key fingerprint, nonce and the TLS 1.3
`tls-exporter` channel binding. Require separate possession proofs by the stored
old key and the exact pending candidate key. This is not permission to prepare
a different rotation, replace a candidate or repair a lost private key.

Use [RFC 9266](https://www.rfc-editor.org/rfc/rfc9266.html#section-2)'s binding
only as connection-binding data, not as a secret signing key. One authentication
exchange per TLS connection; close after completion or failure. Library exposure
of the binding in Python and Go must be demonstrated before implementation;
absence is a failed compatibility gate, not permission to omit binding.

The restricted verifier checks the exact stored certificate/key, chain signature,
issuer, identity and purpose; only the old certificate's time validity may be
relaxed when this precise pending-rotation record permits it. Require live
non-revoked/non-suspended enrollment, matching state version and a still-open
inactivity deadline. Reject every other certificate/profile failure. Check
deadlines and denial again in the activation transaction. An incomplete exchange
does not reset grace, inactivity or receipt lifetime. Successful activation can
establish fresh authenticated activity, but cannot revive an already expired
inactivity deadline.

Permit only confirmation of the durably saved candidate and retrieval of the
same bounded completion result. Never construct ordinary caller context. A lost
confirmation response is reconciled with the candidate or that same completion
record; it cannot reopen a superseded rotation. Retain at most the current pending
transition and its immediately relevant completion record. Revocation or owner
repair invalidates both. After success the client closes the restricted channel
and establishes ordinary mTLS with the new valid credential.
If the saved candidate certificate has expired, completion may reissue a valid
certificate for that exact candidate public key and same enrollment only. This
does not change the pending key, reopen grace or bypass the inactivity cutoff.

The 30-day due threshold, fixed 24-hour grace and independent 180-day inactivity
rule do not change. Required tests cover expired old certificates, wrong purpose,
wrong/cancelled candidate, replay on another TLS connection, revocation races,
lost replies, repeated attempts near cutoff and inability to reach ordinary APIs.

## H3 — artifact origin and processing

The verified inner channel supplies a versioned descriptor binding durable server,
creator client, artifact ID, capability context, recipient-key version, envelope
profile, plaintext length and SHA-256 digest. Capability secrets remain inside
the authenticated channel/private memory and never enter logs. The client checks
the descriptor against the requested artifact and its enrolled identity before
accepting ciphertext. Attacker-supplied encrypted metadata is not that authority.
The envelope must agree with the authenticated descriptor; full decryption,
length/digest and finalization checks must all succeed in verified modes.

For immediate stdin, the verified live channel authenticates the source and each
released encrypted chunk must authenticate before release. Final length/digest
or truncation failure can still follow a valid prefix; it reports failure without
claiming to undo tool effects. Do not describe that mode as whole-file verification.

For verified encrypted-temp processing, retain the authenticated descriptor in
private process state, download to a bounded private file and verify the complete
artifact before launching the tool. Keep the same open file and recipient key
through the second decryption pass; do not reopen an attacker-selected pathname
or trust a descriptor read back from the downloaded file. Do not persist a
plaintext verification copy. Failed verification launches no tool. Restart loses
this transient authority: clean abandoned staging rather than silently trusting
an encrypted file alone or replaying its consumed GET.
The same open descriptor alone does not make bytes immutable. B4 must establish
that no in-scope writer or writable alias can change the verified bytes between
passes. If the staging backend cannot establish that property, obtain a bounded,
immutable process-owned snapshot before tool startup. An end-of-second-pass
rehash alone is insufficient because the tool has already received data. This
does not promise atomic tool execution or immunity to later disk/pipe failure.

For explicit plaintext-temp processing, complete the same origin/content checks
before exposing the pathname to the tool. `save-decrypted` likewise publishes
only after full verification, with private staging and no partial final export.
All existing retention, physical-storage, cancellation and explicit-flag rules
remain. File isolation assumes the trusted client OS/user boundary; it does not
protect against another process with the same user's full access.

Recipient encryption alone is not server-origin proof. No extra artifact signing
key is required for these live/transient modes: the authenticated descriptor
supplies origin. Later independent verification of persistent encrypted archives
would need a separate authenticated-origin format and is not added to this work.
The envelope encoding/backend and Python/Go tamper/truncation/TOCTOU tests remain
B4, including all four client-selected processing paths and their documented tradeoffs.

## H4 — pairing resource admission and semantic attempts

Reserve bounded connection/byte/crypto resources before parsing or cryptography.
That reservation is not one of the five pairing attempts. For the exact Noise
profile, require a fixed non-empty first-flight payload authenticated by the PSK,
binding protocol/version, public selector and the immutable approved request/key
context. Validate that proof before charging a semantic attempt; an AEAD failure,
unknown selector or malformed unauthenticated frame cannot exhaust that request.
Do not send an unauthenticated fallback or reveal detailed pending-request state.
The [Noise PSK pattern](https://noiseprotocol.org/noise.html#pre-shared-symmetric-keys)
is the crypto mechanism; application admission and replay state remain ours.

Atomically admit at most five distinct authenticated exchanges for the request.
Store each admitted first-flight digest and one bounded exchange/result record;
an exact replay is the same attempt, not another charge or a second mutable
handshake. Serialize ownership of a live exchange; retransmission cannot reset
its timers, swap keys or cause a second commit. A fresh authenticated exchange
after failure/abandonment consumes another slot. A successful exchange commits
once; five failed/abandoned admitted exchanges exhaust the request and require
fresh approval. Validate current approval, lifetime, key and account state again
at commit, with revocation taking precedence.

Unauthenticated junk remains subject to bounded resource admission/timeouts and
cannot create an unbounded deduplication store. Pending state, digests and cached
responses expire with the original ten-minute request; restarting without its
PSK invalidates the uncommitted request. Keep the twenty-pending default and no
general fairness guarantee. U53 and other unselected transport tuning values
remain unapproved. B1 tests must demonstrate real PSK proof, concurrent/replayed
first flights, five distinct failures, expiry, abandonment and restart cleanup.

## H5 — private browser administration

The local/SSH CLI uses the trusted OS administration boundary to reserve the
loopback listener/SSH local forward before issuing a random, short-lived,
single-use bootstrap URL. Each launch gets a new unpredictable origin: a fresh
128-bit random hostname label under `.localhost` plus the already-bound local
port. Never reuse it as a stable admin origin. A random port alone is not enough
because the small port space can already have hostile service-worker/cache state.
Configure the exact launch origin through trusted administration, not by trusting
the incoming Host header. Keep the listener/forward reserved for the session and
invalidate it on shutdown; never expose a bootstrap before a successful bind.
The random hostname isolates previous browser state; the bearer still supplies
authentication. `.localhost` is the special loopback namespace described by
[RFC 6761 §6.3](https://www.rfc-editor.org/rfc/rfc6761.html#section-6.3), not a
public domain or a requirement for operator DNS/hosts-file setup. Browser/resolver
support and binding on each supported platform require explicit validation.
If this path cannot be provided, fail with a diagnostic; do not silently fall
back to a reused origin, certificate-warning bypass or public DNS dependency.

For that one exact launch origin, the bootstrap secret
is in the fragment, not the URL query/path, shell arguments, environment or logs.
Only the explicit owner launch action displays it; browser code removes the
fragment from history immediately and exchanges it once at that exact origin.
No bootstrap secret goes to third-party scripts, referrers or external resources.

The exchange returns a bounded admin session bearer and separate CSRF token.
Keep them in origin-scoped browser memory, not ambient cookies or persistent
browser storage. Send the bearer explicitly in an authorization header; require
the CSRF value for state changes. Validate exact configured `Host` and `Origin`,
reject absent/foreign/null origin on bootstrap and authenticated admin API
requests, cross-origin redirects and
credentialed CORS, and accept no state changes through GET/simple form requests.
Browser session authority never comes from loopback source or SSH alone.
Use same-origin JSON POST for those browser API exchanges so the browser sends
`Origin`; public page/static GETs can omit it but carry no session state or
secrets and still require the configured Host. No protected API falls back to
an Origin-free GET when a request fails.

Bootstrap consumption and session creation are atomic. Explicit close/revoke,
expiry and server restart invalidate the session; CLI issuance does not create
an indefinitely reusable admin credential. Bound bootstrap/session count, size,
lifetime and request work; exact numeric tuning remains B3, not a new approved
default. Browser refresh may require a new CLI launch rather than persistent
secret storage. Do not tie unrelated client-enrollment deadlines to the CLI's
process lifetime. Initial delivery remains CLI-only; this specifies the future
browser boundary, not immediate UI implementation.

Private HTTP is loopback-only on both ends. SSH must verify its host key, bind
the local forward to loopback, reach only the server's loopback listener and
fail if the forward cannot bind. A different local port/service has no bearer
or bootstrap authority. A compromised client OS/user or browser extension with
page access is outside this isolation guarantee. Test hostile local origins,
Host rebinding, missing Origin, token replay, expiry/restart, CSRF and failed
forwarding before shipping the browser interface.
Include a browser profile containing a hostile worker/cache at a previously
used origin, attempted new-origin preclaim, listener/forward races and a browser
refresh. Do not register service workers or persistent page caches for the
private admin origin; send no-store responses and a restrictive CSP including
`worker-src 'none'`. Those headers are defense in depth, not a way to evict a
pre-existing hostile worker before it sees the bootstrap. Never recycle the
old origin to make refresh or reconnection work.

Public admin follows DBB's recommended TCP passthrough with HTTPS at Arbiter;
public browser trust does not use the native carrier's certificate exception.
Apache termination remains an explicitly trusted alternative, not required dual
implementation. Neither recipe changes native durable identity or requires
public DNS/certificates for private installations.
