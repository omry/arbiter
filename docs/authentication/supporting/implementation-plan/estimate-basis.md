# Estimate basis and ownership

These are low-confidence engineering ranges, not future measured diffs or a
statistical confidence interval. They were assigned to the complete consumed
changes after inspecting the merged source, not by applying a percentage to
old counts. Display values are incremental final nonblank physical code/test
content; comment-only lines are excluded and docstrings count physically.

The separate authored-churn envelope allows 180 lines of replacement/motion/
comments for3.1.1; 100 for3.2.1/9.1; and80 for other cuts, in addition to upper
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
| Go provisional_store.go / enrollment_receipt.go | 251 / 141 | Private immutable custody/publication exists; atomic mutable lifecycle records reuse native adapters. |
| Go HTTP client.go / timeouts.go / CLI cli.go | 212 / 306 / 2,309 | Existing HTTP/phase timers/process helpers are reused; only selected constructors and command paths change. |

## Per-area sizing rationale

Each slice's exact responsibility, ownership files, dependency and completion
check are in the canonical inventory. These component groups explain its range.
They are overlapping read surfaces, not independent code packages counted again.

| Cuts | Production components charged once | Test work included in those cuts |
| --- | --- | --- |
|3.1.1–3.1.4|Shared carrier extraction/credit pump; strict TLS candidate; Go dial/HTTP factory; h11-ASGI body/response adapter. No second HTTP client or timer stack.|One real cross-language stream harness at3.1.1, extended for roles/relay/partial BIO/framing/body admission and failure peers.|
|3.2.1–3.2.2|One live schema/backfill, one coordinator/writer barrier, fixed-receipt permission and local denial first; ordinary tokens/grant controls extend it.|Existing activation/receipt tests plus corruption/backfill, deterministic revoke/snapshot/final-write and worker-handoff barriers.|
|3.3–3.4|Grant projections through existing schema/policy paths; actual worker caller context; small closed concrete artifact adapter/legacy descriptor closure.|Existing native/admission/store fixtures with hidden-information and real spy/filesystem assertions. No new copy of every discovery fixture.|
|3.6.1–3.7|Cross-connection cancel/final-write ownership, explicit CLI selection, atomic public cutover and installed packaging/timeout composition.|Shared sockets/installed driver extended at first consumer; race cases owned before public cutover, closure adds integrated scenarios only.|
|4.1–4.5|Pending-state plus four bounded ordinary rotation handlers in4.1; shared all-constructor HTTP/CLI preflight and private lifecycle publication in4.2; strict late controls/result/fallback follow.|Real one-request-per-connection peers, every prepare/bind/confirm/result loss and save boundary, shared absolute budgets, concurrent clients, then controlled-clock expiry/denial/replay/native restart.|
|5.1–5.5|Reuse pairing; stateless purpose-separated origin cookie/offline owner JWS in5.2; first-valid-owner atomic access receipt in5.3; actual original-issuer private signing and shared portable publication in5.4; same-key root continuity/registry forwarding plus leaf renewal in5.5. Existing public issuer controls do not sign.|Exact negative schemas and sustained attacker-candidate requests, competing valid owner completions, two real original/remote-public issuer peers, all result/save boundaries, missing issuer/denial/removal/expiry and renewal/recovery races.|
|6.1–6.4|Bounded transition publication and history, verified client adoption, independent compromise proof and same-key renewal.|Reuse recovery/custody fixtures; online/offline/fork/failed-save/expiry tests, exact retained access and no preverification credentials.|
|7.1–7.6|Recipient registration, one binary-age streaming adapter, sticky I/O wrapper, existing store/quota extension, authenticated descriptor/consume and real plugin activation.|One backend fixture set at7.2, existing store/contention fixtures extended for both recipients, limits, fault-finalization and live denial. Native closure adds packaging/composition only.|
|8.1–8.5|Existing child pipeline plus explicit stream parser; immutable ciphertext staging; plaintext temp; no-clobber export and exact help paths.|Reuse real child/stdout/timeout tests; add origin/EOF, writable-alias/native fallback, signal/disk/denial and orphan cases. No duplicate transfer pool.|
|9.1–9.5|Resolve/save selected config/secrets, coherent SQLite snapshot, one strict signed record codec/verifier, public-recipient export and atomic output.|Current generation/config tests plus source-race/snapshot barriers, malicious record corpus, sticky disk failures and one job round-trip driver. No scheduler/cloud client.|
|10.1–10.5|Reuse backup verifier and generation/lock/custody primitives; private offline staging, one active+undo control switch, immutable repeatable undo and discard.|Shared native kill-at-boundary driver with empty/existing host, prior undo preservation, ACL/disk/start races and stale-client lifecycle. Decoder tests are reused, not recopied.|
|11.1–11.3|Persistent inhibition/drain, fixed source handoff and client URL adoption; existing restore/export/transition components reused.|Extend installed drivers to several clients, later destination replacement and whole-parent journey; isolated staging and required native execution. No new migration/fencing framework.|

Every range includes its own negative/failure tests, integration additions and
applicable platform scenarios. Static fixture bytes/generated outputs, dependency
locks and development documents remain separate unquantified review burden;
unknown is not zero. Handwritten CI is separately quantified. Required shipped
help/config/instructions and packaging edits count as production when applicable.
No test suite was rerun merely to produce these design estimates.

The review corrections increased only six cuts:4.1/4.2/5.2–5.5, with their actual source owners and focused native scenarios. Maximum forecast authored churn is now980 at5.4, leaving only20 lines below the hard cap; this is a tight low-confidence cut, not permission to squeeze work. Validate its consumed implementation and tests before admission and revise the design openly if the envelope does not fit. The original51 IDs and historical before snapshot remain exact.
