# DBB re-review: authentication and enrollment repairs

Reviewed 2026-10-20. This is my independent, bounded defensive architecture
assessment of the repaired design package. It is not a protocol,
implementation, dependency, deployment, or release audit.

## Verdict

The repairs close four of the five mechanism gaps at design level. H1, H2, H3,
and H4 now state defensible trust boundaries without changing the approved
behavior. H5 still has one medium-severity gap: a reused loopback HTTP origin can
be controlled by browser state installed by a prior local listener, so the
fragment bootstrap can reach hostile code before Arbiter can enforce `Host`,
`Origin`, bearer, or CSRF checks.

The sixth original finding is an accepted operational limitation, not a
cryptographic fix. The documented migration/restore procedure is sufficient for
the stated cooperative-server, infrequent-migration model. It does not prevent a
mistaken restart, a compromised old host, or an operator from running two writable
copies.

The repaired architecture may proceed after the H5 origin-lifetime correction.
B1–B5 still require their stated protocol, backend, interoperability, crash, and
admission evidence. This verdict does not authorize implementation and is not
complete security signoff.

## Reviewed input identities

I reviewed these exact bytes. The first five match the frozen copies in
`temp/reviews/authentication-repair-2026-10-20/review-inputs/`.

| Input | SHA-256 |
| --- | --- |
| `docs/authentication-and-enrollment-rereview.md` | `f6d0041f2ddde7ef10d444ac4cb5f4b7178191f4326d11a3dac6f657058c8b49` |
| `docs/authentication-and-enrollment.md` | `518cf199e5861f5414e67df2fcc55ddd98e48ebe164a6d64fa03612439983b1c` |
| `docs/authentication-and-enrollment-security-contracts.md` | `ea60ef60cb06d8481284928433fe4ee2984dd704682c2014bfa015b67a5655c0` |
| `docs/authentication-and-enrollment-dbb-review.md` | `9b6f8fa8f9152b37dffb3ed927e077902791399d7bba5f79c0f59ef57c62e644` |
| `docs/authentication-and-enrollment-evidence.md` | `67634ab715b51d43de369c2dd3a9828e1de5e530fe4f4b43bc031b06e57e7762` |
| `docs/archive/authentication-and-enrollment/interview-2026-10-20.md` (targeted decision excerpts only) | `a1c9e4058da8ba970fdd52d95f3561ad01a327b0d7d90c7d890afedab2a278fe` |
| `temp/reviews/authentication-repair-2026-10-20/verification.json` | `6c4b788088b7752f96055a5daca021fcf2562738710d998f28b8063d03ba8109` |

## Disposition of the six original findings

| Original finding | Disposition | Evidence and limit |
| --- | --- | --- |
| 1. Owner recovery authority | **Resolved at design level.** | H1 separates portable identity, per-server access, server identity, and owner recovery. A fresh server-specific owner statement plus exact replacement-key possession is approval under D24; it preserves grants and denials and requires no second server approval. Local owner-approved pairing remains the simple D44 path. Exact JWS/X.509 profiles and transition traces remain B2. |
| 2. Restricted late rotation | **Resolved at design level.** | H2 leaves ordinary mTLS strict and creates a server-authenticated, non-HTTP control carrier. It binds separate old-key and exact candidate-key proofs to the connection and pending record. It permits expired old leaf credentials and reissues an expired candidate only for the same candidate public key, before the unchanged inactivity cutoff. It does not reopen or extend the 24-hour grace. A retained completion record covers lost replies. Library and negative-route evidence remains B2/B3. |
| 3. Artifact origin authority | **Resolved at design level.** | H3 retains the descriptor received through the verified inner channel, treats recipient encryption as confidentiality rather than origin proof, and covers all four approved processing modes. Immediate stdin keeps its honest partial-prefix guarantee. Verified modes complete descriptor, length, digest, and cryptographic finalization checks before tool start or final publication. No archive/signature feature was added. Backend immutability and TOCTOU evidence remain B4. |
| 4. Pairing attempt accounting | **Resolved at design level.** | H4 requires a fixed non-empty PSK-authenticated Noise first-flight payload before charging an attempt. It deduplicates exact authenticated replays, serializes a live exchange, caps semantic records at five, expires them with the original request, and bounds unauthenticated connection/byte/crypto work separately. It does not promise general spam fairness or adopt U53. B1 must still demonstrate the real profile and concurrent replay behavior. |
| 5. Private browser authorization | **Still open.** | H5 correctly makes the bearer, not loopback or SSH, the authority and specifies exact `Host`/`Origin`, explicit authorization, CSRF, one-use bootstrap, bounded lifetime, and verified SSH. It does not isolate a reused loopback origin from persistent hostile browser state. The concrete gap is below. |
| 6. Duplicate writable authority | **Accepted operational limitation.** | The procedure stops and disables the source, restores with the destination stopped, permits only non-mutating handoff, and starts one selected installation. That is coherent under cooperative servers and rare migration. It deliberately supplies no cryptographic fencing, client generation, signed takeover, or persistent retirement. Older restore and post-use undo remain available with warnings. |

## H1 authority assessment

The purpose-separated recovery signer is justified by the requirements that a
compromised server key cannot nominate its successor, portable identity cannot
replace a local access key, and one owner authorization can preserve D24
continuity without another approval ceremony at every server. Reusing the backup
decryption key or a server-held AMI issuer would collapse those purposes. Keeping
the signer in the one existing protected recovery file adds no second file,
service, agent credential, or ordinary local-repair step. It therefore preserves
the simple D64/D101 operator procedure and D44 pairing path.

