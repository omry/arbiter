# DBB review: authentication high-level design

Reviewed 2026-10-20. This is my bounded defensive-security assessment of the
authentication high-level design at HLD maturity. It assesses selected
mechanisms, state ownership, trust boundaries, failure behavior and feasibility.
It is not a wire-protocol, implementation, dependency, platform, deployment or
release audit.

## Verdict

**Pass at HLD level.** The exact reviewed design preserves R01–R25, makes the
required architecture and authority choices, and is sufficiently coherent to be
design-stamped and used as the basis for a separately reviewed implementation
plan. I found no blocking HLD security finding and no silently deferred
requirement or invented authority.

This verdict applies only to
[`docs/authentication/high-level-design.md`](../high-level-design.md) with SHA-256
`b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`,
against the normative
[`docs/authentication/requirements.md`](../requirements.md) with SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`.
A content change requires review of the changed bytes before a new verdict.
This report does not create the durable design stamp.

## Findings and required HLD fixes

**Blocking findings: none. Required HLD fixes: none.**

The following assessment explains why the previously sensitive boundaries are
closed at design level. The cited later evidence remains mandatory, but its
absence is not an HLD defect.

| Boundary | HLD assessment |
| --- | --- |
| Role separation and recovery | The role matrix and verifier rules separate owner issuer, server authority, portable identity, per-server access, server TLS, artifact, backup and recovery keys ([credential roles](../high-level-design.md#credential-roles-and-verification), lines 81–125). Portable identity uses a separate issuer/trust path and exact role/ID checks and is never accepted by ordinary native TLS. This HLD intentionally selects standard `clientAuth` plus that separate trust path rather than the earlier candidate dedicated EKU; that remains role-separated if every verifier enforces the stated issuer, SAN, purpose and live-state checks. Independent owner recovery binds the exact server, enrollment, revision, replacement key, purpose and fresh request, preserves denials, and cannot rely only on a compromised server key ([owner recovery](../high-level-design.md#owner-recovery-and-server-replacement), lines 169–192). |
| Enrollment commit and lost response | The client durably saves provisional keys, server trust and the prepared result before its ready proof. The server then conditionally commits identity, credential, grants and one completion result in one authority transaction ([pairing](../high-level-design.md#b1--pairing-and-durable-activation), lines 203–247). A lost receipt is reconciled through remembered server trust and fresh proof by the already approved access key; it cannot create a second identity, grant or activation. Restart invalidates only uncommitted PSK state. |
| Rotation, expiry and rollback assistance | Preparation fixes one 24-hour deadline before requesting a candidate; retries and withholding cannot move it. Ordinary verification remains strict. A separate server-authenticated control carrier binds the exact pending record, old and candidate keys, a fresh challenge and the TLS exporter, relaxes certificate time only for that transition, and rechecks inactivity, denial and revision in the activation transaction ([rotation](../high-level-design.md#rotation-and-completion), lines 146–165; [control carrier](../high-level-design.md#b3--native-transport-and-administration), lines 274–290). Predecessor use is one authentication-only attempt after authenticated rejection by the same verified server and cannot override denial, expiry, grace or inactivity. |
| Revocation races | One authority coordinator serializes live admission with denial. Revocation commits under that coordinator, prevents later admission, cancels affected work across connections and blocks later output while real worker ownership remains until cleanup ([state ownership](../high-level-design.md#state-and-concurrency-ownership), lines 51–75). Artifact HEAD/GET repeats live identity, grant and nonce checks and coordinates durable consume-before-open with the store ([artifact authorization](../high-level-design.md#b4--encrypted-artifacts-and-client-processing), lines 361–367). Already delivered bytes and committed upstream effects remain explicitly outside recall. |
| Proxy confidentiality and administration | The public WebSocket is only an untrusted byte carrier for inner TLS. The dedicated carrier sends no native credential, capability or application data before expected-server verification, accepts no redirect, inherited credential or ordinary-HTTP fallback, and cannot supply caller identity through proxy headers ([native transport](../high-level-design.md#b3--native-transport-and-administration), lines 249–290). Initial administration uses an OS-authenticated local socket/pipe or verified SSH to it. The future browser mechanism is a boundary only and is not presented as delivered UI. |
| Artifact origin and stable bytes | The authenticated inner API supplies the expected server, creator, recipient/key version, artifact, capability, lengths and hashes; age recipient encryption is not treated as origin proof ([artifact descriptor](../high-level-design.md#b4--encrypted-artifacts-and-client-processing), lines 349–383). Verified modes finish origin/content/finalization checks before tool start or publication. Encrypted-temp processing uses an immutable file arrangement or a bounded process-owned byte snapshot rather than a pathname reopen or late rehash (lines 385–415). Immediate stdin retains the approved authentic-partial-prefix limitation. |
| Backup, restore, undo and migration | Saved application generations bind authority, configuration, policies, keys and actual configured secrets. Export fences mutations and takes a coherent authority/configuration generation; the signed encrypted manifest is verified with external trusted metadata rather than a bundle-nominated key ([application generations](../high-level-design.md#application-generations-and-export), lines 421–460). Offline restore stages and verifies a complete generation before one durable active/undo pointer transition, preserves the prior undo point on failure, and retains lifecycle timestamps ([offline activation](../high-level-design.md#offline-activation-and-undo), lines 464–498). Migration keeps the approved cooperative one-active-copy procedure and purpose-limited, non-mutating URL handoff without inventing generations, takeover authority or fencing ([migration](../high-level-design.md#cooperative-migration-and-url-handoff), lines 500–518). |

## Required next-stage evidence

These are implementation-plan and implementation obligations, not unresolved
HLD findings:

- Define exact ASN.1, SAN URI, key-usage/EKU, JWS, control-message and transcript
  schemas. Negative fixtures must reject cross-role, cross-server, duplicate,
  expired, revoked, wrong-purpose and unknown-critical inputs. In particular, the
  implementation must prove that the portable `clientAuth` certificate's separate
  issuer/trust path cannot enter ordinary TLS caller context.
- Test enrollment and rotation commit/lost-response traces, fixed-deadline races,
  revocation versus admission/output, replay ownership, cancellation, overload,
  SQLite/file publication and crash recovery. The coordinator and publication
  rules are design commitments, not evidence that those races already pass.
- Demonstrate POSIX and Windows immutable ciphertext handling, plaintext staging,
  quota ownership, one-time GET failure, tamper/truncation, finalization and all
  four client processing paths. The platform backend must supply the stable-byte
  property the HLD requires.
- Specify and fault-test backup stream publication, strict record/manifest parsing,
  disk reservation, active/undo pointer durability, stopped-instance locking and
  crash recovery on every supported platform. Stale restore and procedural
  migration remain warning-based by approved requirement.
- Pin supported dependency versions only after advisory review and the Python
  3.10–3.14, Go, OS and CPU build/execution matrix. Future browser UI needs its
  own origin, IPv4/IPv6 binding, hostile browser-state and SSH-forwarding evidence
  before delivery.

Failure of one of these later checks must block the affected implementation or
narrow platform support. It must not be answered by weakening the HLD invariant.

## Evidence reviewed

I reviewed the probe source and recorded results rather than treating the pass
count as complete protocol proof.

| Evidence | SHA-256 | Assessment |
| --- | --- | --- |
| `temp/authentication-hld/probes/results.json` | `34071a913ba400e79242ebe0b4481214a953f14fb78815e0f62ab82b180e789b` | Fourteen synthetic Linux amd64 checks report pass. |
| `temp/authentication-hld/probes/check.py` | `71700a19fb0baa32a0d5995cec6c182ccda4e24bf7ce06a9f9fe01e653d8f8e6` | Python TLS/age/JWS and stable-snapshot probe driver. |
| `temp/authentication-hld/probes/peer.go` | `e83b57d06f68f26e612527072e72c73f108fab380d224c8b8484d393f5b11901` | Go TLS/exporter, age and JWS peer. |
| `temp/authentication-hld/probes/go.mod` | `218277d908ca5b4c9cba6c0a17baaeb967509613af6be0851ccb31298d54b5a0` | Go dependency selection used by the fixture. |
| `temp/authentication-hld/probes/go.sum` | `26631096168a44867435995cc7f64d5f9748780d188c5668acc35adc95c9b306` | Go dependency checksums used by the fixture. |
| `temp/authentication-hld/probes/peer-go124` | `544375bec11fa3d024385933be13a216a5f9b97b5889c9bb3b39bda7c6d5dc91` | Local binary metadata identifies Go 1.24.13, Linux amd64 and the recorded age/WebSocket versions. |
| `temp/authentication-hld/evidence.json` | `ef2c06c95c599c75d5786c7624021ba0dd01b76277fe25add8c1ed362181f5b2` | Run manifest tying the frozen HLD, source, results, install correction, Go-floor compile and vet record together. |

The probes support feasibility of TLS 1.3 over WebSocket with matching exporter,
ordinary expired-certificate rejection, purpose-separated expired-key control
proof and cross-connection replay rejection; Python/Go age interoperability,
multiple recipients, wrong-key/tamper/truncation rejection and the 50 MiB boundary;
immutable snapshot behavior; and fixed-width ES256 signing-byte compatibility.
The result and manifest expressly do not cover the complete certificate/JWS
profiles, product integration, production proxy, concurrency, resource ownership,
crash safety, platform matrix, dependency approval or full lifecycle protocol.

I did not rerun the probe because my delegated ownership permits writing only this
report and the probe mutates its fixture directory. I inspected its code, result,
logs and binary build metadata. The manifest records that `go vet` and the Go
1.24.13 compile passed; empty success logs alone are not broader security proof.

## Accepted limits

I did not reinterpret these approved limits as findings:

- one trusted owner/server can read upstream secrets and plaintext;
- lightweight agents can read and copy their own client credentials;
- ordinary account policy remains the policy after the enrollment grant check;
- immediate plaintext stdin can deliver an authentic incomplete prefix with
  irreversible tool effects before final failure;
- completed upstream effects and already delivered data cannot be recalled;
- a deliberately selected stale restore or undo can reinstate older local state
  and requires prominent warnings rather than an additional approval gate;
- migration is cooperative and procedural, with one active copy and no authority
  generation, automated fencing, permanent retirement or cluster takeover; and
- browser administration is a future compatibility/security boundary, not UI
  implementation in this delivery.

## Reviewed scope and limitations

I read the complete HLD and all R01–R25 requirements. I used the prior candidate
security contracts and three DBB design reports to challenge the selected
mechanisms, but did not treat them as normative or require their candidate
choices. I checked trust and authority separation, attacker-controlled enrollment,
proxy, certificate, recovery, artifact and backup inputs; commit and lost-response
states; deadline and revocation races; bounded resource ownership; stale-state
effects; restore/undo usefulness; and migration handoff.

I made no product or target-document change, ran no live account or network
operation, installed no dependency, committed no repository state and created no
stamp. No conclusion here certifies the complete protocol, implementation,
dependencies, platform behavior, deployment or release.

## Round history

| Round | Exact target | Verdict | Findings |
| --- | --- | --- | --- |
| 1 — 2026-10-20 | `b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57` | Pass at HLD level | No blocking HLD findings |

Any future re-review must preserve this row and append its exact target identity,
verdict and finding disposition.
