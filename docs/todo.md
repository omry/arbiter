# TODO

## Purpose

Track agreed follow-up work that is not specific to tests.

Protocol:

- keep this file focused on open follow-up work
- remove completed items instead of marking them `done`
- capture durable findings in the relevant docs or code comments, not here

## Items

- Evaluate audit-config duplication and consider a simpler config shape
  - Why: SMTP and IMAP audit blocks are verbose and repeated across access-profile examples, which makes the operator-facing config heavier than it needs to be
  - Status: `todo`
  - Steps:
    1. Review which audit settings truly need per-protocol or per-profile control.
    2. Consider whether shared defaults, inheritance, or a smaller audit schema would cover the real use cases with less repetition.
    3. Propose a redesign that reduces audit-related config surface without weakening the safety and review story.

- Review the full documentation set for config and policy alignment
  - Why: the access-policy changes have updated the main contract, and the rest of the docs should be checked for stale language, stale examples, or mismatches with the implemented behavior
  - Status: `todo`
  - Steps:
    1. Review all Mail Sentry docs against the current code and config contract.
    2. Correct stale examples, response shapes, and policy descriptions.
    3. Remove outdated terms that no longer match the implementation.

- Add optional bot-signing behavior for personal-account sends
  - Why: when sending through a personal account, the operator may want the body to disclose that the bot drafted or sent the message
  - Status: `todo`
  - Steps:
    1. Add account-level or skill-level config to control whether bot-signing is enabled for personal-account sends.
    2. Define how the signature text is injected for text and HTML bodies.
    3. Update interactive skill behavior and docs once the policy is decided.

- Add startup logging for Mail Sentry version and non-sensitive config summary
  - Why: operators need a quick sanity check that the expected build and account layout are running without exposing secrets
  - Status: `todo`
  - Steps:
    1. Log the Mail Sentry package/server version at startup.
    2. Log a basic non-sensitive config summary such as transport, bind address, account names, enabled protocols, and sensitivity tiers.
    3. Ensure no secrets, credentials, or raw env values are emitted in those startup logs.

- Replace `sensitivity_tier` with an explicit confirmation-policy set
  - Why: `sensitivity_tier` is too vague for the behavior it currently drives. The actual need is an explicit per-account set of actions that require confirmation, and that should stay separate from capability discovery.
  - Current direction: use a set-style field such as `"confirmation_required": ["smtp_send", "imap_delete"]`.
  - Context:
    1. The current `sensitivity_tier` meaning is really about interactive confirmation behavior, not data classification.
    2. That meaning is currently used for send flows, but it should not automatically apply to all IMAP operations.
    3. IMAP confirmation needs are action-specific: `imap_delete` likely needs confirmation much more often than `imap_read` or `imap_search`.
    4. Capability and confirmation are separate concerns: `list_accounts` should answer both "is this action allowed?" and "does this allowed action require confirmation?" without conflating them.
    5. The preferred representation is a set/list of action identifiers rather than a vague tier label or a large boolean map.
  - Status: `todo`
  - Steps:
    1. Define the initial action vocabulary for confirmation decisions, such as `smtp_send`, `imap_read`, `imap_search`, `imap_move`, and `imap_delete`.
    2. Replace account-level `sensitivity_tier` in config, docs, and `list_accounts` output with a set-style confirmation field.
    3. Update interactive send behavior to use the new confirmation field instead of `sensitivity_tier`.
    4. Decide which IMAP actions, if any, should require confirmation by default for the initial IMAP rollout.
    5. Sweep the docs for stale `sensitivity_tier`, `standard`, and `sensitive` language once the replacement contract is finalized.

- Improve the OpenClaw skill installer with file-change visibility
  - Why: operators should be able to see exactly which skill files are being added or updated during installation
  - Status: `todo`
  - Steps:
    1. Detect which installed files would change copying them into the container (dry mode) or during in real installation.
    2. Print a concise diff or change summary during installation.
    3. Keep the output readable without dumping large file contents by default.
