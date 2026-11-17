# Approved initial WhatsApp credential enrollment

Status: approved historical summary. The owner approved this amendment in [credential-enrollment-approval.md](credential-enrollment-approval.md), and the selected flow is incorporated into the current HLD. The exact approved preimage is retained at [approved-preimages/credential-enrollment-amendment.md](approved-preimages/credential-enrollment-amendment.md).

The approved credential replacement behavior checks the new key against a provider agent ID that Arbiter already trusts. The independent review found that the draft leaves the source and first durable binding of that ID unspecified. This proposal defines initial enrollment and keeps replacement for an existing binding. The operator actions belong to the existing `arbiter-server` CLI.

## Operator flow

Make initial enrollment an explicit step in the existing `arbiter-server` plugin account registration flow. Follow the `bootstrap --plugin whatsapp --account creator` account selection pattern. Registration creates the account configuration when needed and reuses valid existing configuration when the operator returns. Template previews show configuration changes. The existing `config check --live` hook checks readiness. The detailed design selects how the operator requests enrollment within registration.

1. The server operator selects the account in registration. The enrollment step uses the same configuration and private storage root as the server. If the account has a valid committed binding, registration reports that it is already enrolled. The operator uses the replacement action to change that credential. For an unbound account, the operator supplies the candidate key on stdin.
2. Using the candidate key, the command polls the official updates endpoint from `offset=0` with `timeout=0` and follows valid forward cursors within a request and time budget. The first `204` establishes the starting cursor by retaining that request's offset, including `0` for an empty chat. If a response identifies a provider agent already bound in the state root, stop. If the scan succeeds, show a fresh, unpredictable code with a recognizable `Arbiter enrollment` prefix in the local operator terminal. The scan keeps its cursor in memory and leaves the runtime cursor unchanged.
3. The operator sends the exact displayed text to the intended agent's chat in WhatsApp. The command polls from the starting cursor within a short deadline. It accepts only a new creator text update that exactly matches the current attempt's challenge and carries a valid provider agent ID. Any provider identity seen during the attempt must agree. Verification uses read-only polling.
4. The command validates that the local account and this provider ID are still unbound across the entire plugin state root, then activates the credential and identity together. Success makes the configured account available for agent operations. A wrong-chat key, empty poll through the deadline, provider failure, conflicting binding, or failed pre-activation write leaves it unbound.

The operator's control of the intended WhatsApp chat establishes the first identity. A pasted numeric ID would be simpler if the provider reliably gave the operator a separate authenticated way to obtain it. The retained manual shows the ID in API updates authenticated with the candidate key. Those updates alone cannot distinguish the intended chat from a different valid candidate key.

## Unfinished enrollment

- If the operator exits, the terminal closes, or the attempt times out before activation, keep the account configuration and leave the account unbound. Arbiter holds the pending key, challenge and verification cursor only in memory for that attempt. An unbound account remains unavailable to agents.
- To resume setup, reopen registration for the same account and supply the key again. Registration preserves the saved configuration and starts a fresh verification attempt with a new cursor, challenge and deadline. The operator can supply a different candidate key. An earlier challenge cannot complete the new attempt.
- If a verification attempt is still running, a second invocation reports that enrollment is in progress. To restart verification, cancel the active attempt or let it expire, then reopen registration for the same account.
- If the prior attempt lost its result near activation, inspect the binding before starting provider verification. A valid committed binding means setup is complete. An absent binding permits a fresh attempt once local state is confirmed safe. Inconsistent or unreadable state requires operator recovery.

## Persistence and recovery

Use one protected record per stable credential reference containing the provider agent ID and active token. Its atomic publication is the one enrollment commit. Replacement updates the token in that record after same-agent verification and preserves the agent ID. An account lock serializes polling and enrollment for that account. A separate root-wide binding lock is held only for local validation and publication to prevent concurrent enrollment of two aliases for one agent. The server consults these same committed bindings. It reads the identity and token from the same file snapshot, keeps unbound accounts unavailable and rejects inconsistent or duplicate bindings.

The binding record owns the identity. Cursor and reply rows use the bound provider ID in the same private state root. Prepare and validate the local store before publishing the first binding. Runtime startup must refuse a missing or inconsistent state store when active bindings already exist. A crash before publication leaves no active binding. A restart recognizes a committed valid binding without starting another enrollment. If a failure occurs after replacement but durability is uncertain, return a safe storage error and require a fresh inspection of local state before retrying. The command must never overwrite a valid binding when recovering. Reuse the protected-file, same-volume replace and platform flush requirements already specified for credential replacement.

One deployment and private state root own each provider agent. A migration stops the old owner and preserves bindings, cursor and reply state together before the new owner starts. Separate active roots cannot enforce the same polling or deduplication guarantees.

Keep the challenge, token bytes and provider identifiers out of durable logs and operator diagnostics. The local prompt displays the challenge for the current attempt. Agent-visible registration data never contains the token or provider agent ID. The stdin token is never echoed or passed as a process argument.

After activation, a challenge that the provider still retains can appear as ordinary creator text in normal polling or `offset=0` replay under the existing `updates` permission. A challenge from a finished or abandoned attempt cannot authorize a later attempt. The recognizable prefix lets automated callers skip a reply to enrollment messages. Arbiter applies the approved replay rules to these messages, including the existing source-keyed deduplication if a caller chooses to reply.

## Verification to add to the plan

- An unbound account cannot poll as a service, send, handle statuses or use media before enrollment commits.
- A candidate key for the wrong agent does not match a challenge sent to the intended chat. Old, mismatched, absent or malformed updates fail verification. The verification scan leaves the normal cursor unchanged.
- Two concurrent aliases for the same provider ID cannot both enroll. A candidate poll can displace an active poll before the candidate's provider identity is known. Confirm bounded handling of that provider `409`, no cursor loss, and no further enrollment polling once an existing binding is detected.
- Cancellation, timeout and process interruption before publication leave the account configuration intact and the account unbound. Reopening registration preserves operator edits, accepts a fresh candidate key and requires a new challenge. A second invocation cannot take over a running attempt.
- Process interruption near or after publication is resolved by inspecting local state. Registration reports a valid committed binding as complete and stops on unreadable or inconsistent state. Recovery preserves active bindings and refuses to recreate lost reply state.
- Verify that a blank chat can enroll starting from offset `0`, and that a scan that cannot reach an empty response within its budget leaves the account unbound. Verify that retained challenge messages follow the same polling and replay rules as other creator text and cannot satisfy a later challenge.
- Operator-only access, stdout and diagnostics, redaction, and POSIX and Windows file behavior receive focused acceptance checks.

This proposed contract amendment adds initial verification to registration and makes the trust proof explicit before implementation. It requires affected HLD review and verification on the revised candidate by the retained independent reviewer.
