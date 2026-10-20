# DBB review: authentication and enrollment

Reviewed 2026-10-20. This is my bounded defensive architecture assessment of
the frozen authentication design package. It is not a protocol, implementation,
dependency, deployment or release audit.

## Executive verdict

The design has a defensible trust model and can proceed to finite protocol work,
but it is not ready for implementation or security approval. I found one unsafe
attempt-accounting rule and five security-critical mechanism gaps that must be
closed in B1–B5. None requires reopening the broad owner interview. The proposed
Noise pairing and TLS-over-WebSocket composition is viable if the inner channel
is the only identity boundary and all failure paths fail closed. The POC proves
only tested library interoperability, not that composition.

D103 is acceptable as a provisional direction. The portable key is justified by
the already agreed cross-server identity requirement, provided that its certificate
cannot be accepted for service authentication, local access-key replacement or
certificate issuance. It remains part of B2 until the profile, continuity and
owner-compromise recovery paths are complete.

For public browser administration, use **TCP passthrough with HTTPS terminating
at a dedicated Arbiter admin listener** as the primary recipe. This keeps browser
TLS, sessions and admin plaintext in one trusted application boundary and avoids
proxy-identity-header and backend-channel ambiguity. Apache HTTPS termination is
a viable alternative only if explicitly configured as a trusted admin component;
it should not be the primary security recipe.

## Reviewed input identities

I reviewed these exact bytes and rechecked their hashes after writing this report:

| Input | SHA-256 |
| --- | --- |
| `docs/authentication-and-enrollment-review.md` | `ab703dd9255445b5f641fa6823d86150aa57d2c7ec67e369c12659f9285dde13` |
| `docs/authentication-and-enrollment.md` | `389141e6d9ca5e1fe73fa8170168e42e2d8e6ccce9be6afa5e249289a20f55cb` |

The identities match
[`temp/reviews/authentication-fidelity-2026-10-20/verification.json`](../../../temp/reviews/authentication-fidelity-2026-10-20/verification.json).

Severity reflects consequence if the incomplete design were implemented in the
most direct way. “Incomplete mechanism” does not claim that the absent product
behavior is already exploitable.

## Prioritized findings

### 1. High — owner recovery needs one unambiguous authority path

- **Class:** incomplete mechanism and contract reconciliation.
- **Affected invariant/blocker:** portable ownership must not grant local access;
  owner-authorized recovery must preserve identity and grants without clearing
  local denial; B2, D24 and provisional D103.
- **Scenario:** an implementation can resolve the current wording in either unsafe
  direction. If a portable identity proof can replace a server-local access key,
  theft of the lightweight client directory lets an attacker replace credentials
  while preserving grants. If every affected server requires an additional local
  approval after valid owner recovery, D24's recovery promise is not met. If the
  affected server's own identity/issuer key is the only recovery authority, its
  compromise cannot authenticate an independent repair.
- **Preconditions:** theft of the portable identity key, compromise of an old
  server authority, or legitimate owner recovery after loss of local access keys.
