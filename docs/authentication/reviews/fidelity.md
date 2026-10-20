# Authentication summary fidelity review

Reviewed 2026-10-20 by the primary agent before the separately requested DBB
assessment. This is a deep local fidelity review, not an independent SWE stamp
or a security verdict. Review run: `run_6wj0gqdynca9dvjs8k9rnskrvt`.

## Scope and method

Compared all D1–D104 source decision entries, their supersessions and relevant
historical context with the current design, brief, evidence map and checkpoint.
Checked meanings, qualifiers, omissions, invented requirements, approval versus
proposal status, role/authority distinctions and required future compatibility.
Also checked load-bearing POC claims against retained result artifacts rather
than relying only on the old draft's experiment narrative.

The source authorities are the byte-preserved
[interview](../../archive/authentication-and-enrollment/interview-2026-10-20.md) and
[draft](../../archive/authentication-and-enrollment/draft-2026-10-20.md). Actual owner
decisions and later corrections control; old speculative mechanisms are context,
not additional mandatory requirements. The review did not reopen deferred scope
or turn incomplete B1–B5 mechanisms into fidelity findings.

Initial input hashes and the per-decision semantic assessment are retained in
`temp/reviews/authentication-fidelity-2026-10-20/`. The assessment records reviewer
judgment; ID coverage and hashing alone do not prove semantic equivalence.

## Findings and corrections

| Finding | Source and original summary | Correction and verification |
| --- | --- | --- |
| F1 — old-credential cleanup omission | D98, interview lines 2253–2261, explicitly discards the predecessor on unenrollment. The design mentioned expiry/owner recovery but omitted that teardown condition. | Restored explicit unenrollment cleanup in the lifecycle section; rechecked the one-predecessor bound, unchanged server validity/deadlines and authentication-only fallback. |
| F2 — client identity survival was implicit | D61, interview lines 500–506, says surviving client identities need not be recreated after losing a server. The design said to pair/grant existing clients again without stating that distinction. | Added the explicit survival condition beside new-server recovery; it does not promise recovered server trust, grants or lost issuer keys. |
| F3 — required documentation surfaces omitted | D85, interview lines 1757–1763, requires both client documentation and operation help to explain processing tradeoffs. The design said only that guidance must explain them. | Restored both deliverables; retained all processing choices, no selected default, partial-input limitations and explicit plaintext-temp flag. |
| F4 — future UI enrollment controls absent from primary input | D4, interview lines 111–114, requires pending-enrollment display and approve/reject controls in the future UI. The coverage map retained the direction, but the main DBB input mentioned only a future admin surface. | Restored the pending/approve/reject requirement and its future-interface status; initial local/SSH CLI delivery is unchanged. |

These are fidelity/representation fixes. They do not fix implemented product
behavior or establish security. No other semantic mismatch was identified in
this pass; that is a reviewer conclusion, not proof that condensation is lossless.

## Preserved unresolved issues and limits

- D24's no-extra-operator-approval recovery promise versus later server-local
  key restrictions remains explicit B2 work; it was not silently resolved.
- D103 remains provisional pending DBB; U53's 64-connection proposal remains
  unapproved. Short-code pairing, proxy-readable native traffic, extra client
  policy layers and deferred clustering were not restored as current requirements.
- Exact protocol profiles, expiry/recovery composition, transport, artifact
  cryptography and snapshot/restore mechanics remain B1–B5. No implementation
  readiness, production dependency adoption or release approval follows.
- Retained POC JSON confirms the recorded 13 passing Linux/Windows test groups,
  exact reference-vector results and limited advisory result. Cross-compilation,
  wheel resolution and one native target are not full-platform validation.
- Archived source bytes remain unchanged. No product code, live service call,
  new compatibility experiment, dependency scan, commit or publication occurred.

Next: the owner has authorized DBB to assess the corrected architecture package.
That independent review is separate from this local fidelity pass.
