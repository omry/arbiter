# DBB review: authentication plan sizing and child-PR order

Reviewed 2026-10-20. This is my bounded defensive-security assessment of the
revised implementation-plan decomposition. It checks whether the former eleven
large implementation milestones are split into reviewable child PRs without
weakening the governing requirements, HLD, parent acceptance obligations or safe
prefix behavior. It is not an implementation, dependency, platform, deployment or
release audit.

## Verdict

**Pass at implementation-plan sizing level.** The exact round-2 plan provides a
coherent 60-child decomposition and preserves the fixed requirements, HLD, parent
acceptance obligations and safe prefix behavior. Round-1 findings P1 and P2 are
resolved through real runtime closure and repaired child order, not through a
publication label or weakened acceptance text. I found no remaining blocking
plan-level finding. These exact plan bytes may proceed to the separate SWE review
and replacement plan stamp.

This verdict applies only to
[`docs/authentication/implementation-plan.md`](../implementation-plan.md) with
SHA-256
`e8e27181f5f6ab25de0135b44ef188606fa55830cb9a336179c8b43431873e98`,
against the unchanged
[`docs/authentication/requirements.md`](../requirements.md) with SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`
and
[`docs/authentication/high-level-design.md`](../high-level-design.md) with SHA-256
`b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`.
The historical [`plan-dbb.md`](plan-dbb.md) was evidence for this review and was
not modified. This report does not create or replace a stamp.

## Finding disposition

**Blocking findings: none. Required plan fixes: none.** The following dispositions
record how the exact round-2 plan closes the two round-1 findings. They are plan
contract fixes and do not claim that product behavior is implemented.

### P1 — resolved: complete live denial precedes ordinary cutover

**Round-1 prerequisite and impact.** Child 3.5 made ordinary traffic reachable
before child 3.6 installed cross-connection cancellation and output blocking. A
grant removal or enrollment denial during an admitted request could therefore leave
post-denial delivery possible in that prefix, contrary to R14 and the HLD
coordinator contract.

**Round-2 disposition.** The listed order now runs 3.6 before 3.5, with a direct
3.5 dependency on 3.6. The plan expressly keeps ordinary public dispatch closed
while 3.6 installs cancellation through prepared inner handlers and real synthetic
sockets. Child 3.5 is now the first reachable ordinary prefix and owns real
Python/Go denial checks against queued admission, request bodies, upstream dispatch
and ongoing output, including real blocking-worker retention. Anonymous legacy
routes and the artifact path close in that same cutover. The repair preserves the
accepted no-uncertain-replay and closed-artifact requirements.

### P2 — resolved: lost-receipt reconciliation precedes reachable commit

**Round-1 prerequisite and impact.** Child 2.6 made a durable activation commit
reachable before children 2.7 and 2.8 supplied the authenticated status carrier and
exact-result retrieval. Loss of the final reply could strand active server authority
and provisional client state without the R09/HLD reconciliation path.

**Round-2 disposition.** The listed order now runs 2.7 and 2.8 before 2.6, and 2.6
depends directly on 2.8. Runtime text keeps the live ready/activation route absent
through 2.8. Those carrier/reconciliation children use only clearly test-only
already-activated fixture rows, which cannot initialize product trust or create an
activation. Child 2.6 then wires the first reachable commit with the carrier and
exact-result retrieval already present. Its real Python/Go checks cover crash,
lost reply and restart at ready/commit/receipt boundaries, authenticated client
promotion, denial precedence and absence of a second identity or grant. This
closes the durable ambiguity without a permissive stub or temporary protocol.

## Accepted parts of the revision

The sizing arithmetic is internally consistent: 60 unique child identities cover
all eleven parent milestones; their stated ranges sum to 28,630–49,150 changed
handwritten production/test/fixture lines; each row's production and test ranges
sum to its total; and the largest forecast is 960 lines. All explicit child
dependencies point backward in the serial queue. The plan correctly counts
additions plus deletions across Python and Go, forbids moving tests or disguising
handwritten fixtures to satisfy the cap, and requires a forecast and mechanical
split before a child reaches the hard 1,000-line limit.

The estimates remain planning estimates rather than delivery guarantees. Fifty-four
of 60 children are marked low confidence, and 13 have an upper forecast of at least
900 lines. The inspected repository confirms why: authority state, protocol
carriers, cross-platform custody and coherent backup/restore are largely new, while
the existing artifact, ASGI, installed-client and timeout test surfaces are already
substantial. The mandatory stop-and-split rule is therefore necessary. It makes the
decomposition feasible without claiming that exactly 60 PRs will suffice.

The plan preserves parent obligations in its shared rule that each child owns the
relevant parent tests as soon as behavior exists; closure PRs cannot excuse earlier
untested behavior. The two repaired cutover children now own the missing real
prefix evidence. Foundation work has a consumed setup/status, pending-request or
executable peer boundary and expressly forbids empty interfaces, permissive stubs
and temporary production protocols.

The earlier artifact finding remains closed. Child 3.4 installs and tests the closed
plugin artifact adapter before child 3.5 makes ordinary traffic reachable. The
required authenticated Python/Go prefix evidence retains the legacy-store spy,
filesystem/route inspection, restart cleanup and resource-release checks. Children
7.2–7.4 build encrypted creation, quota recovery and creator-bound retrieval behind
that boundary; 7.5 replaces the adapter only after those prerequisites exist.

Certificate lifecycle ownership also remains complete. Child 1.2 fixes the profiles
and renewal schedules; 5.5 owns authorized owner-issuer and portable-leaf renewal;
6.4 owns server-authority and inner-server-leaf renewal plus missed-validity owner
recovery. Their controlled-clock negative checks and the combined four-role renewal
journey retain the prior DBB disposition. These paths remain distinct from access-
key rotation and planned server identity-key replacement.

The split does not add migration fencing, epochs, clustering or another authority.
Child 11.1 and the parent acceptance text retain the approved cooperative operator
procedure. I did not reopen that accepted trust decision.

## Evidence and limits

I read the complete revised plan, unchanged requirements and HLD, the historical
plan DBB review, repository instructions and the relevant current server/client
surfaces. I inspected the native API/client transport, artifact store/admission,
blocking pool, storage/configuration and existing tests to assess the sizing basis
and trust transitions. The inspected checkout revision was `9d5cfc198bb4`.

I compared the complete round-2 target with the frozen round-1 target and report.
I also ran local read-only structural checks for child count/uniqueness, listed
order, range arithmetic, maximum forecast, estimate totals, dependency direction
and exact SHA-256 identities.

I ran no product test, live account operation, dependency install, network scan or
new security scan. No authentication implementation exists to test; absence of
implementation was not treated as a plan defect. This pass approves only the child
order, sizing discipline and assigned verification obligations. It does not approve
code, dependencies, supported-platform behavior, deployment, publication or release.

I changed only this review report, changed no target/governing/historical document,
and did not commit, push, publish, merge or deploy anything.

## Round history

| Round | Exact target | Verdict | Finding disposition |
| --- | --- | --- | --- |
| 1 — 2026-10-20 | `585af79a10f5110c58efc87ce5b4ac29c5ab444c8aea649cf733d362f08d5a33` | Changes required | P1 ordinary cutover preceded complete live denial; P2 durable commit preceded lost-receipt reconciliation. |
| 2 — 2026-10-20 | `e8e27181f5f6ab25de0135b44ef188606fa55830cb9a336179c8b43431873e98` | Pass at implementation-plan sizing level | P1 resolved; P2 resolved; no new blocking finding. |

The frozen round-1 target and report remain at
`temp/authentication-plan-refinement/plan-round-01.md` (SHA-256
`585af79a10f5110c58efc87ce5b4ac29c5ab444c8aea649cf733d362f08d5a33`)
and `temp/authentication-plan-refinement/dbb-round-01.md` (SHA-256
`773f4ed126bf75cb7641c5eab0091ac433542d024dbe7f35969f7910e17c6bcc`).
Any future re-review must preserve these rows and append its exact target identity,
verdict and finding disposition.
