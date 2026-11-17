# WhatsApp Agent Platform plugin

The goal is an optional Arbiter plugin that keeps provider credentials private and lets trusted local callers use operator-configured WhatsApp accounts under explicit policy. See the [requirements](requirements.md).

The [architecture](high-level-design.md) has clean general/security review and explicit owner approval. It uses one plugin, private binding/state, existing artifact capabilities and an operator-only registration/replacement adapter. All reachable callers share account policies; caller authentication stays separate.

The [engineering plan](implementation-plan.md) has an accepted seven-slice scope with one [slice inventory](slices.json). It includes safe enrollment, replay, source-keyed replies, statuses, media, installed-package integration and user/operator documentation. No product implementation has started. The table shows planned work, not accepted delivery.

<!-- awd:slice-table:start -->
| Status | Slice ID | Short description | Production lines | Test lines |
| --- | --- | --- | ---: | ---: |
| ⏳ Pending | `whatsapp-account-plugin/1` | Enroll accounts and replace credentials safely | 850–1,250 | 1,100–1,600 |
| ⏳ Pending | `whatsapp-account-plugin/2` | Receive updates with isolated replay | 350–500 | 450–650 |
| ⏳ Pending | `whatsapp-account-plugin/3` | Send text replies without duplicate submission | 450–650 | 600–850 |
| ⏳ Pending | `whatsapp-account-plugin/4` | Apply independent read and typing policies | 100–180 | 160–260 |
| ⏳ Pending | `whatsapp-account-plugin/5` | Upload existing artifacts exactly once | 260–400 | 400–600 |
| ⏳ Pending | `whatsapp-account-plugin/6` | Download, send and delete media safely | 350–500 | 450–700 |
| ⏳ Pending | `whatsapp-account-plugin/7` | Verify installation and publish complete operator documentation | 300–450 | 400–600 |
| | **Project total** | Current slices; replaced parents excluded. | **2,660–3,930** | **3,560–5,260** |
<!-- awd:slice-table:end -->

The design is ready for implementation. Next: complete the authorized design PR and remote review, then stop before product work. The owner has [accepted the detailed scope and estimates](supporting/implementation-plan/approval-record.md). Product implementation and deployment require separate authorization.

Detailed general and independent DBB reviews are clean with zero findings. Refreshed review of the amended candidate uses its actual parent, which contains no WhatsApp plan. The complete requirements/HLD/plan review chain and current detailed-plan attestation are verified. No SWE policy change was needed. Implementation has not started. The original owner account-setup messages and retained manual provide [declared provider inputs](supporting/implementation-plan/provider-source-notes.md); previous model-authored live results supply no current approval. See [fresh evidence and limits](supporting/implementation-plan/evidence-notes.md).