- **Evidence:** the design distinguishes the key roles and forbids portable-key
  replacement at
  [`authentication-and-enrollment.md`, lines 69–93](../design.md#actors-trust-and-credentials),
  but intentionally leaves the D24 interaction open. The server-key section says
  a compromised old key cannot establish recovery and leaves independent recovery
  in B2 at
  [`authentication-and-enrollment.md`, lines 271–280](../design.md#client-key-rotation-recovery-and-server-replacement).
  [RFC 5280 sections 4.2.1.3 and 4.2.1.12](https://www.rfc-editor.org/rfc/rfc5280.html#section-4.2.1.12)
  support purpose restriction but do not provide continuity, revocation ordering
  or owner-recovery semantics.
- **Consequence:** cross-server credential takeover, recovery that silently clears
  local security state, or an unrecoverable authority after key compromise.
- **Smallest correction:** specify a separate, non-agent owner-recovery statement.
  It must be fresh, signed through previously registered owner trust independent
  of the affected server key, and bind the durable server identity, enduring client
  identity, exact new local access key, recovery purpose and monotonic recovery
  event. Applying that statement is the owner's explicit approval, so no second
  approval is required. The server transition must atomically preserve grants and
  suspension/revocation, invalidate old sessions and defeat concurrent old-key
  rotation. A portable identity proof remains only an input to new-server
  enrollment, which still requires server-local approval and grants.

### 2. High — restricted late rotation cannot be implemented as a general expiry bypass

- **Class:** incomplete mechanism.
- **Affected invariant/blocker:** after the 24-hour grace, an expired or otherwise
  non-ordinary credential may complete only its already pending rotation before
  the 180-day inactivity cutoff; B2/B3.
- **Scenario:** ordinary TLS certificate validation rejects the client before the
  application can recognize its pending rotation. A tempting fix is to disable
  certificate-time or enrollment-state checks broadly and defer them to the ASGI
  application. That can turn an expired credential into ordinary API authority.
  The opposite implementation makes the promised late completion impossible.
- **Preconditions:** a prepared rotation survives past grace or certificate expiry;
  an attacker holds that old key, or a legitimate client returns late.
- **Evidence:** the lifecycle table and unresolved verifier interaction are explicit
  at
  [`authentication-and-enrollment.md`, lines 241–258](../design.md#client-key-rotation-recovery-and-server-replacement).
  The native boundary otherwise requires chain, identity, purpose and live accepted
  state at
  [`authentication-and-enrollment.md`, lines 180–185](../design.md#native-transport-and-browser-administration).
- **Consequence:** authentication bypass if the exception reaches ordinary routes,
  or forced owner repair earlier than the agreed inactivity cutoff.
- **Smallest correction:** define a separate restricted completion verifier and
  route. It may tolerate only the exact failed validity condition needed for one
  stored pending rotation, while still checking chain/signature, dedicated purpose,
  expected old key, enrollment identity, non-revoked/non-suspended state, pending
  rotation ID/version and inactivity cutoff. It must expose only proof of the
  candidate key and idempotent receipt retrieval. It must not create ordinary
  caller context, use TLS early data/resumption, follow redirects or dispatch any
  plugin/artifact operation. Test every verifier failure against route reachability.

### 3. High — B4 must make the authenticated descriptor the origin authority

- **Class:** acknowledged incomplete mechanism, with an unsafe failure mode if the
  implementation treats recipient encryption as origin authentication.
- **Affected invariant/blocker:** only the authenticated creator's artifact may be
  consumed, and verified modes must reject substituted ciphertext before tool
  input; B4.
- **Scenario:** an attacker substitutes a complete, valid age file encrypted to
  the client's public recipient key. If the client accepts decryption/finalization,
  or length/hash values carried only inside that attacker-supplied envelope, as
  proof of server origin, the replacement succeeds. The draft's required
  authenticated download descriptor prevents this only when the client retains
  that independently authenticated identity/recipient/length/hash context and
  compares the completed artifact against it before tool input.
- **Preconditions:** local staging-file replacement, malicious storage/sync, or a
  future path that loses the authenticated transport context; knowledge of the
  recipient public key.
- **Evidence:** the design already requires the expected artifact identity,
  recipient version, length and hash to be bound to the authenticated download
  context, and correctly says age does not authenticate the sender, at
  [`authentication-and-enrollment.md`, lines 293–302](../design.md#artifacts-and-client-processing).
  It requires complete verification before tool startup for buffered modes at
  lines 304–318. This is the right invariant, but the envelope and persistence
  profile that make it true remain B4. Current digest response headers are source
  observations only;
  the server itself states that caller authentication is absent at
  [`server/src/arbiter_server/main.py`, lines 1255–1269](../../../server/src/arbiter_server/main.py#L1255).
- **Consequence:** attacker-selected tool input, potentially causing downstream
  operations even though confidentiality and ciphertext integrity appear valid.
- **Smallest correction:** define one canonical authenticated descriptor containing
  server identity, artifact ID, creator identity, nonce/capability context,
  recipient key version, plaintext length/hash and processing purpose. Obtain it
  through the verified inner channel, bind it to the exact ciphertext download,
  and retain that binding until final length/hash and crypto finalization checks
  complete. For immediate and verified temporary processing, the authenticated
  channel plus protected client state can supply origin; no extra signature layer
  is inherently required. Add a purpose-separated server signature only if an
  encrypted saved file must later prove origin without the original channel or
  protected local descriptor state. Immediate stdin remains explicitly partial:
  authenticate released framing/context and report final failure, but do not claim
  atomic tool input.

### 4. Medium — unauthenticated traffic can exhaust one request's five-attempt budget

- **Class:** unsafe design choice.
- **Affected invariant/blocker:** pairing is bounded without letting an untrusted
  network peer cancel the owner's approved request; B1.
- **Scenario:** the candidate reserves and charges an attempt before cryptographic
  authentication. An on-path peer or forwarding proxy observes the public selector
  and sends five malformed or replayed first flights. The legitimate client then
  finds the request exhausted despite the attack never demonstrating knowledge of
  the 256-bit pairing secret.
- **Preconditions:** visibility of a live selector and reachability of the enrollment
  endpoint during its ten-minute window. The attacker need not know the PSK.
- **Evidence:** the selector is public and attempts are capped at five at
  [`authentication-and-enrollment.md`, lines 102–125](../design.md#enrollment-and-incomplete-attempts).
  The candidate reserves before crypto and the retained POC notes that first-message
  replay still requires application state at
  [`authentication-and-enrollment.md`, lines 127–143](../design.md#enrollment-and-incomplete-attempts)
  and
  [`authentication-and-enrollment-evidence.md`, lines 25–47](../evidence.md#experiment-evidence-and-limits).
- **Consequence:** targeted denial of enrollment and repeated owner ceremony. This
  is narrower than the accepted lack of fairness under general connection spam.
- **Smallest correction:** reserve global/per-peer admission resources before
  crypto, but charge the request's five-attempt budget only after a fixed,
  non-empty first-flight authenticator proves PSK knowledge. Make an exact replay
  of the same authenticated first flight idempotent by selector plus transcript
  digest. Bound unauthenticated junk with connection, byte, CPU and time limits,
  not the request's semantic failure counter. This preserves the approved five
  attempts as five distinct PSK-authenticated failed or abandoned exchanges; it
  does not increase that count or promise fairness under general connection spam.

### 5. Medium — SSH forwarding authenticates the path, not the browser session

- **Class:** incomplete mechanism.
- **Affected invariant/blocker:** only an operator-authorized browser can perform
  private administration; B3.
- **Scenario:** the server sees both a verified SSH forward and an unrelated local
  process as loopback connections. On the browser host, any local process can also
  reach the forwarded port. If an ambient `localhost` cookie is the sole credential,
  a service on another localhost port can receive it because cookies are not
  port-isolated, even though browser origins include the port.
- **Preconditions:** another local service/process can induce browser requests or
  listen on another localhost port; the admin UI relies only on source address or
  a host cookie.
- **Evidence:** the design recognizes these conditions at
  [`authentication-and-enrollment.md`, lines 195–201](../design.md#native-transport-and-browser-administration).
  [RFC 6454](https://www.rfc-editor.org/rfc/rfc6454.html#section-5) defines origins
  using scheme, host and port, while [RFC 6265 section 8.5](https://www.rfc-editor.org/rfc/rfc6265.html#section-8.5)
  states that cookies do not provide port isolation.
- **Consequence:** unauthorized approval, rejection, recovery or grant changes
  from another local web service or process.
- **Smallest correction:** require an operator CLI action to create one bounded,
  single-use browser bootstrap for the exact UI origin. Do not use source address
  or an ambient localhost cookie as sole authority. A minimal private design can
  place the bootstrap secret in a URL fragment, exchange it once over the exact
  origin, and keep the resulting bearer in origin-scoped browser memory while
  sending it explicitly in an authorization header. Enforce exact `Host` and
  `Origin`, reject redirects/cross-origin requests, require a per-session CSRF
  value for state changes, cap lifetime and revoke the server-side session on CLI
  close. The SSH recipe must bind both legs to loopback, verify the host key and
  fail if forwarding was not established.

### 6. Medium — restore and migration need an enforceable single-authority generation

- **Class:** incomplete mechanism.
- **Affected invariant/blocker:** restore/undo and migration must not create two
  independently writable instances with the same durable server identity; B5.
- **Scenario:** a restored or migrated destination starts with copied authority
  keys while the old host remains reachable or later restarts. Clients cannot
  distinguish the two by durable identity. Their accepted-key, revocation,
  consumption and rotation state diverges; a client revoked on one instance may
  remain accepted on the other, and one-time artifact GETs can be consumed twice.
- **Preconditions:** the old host is not cryptographically or operationally fenced,
  or an automated restart/network route restores reachability after cutover.
- **Evidence:** the design requires one writable authority but leaves handoff and
  activation in B5 at
  [`authentication-and-enrollment.md`, lines 368–390](../design.md#backup-restore-undo-and-migration).
  It also accepts that stale snapshots can restore old grants and deadlines at
  lines 360–366.
- **Consequence:** split-brain authorization, replay of supposedly consumed access,
  and rollback of revocation or rotation state.
- **Smallest correction:** make fencing a required activation precondition and
  record a recovery-signed, monotonically increasing server generation in the
  bundle, active authority store and client trust record. A destination proves
  the new generation before receiving credentials or nonces; clients never
  downgrade. Keep the old source network-fenced before destination activation;
  if it remains for URL handoff, give it a purpose-limited handoff credential that
  cannot serve the API or mutate authority state. Preserve the explicit rule that
  uncertain operations and consuming GETs are never replayed.

## D103 and role separation

Keep D103 provisional, with the following minimum X.509 profile obligations:

- The owner issuer is a CA role only. Its private key is absent from agents and
  independent servers. Its recovery path must not depend on a compromised server
  identity key.
- A portable identity certificate is a non-CA leaf with `digitalSignature` and a
  dedicated Arbiter portable-identity EKU. Do not give it `clientAuth`,
  `serverAuth`, `keyCertSign`, `anyExtendedKeyUsage` or ordinary API authority.
- A per-server access certificate is a different key and profile. Bind it to one
  durable server identity and one enrollment, and require live server state in
  addition to chain validation.
- Server identity, artifact-origin signing and backup signing remain separate
  purposes. A verifier must request the exact purpose; a generic “valid chain”
  result is insufficient. If both key usage and EKU are present, RFC 5280 requires
  both restrictions to be satisfied.
- Portable identity proof uses a fresh server challenge and proves possession.
  It never creates grants, revives a denial or replaces an accepted local key.

This extra key is necessary only because portable owner-certified identity is an
agreed requirement. Do not add a provider framework, cross-server registry,
installation inventory or automatic admission around it.

## Transport and browser topology assessment

### Native Noise and TLS-over-WebSocket

The composition is defensible with its stated boundaries:

1. Noise authenticates the initial high-entropy pairing secret and binds the
   proposed keys and durable server trust before activation.
2. Inner mutually authenticated TLS authenticates all later native operations.
3. WebSocket is a byte carrier through an untrusted forwarding proxy.

Disabling outer chain/hostname validation is acceptable only in a dedicated
carrier that sends no credentials, cookies, artifact capabilities or application
requests until inner server verification succeeds. It must reject redirects,
scheme fallback, proxy-supplied identity headers and any ordinary HTTP fallback.
The outer peer retains denial-of-service and traffic-analysis power. Enabling
normal outer validation when a usable public certificate exists is optional
hardening, never a replacement for inner identity.

Starting without TLS resumption or 0-RTT is correct. TLS 1.3 warns that early data
can be replayed and requires an application profile for its safe use; see
[RFC 8446 section 8](https://www.rfc-editor.org/rfc/rfc8446.html#section-8).
No need exists to add resumption before the state machine is stable.

### Public browser administration

Use TCP passthrough to a dedicated Arbiter HTTPS admin listener as the primary
recipe. Arbiter owns the browser certificate and renewal, canonical host, session,
CSRF and UI-code boundary. The forwarding layer can route and deny traffic but
cannot read admin plaintext or cookies. Keep the admin listener distinct from the
native carrier's trust exception even if deployment routing shares an external
port. Public browser certificate renewal must not alter native durable trust.

The main cost is that Arbiter must integrate reliable public certificate loading
and reload/renewal, browser-grade TLS configuration and direct admission limits.
TCP routing also provides fewer HTTP-layer controls and less convenient virtual
host integration.

Apache HTTPS termination offers mature certificate automation, virtual hosting
and HTTP controls, but it becomes fully trusted with admin plaintext, sessions
and delivered code. A secure recipe must remove untrusted `Forwarded` and
`X-Forwarded-*` headers, set one canonical host/scheme, prevent direct backend
reachability, protect the backend with a Unix socket, loopback boundary or mTLS,
constrain request/body/time limits, and configure WebSocket behavior deliberately.
Backend HTTPS does not remove Apache's trust. Those extra trust and configuration
obligations make Apache termination the alternative rather than the baseline.

### Private loopback and SSH

The private transport direction is sound only as a path boundary. Verified SSH
protects and authenticates the tunnel endpoint. Loopback prevents remote network
reachability. Neither fact authorizes a browser request. Finding 5's explicit
session bootstrap, exact-origin validation and non-cookie sole credential are the
minimum remaining B3 work.

## Pairing, revocation and recovery assessment

The Noise pairing direction is suitable for the agreed 256-bit copy/paste secret.
The final B1 transcript must bind the full protocol/version, public selector,
request identity, initiator/responder roles, durable server identity, portable
identity key when present, new per-server access key and both possession proofs.
Owner approval must bind the same immutable request and selected grant set before
commit. Durable client save, atomic server commit and receipt retrieval are three
distinct events; cached responses may make an identical authenticated retry
idempotent but must never replay mutable handshake state or create a second grant.
The official [Noise specification section 9](https://noiseprotocol.org/noise.html#pre-shared-symmetric-keys)
defines PSK mixing, but the application still owns selectors, transcript context,
attempt accounting, activation and replay state.

The revocation policy is coherent within the stated limits. Its implementation
needs one linearization rule: a request must register its verified client,
authorization epoch and cancellable work before the first upstream side effect,
under synchronization with grant removal/revocation. Revocation advances the
epoch, prevents new registration and stops further response/artifact delivery.
Work that has already crossed its documented upstream commit boundary can finish
at the upstream service and retain its resource slot until it actually exits;
the design correctly does not promise to reverse that effect. Rotation activation
may let previously admitted work finish only while the same live access checks
permit it. Revocation and owner recovery take precedence over stale rotation or
receipt completion.

The backup envelope direction is also sound as a candidate. Decrypt into bounded
private staging, verify the canonical signed manifest against public verification
data from the external recovery record, and verify all names, types, lengths and
hashes before extraction or activation. A key or certificate supplied only by the
incoming bundle cannot authenticate it. Snapshot data, configuration, secrets,
authorization state and key generations need one commit identity so a crash cannot
activate a mixed generation. Finding 6 addresses the remaining post-activation
split-brain boundary. A valid older signed bundle remains intentionally restorable;
the resulting rollback of grants and deadlines is an accepted operator-visible
limitation rather than a claim of freshness.

## Accepted limitations

These are explicit limits, not findings to silently redesign:

- The trusted operator/server can read upstream secrets and plaintext.
- Theft of the lightweight client credential directory permits impersonation to
  the extent of the stolen keys. For D103, that includes presenting the portable
  identity in a new enrollment request, but not receiving grants automatically.
- Immediate plaintext stdin can deliver an authentic prefix before a later
  integrity, truncation or transport failure is known. A tool can act irreversibly
  on that prefix. Final failure reporting does not undo the action.
- A deliberately restored stale snapshot can reinstate old grants, revocations
  and deadlines. The date warning and trusted operator choice remain the agreed
  control; this review does not add mandatory grant review or paused mode.
- Completed upstream effects and already delivered data cannot be recalled.
- General connection-spam fairness is not guaranteed. Finding 4 is narrower: an
  unauthenticated peer should not consume a specific request's semantic attempts.
- U53's 64-connection setting remains unapproved. This review selects no numeric
  connection default.

## Optional hardening

- Validate the outer WSS certificate when a deployment has a stable public PKI
  name, while retaining mandatory inner verification and no fallback.
- Add deployment checks for unexpected direct backend reachability and unsafe
  proxy headers for the Apache alternative.
- Record a protected external high-water mark for backup generation to improve
  stale-bundle warnings. This must not make an intentionally selected offline
  older restore impossible.

## Minimum next work

Complete the blockers in this order:

1. **B2/B1:** write the role/profile and owner-recovery contract, then the complete
   enrollment and recovery state machine. Include transcript fields, separate
   possession proofs, authenticated attempt accounting, commit/retrieval
   idempotency and crash/replay traces.
2. **B2/B3:** specify ordinary versus restricted late-rotation verification and
   the revocation linearization point. Prove that no expired/revoked credential
   reaches ordinary ASGI caller context.
3. **B3:** implement one bounded TLS-over-WebSocket adapter contract and the
   private browser session contract. Test proxy substitution, redirect, malformed
   framing, disconnect, cancellation, revocation and admission ownership.
4. **B4:** select the artifact envelope and authenticated-descriptor profile,
   including when independent signatures are and are not required. Test all four
   processing modes for substitution, truncation, finalization and partial stdin.
5. **B5:** define snapshot ownership, signed framing, activation fencing,
   generation transitions and purpose-limited migration handoff. Test crash and
   rollback at each activation boundary.

Do not add short-code PAKE, dual public-admin topology, TLS resumption/0-RTT,
cross-server registries, periodic server-root rotation, broader credential
archives or a second client-policy layer to complete this work.

## Fidelity and evidence limits

I independently checked the four prior fidelity corrections against their cited
source excerpts:

- D98 unenrollment cleanup is present at design lines 265–267.
- D61 surviving-client-identity wording is present at lines 345–347.
- D85 requires both client documentation and operation help at lines 311–312.
- D4's future UI pending/approve/reject controls are present at lines 29–32.

I found no additional fidelity mismatch during this architecture review. That is
a targeted conclusion, not proof that the 6,602-line history was losslessly
summarized. I did not read that full archive; I inspected only the cited decision
excerpts and portable-identity synthesis needed to validate these points.

The retained Noise JSON reports 13 passing compatibility test groups, exact-profile
cross-language evidence and bounded platform checks. I did not rerun experiments,
install dependencies, scan dependencies or test a composed enrollment/tunnel.
The current source still explicitly warns that caller authentication is absent,
and its Go client contains a general insecure-TLS constructor. Those observations
confirm that the proposed dedicated identity boundary is not implemented; they
do not establish an exploit in the proposed design.

No conclusion here certifies the complete protocol, implementation, dependencies,
deployment or release as clean.
