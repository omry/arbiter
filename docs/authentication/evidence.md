# Authentication design: decision and evidence index

Current reading scope: the [requirements baseline](requirements.md) is the
requirements surface for the initial stamp. This index preserves provenance
and historical dispositions in the earlier synthesis; candidate mechanisms and
old readiness statements are not extra requirements. The staged review does
not require treating the entire archive as a current normative baseline.

Supporting evidence, 2026-10-20. The historical consolidation review used the
[brief](reviews/initial-brief.md) and [earlier synthesis](design.md).
For the current stamped requirements, HLD and implementation plan, start with
the [authentication overview](README.md). This index and the archives preserve
provenance; they do not define the target of a new DBB review.

## Scope of the evidence

The original draft and interview are [byte-preserved](../archive/authentication-and-enrollment/README.md).
Their [manifest](../archive/authentication-and-enrollment/manifest.json) records
SHA-256 hashes and decision source lines. Those hashes prove snapshot preservation,
not the truth or security of the recorded design. Historical links inside the
snapshots retain their original paths. Later decisions take precedence as mapped below.

Current source observations: [server configuration](../../server/src/arbiter_server/config.py),
[HTTP application](../../server/src/arbiter_server/main.py),
[operation catalog](../../server/src/arbiter_server/services.py),
[core limits](../../server/src/arbiter_server/limits.py),
[Go HTTP client](../../client/go-cli/internal/arbiterhttp/client.go) and
[CLI/download paths](../../client/go-cli/internal/cli/cli.go). No product tests or live
service calls ran for this consolidation. The existing
[security assessment](../prerelease-security-assessment.md) concerns the assessed
implementation, not approval of this proposed authentication architecture.

## Experiment evidence and limits

The [tracked Noise probe](../../experiments/pairing-noise/README.md) and archived
draft's compatibility section record the isolated checks performed on 2026-10-19:

- Exact candidate: Noise_NNpsk0_25519_ChaChaPoly_SHA256; Python noiseprotocol 0.3.1
  and Go flynn/noise 1.1.0. Thirteen compatibility groups passed locally on Linux
  amd64 and in the recorded native Windows ARM64/Python 3.13 job.
- Go peers cross-compiled for six OS/architecture combinations; that is not native
  execution on six targets. A separate Linux probe passed with the Go 1.24 floor
  and reproduced all six messages/hash of the exact-profile reference vector.
- Python wheel-only resolution passed 25/30 checked combinations. The native
  Windows ARM64 result used its build/cache setup and does not prove public wheel
  availability or all Python versions. The full release matrix remains unverified.
- The Python wrapper is old. Missing PSK can downgrade the Go setup unless rejected;
  first-message replay still needs application state. The Python plaintext-size
  defect was reproduced; enforcing the standard external boundary is a proposal.
- The recorded Go advisory scan found no reachable symbol in the probe, not an
  all-dependencies-clean result or an independent audit. No complete enrollment,
  tunnel/ASGI/proxy, artifact encryption, backup or restore integration was tested.
- age/pyrage and JWS/ES256/PyJWT remain library/format candidates. Python GIL,
  flush/finalization, packaging and native execution concerns are unverified
  integration obligations, not reproduced security findings or adopted dependencies.

Raw local artifacts: `temp/pairing-noise-poc/`, including `test-results.json`,
`platform-results.json`, `dependency-screen-summary.json`,
`windows-arm64-ci-39146689439/` and `go124-assessment/`. Detailed pins, source
references and limits are retained in the archived draft; no rescan was run here.

## Decision coverage

Every D1–D104 has one row. Source lines refer to the unchanged archived
[interview](../archive/authentication-and-enrollment/interview-2026-10-20.md).
A row maps provenance and disposition, not a claim that its mechanism is complete.
Workflow-only interaction preferences stay with the updated interview prototype.

