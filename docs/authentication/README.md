# Authentication and enrollment

Start with the [requirements baseline](requirements.md), then the
[high-level design](high-level-design.md), followed by the
[implementation plan](implementation-plan.md). All three passed DBB and
their respective SWE reviews. Their [requirements stamp](.review/requirements.md),
[HLD stamp](.review/high-level-design.md) and
[plan stamp](.review/implementation-plan.md) bind exact bytes and the appropriate
requirements/HLD dependencies. The delivery sequence is requirements,
high-level design, then an implementation plan with slices. Each stage
is reviewed and stamped at its own level, with DBB review of the final
document bytes before stamping.

The [earlier synthesis](design.md) and [candidate security contracts](security-contracts.md)
retain the mechanisms already assessed by DBB as historical inputs to the HLD.
They are not a completed high-level design or additional binding requirements.

The interview is closed. DBB resolved findings 1–5 at design level;
finding 6 uses the owner's accepted restore/migration procedure for cooperative
servers. Protocol, implementation and release readiness are not established.
The B1–B5 groups supplied the HLD agenda; their implementation evidence remains
outstanding. They did not require implementation detail for the initial
requirements stamp. No product authentication implementation has been authorized
by these documents.

## Supporting material

- [Decision and experiment evidence](evidence.md): provenance for the 104 recorded
  decisions and limits of the retained compatibility experiments.
- [Review history](reviews/README.md): completed fidelity and DBB assessments.
  The [requirements DBB report](reviews/requirements-dbb.md) gives the current
  requirements-level verdict; the [HLD DBB report](reviews/hld-dbb.md) gives the
  exact-byte HLD pass. Earlier reports concern candidate architecture.
  The [initial plan review](reviews/plan-dbb.md) records the safe artifact-prefix
  and certificate-renewal repairs. The [plan sizing review](reviews/plan-sizing-dbb.md)
  covers the current child-PR estimates and repaired activation/denial order.
- [Original draft and interview](../archive/authentication-and-enrollment/README.md):
  immutable historical evidence, not required reading or instructions to resume
  questioning.

The HLD covers B1–B5 and shared boundaries. Fourteen local Python/Go feasibility
checks passed; platform, production dependency, race and crash validation remain
implementation obligations. No product authentication is implemented by this work.

The plan retains eleven milestones under stable identity `arbiter-authentication`,
split into sixty planned PRs with separate production/test estimates and confidence.
Target about 600 changed handwritten code lines per PR; the hard maximum is 1,000,
including tests. Estimates remain uncertain and can require further splits.
DBB required receipt recovery before activation and complete live denial before
ordinary API cutover; the reviewed order now enforces both. Earlier artifact-prefix
and renewal obligations remain. These are plan corrections, not implemented fixes.

Next, with separate authorization: child PR 1.1, private authority initialization,
within milestone 1 (operator authority setup and authenticated local control).
No implementation slice or PR has started.
