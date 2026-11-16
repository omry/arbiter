# Estimate basis and ownership

These are low-confidence engineering ranges, not future measured diffs or a
statistical confidence interval. They were assigned to the complete consumed
changes after inspecting the merged source, not by applying a percentage to
old counts. Display values are incremental final nonblank physical code/test
content; comment-only lines are excluded and docstrings count physically.

The separate authored-churn envelope allows 180 lines of replacement/motion/
comments for3.1.1; 100 for3.2.1.1/9.1; and80 for other cuts, in addition to upper
final-content and CI estimates. This is a conservative **forecast**, not proof
of the cap or a standing correction allowance. Source-dependent headroom can be
wrong. Recheck it against the actual predecessor and return to design if a cut
cannot fit the unchanged1,000-line authored change maximum. No test deletion,
code compression, fixture relabeling or extra review budget is authorized.

## Current inspected foundations

Physical source lines below are inspection context, not the estimate unit.

| Source at merged baseline | Physical lines | Reuse and missing change |
| --- | ---: | --- |
| authority.py / credentials.py | 498 / 550 | Private SQLite/instance/certificate roles exist; live lifecycle, recovery and renewal writers remain work. |
| generations.py / recovery.py | 224 / 274 | Initial-only generation and external key custody exist; full config/secret capture, active+undo transaction and backup codec are new. |
| enrollment_activation.py / enrollment_receipt.py | 165 / 129 | Exact prepared and durable completion exist; live row/coordinator guards extend them without copying identity authority. |
| artifacts.py / http_work.py / blocking.py | 570 / 170 / 102 | Store/quota/HTTP bridge/actual worker retention exist; encryption, creator/account and live permission are new. |
| local_control.py | 463 | OS-authenticated owner boundary exists; commands extend this owner rather than introducing remote admin. |
| Go credentials.go / control_tls.go | 428 / 271 | Strict roles and bounded carrier exist; ordinary HTTP stream and lifecycle are not implemented. |
| Go provisional_store.go / enrollment_receipt.go | 251 / 141 | Private immutable custody/publication exists; linked 4.2.1 assigns the distinct native mutable lifecycle publisher and its platform failure tests, while linked 8.4.1 consumes an exported artifact-safe no-clobber facade over that primitive; both are planning obligations, not existing guarantees. |
| Go HTTP client.go / timeouts.go / CLI cli.go | 212 / 306 / 2,309 | Existing HTTP/phase timers/process helpers are reused; only selected constructors and command paths change. |

## Per-area sizing rationale

Each slice's exact responsibility, ownership files, dependency and completion
check are in the canonical inventory. These component groups explain its range.
They are overlapping read surfaces, not independent code packages counted again.

