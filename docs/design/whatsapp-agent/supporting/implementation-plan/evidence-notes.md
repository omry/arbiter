# Fresh engineering evidence

## Scope and provenance

This file records source inspection and new disposable probes for the clean design on 2026-11-19 at Sapling revision `b41e6e767b9ec5862eee5fe9ed2e4d612b030ff7`. Earlier agent-authored probe/live-result claims and estimates have not been accepted as current evidence. Their former text is preserved outside the candidate at `/tmp/whatsapp-clean-continuation/untrusted-old-evidence-notes.md`; it grants no approval.

The retained external artifact [WhatsApp Agent Platform Developer Manual](whatsapp-agent-developer-manual.txt) identifies itself as version 1, originally published August 25, 2026. SHA256: `db6a3362601575a08d2ca921244f112d517ae8bc74962e72c73b37b7246c1380`. This is the manual retained with the original contract decisions. The exact retained source version is declared here. Direct owner account-setup messages were independently recovered from the original conversation; see [provider input provenance](provider-source-notes.md). Those messages establish the tested account's eligibility, not broad regional availability or a live endpoint result in this clean run. No live account call was performed in this clean run.

Manual sections inspected: participant identifiers and creator-only addressing; messages and supported payloads; exact update envelope/cursors, statuses and empty responses; combined read/typing; MIME/size restrictions, media metadata and digest encodings; fixed API/download destinations and provider errors. The public contract is governed by the accepted requirements/HLD and their retained human approvals, not by inherited implementation claims.

## Existing source and assigned additions

- `services.py` already validates nested dataclass values through OmegaConf and converts them to plain dictionaries. `_type_input_schema` lacks recursive nested-dataclass generation. Add and regress that branch with the message slice; do not add another argument-validation framework.
- `ArtifactStore` owns the root lock, nonce validation, capacity, expiry, consumed marker and access bookkeeping. `PluginArtifactStore` exposes creation only. Add a bounded immutable-byte read in the media-upload slice, within that same root lock. Existing HTTP reads keep their behavior.
- `PluginStorage` supplies private paths but lexical containment alone does not protect against unsafe existing filesystem entries. Credential/state handling must validate protected parents, file type, ownership and permissions. `authority.py` shows persistent nonblocking process locks, protected opens, POSIX replacement/flush, Windows ACL checks and `MoveFileExW` write-through. These are reference patterns, not an existing generic provider-secret API.
- `main.py` account activation edits config and emits guidance; it has no provider-credential enrollment hook. The first slice adds an optional local hook and explicit CLI options. Existing plugin activation remains compatible.
- SMTP idempotency provides a comparison for reservation/results. WhatsApp additionally needs cursor/source transactions, persistent unknown outcomes and replay-safe retention. Do not reuse SMTP records or claim they already implement these needs.
- Website plugins use `index.md`, `configure.md`, `behavior.md` and `reference.md`, registered in `website/sidebars.js`. The final slice owns complete integrated documentation; earlier behavior slices own their relevant examples and instructions.

## Fresh Linux probe

Replay source: [feasibility-probe.py](feasibility-probe.py), SHA256 `3b3cf28bfedaf83bcadf643a847c35726f95a1bbf1ca9245505f64467702e36f`. The probe locates server source relative to this checkout.
Results: [feasibility-results.json](feasibility-results.json), SHA256 `3801663eca591e4fc0ea9cca5fdf5f9d6b693584dcc9ce869c9021e37e70c819`. The portable retained probe was rerun successfully before the design amendment.
Command: `UV_CACHE_DIR=/tmp/whatsapp-clean-continuation/uv-cache uv run --offline --no-project --with omegaconf==2.4.0.dev11 python docs/design/whatsapp-agent/supporting/implementation-plan/feasibility-probe.py`.

The dependency was installed in a temporary environment. The probe imported the current server source, used synthetic data and disposable private directories, and passed:

