# Authentication and enrollment

Start with the [requirements baseline](requirements.md), then the
[high-level design](high-level-design.md), followed by the
[implementation plan](implementation-plan.md). All three passed actual DBB and
their respective SWE reviews. Their [requirements stamp](.review/requirements.md),
[HLD stamp](.review/high-level-design.md) and
[plan stamp](.review/implementation-plan.md) bind exact bytes and the appropriate
requirements/HLD dependencies. The delivery sequence is requirements,
high-level design, then an implementation plan with slices. Each stage
is reviewed and stamped at its own level, with actual DBB review of the final
document bytes before stamping.

The [earlier synthesis](design.md) and [candidate security contracts](security-contracts.md)
retain the mechanisms already assessed by DBB as historical inputs to the HLD.
They are not a completed high-level design or additional binding requirements.

The interview is closed. Actual DBB resolved findings 1–5 at design level;
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
  The [plan DBB report](reviews/plan-dbb.md) records two plan gaps, their repair
  and the exact-byte round-two pass.
- [Original draft and interview](../archive/authentication-and-enrollment/README.md):
  immutable historical evidence, not required reading or instructions to resume
  questioning.

The HLD covers B1–B5 and shared boundaries. Fourteen local Python/Go feasibility
checks passed; platform, production dependency, race and crash validation remain
implementation obligations. No product authentication is implemented by this work.

The plan defines eleven PRs under stable identity `arbiter-authentication`, with
per-slice acceptance, integration and review gates. Actual DBB required explicit
safe artifact-prefix tests and certificate-renewal ownership; both are now in
the stamped plan. These are plan corrections, not implemented security fixes.

Next, with separate authorization: slice 1, operator authority setup and local
control. No implementation slice or PR has started.