| Cuts | Production components charged once | Test work included in those cuts |
| --- | --- | --- |
|3.1.1–3.1.4.1|Shared carrier extraction/credit pump; strict TLS candidate; Go dial/HTTP factory; h11-ASGI body/response adapter. Linked3.1.4.1 also assigns the existing structured limits.py owner and configured receipt-deadline validation; no second HTTP client or timer stack.|One real cross-language stream harness at3.1.1, extended for roles/relay/partial BIO/framing/body admission and failure peers;3.1.4.1 owns configuration/default/rejection and configured-deadline acceptance through the existing pairing seam.|
|3.2.1.1/3.2.2|Linked3.2.1.1 owns one live schema/backfill through the existing authority.py/owner_issuers.py startup owners, then one coordinator/writer barrier, fixed-receipt permission and local denial first; ordinary tokens/grant controls extend it.|Existing authority/owner-issuer migration tests plus activation/receipt tests cover valid schema3/schema4, fresh setup, restart, rollback/interruption, malformed/conflicting rows, unknown versions, corruption/backfill, deterministic revoke/snapshot/final-write and worker-handoff barriers. No parallel schema mechanism.|
|3.3–3.4|Grant projections through existing schema/policy paths; actual worker caller context; small closed concrete artifact adapter/legacy descriptor closure.|Existing native/admission/store fixtures with hidden-information and real spy/filesystem assertions. No new copy of every discovery fixture.|
|3.6.1–3.7|Cross-connection cancel/final-write ownership, explicit CLI selection, atomic public cutover and installed packaging/timeout composition.|Shared sockets/installed driver extended at first consumer; race cases owned before public cutover, closure adds integrated scenarios only.|
|4.1/4.2.1/4.3–4.5|Pending-state plus four bounded ordinary rotation handlers in4.1; linked 4.2.1 owns shared all-constructor HTTP/CLI preflight, the existing lifecycle integration and the distinct native mutable publisher; strict late controls/result/fallback follow.|Real one-request-per-connection peers, every prepare/bind/confirm/result loss and save boundary, shared absolute budgets, concurrent clients, then controlled-clock expiry/denial/replay/native restart, including native replacement/flush failure cases.|
|5.1–5.5|Reuse pairing; stateless purpose-separated origin cookie/offline owner JWS in5.2; first-valid-owner atomic access receipt in5.3; actual original-issuer private signing and shared portable publication in5.4; same-key root continuity/registry forwarding plus leaf renewal in5.5. Existing public issuer controls do not sign.|Exact negative schemas and sustained attacker-candidate requests, competing valid owner completions, two real original/remote-public issuer peers, all result/save boundaries, missing issuer/denial/removal/expiry and renewal/recovery races.|
|6.1–6.4|Bounded transition publication and history, verified client adoption, independent compromise proof and same-key renewal.|Reuse recovery/custody fixtures; online/offline/fork/failed-save/expiry tests, exact retained access and no preverification credentials.|
|7.1–7.6|Recipient registration, one binary-age streaming adapter, sticky I/O wrapper, existing store/quota extension, authenticated descriptor/consume and real plugin activation.|One backend fixture set begins with the real recipient challenge at7.1.1 and is extended at7.2, existing store/contention fixtures extended for both recipients, limits, fault-finalization and live denial. Native closure adds packaging/composition only.|
|8.1–8.4.1/8.5|Existing child pipeline plus explicit stream parser; immutable ciphertext staging; plaintext temp; linked 8.4.1 owns the exported artifact-safe identity.PublishNoReplace facade over the existing native no-replace publisher, with exact help paths; 8.5 composes installed coverage.|Reuse real child/stdout/timeout tests; add origin/EOF, writable-alias/native fallback, signal/disk/denial and orphan cases; 8.4.1 adds generic, POSIX, Darwin and Windows existing-target/symlink/final-write/flush/failure tests. No duplicate transfer pool or parallel publisher.|
|9.1–9.5|Resolve/save selected config/secrets, coherent SQLite snapshot, one strict signed record codec/verifier, public-recipient export and atomic output.|Current generation/config tests plus source-race/snapshot barriers, malicious record corpus, sticky disk failures and one job round-trip driver. No scheduler/cloud client.|
|10.1–10.5|Reuse backup verifier and generation/lock/custody primitives; private offline staging, one active+undo control switch, immutable repeatable undo and discard.|Shared native kill-at-boundary driver with empty/existing host, prior undo preservation, ACL/disk/start races and stale-client lifecycle. Decoder tests are reused, not recopied.|
|11.1–11.3|Persistent inhibition/drain, fixed source handoff and client URL adoption; existing restore/export/transition components reused.|Extend installed drivers to several clients, later destination replacement and whole-parent journey; isolated staging and required native execution. No new migration/fencing framework.|

Every range includes its own negative/failure tests, integration additions and
applicable platform scenarios. Static fixture bytes/generated outputs, dependency
locks and development documents remain separate unquantified review burden;
unknown is not zero. Handwritten CI is separately quantified. Required shipped
help/config/instructions and packaging edits count as production when applicable.
No test suite was rerun merely to produce these design estimates.

The review corrections increased only seven cuts:4.1/4.2/5.2–5.5/8.4.1, with their actual source owners and focused native scenarios. Maximum forecast authored churn remains980 at4.2.1 and5.4, leaving20 lines below the hard cap; 8.4.1 forecasts960 (combined upper880 plus headroom80), leaving40 lines. All are tight low-confidence cuts, not permission to squeeze work. Validate each consumed implementation and tests before admission and revise the design openly if the envelope does not fit. The original51 IDs and historical before snapshot remain exact.

## Focused recipient-backend first-use correction

Original 7.1 is retained as replaced history; its linked executable child is
7.1.1. The unchanged recipient registration/key-retention work still owns its
existing estimate. Reassign the minimal shared-backend adoption from 7.2 to
its actual first consumer: 10–20 production lines for dependency declarations
and adapter wiring, 40–60 test lines for native challenge/install smoke cases,
and 0–5 handwritten CI lines. These are low-confidence engineering forecasts,
not measured implementation or proof of supported installation. Locks remain
unquantified review burden.

7.1.1 becomes production190–300/tests260–390/CI0–5; 7.2 becomes
production210–310/tests220–330/CI0–0. Streaming, dual recipients, sticky I/O,
progress/cancellation and encrypted metadata remain in 7.2. The first-use fixture
is extended there rather than copied. Milestone/project totals remain unchanged
because this is a transfer of the same shared work. Before implementation,
recheck actual churn and native/platform obligations; do not fit work by dropping
tests or weakening the 1,000-line cap. No review allowance is created.

## Focused native mutable-record correction

