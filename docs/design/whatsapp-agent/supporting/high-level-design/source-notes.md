# HLD source inspection

Inspected the current checkout at b41e6e767b9ec5862eee5fe9ed2e4d612b030ff7 on 2026-11-19. These observations are from the clean run; no old review or probe result grants current approval.

- services.py provides plugin configuration, operation descriptors and dataclass validation. Its _type_input_schema supports primitive/list/optional fields but lacks nested dataclass schema generation. Typed Message and ArtifactInput require a small shared schema extension.
- PluginStorage provides a plugin-private root and lexical path checks; those checks alone do not prove safe credential files. authority.py and file_protection provide protected-file, locking and platform publication patterns to evaluate in LLD.
- PluginArtifactStore currently exposes creation and limits. ArtifactStore owns capability checks, expiry, the root lock and one-time consumption. A bounded same-store read method must be added and must coordinate with HTTP reads and cleanup.
- main.py config activate edits account/policy selections and prints guidance. It has no provider-credential enrollment/replacement dispatch. Keep the new hook optional and local to that CLI.
- SMTP idempotency stores pending and accepted results. WhatsApp additionally needs durable observed inbound sources, normal cursors and conservative recovery of ambiguous pending sends.
- The optional package follows the existing arbiter.services entry point and package/server compatibility line.

The retained external developer manual identifies the API origin, update/status/media paths, 64-bit cursors, non-consuming replay, 204/409 handling, combined read/typing behavior and media constraints. It is provisional external documentation. No live account, endpoint compatibility, platform durability or implementation test was run during this source inspection.

HLD feasibility uses these existing primitives and explicitly assigns missing adapters. LLD must prove selected transactions/locks, artifact read lifetime and credential publication with bounded disposable probes, define platform acceptance and establish the provenance of provider evidence before admission.

The current main.py startup warning and invoke_operation route confirm that ordinary API callers are not authenticated and their stored enrollment grants are not enforced. Account-policy configuration is the current operation boundary for trusted local deployments. The HLD must not claim active caller grants.
