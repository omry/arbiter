# Authentication review history

Start with the stamped [requirements baseline](../requirements.md) and its
[initial stamp](../.review/requirements.md). The earlier [synthesis](../design.md)
and [security contracts](../security-contracts.md) are candidate inputs to the
high-level-design stage, not additional requirements or an implemented system.
The [HLD](../high-level-design.md) now has its own
[validated stamp](../.review/high-level-design.md), dependent on the requirements.

| Record | Result |
| --- | --- |
| [Local fidelity review](fidelity.md) | Four summary omissions corrected before DBB. This was a primary-agent review, not an independent SWE stamp. |
| [Initial DBB review](dbb-initial.md) | Six findings in the original consolidated design. |
| [DBB repair review](dbb-repairs.md) | Findings 1–4 resolved at design level; one browser-origin gap remained; 6 accepted operationally. |
| [Final DBB follow-up](dbb-followup.md) | Browser repair accepted. Findings 1–5 resolved at design level; 6 remains the accepted operational limitation. |
| [Requirements DBB review](requirements-dbb.md) | Pass on the exact initial requirements baseline; safe to begin high-level design. No blocking requirements findings. |
| [HLD DBB review](hld-dbb.md) | Pass on the exact HLD, with no blocking HLD findings. Wire, implementation, dependency, platform and release approval remain separate. |
| [Plan DBB review](plan-dbb.md) | Two plan gaps repaired: safe slice-3 artifact prefix and certificate renewal ownership/tests. Round-two exact-byte pass; no remaining blocking plan findings. |
| [Plan sizing DBB review](plan-sizing-dbb.md) | Current sixty-child plan passed after receipt recovery moved before activation and complete live denial moved before ordinary cutover. Size forecasts and hard-cap enforcement remain implementation obligations. |

The requirements stamp followed a separate primary-agent requirements review
through SWE, after the DBB pass. Its immutable round report is retained
locally under `temp/reviews/requirements/rounds/round-01.md`. The stamp is limited
to requirements maturity; no high-level design, plan, implementation or release
is approved by it. The owner requires DBB review before stamping each
new or modified document in this sequence.

The HLD then passed DBB and a separate primary-agent seven-dimension SWE
design review in its first round. Its immutable SWE report is retained locally at
`temp/reviews/high-level-design/rounds/round-01.md`. Fourteen local feasibility
checks, Go vet and Go 1.24 compilation passed, with limits recorded in the HLD and
DBB report. Recursive stamp validation accepted the HLD and exact requirements
dependency. Neither an implementation plan nor product implementation was started.

The initial [implementation plan](../implementation-plan.md) passed DBB
in round two after those two plan repairs, then a primary-agent seven-dimension
SWE review with zero findings. Its immutable report is retained locally at
`temp/reviews/implementation-plan/rounds/round-01.md`. The
[standardized plan stamp](../.review/implementation-plan.md) binds
`arbiter-authentication` / `hierarchical-v1` and both exact governing documents;
recursive validation accepted the complete requirements/HLD/plan chain. That
revision treated the eleven milestones as intended PRs; it is historical evidence.

The sizing revision replaces that assumption with sixty planned child PRs, separate
production/test churn ranges and confidence, a target near 600 lines and a hard
1,000-line combined handwritten-code limit. DBB round two passed the exact revised
bytes after two ordering repairs; a new primary-agent seven-dimension SWE review
was clean. Its immutable report is retained locally at
`temp/reviews/implementation-plan/rounds/round-02.md`. The replacement plan stamp
validates recursively against the unchanged requirements/HLD, with the prior plan
retained as a reviewed predecessor. No implementation PR has started. Forecasts
are not measured implementation, a guaranteed PR count or platform certification.

The [initial brief](initial-brief.md) and [repair brief](repair-brief.md) preserve
the historical review requests. Their pending-status text and output filenames
describe those past requests; they are not current instructions or readiness.
No report certifies the full protocol, implementation, dependencies or release.

## Paths and evidence identities

These files were grouped here after review. Only relative Markdown link targets
changed inside the moved documents. Historical inline paths, line references,
SHA-256 tables, conclusions and limitations retain their original meaning.
The [relocation map](relocation.json) records old/new paths and before/after file
hashes. Each `organized_sha256` preserves the relocation-time snapshot; each
`current_sha256` verifies the final published file, including later status and
evidence-link edits. The tables inside reports identify their exact historical reviewed
inputs, not a claim that a later document with repaired navigation has identical
bytes. Original archive files and frozen local review snapshots remain unchanged.
