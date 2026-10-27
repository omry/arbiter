---
plan_key: arbiter-authentication
slice_convention: hierarchical-v1
---

# Authentication and mutual enrollment: implementation plan

Stage: design-update candidate after the milestone-2 stack merge, 2026-10-28.
Governing documents are the
[requirements](requirements.md), SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`,
and [HLD](high-level-design.md), SHA-256
`b6511414b3a9b485542df83c8f7abfc499dc8d06e522819b48e162a562d21c57`.
Their exact reviewed stamps, the [portable enrollment LLD](portable-enrollment-lld.md)
and the [milestone-3 LLD](milestone-3-lld.md) are explicit design dependencies of
this plan's stamp. The LLDs own their detailed schemas, state transitions,
acceptance cases and already sized cuts. Neither requirements nor HLD changes.
This plan authorizes no
implementation, release, live-account operation or deployment by itself.
DBB must approve the exact final plan before its SWE design stamp.

## Current delivery state and next design scope

Source baseline is merged main `25a8cf2196f36499ff92ce890f313901b51b4f2b`.
Milestone 1 authority/setup/local control and the milestone-2 fresh enrollment,
single activation and receipt stack are merged. The latter closed with
[PR74](https://github.com/omry/arbiter/pull/74); its 21 delivered PRs consumed the
22-attempt allowance, including one unshipped parent attempt. Those delivery
identities, approvals and spent allowance remain history, not permission for
additional work. All 13 reported post-merge checks passed in the
[baseline workflow run](https://github.com/omry/arbiter/actions/runs/40579826766).

Milestone 2 still lacks the approved existing-portable-identity journey. Current
Go provisional creation always generates a new portable key; Python prepare
always assigns a new agent. Fresh-key possession and same-selector receipt
resume do not satisfy reuse of a registered portable identity at a second server.
This is an implementation gap, not an accepted scope deferral. Proposed `2.10`
closes it through the portable LLD before milestone 3 starts. These documents
specify that fix; no product fix is implemented by this design update.

The original milestone-3 seven-PR estimate lacks the streaming, authority
integration and failure-test detail needed for a credible execution cut. Its approved
behavior is retained, with the LLD's 13 already sized proposed cuts replacing the
old boundaries. Milestones 4–11 retain their ordered scope and historical child
outlines below; their old estimates are not newly validated execution forecasts.
Refine them into source-based, reviewed LLDs with bounded complete cuts before
implementing each milestone. No cumulative remaining-PR or LOC promise follows
from adding those old outline counts to the newly sized work.

The current authorized work is design and its local/PR review only. Implementing
`2.10`, milestone 3 or a later milestone requires separate authorization. No old
allowance is reset; no merge, release, deployment or live account work is authorized.
The [original plan at the merged baseline](https://github.com/omry/arbiter/blob/25a8cf2196f36499ff92ce890f313901b51b4f2b/docs/authentication/implementation-plan.md)
preserves the exact earlier text, forecasts and original cuts.

## Delivery identities and rules

`arbiter-authentication` owns this plan. Its numbered slices are `1` through `11`;
references below use `arbiter-authentication/1`, for example. Under
`hierarchical-v1`, an approved later split of `4` becomes `4.1`, `4.2`, etc.; never
renumber another slice or reuse a completed identity. The parent retains the
aggregate acceptance obligation. Delivery records bind exact PR URL, revision,
dependencies, checks, reviews and state for each canonical identity. Merged
deliveries are not renumbered or reclassified as unstarted by this plan revision.

The eleven numbered slices are milestones, not individual PRs. The current LLD
cuts are proposed reviewable PRs with bounded behavior, failure tests and relevant
guidance/fragments. Older child tables are historical outlines as labeled. Work
serially in milestone order and then the listed child order (not numeric sort). Complete local review, checks,
submission and the approved remote review cycle for the current PR before advancing; approval binds
the final PR head, with restacking invalidating affected evidence as appropriate.
This document does not launch those cycles. An LLD must produce already sized
implementable slices; dynamic resizing should be rare. If source evidence shows
a cut is insufficient, revise and review the design before implementing it rather
than treating an execution split as routine capacity. A foundation PR must implement a real consumed boundary
(setup/status, registered pending work or an executable protocol peer), not empty
interfaces, permissive stubs or a throwaway intermediate protocol. A synthetic
peer may exercise a not-yet-public transport; it cannot authorize product traffic.
The historical 2.7/2.8 cuts used synthetic already-activated fixture rows only in
tests before 2.6 enabled real activation; this is not a production trust initializer.
Ordinary public dispatch on the new native boundary remains closed until `3.5.2`.
Both `3.6.1` and `3.6.2` install and test complete live denial first through
private real peers and synthetic accounts.
The eventual cutovers cannot rely on development-only publication policy as their
safety mechanism; the respective runtime routes must actually remain closed.

Every implementation and review-fix commit subject, and its PR title, starts with
the short slice label in brackets. For example:
`[authentication/1.1] Private authority initialization`. The repository already
identifies Arbiter; do not repeat its name in the title prefix. This display label
maps to canonical ledger identity `arbiter-authentication/1.1`; the plan key and
tracking identities remain unchanged. Use the deeper child ID after an approved
further split, and preserve the label through restacks and corrections. A title or
PR number does not replace the execution-ledger identity. This does not rename
historical commits.

### Milestone stacks and merge boundary

Deliver one connected PR stack per milestone. Review and approve its child PRs
serially as described above, retaining the stack until the milestone is complete.
Before merging, require current approval and required checks for every final PR
head, the parent milestone's full acceptance and integration checks, and relevant
integration with completed milestones. Reconcile approval/check evidence after
any correction or restack. Safe intermediate behavior remains mandatory.

Present the verified milestone stack for operator-authorized merge, bottom-up.
Do not merge automatically. Verify the resulting main-branch state before basing
the next milestone on it; do not carry the previous milestone's unmerged stack
into the next one. A delivery request may stop with the approved stack ready for
the operator to merge. Merging is separate from publishing authentication support:
all eleven milestones and the combined publication gate remain required. No
milestone merge authorizes a package release, deployment or live-account operation.

Use existing towncrier release surfaces: `server/news/` for core/native-client
integration behavior, `plugins/imap/news/` or `plugins/smtp/news/` for changed plugin
contracts, and `meta/arbiter-suite/news/` only for bundle dependency changes.
Current release tooling associates the native client with the server release
surface; do not invent an unconfigured `client/news/` directory. Internal planning
documents themselves do not need a user-facing release fragment.

All intermediate revisions are development work, not publishable authentication
support. Use isolated synthetic servers/accounts/state for prefix integration.
Do not add an anonymous compatibility mode, bypass switch, dummy production
identity, second policy layer or temporary protocol that must later be removed.
Product activation of the complete upgrade requires all eleven slices and the
combined gate below. Existing public API exposure is not declared fixed merely
because setup/enrollment exists. Once ordinary native authentication is cut over
in slice 3, anonymous legacy operation/artifact paths stay closed; an incomplete
artifact path must fail safely rather than emit an ownerless/plaintext substitute.
Missing authority state fails closed and directs the operator to trusted setup.

## Ordered milestone queue

Dependencies below are direct semantic prerequisites. Serial delivery additionally
requires the preceding PR to finish its review cycle. Final integration covers
cross-milestone interactions, not just each milestone in isolation. The historical
milestone-2 parent order was 2.1–2.5, 2.7, 2.8, 2.6, 2.9. Proposed next work is
`2.10.1 -> 2.10.2 -> 2.10.3 -> 2.10.4`, then the exact displayed milestone-3
LLD order: `3.1.1–3.1.4`, `3.2.1–3.2.2`, `3.3`, `3.4`, `3.6.1–3.6.2`,
`3.5.1–3.5.2`, `3.7`. Existing parents retain their aggregate obligations. The explicit
child dependencies below are technical prerequisites; serial delivery also
requires the preceding child PR to finish its review cycle. An external dependency
on a milestone means all of that milestone's children, not just its first PR.

| Slice | Observable result | Direct dependencies | Governing coverage |
| --- | --- | --- | --- |
| 1 | Operator initializes durable authority and uses authenticated local control. | None | R01–R06, R11–R12; HLD shared/B2/B3 |
| 2 | Owner pairs a real Go client with one secret handoff and durable single activation. | 1 | R04–R09, R10–R12; HLD B1/B2/B3 |
| 3 | Enrolled clients discover/invoke only granted accounts; live removal stops affected delivery. | 1, 2 | R10–R14; HLD shared/B2/B3 |
| 4 | Due client keys rotate, reconcile lost replies and complete safely after grace/expiry. | 1, 2, 3 | R15–R17; HLD B2/B3 |
| 5 | Owner repairs client identity/access without losing grants or clearing denials. | 1, 2, 3, 4 | R04–R06, R16–R17; HLD B2 |
| 6 | Planned or independently authorized server-key replacement preserves durable trust. | 1, 2, 4, 5 | R06, R10, R17; HLD B2/B3 |
| 7 | Server retains and delivers encrypted creator-bound artifacts with existing quotas. | 1, 2, 3 | R14, R18, R20; HLD B4 |
| 8 | Client offers all four artifact processing modes with honest verification/cleanup behavior. | 3, 4, 7 | R18–R20; HLD B4 |
| 9 | External local/SSH jobs obtain coherent, signed encrypted application backups. | 1, 3, 4, 5, 6, 7 | R21–R22; HLD B5 |
| 10 | Operator restores offline and can undo the latest restore, including after use. | 4, 5, 6, 9 | R23–R24; HLD B5 |
| 11 | Operator migrates paired clients during maintenance with verified URL handoff. | 3, 4, 6, 9, 10 | R10, R14, R17, R25; HLD B5 |

## PR sizing and estimation basis

The original 2026-10-20 breakdown contained **60 planned PRs**; the earlier
30–45 conversation estimate was preliminary. Both are historical, not the
current remaining queue. Only `2.10` and milestone 3 have newly sized LLDs here:

| Proposed work | Cuts | Production | Tests/fixtures | Handwritten CI | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `2.10` portable reuse | 4 | 850–1,220 | 1,300–1,770 | 0–25 | 2,150–3,015 |
| `3` native API/grants/denial | 13 | 2,350–3,360 | 4,440–5,980 | 0–70 | 6,790–9,410 |

These are engineering estimates for review and later authorization, not approved
delivery allowances or guaranteed future diffs. Each full cut includes code
motion, helpers, fixtures, negative/race/native composition tests and correction
headroom. Shared harness code is charged once. The LLDs own the per-cut tables;
the plan does not maintain a second detailed sizing copy.

Target **about 600 changed handwritten code lines** per PR. The **hard maximum is
1,000**, counting additions plus deletions in production code, tests and handwritten
fixtures across Python and Go combined. This is not 1,000 per language or per
category. Count moved/reformatted code too; avoid incidental cleanup. Do not drop
negative tests, move a PR's tests into a later PR, compress code, label handwritten
fixtures generated or weaken a boundary to fit. Estimate and report documentation,
generated fixtures/code, lockfiles and tooling output separately in the PR; they
still contribute review burden. Do not introduce new generation tooling merely to
avoid the limit. A large mechanical change should be a separate coherent PR.

The target is flexible; the maximum is not. Before coding each child, check its
source-based forecast against predecessor changes. Do not begin an overlarge or
underspecified cut expecting to split it later. If actual evidence invalidates
the cut, stop expansion and return to the design boundary before submission.
A rare changed split needs focused review
and updated dependency/acceptance mapping; changes to the stamped plan require
DBB/SWE re-review and replacement stamp before treating that revision as approved.
No additional owner interview is required for a mechanical split that preserves
approved requirements, HLD, size policy and acceptance.

Historical ranges below estimate handwritten churn, including assertions/helpers and fixture
construction. Production and test ranges are separate; the total is their sum.
They are task-by-task engineering estimates, not statistical confidence intervals
or a guarantee that the implementation will stay within the forecast. `Medium`
means there is a close existing implementation to extend. `Low` means new protocol,
durable state, platform custody or fault behavior dominates. No high-confidence
estimate is claimed. Exact files/modules follow implementation style, not a promise
to create one file for each PR. Existing code/tests are reused rather than copied;
only new/changed lines count. Shared harness work is charged to the first child
that uses it, and later children budget their own scenario additions.

Historical inspection basis for the original forecast (2026-10-20,
checkout `9d5cfc198bb4`):

| Existing surface | Measured lines | Consequence for estimates |
| --- | ---: | --- |
| `server/src/arbiter_server/artifacts.py` / `server/tests/unit/test_artifacts.py` | 570 / 810 | Extend the real store and tests; encryption, quota and access cutovers need separate boundaries. |
| `server/src/arbiter_server/http_work.py` / `server/tests/unit/test_artifact_http_admission.py` | 170 / 497 | Reuse bounded streaming owners and real contention barriers; do not budget cancellation as a few happy-path assertions. |
| `server/src/arbiter_server/blocking.py` / `server/tests/unit/test_blocking_pool.py` | 93 / 138 | Preserve the existing worker pool; add authority coordination rather than replace it. |
| `client/go-cli/internal/arbiterhttp/client.go` / `client/go-cli/internal/arbiterhttp/client_test.go` | 212 / 251 | Preserve API construction/error shape while changing the carrier. |
| `client/go-cli/internal/arbiterhttp/timeouts.go` / `client/go-cli/internal/arbiterhttp/timeouts_test.go` | 306 / 368 | Reuse budgets; extend real socket coverage at transport cutover. |
| `client/go-cli/internal/cli/cli.go` / `client/go-cli/internal/cli/artifact_commands_test.go` | 2,306 / 869 | Change relevant command paths; avoid an unrelated whole-file rewrite. |
| `server/tests/unit/test_native_http_protocol.py` / `server/tests/integration/test_cli_entrypoint.py` | 1,394 / 1,245 | Reuse synthetic ASGI and installed Go-wheel fixtures; charge new authenticated/process scenarios separately. |
| `server/src/arbiter_server/storage.py` / `server/src/arbiter_server/config.py` | 57 / 376 | Private generation/authority/ACL recovery is mostly new work, not an existing backup subsystem. |

Read-through of these paths and the retained HLD probes informed the original
estimates. The two new LLDs use actual merged `25a8cf2` primitives and their named
missing work; old source-line counts above are not measurements of that baseline.
Probe code is not assumed production-ready or copied wholesale. No product code
or product tests were created/run by this design revision. Future streaming,
race/crash and installed-native acceptance remains implementation work, not proof
supplied by design checks or the existing baseline's green CI.

The per-child tables define scope, a focused minimum acceptance check, code ranges,
confidence and direct dependencies. Every child also owns relevant tests from the
shared contracts and its parent milestone: a parent acceptance requirement is not
postponed until its final closure PR when the behavior already exists. Closure PRs
exercise composition, native builds and additional process-level failure cases;
they are not permission to submit earlier untested behavior. Any new runtime
setting/format is documented and tested where first consumed. Each child includes
its applicable package fragment and command/operator guidance; final documentation
passes only reconcile the complete journey. Documentation effort is not included
in the handwritten-code totals, and must be called out before each PR starts.

### Historical estimate summary (2026-10-20)

The following table preserves the original 60-PR forecast for comparison. It is
not a statement that those cuts remain unstarted, an actual delivered-LOC count,
or a newly reviewed forecast for the remaining milestones. In particular, the
old rows for 2 and 3 are superseded for future work by the status and LLDs above.

| Milestone | Planned PRs | Production churn | Test churn | Combined churn |
| --- | ---: | ---: | ---: | ---: |
| 1 | 6 | 1,340–2,240 | 1,520–2,630 | 2,860–4,870 |
| 2 | 9 | 2,160–3,490 | 2,600–4,380 | 4,760–7,870 |
| 3 | 7 | 1,400–2,410 | 1,950–3,430 | 3,350–5,840 |
| 4 | 5 | 960–1,640 | 1,420–2,440 | 2,380–4,080 |
| 5 | 5 | 960–1,650 | 1,320–2,280 | 2,280–3,930 |
| 6 | 4 | 840–1,430 | 1,080–1,880 | 1,920–3,310 |
| 7 | 6 | 1,160–2,000 | 1,680–2,920 | 2,840–4,920 |
| 8 | 5 | 810–1,430 | 1,300–2,290 | 2,110–3,720 |
| 9 | 5 | 990–1,700 | 1,420–2,450 | 2,410–4,150 |
| 10 | 5 | 920–1,580 | 1,460–2,520 | 2,380–4,100 |
| 11 | 3 | 480–860 | 860–1,500 | 1,340–2,360 |
| **Total** | **60** | **12,020–20,430** | **16,610–28,720** | **28,630–49,150** |

These totals sum estimated PR churn, including edits to previously introduced code;
they are not final repository size or time estimates. Existing fixtures are reused.
Generated/binary fixtures, dependency locks and documentation have no credible
line forecast yet: quantify them at each child kickoff and separate them in the
review summary. Their absence here is uncertainty, not zero work. No fixed delivery
date or throughput promise follows from these ranges.

## Shared implementation contracts

### Code ownership and schemas

Add cohesive server identity/authority/transport/control modules beneath
`server/src/arbiter_server/`; add Go identity/state/carrier code beneath
`client/go-cli/internal/`. The exact module decomposition follows existing code
style; do not create a plugin/provider framework. `main.py` remains the ASGI
composition point, with immutable verified caller context and shared admission.
Existing `http_work.py`, `blocking.py`, artifact storage and account policies keep
their behavior except for the expressly planned identity/encryption checks.

Each slice commits its versioned schemas and shared positive/negative Python/Go
fixtures with the implementation that consumes them. Put synthetic protocol
fixtures in a clearly test-only shared location; fixed test keys never initialize
product trust. Schemas reject duplicate identities/JSON fields, unknown critical
fields, invalid lengths/encodings and trailing input. Sign/verify exact bytes;
ES256 is fixed-width `R || S`, not an ASN.1 signature or a reserialized payload.
No unspecified schema field may silently grant authority or select an algorithm.

Slice 1 fixes X.509/SAN URI/purpose/key-usage profiles, role trust stores, key-file
and recovery-file formats, authority row/revision semantics, and certificate
validity/renewal schedule specifications. Slice 5 owns owner-issuer and portable
certificate renewal; slice 6 owns server-authority and inner server-leaf renewal,
including trust-expiry recovery. Routine certificate renewal preserves the
appropriate key and durable IDs; it is not periodic server identity-key replacement.
Slice 2 fixes
Noise prologue/transcript/messages, pending/receipt state and inner-carrier ALPN.
Slice 4 fixes rotation/control proofs and acceptance state. Slice 5 fixes owner
recovery JWS and fresh challenge consumption. Slice 6 fixes server transition
proofs/history. Slice 7 fixes the authenticated artifact descriptor/age profile.
Slice 9 fixes backup records/manifest/signature and coherent snapshot semantics.
Slice 10 fixes durable active/undo publication and offline lock behavior.
Fixtures are reviewable parts of those PRs, not later unspecified integration work.

### Dependencies, platforms and defaults

Keep Python 3.10–3.14 and Go 1.24 as current compatibility floors; preserve the
existing Linux/macOS/Windows x64/arm64 CI matrix. Before adding each dependency,
select an exact tested compatible version set, review advisories/reachable uses,
record wheel/source-build needs and run its affected native matrix. The fourteen
Linux HLD probes are evidence of feasibility, not production dependency approval.
In particular, do not install pyOpenSSL26.3/cryptography49 under the current
`cryptography<49` constraint; evaluate a supported compatible set. Age's Rust
binding and the older Python Noise wrapper require explicit packaging/boundary
tests. Source builds may need build tools but must not create a separately
operated runtime service. Failure blocks the affected slice/release; changing
supported platforms or the HLD mechanism requires alignment, not silent narrowing.

Use structured named config fields owned by their domain. Carry HLD defaults
unchanged: 16 total carriers, 4 handshakes, 10-second handshake/control budgets,
256 KiB buffers and total handshake bytes per direction, 32 KiB headers/cert
chain, 64 KiB controls/WebSocket messages, no compression, five-minute idle with
active-operation exceptions, four client transfers and one administrative job.
Retain the Noise wire/plaintext limits, twenty pending requests, five authenticated
attempts, ten-minute enrollment lifetime, 30-day rotation, 24-hour fixed grace,
180-day inactivity and 365-day access certificates. Do not turn U53 into a
64-carrier default. Preserve existing worker/body/artifact limits and separate
connect/API/download-idle semantics. Validate types/ranges and expose effective
nonsecret settings through authenticated diagnostics.

For every public input, enforce aggregate byte/work limits before expensive
decode/crypto or allocation, including fragmentation, age headers, certificate
chains and backup manifests. Library convenience APIs are not admission control.
Reject overload without an unbounded queue; retain ownership until actual cleanup.

### Test layers and review evidence

Each PR has focused server/Go unit tests and a real Python-server/Go-client
prefix integration test for its behavior. Include real loopback sockets, a test
TLS-terminating WebSocket relay, restartable temporary directories and deterministic
clock/fault barriers. Isolated mock upstream SMTP/IMAP services supply fixtures;
no personal accounts or external mail effects. Library fixtures alone do not
prove application authorization, resource ownership or crash consistency.

Run relevant regressions, repository lint/type checks and required CI checks for
that PR. Commands include repo-local pytest targets, `.venv/bin/nox -s lint`
(Black uses one worker), and `go test ./...`/`go vet ./...` in `client/go-cli`.
Use existing supported test runners instead of bypassing their setup. The final
combined gate runs the full Python suite, server/Go integration and complete
supported matrix. Unavailable checks remain unverified and block relevant claims.

Before each PR is treated as complete, review its final code locally, resolve
findings, run its checks, checkpoint, submit and finish its remote PR review
cycle. Use actual DBB for security-sensitive changes with exact reviewed revision
evidence; if the optional reviewer workflow feature is unavailable, keep explicit
DBB orchestration. Changes after approval need appropriate re-review. No silent
reviewer substitution. This plan does not authorize automatic merging/deployment.

## Slice 1 — operator authority and local control

Historical note: the original child table in this section records the design cuts, not an unstarted queue. Authority/setup/local-control implementation is already merged at the source baseline above.


Deliver a complete operator setup/status journey: create durable owner/server IDs,
issuer and role-separated keys, external operator recovery file/public trust and
private SQLite authority state; inspect/manage it through authenticated Unix
socket or Windows named-pipe control. Recovery material stays outside running
server/agent/job storage. Status reports setup state without printing private keys.
An ordinary loopback connection cannot administer the instance. Initialize only
through trusted OS/SSH tooling; repeat setup cannot overwrite existing authority.

Scope includes OS ownership/ACL validation, private atomic file publication,
single-writer instance lock, exact credential profiles and shared Python/Go profile
fixtures. Keys/profile functions are exercised by real initialization and local
control, not a standalone abstraction PR. Register owner public trust before use.
Agent `NEVER SHARE THIS` custody and recovery-file combined powers are documented.

Acceptance: restart preserves IDs/trust; injected file/DB failures leave either
complete setup or safely recoverable unpublished staging; concurrent init/start
cannot create two writers. Wrong OS user, permissive ACL, unregistered owner,
wrong EKU/issuer/role/SAN, duplicate IDs and generic valid certs fail. Owner/recovery
private material never appears in server state, logs, argv, env or ordinary APIs.
Test native POSIX and Windows control boundaries. Add setup/trust documentation,
operator command help and an `arbiter-server` news fragment.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `1.1` — Private authority initialization | Create UUID-backed owner/server records and versioned SQLite authority schema through stopped-instance init/status; reject a second initialization. **Check:** Reopen preserves IDs; concurrent init and partial DB writes cannot publish two instances. | 200–340 | 220–380 | 420–720 | Medium | None |
| `1.2` — Role-separated credential profiles | Issue and validate setup credentials with explicit SAN/EKU/issuer profiles, exact certificate bounds and validity/renewal schedules. Publish shared profile fixtures consumed by Python now and Go in 2.1. **Check:** Reject cross-role, duplicate SAN, unregistered issuer, malformed DER and wrong key usage; verify profile time boundaries. | 260–420 | 280–450 | 540–870 | Low | 1.1 |
| `1.3` — External operator recovery file | Extend initialization with external recovery signer/decryptor, backup public configuration and registered public trust; keep private recovery material out of server state. **Check:** No overwrite or secret leakage; interrupted publication never reports successful setup with missing recovery trust. | 200–330 | 230–400 | 430–730 | Low | 1.2 |
| `1.4` — Durable application-state publication | Put initialized authority in a private generation with a durable active selector, instance lock and restart reconciliation; validate native ownership and ACLs. **Check:** Faults around flush/reference changes leave one coherent active state; Windows/POSIX locking prevents concurrent start. | 260–420 | 300–500 | 560–920 | Low | 1.3 |
| `1.5` — POSIX authenticated local control | Serve setup/status and bounded administrative messages over a private Unix socket; verify peer credentials and keep live DB writes server-owned. **Check:** Wrong UID, path replacement, oversized input and disconnect fail without loopback authority or leaked slots. | 190–330 | 230–400 | 420–730 | Medium | 1.4 |
| `1.6` — Windows authenticated local control | Provide the same real setup/status journey via named-pipe ACL and caller-token checks, with platform selection and trusted SSH instructions. **Check:** Native unauthorized caller, permissive ACL and pipe replacement fail; installed local commands work on both OS families. | 230–400 | 260–500 | 490–900 | Low | 1.5 |

## Slice 2 — pairing a real client and reconciling activation

Historical note: the original child table below retains parent identities and initial forecasts. The delivered stack used reviewed descendant cuts and is merged; existing-portable reuse remains the explicit `2.10` gap below.


Deliver `enrol` from a real Go CLI against a real initialized Python server:
generate provisional per-server/portable/recipient keys and 32-byte secret; closed
server explains opening; trusted operator opens a bounded window, lists/selects
request, supplies protected PSK and approves exact keys/grants once. Existing
portable identity requires registered issuer/fresh possession but no automatic
access. Client receives prepared trust/certs directly and durably saves before
ready proof; one authority transaction activates and returns its recorded receipt.

Implement production Noise framing and bounded WebSocket adapter together with
the minimal inner server-authenticated TLS receipt/status control required to
recover committed activation without PSK. Python/Go fixtures exercise exact
transcript/key/role/request bindings. This slice includes the real encrypted
transport dependency and platform checks; it does not defer a lost-reply path to
an unspecified future carrier. Ordinary operation dispatch is slice 3.

Acceptance: one secret handoff/approval, no return bundle or extra fingerprint
step; anonymous/wrong-PSK traffic cannot spend five attempts; identical authenticated
replays share one owner; five distinct failed/abandoned exchanges exhaust without
reset. Enforce original ten-minute deadline and twenty pending records across
reconnect/open/approval races. Fault/crash tests before ready, after client flush,
before/after commit and after lost reply prove one durable identity/grant/result.
Restart invalidates uncommitted PSKs only; denial beats stale completion. Abandoned
client/server state cleans within bounds. Add enrollment/recovery diagnostics,
custody guidance and fragments for server and native client packaging as applicable.

### Child PRs
| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `2.1` — Client provisional identity custody | Add real enrol key generation, private atomic client state and closed-server diagnostics; Go consumes 1.2 profile fixtures. **Check:** Restart, failed save and bad permissions leave recoverable provisional state; keys/PSK never reach argv, env or logs. | 240–380 | 260–430 | 500–810 | Low | 1.6 |
| `2.2` — Pending requests and operator approval | Add bounded public request registration and local open/list/approve/reject; bind immutable proposed keys/grants, protected PSK input and original deadlines. **Check:** Closed enrollment cannot open remotely; twenty pending entries include approved work; reopen/expiry races never reset deadlines. | 240–400 | 280–460 | 520–860 | Low | 2.1 |
| `2.3` — Bounded Python pairing responder | Wire binary WebSocket framing and Noise responder into approved pending requests with carrier/crypto reservations and authenticated first-flight accounting. **Check:** Anonymous failures spend no attempts; authenticated duplicates share one owner; five distinct attempts, byte limits and cancellation retain correct ownership. | 300–450 | 300–500 | 600–950 | Low | 2.2 |
| `2.4` — Go pairing initiator and transcript proof | Connect the real enrol command through the bounded WebSocket/Noise adapter; prove proposed keys and exact role/request transcript to the Python responder. **Check:** Wrong PSK/prologue/selector/key or fragmented oversized input cannot advance; a real Python/Go exchange verifies both peers. | 260–420 | 280–470 | 540–890 | Low | 2.3 |
| `2.5` — Prepared enrollment and durable client ready | Issue the owner-approved prepared result, verify it at the client and flush complete keys/trust/result before emitting ready proof. **Check:** Tampered prepared data, wrong issuer/IDs and save failures prevent ready; prepared certificates alone cannot authorize ordinary work. | 260–430 | 300–500 | 560–930 | Low | 2.4 |
| `2.7` — Server-authenticated inner TLS carrier | Implement bounded Python memory-BIO and Go TLS-over-WebSocket streams for fixed receipt control; verify remembered server and exporter, with no ASGI dispatch. No live activation is reachable yet; activated state exists only in synthetic test fixtures. **Check:** Wrong server/ALPN, overrun, blocked writes and cancellation fail; dedicated outer TLS exception leaks no inherited credentials or redirects. | 300–450 | 300–500 | 600–950 | Low | 2.5 |
| `2.8` — Authenticated receipt reconciliation | Implement exact-result receipt control and client reconciliation against synthetic already-activated fixture rows; no product activation route exists until 2.6. **Check:** Test lost reply, restart, wrong key, replay and denial using exact stored results without PSK; synthetic fixtures cannot become product trust or create another activation. | 200–340 | 260–450 | 460–790 | Low | 2.7 |
| `2.6` — Single activation and receipt | With 2.7/2.8 present, wire the first reachable ready/activation commit: conditionally persist identity, credential, grants and result; promote only from authenticated receipt/retrieval. **Check:** Real Python/Go crash/lost-reply/restart at ready/commit/receipt proves one durable result, authenticated promotion and denial precedence; retries create no second identity or grant. | 280–440 | 320–520 | 600–960 | Low | 2.8 |
| `2.9` — Pairing installation and overload closure | Complete prefix packaging/platform checks and process-level pairing failures, reusing earlier fixtures rather than deferring core assertions here. **Check:** Installed CLI pairs/reconciles; dependency builds, proxy fragmentation and simultaneous expiry/shutdown release real owners and keep health responsive. | 80–180 | 300–550 | 380–730 | Low | 2.6 |

### Proposed corrective parent `2.10` — existing portable identity enrollment

The [portable enrollment LLD](portable-enrollment-lld.md) owns the exact source
selection, independently registered issuer, v2 schema, custody/proof, unique
agent binding, migration and receipt contract. Its P01–P10 acceptance and four
complete implementation cuts are normative for this proposed correction:
`2.10.1 -> 2.10.2 -> 2.10.3 -> 2.10.4`, then `3.1.1`.
The first schema/backfill cut also installs binding maintenance in every fresh-v1
activation transaction; intermediate enrollments cannot evade later uniqueness or
denial. P05/P09 cover active/denied/inactive records created on both intermediate
prefixes. Each cut has its own real consumer and failure tests; `2.10.4` adds
installed fresh/reuse closure to the existing driver. The total is 2,150–3,015 handwritten
production/test/CI lines with all per-cut upper bounds below 1,000. This is no
reset of the exhausted milestone-2 allowance and no implementation authorization.
Existing-enrollment repair stays in milestone 5 and does not substitute for P01.

## Slice 3 — native API, account grants and live denial

Route every ordinary CLI discovery/schema/invocation/download constructor through
the expected-server-verified inner TLS 1.3 transport, including errors and tool
paths. Preserve API shape and existing policies after `(plugin, account)` grants.
Exercise the native factory through private peers in `3.5.1`; keep production
constructor selection on its working route until the atomic client/server switch
in `3.5.2`, with no anonymous fallback after that cut.
Reject anonymous/proxy-header callers and hide ungranted account information from
discovery and direct routes. Provide local grant add/remove and enrollment denial.
Creator/account caller context is mandatory for subsequent artifact work.

Implement shared coordinator admission and output cancellation across connections;
ordinary TLS checks exact live enrollment/accepted key, certificate and role.
Use `agent_enrollments` as the sole agent-to-enrollment relation; live admission
must not create an independently authoritative `enrollments.agent_id` copy.
Remove anonymous legacy API/artifact routing in the same cutover, invalidate
server-managed ownerless artifacts without guessing ownership or touching mail/
client files. No artifact storage fallback may bypass caller context. Keep
setup/health/bootstrap separate, and missing authority fails closed.

Until slice 7 installs the encrypted store, slice 3 must replace the plugin-facing
artifact creation/inspection dependencies with an explicitly closed adapter.
Reject artifact creation before calling the legacy store or creating a payload
file; never delegate to it, return a usable descriptor or expose nonce-only access.
This is an implementation-readiness boundary, not a client policy/allowlist or
operator bypass flag. Errors report any already-completed upstream effect honestly
and never trigger automatic operation replay. Slice 7 replaces this closed adapter
with the encrypted creator-bound adapter through the same caller-context boundary.

Acceptance: verified enrolled Go client invokes a granted synthetic operation;
portable/cross-server credentials cannot. Denial/grant removal wins races against
queued admission, request bodies, upstream dispatch and ongoing output; retained
blocking work keeps its slot until exit. Password renewal does not require grants;
repointed names keep deliberate grants. Test substituted server, changed outer
cert/proxy, malicious headers/redirects, byte/control limits, slow/chunked bodies
and unchanged timeout budgets; no native secret reaches the relay. Add account
grant/transport/proxy documentation, server/client fragments and skill guidance
where discovery/enrollment instructions change.

The slice-3 prefix test must also invoke an artifact-producing operation through
the real authenticated Python/Go path before slice 7. Assert the closed adapter
rejects before legacy store entry/payload publication, no plaintext or ownerless
file/metadata/nonce/usable descriptor is created, no legacy route can read one,
and cancellation/repeated failure release bounded staging and worker ownership.
Use a legacy-store spy plus filesystem inspection, including restart cleanup;
the final no-publication gate alone is not this acceptance evidence.

### Current proposed cuts and dependencies

The [milestone-3 LLD](milestone-3-lld.md) owns exact N01–N12 acceptance, complete
per-cut scope and production/test/CI ranges. Its 13 cuts replace the historical
seven rows; do not add the two forecasts together. All work remains unimplemented.

| Cut | Direct implementation prerequisite | Owning result / acceptance |
| --- | --- | --- |
| `3.1.1` | `2.10.4` | Shared carrier/pump with real existing-control and streaming consumers; N03/N04. |
| `3.1.2` | `3.1.1` | Strict Python ordinary mTLS peer; N01. |
| `3.1.3` | `3.1.2` | Go native identity/dial and standard HTTP transport peer; N01/N02/N11. |
| `3.1.4` | `3.1.3` | Bounded single-request HTTP/ASGI, absolute request-receipt timer and pre-body admission; N03/N04. |
| `3.2.1` | `3.1.4` | Existing enrollment-row live fields/grants and migration; first OS enrollment-deny control and minimal shared coordinator guard the real fixed receipt through final output/actual-owner cleanup; N05/N07 receipt races. |
| `3.2.2` | `3.2.1` | Extend the same coordinator, writer/read epochs and actual worker ownership to ordinary authority; OS grant add/remove controls; N04/N05/N06/N07. |
| `3.3` | `3.2.2` | Discovery/direct/policy/error filtering; N06. |
| `3.4` | `3.3` | Real dispatch caller context, closed artifact dependency/inline/legacy cleanup; N09. |
| `3.6.1` | `3.4` | Cross-connection body/dispatch denial and retained actual workers; N07/N08. |
| `3.6.2` | `3.6.1` | Final output permission after backpressure and abort without revoked flush; N03/N07/N08. |
| `3.5.1` | `3.6.2` | Every CLI constructor and explicit enrollment selection through private real peers; production selection retains its working route. N10/N11. |
| `3.5.2` | `3.5.1` | Atomic production CLI/native server switch, anonymous closure and first installed denial/artifact prefix; N01/N04/N07/N09/N10/N12. |
| `3.7` | `3.5.2` | Installed relay/timeouts/native composition and full N01–N12 closure. |

All `3.1` descendants close parent 3.1, both `3.2` descendants close 3.2, both
`3.6` descendants close 3.6 before cutover, and both `3.5` descendants close 3.5.
Later references to a parent still require all its descendants and aggregate
acceptance. The first public ordinary prefix includes denial and safe artifact
failure; final 3.7 is not a budget for missing earlier mechanisms or tests.

## Slice 4 — rotation, late completion and bounded fallback

Milestones 4–11 below retain their approved scope/order and historical outlines.
Their child boundaries and estimates require source-based LLD refinement/review
before implementation. This design revision does not claim those future cuts
are already sized or predictive after milestone-2 implementation changes.

Deliver on-contact due rotation, fixed grace, durable candidate save/confirmation
and lost-response reconciliation. A pending deadline is recorded before asking
for candidate; withholding/retry/old ordinary use cannot move it. Current,
predecessor and pending candidate form coherent client state. Use the separate
fixed control carrier to finish the exact rotation after grace or cert expiry;
ordinary mTLS stays strict. Control proofs bind both stored keys, fresh challenge,
TLS exporter, identities, purpose and pending revision. Reissue only that same
candidate certificate; no general expired-credential exception.

Acceptance: test 30-day/24-hour/180-day boundaries, old/candidate expiration,
candidate-withholding, disconnect and restart at every save/commit/receipt point.
Ordinary access remains until grace, then only pending completion is possible;
incomplete controls cannot refresh inactivity. Revocation/owner repair wins every
activation race. Receipt survives needed expiry/reconciliation intervals. One
predecessor authentication attempt requires unambiguous authenticated rejection
by the same verified server; never after denial/timeout/unverified peer or operation
submission, and never replay uncertain mutation/GET. Test across both languages
with real controlled clocks/sockets and malicious control transcripts. Add lifecycle
configuration/help, repair guidance and server/client fragments.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `4.1` — Server pending rotation and fixed grace | Prepare a due transition on authenticated contact, bind one candidate and preserve current ordinary access until its original deadline. **Check:** Controlled 30-day/24-hour/180-day boundaries, withheld candidates and restart cannot slide grace or inactivity. | 220–360 | 260–450 | 480–810 | Low | 3.7 |
| `4.2` — Durable candidate confirmation | Save candidate state in Go, prove possession and conditionally activate/promote with one retained predecessor and idempotent result. **Check:** Crash/lost reply at save/activate/promote cannot install an unsaved key, clear denial or duplicate transition. | 240–400 | 300–500 | 540–900 | Low | 4.1 |
| `4.3` — Expired-key restricted completion | Add the fixed exporter-bound old-plus-candidate control and reissue only that pending candidate after grace/certificate expiry. **Check:** Ordinary TLS remains strict; wrong pending purpose/revision, replay, inactivity and denial cannot complete or refresh deadlines. | 260–420 | 300–520 | 560–940 | Low | 4.2 |
| `4.4` — Authenticated predecessor assistance | Return authenticated credential status and permit one authentication-only predecessor attempt after the same verified server rejects current credentials. **Check:** Never fallback after timeout, unverified peer, denial or submitted operation/consuming GET; superseding recovery discards history. | 160–280 | 240–420 | 400–700 | Low | 4.3 |
| `4.5` — Lifecycle race and clock closure | Exercise the implemented transitions through process restarts and competing denial/recovery, with final lifecycle diagnostics and retained-receipt cleanup. **Check:** Full Python/Go boundary matrix preserves unattended completion where permitted; incomplete exchanges cannot keep an inactive enrollment alive. | 80–180 | 320–550 | 400–730 | Low | 4.4 |

## Slice 5 — owner-authorized client recovery

Deliver trusted owner tooling to authorize a fresh per-server access key or
portable-key repair for an existing durable identity. Consume a server-specific,
fresh challenged ES256 authorization and replacement-key possession proof in one
conditional authority transaction. Signature supplies approval; no second remote
operator approval or grant recreation. Preserve suspensions/revocations. Local
pairing repair of the existing enrollment remains the simple alternative.

Acceptance: two isolated servers register the same owner trust but distinct access
keys; owner tooling produces per-server proofs and repairs both without sharing
issuer private keys or trusting another server's credential. Portable possession
alone fails. Wrong algorithm/key/purpose/server/revision/expiry, replay, concurrent
recovery/rotation/denial and lost response fail or reconcile exactly once. Revoked
enrollment remains revoked after repair. Owner/recovery file stays outside agent
and server custody; missing proof offers explicit repair, not identity reset.
Add recovery-file theft consequences, CLI journey and server/client fragments.

This slice also implements owner-issuer and portable-leaf certificate renewal
using slice 1's selected schedules, registered signer and live-state checks while
preserving owner/agent IDs and purpose separation. Controlled-clock tests cover
before-expiry renewal, signer/key mismatch, denial and renewal races, rejection
of portable credentials on ordinary TLS, restart and missed-renewal diagnostics.
Certificate renewal must not become an automatic access-key replacement or grant.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `5.1` — Owner-approved pairing repair | Extend local pairing for an existing enrollment with explicit owner selection while retaining durable identity, grants and denial. **Check:** New-client enrollment cannot silently select an existing identity; repair invalidates superseded pending/current credentials without regranting. | 180–300 | 240–420 | 420–720 | Low | 4.5 |
| `5.2` — External owner challenge proofs | Implement strict ES256 recovery statements, externally held signer tooling and fresh server-bound challenge with replacement possession proof. **Check:** Shared Python/Go fixtures reject wrong algorithm, purpose, signer, server, expiry, duplicate fields and changed signed bytes. | 220–370 | 280–480 | 500–850 | Low | 5.1 |
| `5.3` — Atomic access-key recovery | Consume the exact challenge and replace access state in one conditional live-authority transaction; reconcile lost response. **Check:** Recovery/rotation/denial races consume once; revoked state stays revoked; no second per-server approval or issuer private-key sharing. | 200–330 | 260–450 | 460–780 | Low | 5.2 |
| `5.4` — Portable identity repair across servers | Apply the owner-authorized portable replacement and orchestrate distinct per-server recovery proofs using the same external owner trust. **Check:** Two real isolated servers keep independent access keys and grants; portable possession alone cannot replace access or trust another server credential. | 180–320 | 280–480 | 460–800 | Low | 5.3 |
| `5.5` — Owner and portable certificate renewal | Implement authorized before-expiry owner-issuer/portable-leaf renewal under 1.2 schedules, keeping purpose and durable IDs. **Check:** Controlled time, wrong signer, denial, concurrent renewal and restart cannot grant native access or masquerade as access-key rotation. | 180–330 | 260–450 | 440–780 | Low | 5.4 |

## Slice 6 — server identity replacement

Deliver operator-triggered planned authority replacement with old endorsement,
new-key possession, stable server ID and durable client adoption/transition
history. Separately deliver compromise repair using the independently registered
external owner verifier, never compromised server key alone. URL/HTTPS renewal
does not trigger identity rotation. Private/public-key roles remain distinct.

Acceptance: online and offline clients recognize legitimate replacement, including
lost transition response/restart and later key changes. Wrong ID, missing history,
expired/untrusted chain or endorsement by compromised key alone cannot install new
trust; missing independent authority gives explicit owner pairing guidance. Client
never sends credentials/capabilities before verifying the expected server or
authorized repair. Neither path clears enrollment denial or duplicates grants.
Test installed-client state and preserved history with real Python/Go peers.
Document planned versus compromised-key procedure and add server/client fragments.

This slice separately implements server-authority and inner-server-leaf certificate
renewal under slice 1's validity/schedule profiles, without scheduled identity-key
rotation. Test authorized before-expiry renewal with stable server ID/key,
appropriate issuer/live-state checks and clients retaining remembered trust.
Controlled-clock negative tests reject peer-provided renewal of an expired server
trust anchor; missing the trust-validity deadline requires the independently
authorized owner recovery path (or explicit pairing when that trust is missing).
Test expiry/restart/renewal races and ensure HTTPS renewal/URL changes do not replace
native identity or silently extend authority. Keep these tests distinct from
access-key rotation and planned server-key replacement.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `6.1` — Planned server transition publication | Produce old endorsement plus new possession with stable server ID and durable transition history through operator tooling. **Check:** Wrong identity/key, incomplete publication and restart cannot install unsigned trust or lose the prior complete history. | 200–340 | 260–450 | 460–790 | Low | 5.5 |
| `6.2` — Client transition adoption | Fetch and verify legitimate transition chains, then durably adopt server trust for online and long-offline clients. **Check:** Missing/interleaved history, replay and failed save reject safely; credentials are withheld until destination identity is verified. | 200–340 | 260–450 | 460–790 | Low | 6.1 |
| `6.3` — Independent compromise recovery | Use registered external owner authorization for server-key compromise recovery and propagate explicit client repair diagnostics. **Check:** Stolen old server key alone cannot bless a replacement; owner proof cannot clear client denial, change server ID or duplicate grants. | 220–370 | 280–480 | 500–850 | Low | 6.2 |
| `6.4` — Server authority and leaf renewal | Renew server-authority and inner-server-leaf certificates before expiry without scheduled identity-key replacement; use independent owner recovery after missed trust validity. **Check:** Controlled clocks reject expired peer-driven trust renewal; stable IDs/keys, restart and renewal races work; outer HTTPS renewal changes no native trust. | 220–380 | 280–500 | 500–880 | Low | 6.3 |

## Slice 7 — encrypted server artifacts and ownership

Deliver server-side age encryption for the creator's registered recipient and
server storage recipient, authenticated origin descriptors and ciphertext delivery
through the native API. Thread immutable caller/account context into all plugin
artifact creation, including inline native descriptors; a plugin cannot choose
another creator. Keep server-readable inspection possible without adding scanning.
Recipient registration needs authenticated proof/new-key challenge and retained
key history is bounded by active artifact lifetime; auth rotation cannot discard
needed recipient keys. No persistent plaintext staging is introduced as a shortcut.

Acceptance: creator plus nonce plus live enrollment/account grant is required for
metadata/HEAD/GET; shared account, copied URL or revoked grant fails. HEAD is
non-consuming; GET atomically consumes before open, including failed streams.
Test nonce/consume/revoke/store-lock races, slow writer/disconnect, restart, orphan
cleanup and quota reservations through actual cleanup. Preserve 50 MiB plaintext,
2,000 artifacts, 1 GiB physical, ten-minute idle and 24-hour maximum; count all
crypto/metadata/staging overhead without lowering plaintext cap. Mock IMAP/SMTP
flows exercise real artifact adapters. Legacy cleanup is idempotent and confined
to server-managed ownerless files. Add storage/ownership docs and affected server/
plugin fragments; server encryption is not whole client-processing completion.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `7.1` — Artifact recipient registration and retention | Register fresh recipient possession through authenticated identity, persist bounded client/server recipient history and separate it from access-key rotation. **Check:** Wrong origin/key/version or unauthenticated change fails; keys needed by active artifacts survive access rotation until bounded expiry. | 220–370 | 260–450 | 480–820 | Low | 6.4, 3.6 |
| `7.2` — Encrypted payload and metadata writing | Add streaming age dual-recipient creation and encrypted descriptors behind the closed plugin adapter; preserve server inspection capability. **Check:** Python/Go fixtures prove both recipients, wrong-key/tamper/truncation failure and no persistent plaintext staging; disabled delivery cannot publish a usable nonce. | 240–400 | 280–480 | 520–880 | Low | 7.1 |
| `7.3` — Physical quota and encrypted-store recovery | Integrate ciphertext/metadata/staging reservations, 50 MiB plaintext and existing count/storage/retention limits, plus restart orphan cleanup. **Check:** Near-cap overhead, full disk, concurrent writers, cancellation and open readers hold reservation until actual cleanup; no cap is silently lowered. | 220–360 | 280–480 | 500–840 | Low | 7.2 |
| `7.4` — Creator-bound HEAD and consuming GET | Authorize descriptors/HEAD/GET by creator, account grant and nonce, with atomic consume-before-open and authenticated origin. **Check:** Shared account/copied URL/revocation fails; HEAD never consumes; failed/slow GET stays consumed across restart. | 220–370 | 300–500 | 520–870 | Low | 7.3 |
| `7.5` — Plugin adapter and descriptor activation | Replace the closed adapter with creator-bound encrypted creation for all artifact-producing and inline result paths, retaining legacy-route closure. **Check:** Real mock IMAP/SMTP and Python/Go dispatch cannot choose another creator or return a legacy descriptor; ownerless cleanup stays confined. | 180–320 | 260–460 | 440–780 | Low | 7.4 |
| `7.6` — Artifact contention and packaging closure | Complete real storage-lock/consume/revoke/stream races and age dependency/install checks on supported platforms. **Check:** Health stays responsive under valid/invalid nonces; disconnect/shutdown retain file/crypto owners until cleanup; no replay reopens GET. | 80–180 | 300–550 | 380–730 | Low | 7.5 |

## Slice 8 — four client artifact paths

Deliver immediate plaintext stdin, fully verified encrypted-temp-to-stdin,
explicit plaintext-temp pathname and deliberate `artifact save-decrypted`.
Authenticated descriptor and key remain private process state; downloaded metadata
cannot nominate origin. Verified modes finish digest/length/age finalization and
source checks before tool launch/publication. Keep stable ciphertext between
passes through proven platform handles or the HLD's bounded immutable snapshot.
Do not select a universal mode/default or preserve an ambiguous `save` alias.

Acceptance: tamper, truncation, wrong origin/creator/recipient and changed staging
path cannot start a tool in verified modes. Immediate stdin may deliver valid
prefix, but reports final failure honestly. Test POSIX writable-alias removal,
Windows sharing/ACL constraints, fallback reservation, four-transfer saturation,
full disk, child failure, signals/cancellation and crash cleanup. No orphan file
resumes a consumed GET; plaintext temp/export require explicit action and no
partial final export. Access/recipient rotation and grant denial during download
compose with slices 3/4/7. Explain delay/storage/partial-effects/plaintext exposure
in both client reference and operation help, discourage persistent agent exports,
and add native-client fragments and affected skill updates.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `8.1` — Immediate authenticated plaintext stdin | Decrypt the one native GET directly to child stdin using the authenticated descriptor and bounded process ownership. **Check:** Final digest/EOF failure reports honestly after any valid prefix; no automatic retry, sensitive stdout or default plaintext file. | 180–320 | 260–450 | 440–770 | Medium | 7.6 |
| `8.2` — Verified encrypted-temp stdin | Reserve ciphertext staging, fully verify to a discard sink, then process exact immutable bytes via native handles or bounded memory fallback. **Check:** Path replacement/writable aliases, tamper/truncation and failed verification cannot start tools on POSIX or Windows; four transfers bound fallback memory. | 260–420 | 300–520 | 560–940 | Low | 8.1 |
| `8.3` — Explicit plaintext-temp path | Require explicit selection, privately stage and fully verify plaintext before tool launch, and clean up on exit/cancellation. **Check:** Bad origin, child failure, signals and path abuse cannot expose unverified files or publish a final artifact; document best-effort crash cleanup. | 150–260 | 220–400 | 370–660 | Medium | 8.2 |
| `8.4` — Deliberate save-decrypted export | Rename intentional persistent plaintext export without the ambiguous save alias; verify private staging before atomic final publication. **Check:** No partial output on full disk/tamper/cancellation; overwrite permissions and explicit user-facing warnings are tested. | 140–250 | 220–400 | 360–650 | Medium | 8.3 |
| `8.5` — Four-mode process and platform closure | Finish CLI/skill tradeoff guidance and real child-process, cleanup and rotation/revocation composition checks without adding a universal mode default. **Check:** Installed tools demonstrate all four paths; consumed GET cannot be resumed from an orphan; native platform process/handle limits are enforced. | 80–180 | 300–520 | 380–700 | Low | 8.4 |

## Slice 9 — useful consistent encrypted export

Deliver local/SSH export usable by an external startup/periodic job: capture one
saved config/policy/actual-secret/authority generation, briefly fence mutations,
take coherent SQLite snapshot, then stream signed manifest/records through age to
the registered external recipient. Capture configured secret-file values without
arbitrary environment dumps; changed source during capture aborts before publish.
Exclude the HLD omissions, especially sessions, PSKs, client private keys and
transient artifact payloads. Operator file/public metadata and encrypted bundle
are clearly distinct; server/job has no private recovery signer/decryptor.

Acceptance: enroll/grant/revoke/rotate/config reload during export yields one
coherent snapshot. Secret-source changes, output failure/cancellation/disk full,
oversize/header/manifest abuse and crash preserve the previous complete export;
never expose partial final bundle or plaintext staging. One admin job at a time,
4 GiB total plaintext/10,000 entries/64 MiB per entry/1 MiB manifest and bounded
encrypted header/overhead parsing are enforced before growth. Verify exact signed
manifest/file digests using external trusted metadata in a round-trip harness,
not a signer supplied by the bundle. Include job examples for durable storage and
failure reporting without adding scheduler/cloud integration/first-enrollment
gate. Add backup CLI/docs and an arbiter-server fragment.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `9.1` — Saved configuration and real secret capture | Extend private generations to active config/policies and materialized configured file-backed secrets on activation/reload. **Check:** Changed-source capture aborts before publication; arbitrary environment/host files never enter saved state; normal reload remains coherent. | 240–400 | 280–480 | 520–880 | Low | 8.5, 1.4 |
| `9.2` — Coherent authority snapshot | Fence live mutations briefly and capture SQLite plus the exact saved config/key/secret generation, releasing the fence before encrypted streaming. **Check:** Concurrent enroll/grant/revoke/rotate/reload yields one coherent snapshot; failure/shutdown releases the real admin owner and preserves service state. | 200–340 | 280–470 | 480–810 | Low | 9.1 |
| `9.3` — Signed bounded recovery records | Write versioned uncompressed records and exact-byte ES256 manifest; provide strict verification primitives consumed by export self-checks and later restore. **Check:** Reject duplicate paths/fields, links, traversal, oversized entries/count/manifest and bundle-supplied verifier substitution in shared synthetic fixtures. | 250–420 | 300–520 | 550–940 | Low | 9.2 |
| `9.4` — Encrypted local and SSH export | Stream the coherent snapshot through age to external recipient with one admin job and safe publication of complete outputs. **Check:** Private recovery signer/decryptor never enters server/job/bundle; 4 GiB limit, disconnect/full disk and cancellation preserve last good bundle. | 220–360 | 280–480 | 500–840 | Low | 9.3 |
| `9.5` — External job and concurrent-export closure | Document startup/periodic external scheduling, durable destination/alerting and round-trip verifier usage; finish process-level snapshot contention tests. **Check:** Real local/verified-SSH jobs produce usable signed encrypted snapshots; excluded transient artifacts/PSKs/client keys remain absent across failures. | 80–180 | 280–500 | 360–680 | Low | 9.4 |

## Slice 10 — offline restore and latest undo

Deliver offline restore while the server is broken/stopped, using the external
operator file to decrypt/authenticate a complete bundle privately. Verify strict
records/manifest/origin/hashes/key correspondence/config before publishing a fresh
saved generation. Use exclusive stopped-instance lock and one durable transaction
for active/undo references; failed restore preserves current state and previous
undo point. Preserve saved deadlines, not restore-time authority extensions.

Deliver offline undo of that latest successful restore, without expiry or a
post-use hard cutoff; explicit discard or next successful restore replaces it.
Warn very prominently about snapshot date, lost post-snapshot/post-restore local
changes, restored grants/revocations and upstream effects that cannot be undone.
No silent merge, restore wizard, redo history, paused-access mode or acceptance
ceremony. Ordinary different-host edits remain available before start.

Acceptance: real process termination/fault injection before/after flush and each
reference update leaves an old or new coherent generation on native POSIX/Windows.
Reject malformed/duplicate/traversing/link/device/oversized records and wrong
external verifier; no partial activation. Restore to an empty host and over an
existing instance; test old snapshots, client current/predecessor divergence,
pairing repair, revocation preservation within the selected snapshot, repeated
restore failure, undo after use and explicit discard. Show clear repair diagnostics
without claiming to know unseen client history. Add full restore/undo examples,
host-configuration exclusions and arbiter-server fragment.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `10.1` — Strict offline bundle validation | Decrypt against external trust, validate all record/hash/EOF/certificate/key/config constraints before exposing a restorable generation. **Check:** Malformed/traversing/duplicate/oversized records, wrong verifier and incomplete stream fail with bounded I/O and no active-state changes. | 240–400 | 300–520 | 540–920 | Low | 9.5 |
| `10.2` — Stopped-instance private restore staging | Require exclusive stopped-instance lock, available-disk reservation and same-filesystem private staging; reconcile incomplete restore on restart. **Check:** Concurrent server start/restore, disk failure and real process termination preserve active generation and existing undo. | 200–340 | 280–480 | 480–820 | Low | 10.1 |
| `10.3` — Atomic full restore activation | Publish verified generation with active/undo references in one durable control transaction, retaining saved lifecycle timestamps and host exclusions. **Check:** Empty/existing hosts restore coherently; faults at flush/reference boundaries choose only old or new complete state, never merge or extend authority. | 220–360 | 300–520 | 520–880 | Low | 10.2 |
| `10.4` — Latest undo and explicit discard | Implement offline undo without expiry or post-use hard cutoff; retain the point through ordinary operation and warn prominently before discarding later changes. **Check:** Failed restore preserves undo; next successful restore replaces it; undo after use and discard have no redo/history or upstream rollback promise. | 180–300 | 260–450 | 440–750 | Low | 10.3 |
| `10.5` — Restore-client and native crash closure | Complete controlled process-crash and native durability tests, stale client/predecessor diagnostics and useful full restore/undo examples. **Check:** Multiple paired clients either recover within bounded fallback or require explicit repair; old snapshot denial/deadlines reflect snapshot, not guessed unseen history. | 80–180 | 320–550 | 400–730 | Low | 10.4 |

## Slice 11 — cooperative migration and handoff

Deliver maintenance/migration tooling and runbook: disable source ordinary work
and state-changing admin, drain/cancel real owners, export final snapshot, restore
stopped destination, edit host settings, start destination only and keep source
disabled. Optional source handoff is a fixed authenticated URL response using
saved source identity, without ordinary dispatch/enrollment/grant/rotation writes.
Client obtains URL before operation submission, verifies the same durable identity
and legitimate current key-transition chain at destination, then stores the URL.
No HTTP redirect or repair replays uncertain mutation/consuming GET.

Acceptance: multiple previously paired clients migrate with distinct grants,
pending/completed rotations and receipt state. Test destination identity mismatch,
later destination key replacement without changing old handoff URL, offline/old
URL failure, lost response and explicit URL edits. Clients send no credentials/
nonces to an unverified destination. Source mode cannot mutate authority. Post-use
undo/fallback warns of stale state and never automatically starts the old host.
The operator, not a new epoch/fencing/cluster mechanism, prevents two writable
copies. Run actual Reploy staging on its separate port using synthetic state;
no live installation migration is authorized by this plan. Add maintenance/handoff
docs, client diagnostics and affected server/client fragments.

### Child PRs

| PR | Scope and focused acceptance | Production | Tests | Total | Confidence | Direct prerequisites |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `11.1` — Cooperative maintenance and migration | Disable source ordinary work/admin mutations, drain real owners, export final snapshot and restore stopped destination under documented operator coordination. **Check:** Source stays disabled through failures; no new fencing/epoch/cluster mechanism or automatic old-host restart is introduced. | 180–300 | 260–450 | 440–750 | Low | 10.5 |
| `11.2` — Verified source URL handoff | Serve only a fixed authenticated destination URL from disabled source; client verifies same durable identity/transition chain before adopting and sending any credential. **Check:** Mismatch/unavailable URL/later destination replacement/lost response never replays submitted mutation or GET; handoff cannot mutate authority. | 220–380 | 280–500 | 500–880 | Low | 11.1 |
| `11.3` — Migration and combined release evidence | Finish synthetic multi-client Reploy staging on its separate port and the full combined journey/matrix; reconcile release/help/skill/dependency metadata. **Check:** Migration with grants/pending rotations and post-use undo is honest; all required checks pass or remain explicitly unverified and block publication. | 80–180 | 320–550 | 400–730 | Low | 11.2 |

## Combined acceptance and publication gate

Every slice runs prefix integration; do not defer first Python/Go composition,
platform packaging or cancellation testing until this gate. Once all child PRs across the eleven milestones
have approved final heads and required checks, run the complete synthetic journey:

1. Fresh private setup with no public CA/DNS or external identity account; enroll
   first and additional clients with one handoff/approval, restart and reconcile.
   Reuse one portable identity at a separately registered second server with
   distinct access/grants and no source-server availability requirement (P01–P10).
2. Authenticated discovery/direct invocation of mock SMTP/IMAP accounts with
   distinct grants, existing policies, grant change/denial and concurrent output.
3. Due rotation, fixed grace, expired-certificate control completion, inactivity,
   predecessor fallback, owner client repair, owner-issuer/portable/server-authority/
   server-leaf certificate renewal and server-key replacement, including refused
   expired peer-driven trust renewal and explicit owner recovery after missed validity.
4. Encrypted artifacts through all four modes with origin/recipient checks,
   failed consuming GET, full limits, quota contention and real cancellation.
5. Concurrent-state export, offline restore, failed restore preserving undo,
   after-use undo/repair, maintenance migration and non-mutating URL handoff.

Run combined contention/cancellation/deadline/cleanup checks so carrier, crypto,
HTTP/body, plugin worker, file/quota and admin ownership cannot each pass while
their composition leaks work. Run all Python tests and Go tests/vet, lint/type
checks, client/server builds/install smoke, supported Python/OS/CPU matrix and
Reploy staging regressions. Public proxy tests include TLS termination with an
untrusted inspecting relay, changed outer cert, and TCP passthrough; future
browser administration remains unimplemented, with its documented trust boundary.

Published help/docs/skill, package dependency metadata, release notes and upgrade
guidance must agree with the final implementation. Existing pre-auth clients get
actionable enrollment guidance, not an unauthenticated fallback. Do not require
live accounts to prove safety or mark unavailable checks as passed. Remaining
failures require fixes/re-review or an explicit requirements/HLD decision; no
scope reduction, platform drop or readiness claim is hidden in release wording.

Approval of this revision attests its design, ordering and verification
obligations. Implemented/merged state is reported explicitly above; the newly
proposed work begins only on separate implementation authorization. Reviews and exact-byte stamp evidence are stored outside this
document so recording its verdict does not invalidate the reviewed plan.
