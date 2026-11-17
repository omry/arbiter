# Provider input provenance and eligibility scope

The clean design uses the retained external artifact `whatsapp-agent-developer-manual.txt`, version 1, August 25, 2026, SHA256 `db6a3362601575a08d2ca921244f112d517ae8bc74962e72c73b37b7246c1380`. Its fixed API origin and documented wire fields are design inputs. This is an exact retained source version, not a claim that a newer publication was fetched or that live endpoint behavior was checked in this clean run. The accepted contract already identifies this retained manual as provisional source evidence. New provider behavior must be reconciled rather than silently enabled.

Direct human eligibility evidence was recovered from the original [account-test conversation](t3-thread://v2/714d9612-7992-4d9f-aeae-6d77c2f5f81e/2474ea42-9188-4f34-a067-081f5e7253a1), without adopting that model's review conclusions or probe-result summaries:

- The owner asked: `can we do live check against my own account?`
- Asked whether WhatsApp exposed `Settings → Agents → Create an agent`, the owner answered `ytes`, then `yes. I can.`
- After the explicit instruction to create a dedicated `Arbiter Test` agent and send a synthetic creator message, the owner answered `done`.
- The owner later reported `regenerated and sent a sticker.` and clarified `the sticker was animated. I sent a new one. statric`.

The records establish that the owner's tested account exposed agent creation and was used for the dedicated test setup. They do not establish eligibility for another account, region, phone or future provider version. Setup documentation requires the operator to check that their own account exposes this feature. No broad geographic/phone availability guarantee is made.

The original owner also authorized account testing (`you may do whatever you need to do against the account. stop asking.`). This evidence is retained to avoid repeatedly asking for the same scope. No account operation or fresh secret request was needed for this design-draft continuation. A future actual test must use authorized existing access or the secure input tool; no token may be recovered from conversation text.

Schema decisions in the plan follow the retained manual's explicit fields, not undocumented assumptions: status receipts are documented delivered/read; outbound text uses `text.body`; status typing includes read; incoming media digest is base64 while metadata digest is hex; metadata provides an ID-bound download URL; unavailable media does not prove successful deletion. Fresh metadata per download, fixed destination validation, digest/length checks and confirmed deletion avoid relying on a guessed signed-URL lifetime or inferred ownership. Those decisions resolve engineering choices without asserting a live-provider test passed.

Complete plugin compatibility, real endpoint/fixture acceptance and platform behavior remain implementation acceptance before rollout. Direct human eligibility evidence and a declared source version are distinct from those tests. General and independent detailed review must judge the draft against this exact scope; it is not reviewed or accepted merely because its inputs are recorded.
