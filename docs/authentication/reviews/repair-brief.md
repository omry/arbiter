# Authentication and enrollment: second DBB review brief

Prepared 2026-10-20 after the owner authorized design repair and actual DBB
re-review. Review the revised architecture and the concrete repair contracts;
do not implement the feature or start another interview.

## Required inputs

1. [Current design](../design.md).
2. [Candidate security contracts H1–H5](../security-contracts.md).
3. [First DBB report](dbb-initial.md), as the
   independent finding baseline, not an already approved design.

Use the [decision/evidence index](../evidence.md) for
targeted original-decision checks only. The first reviewed inputs and long
archives are preserved; do not read the whole interview by default.

## Owner decisions since the first review

- Keep the five-attempt cap, but separate unauthenticated resource admission from
  PSK-authenticated pairing attempts. Do not promise general spam fairness.
- Assume cooperative servers and infrequent migration. Use a documented
  maintenance/restore procedure to keep one active server. No client authority
  generation counter, signed takeover or permanent retirement mechanism is
  required. Older restore and post-use undo remain supported with warnings.
  This is an accepted operational risk/control, not automated prevention.
- No further interview round is needed for repairs preserving approved behavior.
  A material policy/trust/operational-cost change must be reported, not hidden.

## Required review output

Write `docs/authentication-and-enrollment-dbb-rereview.md`. Record exact reviewed
input hashes. Assess each of the six original findings as resolved at design
level, accepted operational limitation, still open or superseded with evidence.
Report any concrete new conflict/attack caused by the repairs, including:

- H1's independent signer, owner trust provisioning, D24 continuity without a
  second approval, portable-key restrictions and server-compromise recovery.
  The extra signer lives in the one existing protected recovery file; assess
  whether this candidate adds unjustified setup or authority assumptions.
- H2's separate server-authenticated control carrier and bound old/candidate
  possession proofs, expired certificates, lost replies and fixed deadlines.
  No ordinary verifier weakening or unauthenticated ASGI context is permitted.
- H3's origin descriptor, verified temporary modes, partial immediate stdin and
  mutable-staging boundaries. No mandatory persistent archive/signature system.
- H4's real first-flight PSK proof, replay/parallel accounting and bounded state.
- H5's local browser authorization, explicit bearer, CSRF, bootstrap lifetime,
  exact origin and SSH boundary; public topology remains the recommendation.
- The sufficiency and limits of the owner's procedural migration disposition,
  without demanding a cluster protocol or silently removing restore/undo.

Distinguish an unsafe architecture decision from a candidate whose exact wire,
backend or evidence is still unfinished. B1–B5's interoperability, implementation
and crash/admission tests have not run. A defensible repaired architecture is
not complete protocol approval, implementation readiness or release signoff.
Do not add speculative hardening or reopen accepted lightweight key custody,
trusted operator, stale-backup rollback or client-selected processing tradeoffs.