1. Nested structured input validation accepts the expected object and rejects an unknown nested field. Current schema generation rejects that unsupported nested type, confirming the exact missing branch.
2. Killing a process during a SQLite transaction rolls back both cursor and source changes. Replay source insertion leaves the cursor unchanged. A committed pending reservation survives reopening and can be conservatively changed to unknown.
3. A second process cannot acquire a held POSIX lock; it can acquire it after release.
4. A synchronized reader observed both complete credential-record versions during 50 same-directory replacements with file/directory flushes. No partial version was observed; mode remained `0600`.
5. A proposed bounded in-memory artifact read raced the existing `open_once` in separate processes. The existing root lock and consumed state admitted exactly one consumer for a synthetic 1 MiB artifact.

These prove limited Linux primitive feasibility. They do not prove the future plugin, abrupt power loss, Windows/macOS behavior, production contention, full URL parsing, artifact rollback faults, TLS/DNS behavior or live-provider compatibility. The implementation acceptance matrix owns those checks. The artifact probe used private methods to examine ordering; the production addition belongs inside the store and does not expose those methods to plugins.

## Simpler retained design

Keep compact source/reply records indefinitely. Do not copy the former expiry clock/stability mechanism. A bounded store stops new durable admissions without deleting deduplication history. This directly satisfies retention through any provider replay window and avoids depending on an unverified provider expiry or local clock.

Use one SQLite database per private plugin root and one persistent poll lock and send lock per enrolled credential reference, plus a short root binding lock. The account send lock makes pending-reservation recovery unambiguous after lock acquisition. No inbox/ack service, lease timer, background cleanup scheduler, provider rate-limit emulator or new administrative network endpoint is needed.

## Readiness boundary

The source version and tested-account eligibility are supported by the retained manual and recovered direct owner messages, respectively. Historical model-authored live claims do not supply current approval. Important wire decisions are explicit in the draft and must pass complete detailed review. Broader eligibility is not promised. Dynamic platform, real endpoint/fixture and integrated plugin acceptance remain implementation work before rollout; they are not prerequisites to writing their implementation. The draft is not implementation-ready until its reviews and human approval are complete.

## Estimate basis for the clean inventory

The canonical inventory defines seven independently verifiable behaviors. These are incremental final-delivered physical-line ranges using the plan's declared units; development design prose and the disposable probes are excluded.

| Slice | Production | Tests | Concrete basis |
| --- | --- | --- | --- |
| 1 | 850–1,250 | 1,100–1,600 | Plugin/config registration, bounded transport/state initialization, protected binding and locks, optional CLI dispatch, enrollment/replacement/recovery, three-platform fault cases and setup instructions. Existing config/authority patterns constrain the additions; no message/media implementation is counted here. |
| 2 | 350–500 | 450–650 | Typed envelope projection, exact cursor/source transaction and normal/replay operation, crash/identity/ingress cases and polling examples. Reuse slice 1 transport/store. |
| 3 | 450–650 | 600–850 | Nested dataclass schema branch/regressions, typed text admission, canonical payload and durable send results/recovery, concurrent/crash tests and automation guidance. Reuse source observation. |
| 4 | 100–180 | 160–260 | Two small status operations, composed policy/ack/error checks, complete policy-combination tests and instructions. |
| 5 | 260–400 | 400–600 | Bounded same-store artifact reader/URL validation, multipart upload, local image header limits, process/cleanup/fault cases and consumption guidance. This is the sole artifact-reader owner. |
| 6 | 350–500 | 450–700 | Metadata/download/delete and typed media-send extension, fixed destination/digest/length validation, policy/redirect/capacity tests and examples. Reuse reservations and artifact reader. |
| 7 | 300–450 | 400–600 | Package/test/lint/release maps and suite wiring, four-page documentation/navigation/security/credentials completion, installed cross-slice/platform/redaction/integration drivers. Earlier slice documentation is counted in its owner; only completion and integration are counted here. |
| Total | 2,660–3,930 | 3,560–5,260 | Shared foundations counted once. |

The ranges are estimates against the inspected server/service/artifact/authority/CLI/plugin/release/website surfaces, not a claim that those lines already exist. No automatic splitting, time conversion or universal cap is introduced. Independent review may identify missing engineering; any correction must preserve the accepted parent scope and refresh affected evidence.
