# DBB review brief: authentication and enrollment

Prepared 2026-10-20. **The owner authorized DBB after the fidelity pass; findings
are pending.** This is a bounded architecture and design-gap assessment, not a
release audit, implementation review or request to build the feature.

## Read these inputs

1. [Consolidated design](../design.md): current agreed
   behavior, concrete candidates, known risks and five finite blocker groups.
2. This brief. Consult the [evidence index](../evidence.md)
   only to verify a specific decision, source observation or POC claim.

Do not treat the archived 6,602-line interview/draft history as required reading.
It is preserved for disputed provenance, not the primary review surface. The
coverage map identifies superseded choices; historical proposals are not current
requirements. Review this package's actual blockers rather than certify missing
wire details. No product authentication or grant layer is claimed implemented.

## Requested assessment

- Identify contradictions or missing trust mechanisms that could make the
  approved journeys unsafe or infeasible. In particular, assess pairing-to-key
  binding, role/purpose separation, portable ownership versus local access,
  expiry versus restricted late rotation, revocation races, stale restore and
  independent recovery after authority compromise.
- Assess the proposed Noise pairing and TLS-over-WebSocket composition. Distinguish
  tested library interoperability from untested activation/transport security.
  Evaluate the narrow outer-certificate exception and fail-closed inner identity
  boundary; require a concrete attack scenario when challenging an assumption.
- Assess artifact recipient/origin/completion guarantees and all processing modes,
  including authentic partial stdin. Check that server inspection remains possible
  and public-recipient encryption is not mistaken for sender authentication.
- Assess coherent backup/restore/undo and migration: independently trusted bundle
  verification, crash/state boundaries, stale grants and client-key mismatch,
  single-active authority and no uncertain-operation replay.
- Recommend one public browser-admin topology: TCP passthrough or trusted Apache
  HTTPS termination. Explain the alternative's trust/certificate/backend-defense
  and configuration costs. Also assess private loopback/SSH session assumptions;
  an exact browser session scheme is still a blocker, not an implemented defense.
- Identify unnecessary mechanisms and prioritize the minimum work within B1–B5
  required to make this design concrete. Do not add new features or turn every
  numerical tuning proposal into a new owner interview.

## Fixed context and output

The operator/server are trusted; agent-readable lightweight client keys are an
accepted limitation. No claim protects secrets from that operator, prevents a
compromised client from making policy-permitted requests, guarantees fairness
under connection spam, or reverses upstream side effects. Sandbox/broker custody,
hosted multi-owner execution and clustering remain future work.

Return one prioritized finding list: affected invariant or blocker, attack/failure
scenario, preconditions, evidence, consequence and smallest proposed correction.
Separate unsafe design choices, incomplete mechanisms, accepted risks and optional
hardening. Preserve provisional D103 and unanswered U53 as such. State limits of
any positive conclusion; do not call the complete protocol or implementation clean.
Request owner input only when a concrete finding would change agreed behavior,
trust, scope or significant cost. Required review findings must be dispositioned
before finalization. No new scan, service operation, subagent, product edit, commit,
push, PR, publication or deployment is authorized by this brief itself.