It does add a second private key and materially broadens that file's authority.
With the file, reachable registered servers, and valid recovery requests, an
attacker can sign exact attacker-held replacement keys for live enrollments or a
server-identity repair. Previously, possession of the file's private decryption
key alone authorized decryption of available bundles, not live-server recovery.
Purpose separation limits accidental key reuse but does not reduce this custody
impact. This is the inherent cost of the independent owner authority H1 needs,
not an implementation defect. The adoption documentation must describe the file
as both backup-decryption and live recovery authority. The current candidate
does not conflict with D101's narrower statement that adding *public* verification
metadata added no secret; H1 is an explicit later schema extension.

## Remaining finding

### Medium — a reused loopback origin can retain hostile browser control

- **Class:** incomplete browser-origin isolation in H5.
- **Affected invariant:** only the Arbiter page launched by the trusted CLI may
  receive and exchange the one-use fragment bootstrap.
- **Preconditions:** a hostile local listener previously serves the same
  scheme/host/port and induces this browser profile to visit it while Arbiter is
  not bound there. It registers a service worker or persistent response state for
  that origin. Arbiter later reuses the origin and the owner opens a valid
  bootstrap URL. This does not require compromise of the currently running
  Arbiter server, SSH connection, or bearer.
- **Attack:** the browser can route the navigation through the persistent worker,
  which supplies attacker-controlled page code. The fragment is not sent in the
  HTTP request, but code running in the resulting page can read it and exchange
  it. Arbiter's exact `Host` and `Origin` checks see the expected origin. Atomic
  one-use consumption then gives the attacker the admin bearer or races the owner.
- **Evidence:** H5 uses one configured loopback origin and places the secret in
  the fragment at
  [security contracts, lines 211–229](../security-contracts.md#h5--private-browser-administration).
  It protects against a different local port but does not require a fresh browser
  origin. The [Service Workers specification](https://www.w3.org/TR/service-workers/#service-worker-registration-lifetime)
  requires registrations to persist until explicitly unregistered and permits
  them to control later clients in scope. It also permits service workers for
  loopback development origins; the [Secure Contexts specification](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy)
  treats `127.0.0.0/8`, `::1`, and conforming `localhost` names as potentially
  trustworthy.
- **Impact:** unauthorized enrollment approval/rejection, grant changes, recovery,
  or other browser-admin actions available to the session.
- **Smallest correction:** reserve the listener before producing the URL and use
  a fresh, unpredictable browser origin for each CLI launch, such as a newly
  selected loopback port that is not reused as stable application identity.
  Retain the existing one-use fragment, explicit bearer, CSRF, exact
  `Host`/`Origin`, loopback binding, and SSH host verification; the fresh origin
  is isolation, not authentication. If the implementation cannot provide a fresh
  origin, it needs an equally strong mechanism that proves no pre-existing worker
  or cached page can control the bootstrap navigation before exposing the secret.
- **Required evidence:** test a prior service worker/cache registration at the
  old origin, origin reuse, port-binding races, SSH forwarding, token replay, and
  expiry/restart. Confirm that no protected API becomes usable without the
  explicit bearer.

## Residual boundaries, not additional findings

- H2's `tls-exporter` access, fixed control framing, exact old/candidate proof
  profile, cutoff races, and lost-response behavior are specified but untested.
  Failure to expose the binding is a compatibility-gate failure, not permission
  to weaken ordinary mTLS.
- H3 does not promise atomic tool execution. Pipes, disks, and tools can still
  fail after launch. Under its stated trusted client OS/user boundary, the private
  file and retained open descriptor remove attacker-selected pathname reopening.
  B4 must ensure the chosen staging location has no in-scope writable alias from
  successful first-pass verification through the second pass. If that cannot be
  shown, the ciphertext must be made an immutable process-owned snapshot before
  tool launch. Rehashing only at the end of the second pass would detect mutation
  too late to preserve the verified-mode guarantee; per-chunk descriptor hashes
  are unnecessary when the verified bytes are stable.
- H4's contract is stronger than the retained isolated POC: the POC used a
  non-empty first message and demonstrated PSK/tamper rejection, but it did not
  implement semantic attempt ownership or bounded replay records. I did not rerun
  it.
- H1's fixed schemas, verifier selection, transition history, and cross-language
  fixtures remain B2. H3's envelope/backend remains B4. Restore framing,
  activation, undo, and handoff evidence remains B5.
- The procedure for finding 6 assumes cooperative operators and hosts. A
  compromised or mistakenly restarted old host can still fork authorization and
  consumption state. That is the accepted limit of the chosen control, not a
  claim of automated prevention.

## Checks performed and signoff limit

I compared all five current review documents byte-for-byte with their frozen
copies, traced the six original findings into H1–H5 and the migration procedure,
checked targeted D24, D44, D64, D74–D75, D81, D83–D85, D94, D96, D98–D101,
D103, and D104 provenance, and inspected the retained Noise POC only to confirm
what its first-flight tests did and did not establish. I ran no product tests,
protocol tests, scans, installations, or live operations.

This assessment signs off only the four design-level dispositions and the stated
operational acceptance. Finding 5 remains open. Nothing here establishes complete
protocol approval, implementation readiness, dependency safety, deployment
safety, or release readiness.
