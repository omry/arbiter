# DBB review: authentication implementation plan

Reviewed 2026-10-20. This is my bounded defensive-security assessment of the
authentication implementation plan at implementation-plan maturity. It assesses
slice ownership and order, safe intermediate states, trust-boundary coverage and
the assigned verification obligations. It is not an implementation, dependency,
platform, deployment or release audit.

## Verdict

**Pass at implementation-plan level.** The exact round-2 plan preserves the fixed
requirements and HLD direction, assigns coherent independently reviewable vertical
slices, and supplies the required prefix and combined verification obligations.
Round-1 findings P1 and P2 are resolved. I found no blocking plan-level finding
and no silently deferred requirement or invented authority. These exact plan bytes
can receive the plan-level SWE stamp.

This verdict applies only to
[`docs/authentication/implementation-plan.md`](../implementation-plan.md) with
SHA-256
`2bf706ba336f4560a5a40409e0928cc9cd9cb91bc00934045bd79313f66c95de`,
against the normative
[`docs/authentication/requirements.md`](../requirements.md) with SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`
and fixed
[`docs/authentication/high-level-design.md`](../high-level-design.md) with SHA-256
`b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`.
Both governing documents retain their existing exact-byte stamps. A content
change to the plan requires review of the changed bytes before a new verdict.
This report does not create a stamp.

## Finding disposition

**Blocking findings: none. Required plan fixes: none.** The following dispositions
record how the exact round-2 plan closes the two round-1 findings. They are
plan-contract fixes; they do not claim that product behavior is implemented.

### P1 — resolved: slice 3 proves the safe artifact prefix

**Prerequisite and impact.** After slices 1–3 and before slice 7, an authenticated
client can invoke ordinary operations while the encrypted creator-bound artifact
store is not implemented. An artifact-producing operation must not reach the
legacy plaintext/ownerless store or return a legacy nonce-only artifact. Otherwise
the accepted prefix can persist sensitive plaintext without the R18 ownership and
origin boundary, even though the later complete release remains gated.

**Round-2 disposition.** The plan still carries the cross-slice fail-safe invariant
([delivery rules](../implementation-plan.md#delivery-identities-and-rules)) and now
makes [slice 3](../implementation-plan.md#slice-3--native-api-account-grants-and-live-denial)
replace plugin artifact creation and inspection dependencies with an explicitly
closed adapter. Rejection occurs before legacy-store entry or payload publication;
the prefix cannot return a usable descriptor or nonce-only path. It also preserves
truthful reporting of completed upstream effects and prohibits automatic replay.

The assigned real authenticated Python/Go prefix test invokes an artifact-producing
operation, spies legacy-store entry, inspects the filesystem and routes, checks
restart cleanup, and exercises cancellation and repeated failure through actual
resource release. Slice 7 then replaces the closed adapter through the same caller
context with the encrypted creator-bound adapter. This closes the unsafe slice-3-
to-7 prefix without a bypass, client policy or temporary production protocol.

### P2 — resolved: certificate renewal transitions are owned and tested

**Prerequisite and impact.** When an owner issuer, server identity authority,
portable leaf or inner server leaf approaches or passes its selected renewal
boundary, the implementation needs an authorized transition that preserves
durable IDs. Without an owned and tested path, the system can either fail closed
into avoidable outage/re-pairing or accept an unsafe peer-supplied renewal after
trust expiry.

**Round-2 disposition.** The fixed HLD requires CA/portable/server-leaf renewal to
retain durable IDs, use existing signing and live-state authority, reject
peer-driven renewal of an expired server trust anchor, and route missed server
trust validity to owner recovery
([HLD credential roles](../high-level-design.md#credential-roles-and-verification)).
The repaired plan assigns validity and schedule profiles to slice 1, owner-issuer
and portable-leaf renewal to slice 5, and server-authority and inner-server-leaf
renewal plus missed-validity recovery to slice 6. It keeps these paths distinct
from access-key rotation and server identity-key replacement.

Slices 5 and 6 now require positive and negative controlled-clock coverage for
before-expiry renewal, stable IDs and keys, signer and live-state checks, purpose
separation, denial and renewal races, restart, refusal of expired peer-driven
trust renewal, and explicit independent owner recovery or pairing when trust is
missing. The combined journey includes all four renewal roles and the missed-
validity recovery path. This satisfies the HLD's assignment requirement without
adding a policy default or weakening expiry.

## Assessment of the remaining plan

With P1 and P2 resolved, the plan is suitable for implementation-plan maturity. The
hierarchical identities permit later review-driven splits without losing aggregate
acceptance. Serial delivery and direct dependencies keep authority, enrollment,
native dispatch, lifecycle, artifacts and recovery in a safe order. Schema and
fixture work is assigned to the PR that first consumes it rather than hidden in a
scaffolding PR. Each slice owns an observable vertical result, focused negative
and failure checks, prefix Python/Go integration, documentation and the applicable
configured release-note surface.

The plan carries the important positive, negative, race, lost-response, restart,
crash, resource-ownership and platform obligations into the affected slices and
the final combined gate. It also preserves the accepted constraints: one trusted
owner; readable lightweight client keys; current account-policy flow; immediate
stdin's authentic partial-prefix risk; warning-based stale restore/undo; procedural
migration without fencing or client generations; and future browser administration
as a boundary only. I found no other silently deferred requirement or newly
invented authority.

## Evidence reviewed

| Evidence | SHA-256 | Assessment |
| --- | --- | --- |
| `temp/authentication-plan/plan-round-01.md` | `889ddcf1e7d0f3211d7b0f30840f1fedd86869991f776506d89caaac29810f62` | Frozen round-1 target used to verify the complete repair diff. |
| `temp/authentication-plan/plan-round-02.md` | `2bf706ba336f4560a5a40409e0928cc9cd9cb91bc00934045bd79313f66c95de` | Byte-identical to the reviewed plan. |
| `temp/authentication-plan/repairs-round-02.json` | `2aa1815840415cc8356ddcacf00b3fb9e031c9ecd87a9920c9724d2e40fa0070` | Repair manifest inspected as supporting evidence, not treated as proof of disposition. |

## Next-stage limits

No product implementation exists to test, and absence of implementation was not
treated as a plan defect. This pass approves only the slice order and verification
obligations. It does not approve code, dependencies, supported platform behavior,
deployment, publication or release.

I read the complete plan, requirements, HLD and prior HLD DBB report. I inspected
the current artifact dispatch/store only to validate the slice-3 trust transition.
I ran no product test, live account operation or network scan, changed no target
or governing document, committed nothing and created no stamp.

## Round history

| Round | Exact target | Verdict | Finding disposition |
| --- | --- | --- | --- |
| 1 — 2026-10-20 | `889ddcf1e7d0f3211d7b0f30840f1fedd86869991f776506d89caaac29810f62` | Changes required | P1 safe artifact prefix open; P2 certificate renewal ownership open |
| 2 — 2026-10-20 | `2bf706ba336f4560a5a40409e0928cc9cd9cb91bc00934045bd79313f66c95de` | Pass at implementation-plan level | P1 resolved; P2 resolved; no new blocking finding |

Any future re-review must preserve these rows and append its exact target identity,
verdict and finding disposition.
