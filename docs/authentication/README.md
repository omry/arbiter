# Authentication and enrollment

Read the [requirements](requirements.md), [HLD](high-level-design.md),
[portable enrollment LLD](portable-enrollment-lld.md),
[milestone-3 LLD](milestone-3-lld.md), then the
[implementation plan](implementation-plan.md). The requirements/HLD remain the
unchanged governing design. The two LLDs specify the next proposed work, its
complete acceptance and already sized implementation cuts; the plan owns delivery
ordering and stable identities. This page summarizes those four design inputs
and the plan; it adds no separate product requirements.

Review evidence lives beside documents in `.review/`, including the
[requirements](.review/requirements.md), [HLD](.review/high-level-design.md),
[portable enrollment](.review/portable-enrollment-lld.md),
[milestone 3](.review/milestone-3-lld.md) and
[plan](.review/implementation-plan.md) attestations. A stamp binds exact bytes,
explicit design dependencies and its actual review evidence. An older stamp does
not approve changed text; every changed design must complete DBB/general review
and receive a current stamp before acceptance. Review approval is separate from
implementation authorization, merge and publication.

## Current state and proposed next work

Merged baseline: `25a8cf2196f36499ff92ce890f313901b51b4f2b`.
Authority/setup/local control and the milestone-2 fresh-pairing/activation/receipt
stack are merged; [PR74](https://github.com/omry/arbiter/pull/74) closed the latter.
All 13 reported post-merge checks passed in the
[baseline workflow run](https://github.com/omry/arbiter/actions/runs/40579826766).
That is source-baseline evidence, not
completion of all authentication requirements or publication approval.

Existing-portable-identity reuse remains unimplemented. Fresh-key proof and
same-selector receipt resume do not preserve one agent across two servers.
The portable LLD's proposed `2.10.1–2.10.4` closes that gap with independently
registered issuer trust, original portable-key possession and fresh per-server
access/approval/grants. It retains the existing pairing and private publisher;
no key broker or second enrollment service is proposed. The first upgraded cut
also maintains unique agent bindings during fresh-v1 activation; safe intermediate
state is not deferred until v2 cutover. The old milestone-2
22-attempt allowance is exhausted and does not authorize these four new cuts.

Milestone 3 is also unimplemented. Its LLD specifies strict native mTLS, existing
ASGI/policy integration, live grants/denial and closed artifact behavior until the
encrypted store. It proposes 13 complete cuts, ordered so denial is ready before
public ordinary cutover. The server request-body deadline and shared enrollment
denial/contact fields are design specifications, not applied runtime changes.
The existing agent binding remains canonical. The production CLI switch and
server enablement share the same cut; earlier private-peer coverage preserves
the working product route. The first live enrollment-deny control and fixed-receipt
output guard share the same coordinator and cut; ordinary admission extends it
in the following cut.

The next design-reviewed implementation proposals are:

| Work | Proposed PRs | Handwritten production/test/CI estimate |
| --- | ---: | ---: |
| `2.10` portable reuse | 4 | 2,150–3,015 |
| `3` native API/grants/denial | 13 | 6,790–9,410 |

Detailed ownership, acceptance and per-cut estimates live once in their LLDs.
Docs/news/lock data are separate. Counts include code motion, fixtures and review
headroom; no later dynamic split is assumed. These are reviewable estimates,
not approved delivery allowances or guarantees. Preserve the existing 1,000-line
handwritten-code cap, including tests. The original 60-PR plan and milestone-3
seven-PR estimate are historical. Milestones 4–11 retain scope/outlines, but need
source-based LLD sizing/review before execution; no credible total remaining-PR
forecast is claimed yet.

Current work is design and local/PR review only. Starting `2.10`, milestone 3 or
later implementation requires separate authorization. All eleven milestones and
the combined publication gate remain required; a merged development stack is
not releasable authentication support.

The [earlier synthesis](design.md) and [candidate security contracts](security-contracts.md)
are historical HLD inputs. The closed interview and earlier findings remain
provenance, not additional binding requirements or instructions to reopen questions.
These design documents themselves grant no product implementation authorization.

## Supporting material

- [Decision and experiment evidence](evidence.md): provenance for the 104 recorded
  decisions and limits of the retained compatibility experiments.
- [Review history](reviews/README.md): completed fidelity and DBB assessments.
  The [requirements DBB report](reviews/requirements-dbb.md) gives the current
  requirements-level verdict; the [HLD DBB report](reviews/hld-dbb.md) gives the
  exact-byte HLD pass. Earlier reports concern candidate architecture.
  The [initial plan review](reviews/plan-dbb.md) records the safe artifact-prefix
  and certificate-renewal repairs. The [plan sizing review](reviews/plan-sizing-dbb.md)
  records the original child-PR estimates and repaired activation/denial order;
  it does not review the new LLD forecasts.
- [Original draft and interview](../archive/authentication-and-enrollment/README.md):
  immutable historical evidence, not required reading or instructions to resume
  questioning.

The HLD covers B1–B5 and shared boundaries. Its fourteen retained Python/Go
feasibility checks were design inputs, not proof of the later ordinary transport.
Native implementation, race/crash and installed-prefix evidence belongs to each
owning implementation cut. The reviewed LLDs retain receipt-before-activation,
denial-before-public-cutover and safe artifact-prefix obligations. This revision
updates the design and status; it implements no product behavior.