Original 4.2 is retained as replaced history. Linked executable 4.2.1 is the first mutable-record consumer and owns the original 4.2 lifecycle paths plus `provisional_store.go`, `provisional_store_unix.go`, `provisional_store_windows.go` and their generic/native tests. The existing `SaveProvisional` enrollment publisher remains immutable and no-replace.

The complete consumed child forecast is production300–410/tests360–490/CI0–0/combined660–900 with predicted authored churn980. This is low confidence and leaves only a 20-line margin to the unchanged 1,000-line authored-churn cap; the native failure tests are included, not dropped or relabeled. Recheck actual churn and all platform obligations before implementation. If the 980 envelope does not hold, return 4.2.1 to design. Future 4.3 depends on 4.2.1. No review allowance or M4 implementation is authorized.

## Focused artifact-publication facade correction

Original 8.4 is retained as replaced history. Linked executable 8.4.1 is the
first persistent-export consumer of an exported `identity.PublishNoReplace`
facade over the existing native no-replace publisher. It owns the CLI save-
decrypted integration plus the generic, POSIX, Darwin and Windows source/tests
needed to bind private same-filesystem staging, reject existing targets and
symlinks, flush the parent and report final-write, cancellation/close and
ambiguous-publication failures. It does not add a backend, package, identity
record encoding or parallel publisher; 4.2.1 remains the explicit prerequisite
for the shared native primitive.

The complete consumed child forecast is production190–320/tests380–560/CI0–0/
combined570–880 with predicted authored churn960 (combined upper880 plus
headroom80). This is a low-confidence forecast; it leaves40 lines below the
1,000-line authored-change cap and is not proof that implementation will fit.
The native platform tests are included, not dropped or relabeled. Recheck actual
churn and all platform obligations before implementation; if the envelope does
not hold, return 8.4.1 to design. Future 8.5 depends on 8.4.1. No M8
implementation or review allowance is authorized.

## Focused request-receipt ownership correction

Original 3.1.4 is retained as replaced history. Linked executable 3.1.4.1 is
the first request-receipt consumer that owns the existing structured
`server/src/arbiter_server/limits.py` field/default/positive-finite validation,
its `main.py` propagation and the existing `test_pairing.py` configuration
seam. Its acceptance adds the default30-second and positive-finite override
cases, zero/negative/bool/nonfinite rejection, and real silent/drip peer cases
for the absolute non-sliding deadline. It does not add a second limits
mechanism or change the approved HTTP behavior. Future 3.2.1.1 depends on
3.1.4.1; the retired 3 and 3.1 aggregate intents remain unchanged.

The complete consumed child forecast remains production210–290/tests300–420/
CI0–0/combined510–710 with predicted authored churn790. This is a low-
confidence forecast, not measured implementation or proof of the 1,000-line
authored-change cap. The existing HTTP envelope is retained while the added
configuration and deadline cases remain an explicit actual-churn gate; do not
drop tests or compress code to fit. Recheck the native owner, configuration
propagation and real peers before implementation. No product implementation,
review allowance or runtime cap proof is authorized by this planning repair.

## Focused live-schema ownership correction

Original 3.2.1 is retained as replaced history. Linked executable 3.2.1.1
retains its complete live-enrollment, grant, denial and receipt responsibility
and adds the existing `authority.py` and `owner_issuers.py` schema/version/startup
owners with `test_authority.py` and `test_owner_issuers.py`; `test_enrollment_live.py`
remains the live-schema consumer test. Its acceptance covers valid prior schema3
and schema4, fresh setup, atomic one-way upgrade/restart, interruption/rollback,
malformed or conflicting rows and unknown future-version rejection through the
existing stopped-instance reconciliation path. No parallel schema mechanism or
mixed-layout serving is planned. Future 3.2.2 depends on 3.2.1.1.

The complete consumed child forecast is retained at production210–310/tests370–
510/CI0–0/combined580–820 with predicted authored churn920 and headroom100.
This remains a low-confidence forecast, not measured implementation or proof of
the 1,000-line authored-change cap. The retained production range accounts for
bounded edits to the existing authority startup/version gate (50–70 lines),
owner-issuer upgrade sequencing (30–50), and live coordinator/schema integration
(130–190), totaling210–310. The retained test range accounts for migration and
fresh/restart/version cases in `test_authority.py` (100–140), owner-issuer cases
in `test_owner_issuers.py` (60–90), and live enrollment/receipt integration
(210–280), totaling370–510. These component ranges include expected edits and
replacement/deletion of obsolete assertions within the existing files; the
authored-churn forecast is the combined upper820 plus headroom100 for actual
additions, deletions, motion and comments, not a zero-deletion assumption.
Existing source inspection shows the owners and test seams already exist, so no
new module, schema mechanism or selected cut is needed. Recheck actual authored
additions and deletions before implementation; return to design if the envelope
does not hold. No product implementation, review allowance or cap increase is
authorized by this planning repair.