| Decision | Current disposition | Preserved outcome and current section | Interview line |
| --- | --- | --- | --- |
| D1 | Deferred | [Confidential execution remains future research.](design.md#scope) | 98 |
| D2 | Agreed | [Single trusted operator supplies upstream credentials.](design.md#scope) | 102 |
| D3 | Agreed | [Clients have independently controllable identity and credentials.](design.md#actors-trust-and-credentials) | 107 |
| D4 | Future interface | [Browser UI will review pending enrollment; initial delivery uses CLI.](design.md#native-transport-and-browser-administration) | 111 |
| D5 | Agreed | [Enrollment normally closed; bounded operator-opened windows.](design.md#enrollment-and-incomplete-attempts) | 115 |
| D6 | Agreed | [Initial trusted administration uses local CLI or SSH.](design.md#enrollment-and-incomplete-attempts) | 121 |
| D7 | Agreed | [Account grants are mutable after enrollment.](design.md#account-grants-and-revocation) | 126 |
| D8 | Agreed | [Bidirectional trust precedes service authority.](design.md#enrollment-and-incomplete-attempts) | 130 |
| D9 | Agreed | [Owner approves agents and account grants.](design.md#account-grants-and-revocation) | 134 |
| D10 | Constraint | [Use established identity/signature mechanisms; do not invent derivation.](design.md#finite-blockers-and-review-boundary) | 139 |
| D11 | Agreed; refined by D41 | [Hermes/OpenClaw compatibility through Arbiter tooling; native external-ID reuse later.](design.md#scope) | 143 |
| D12 | Superseded by D36/D38/D77 | [Earlier server nonce/manual return is not the current pairing flow.](design.md#enrollment-and-incomplete-attempts) | 149 |
| D13 | Revised by D33 | [Lightweight owner-approved provisioning creates durable identity during enrollment.](design.md#enrollment-and-incomplete-attempts) | 154 |
| D14 | Agreed | [Closed enrollment gives operator-opening guidance.](design.md#enrollment-and-incomplete-attempts) | 158 |
| D15 | Agreed | [Enrollment is available after server setup.](design.md#enrollment-and-incomplete-attempts) | 162 |
| D16 | Agreed | [Minimize operator interactions without losing trust or approval.](design.md#enrollment-and-incomplete-attempts) | 168 |
| D17 | Agreed; mechanism open | [Register trusted owner authority before attributing agent ownership.](design.md#actors-trust-and-credentials) | 3287 |
| D18 | Agreed | [Provide a lightweight identity path.](design.md#scope) | 3281 |
| D19 | Agreed | [No mandatory central external identity authority.](design.md#scope) | 175 |
| D20 | Extensibility; D41 scopes delivery | [Other providers may fit later; no speculative framework required.](design.md#scope) | 180 |
| D21 | Corrected requirement | [Lightweight setup required; self-identification was only a candidate.](design.md#scope) | 184 |
| D22 | Agreed | [No extra identity application or independently operated client daemon.](design.md#scope) | 191 |
| D23 | Agreed | [Owner-authorized lost-key replacement preserves durable agent identity.](design.md#client-key-rotation-recovery-and-server-replacement) | 198 |
| D24 | Agreed; reconciliation remains B2 | [Verified owner recovery preserves grants/denials without another operator approval; reconcile later local-key rules explicitly.](design.md#actors-trust-and-credentials) | 206 |
| D25 | Agreed | [Only the owner can recover owner identity.](design.md#actors-trust-and-credentials) | 212 |
| D26 | Agreed | [Agents cannot create durable identities without owner authorization.](design.md#actors-trust-and-credentials) | 217 |
| D27 | Agreed; narrower access deferred | [Helpers share parent identity and permissions.](design.md#actors-trust-and-credentials) | 224 |
| D28 | Deferred | [Reploy-isolated broker is a future integration.](design.md#actors-trust-and-credentials) | 232 |
| D29 | POC limit superseded by D30 | [Key warning remains guidance; lightweight acceptance extends beyond POC.](design.md#actors-trust-and-credentials) | 241 |
| D30 | Accepted risk | [Agent-readable lightweight keys; doctor warns once a secure alternative exists.](design.md#actors-trust-and-credentials) | 250 |
| D31 | Accepted risk; mitigation deferred | [Sandbox/broker is intended mitigation, not implemented protection.](design.md#actors-trust-and-credentials) | 259 |
| D32 | Agreed; mechanism open | [Portable ownership across independent servers; local grants remain independent.](design.md#actors-trust-and-credentials) | 270 |
| D33 | Agreed | [Trusted AMI server represents owner and provisions on approval.](design.md#actors-trust-and-credentials) | 277 |
| D34 | Agreed name | [Arbiter-managed identity, AMI; no CLI/config spelling implied.](design.md#scope) | 287 |
| D35 | Agreed | [The owner initiates via client; software cannot authorize itself.](design.md#enrollment-and-incomplete-attempts) | 290 |
| D36 | Agreed | [Direct completion, bounded junk cleanup and no duplicate enrollment.](design.md#enrollment-and-incomplete-attempts) | 298 |
| D37 | Fingerprint handoff superseded by D38 | [Public certificates remain optional; current pairing has no second fingerprint ceremony.](design.md#native-transport-and-browser-administration) | 310 |
| D38 | Agreed; entropy revised by D77 | [One copied secret authenticates pairing and automatic trust exchange.](design.md#enrollment-and-incomplete-attempts) | 319 |
| D39 | Optional deployment | [Apache/Certbot may supply browser HTTPS trust.](design.md#native-transport-and-browser-administration) | 332 |
| D40 | Agreed | [Durable identity independent of outer certificates and proxies.](design.md#native-transport-and-browser-administration) | 339 |
| D41 | Agreed delivery scope | [AMI first; external providers deferred.](design.md#scope) | 350 |
| D42 | Agreed format; profile open | [X.509 identity credentials; complete issuer/purpose profile still B2.](design.md#actors-trust-and-credentials) | 356 |
| D43 | Agreed | [Routine client rekey without fresh owner approval is not compromise recovery.](design.md#client-key-rotation-recovery-and-server-replacement) | 362 |
| D44 | Agreed | [Rotate when connecting; owner pairing recovers existing enrollment after inactivity/loss.](design.md#client-key-rotation-recovery-and-server-replacement) | 370 |
| D45 | Agreed default | [180-day inactivity cutoff, not arbitrary network traffic.](design.md#client-key-rotation-recovery-and-server-replacement) | 385 |
| D46 | Agreed default | [30-day access-key threshold, checked on authenticated connection.](design.md#client-key-rotation-recovery-and-server-replacement) | 391 |
| D47 | Agreed | [All ordinary clients enroll, including localhost callers.](design.md#native-transport-and-browser-administration) | 397 |
| D48 | Agreed | [Artifact metadata/download requires creator identity plus nonce.](design.md#artifacts-and-client-processing) | 403 |
| D49 | Future feature; current compatibility | [Preserve future upstream prompt-injection inspection.](design.md#artifacts-and-client-processing) | 410 |
| D50 | Agreed compatibility | [Retained server artifacts remain independently rescan-capable.](design.md#artifacts-and-client-processing) | 416 |
| D51 | Agreed; mechanism open | [Server-decryptable storage and recipient-encrypted downloads/saved files.](design.md#artifacts-and-client-processing) | 422 |
| D52 | Agreed name | [Explicit persistent plaintext export is artifact save-decrypted.](design.md#artifacts-and-client-processing) | 429 |
| D53 | Agreed | [Plaintext tool temp file requires an explicit flag.](design.md#artifacts-and-client-processing) | 437 |
| D54 | Agreed | [Hide ungranted account information in discovery and lookup.](design.md#account-grants-and-revocation) | 445 |
| D55 | Agreed | [Only minimal health and pairing bootstrap are anonymous.](design.md#account-grants-and-revocation) | 451 |
| D56 | Agreed | [Revocation terminates delivery/access, not committed upstream effects.](design.md#account-grants-and-revocation) | 458 |
| D57 | Rejected extra layer | [Do not add a second client policy/operation allowlist.](design.md#account-grants-and-revocation) | 466 |
| D58 | Agreed existing flow | [Add account-access authorization before existing account-policy enforcement.](design.md#account-grants-and-revocation) | 473 |
| D59 | Agreed scope; hosting deferred | [One owner now; future owners have isolated accounts/policies.](design.md#scope) | 482 |
| D60 | Superseded by D78 | [Proxy-readable native API is not the current confidentiality requirement.](design.md#native-transport-and-browser-administration) | 491 |
| D61 | Agreed | [Consistent application backup restores authority; without it pair a new server.](design.md#backup-restore-undo-and-migration) | 500 |
| D62 | Agreed | [Encrypted recovery bundle also supports host migration.](design.md#backup-restore-undo-and-migration) | 508 |
| D63 | Refined by D64/D101 | [Recovery belongs to operator; no identical identity/encryption-key reuse selected.](design.md#backup-restore-undo-and-migration) | 518 |
| D64 | Agreed; passkeys deferred | [Operator-held private backup key outside server/agent/sync destination.](design.md#backup-restore-undo-and-migration) | 526 |
| D65 | Agreed; rejected extra gate | [Restore saved snapshot with date warning; no mandatory permissions review.](design.md#backup-restore-undo-and-migration) | 537 |
| D66 | Agreed upgrade policy | [Discard only legacy server-managed ownerless artifacts; no deletion executed.](design.md#artifacts-and-client-processing) | 546 |
| D67 | Agreed | [Bundle includes actual configured upstream secrets and policies.](design.md#backup-restore-undo-and-migration) | 554 |
| D68 | Review requirement | [Interview approval is not finalization; DBB review/disposition required.](design.md#evidence-and-readiness) | 566 |
| D69 | Superseded by D77 | [Short-code PAKE is not the first-delivery requirement.](design.md#enrollment-and-incomplete-attempts) | 1258 |
| D70 | Partly superseded by D77 | [Eight digits replaced; ten-minute lifetime and five attempts retained.](design.md#enrollment-and-incomplete-attempts) | 1243 |
| D71 | Agreed default | [Twenty uncommitted requests, not an enrolled-client cap.](design.md#enrollment-and-incomplete-attempts) | 1181 |
| D72 | Agreed | [Distinct access keys per server; no registry/cross-server replacement authority.](design.md#actors-trust-and-credentials) | 1112 |
| D73 | Deferred extension | [No independently revocable installation credentials under one identity yet.](design.md#actors-trust-and-credentials) | 1020 |
| D74 | Agreed | [Preparation starts fixed grace; confirmation activates; no retry extension.](design.md#client-key-rotation-recovery-and-server-replacement) | 884 |
| D75 | Agreed default | [24-hour grace, then restricted unattended completion before inactivity cutoff.](design.md#client-key-rotation-recovery-and-server-replacement) | 848 |
| D76 | Requirement; partial evidence | [Early Python/Go POC required; platform and maintenance limits remain explicit.](design.md#evidence-and-readiness) | 768 |
| D77 | Agreed replacement | [256-bit copy-paste secret; short codes deferred.](design.md#enrollment-and-incomplete-attempts) | 660 |
| D78 | Agreed replacement for D60 | [Native authenticated encryption survives forwarding proxy; browser remains separate.](design.md#native-transport-and-browser-administration) | 634 |
| D79 | Agreed setup priority | [Private deployment needs no public DNS/certificate/exposure.](design.md#scope) | 616 |
| D80 | Agreed direction | [Keep pairing approach; defer convenience improvements.](design.md#enrollment-and-incomplete-attempts) | 601 |
| D81 | Agreed direction; session open | [Private browser loopback/verified SSH; no self-signed browser bypass.](design.md#native-transport-and-browser-administration) | 588 |
| D82 | Review obligation | [DBB recommends one public admin topology, not assumed dual support.](design.md#native-transport-and-browser-administration) | 576 |
| D83 | Agreed | [Client chooses streaming or verified plaintext temp-file processing.](design.md#artifacts-and-client-processing) | 1731 |
| D84 | Agreed optional mode | [Verified encrypted temp download followed by plaintext stdin.](design.md#artifacts-and-client-processing) | 1745 |
| D85 | Agreed guidance | [Explain partial-input, storage, startup and plaintext-file tradeoffs.](design.md#artifacts-and-client-processing) | 1757 |
| D86 | Agreed; artificial gate withdrawn | [Ongoing backups from startup, not a before-first-enrollment decision.](design.md#backup-restore-undo-and-migration) | 1817 |
| D87 | Agreed orchestration boundary | [External job handles scheduling/off-host storage; server exports consistently.](design.md#backup-restore-undo-and-migration) | 1835 |
| D88 | Agreed; remote export deferred | [First export interface is trusted local/SSH administration.](design.md#backup-restore-undo-and-migration) | 1883 |
| D89 | Agreed | [Restore full saved Arbiter configuration; ordinary edits on new host.](design.md#backup-restore-undo-and-migration) | 1959 |
| D90 | Agreed | [Undo restores coherent pre-restore state after attempted startup too.](design.md#backup-restore-undo-and-migration) | 1966 |
| D91 | Agreed reaffirmation | [Keep undo; staging does not replace it.](design.md#backup-restore-undo-and-migration) | 2005 |
| D92 | Agreed reaffirmation | [Preserve server migration journey and paired-client testing need.](design.md#backup-restore-undo-and-migration) | 2010 |
| D93 | Direction accepted by D94 | [Authenticated old-source URL handoff; exact protocol remains B5.](design.md#backup-restore-undo-and-migration) | 2063 |
| D94 | Agreed | [Maintenance window and single-active migration; no zero-downtime guarantee.](design.md#backup-restore-undo-and-migration) | 2078 |
| D95 | Deferred | [Clusters, config/artifact synchronization and redundancy are future work.](design.md#scope) | 2115 |
| D96 | Agreed | [Stale restore/undo may require owner-approved repair for affected clients.](design.md#client-key-rotation-recovery-and-server-replacement) | 2169 |
| D97 | Agreed diagnostics | [Distinguish verified rejection from wrong server; never invent a cause.](design.md#client-key-rotation-recovery-and-server-replacement) | 2176 |
| D98 | Agreed bounded fallback | [Current plus one predecessor; authentication-only fallback, no replay/downgrade.](design.md#client-key-rotation-recovery-and-server-replacement) | 2253 |
| D99 | Agreed retention | [One latest undo point, no automatic expiry; failed restore preserves it.](design.md#backup-restore-undo-and-migration) | 2329 |
| D100 | Agreed clarification | [Undo after use remains possible with very prominent loss warnings.](design.md#backup-restore-undo-and-migration) | 2355 |
| D101 | Agreed shape; format open | [One recovery file contains private decryption plus trusted public verification data.](design.md#backup-restore-undo-and-migration) | 2435 |
| D102 | Agreed | [Grants follow plugin/account names, including repointed upstream accounts.](design.md#account-grants-and-revocation) | 2496 |
| D103 | Provisional, DBB-dependent | [Separate portable owner-certified identity key grants no ordinary service access.](design.md#actors-trust-and-credentials) | 2571 |
| D104 | Agreed | [Operator-triggered server-key replacement; verified adoption; no periodic schedule.](design.md#client-key-rotation-recovery-and-server-replacement) | 2623 |

## Not approved or performed

U53's proposed 64-connection default was never answered. Exact certificate,
wire, encryption/envelope, local persistence, session and recovery profiles remain
B1–B5. No percentage or exhaustion state establishes design completion. This
package does not waive DBB, resolve those gaps, select production dependencies,
launch a reviewer, authorize product implementation or alter the original
interview's saved pins and counters.
