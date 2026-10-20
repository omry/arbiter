# Internal documentation

Public operator, client and plugin documentation lives in
[website/docs/](../website/docs/intro.md). This directory contains internal
planning, assessments and design records.

## Active work and release records

| Document | Purpose |
| --- | --- |
| [Backlog](BACKLOG.md) | Implementation priorities; some older entries need status reconciliation. |
| [Testing backlog](testing_backlog.md) | Test and CI gaps; older blocked entries are not a fresh audit of current behavior. |
| [Release readiness](release-readiness.md) | Release gates and historical rehearsal evidence; not release signoff. |
| [Security assessment](prerelease-security-assessment.md) | Findings, remediation, trust decisions and independent review history. |
| [Security evidence](security-evidence/README.md) | Retained DBB probe and captured results for the latest assessment. |

## Designs with remaining work

| Document | Status |
| --- | --- |
| [Authentication and enrollment](authentication/README.md) | Requirements, HLD and eleven-PR implementation plan stamped after actual DBB and SWE reviews. Next is operator authority/local control implementation; no code slice has started. |
| [IMAP folder policy](imap-folder-policy-design.md) | Much of the policy is implemented; the proposed client policy-check surface remains design work. |
| [Discovery surface](info-surface.md) | Mixes implemented discovery with proposed diagnostics. Public references describe current behavior. |
| [Transport and identity](future/transport-and-identity.md) | Future identity direction; its earlier HTTP transport proposal is superseded by native HTTPS. |

## Historical designs

[archive/](archive/README.md) holds superseded drafts. Archiving a draft does not
mean that every proposal in it was implemented, approved or cancelled. Use the
public references for the shipped contract and the active planning documents
for remaining work.
