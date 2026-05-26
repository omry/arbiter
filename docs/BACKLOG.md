# Mail Sentry Backlog

## Agent instructions

When helping with backlog work, treat this file as the active planning
surface for Mail Sentry. Keep it short, concrete, and easy to scan. Prefer
moving work between queues over growing process, and avoid inventing GitHub
issues unless the user asks for them.

Use [testing_backlog.md](testing_backlog.md) as the testing-specific queue.
This file is the day-to-day queue for design and implementation gaps.

## How to use this file

- Keep each item small enough for one focused change.
- Put only the most urgent items in `Now`.
- Prefer richer items with brief context and concrete acceptance checks.
- Move completed items out instead of keeping a long archive here.
- Treat config and policy items as operator-facing product work, not only as
  internal refactors.

## Now

- [ ] `P1` Decide whether audit config should stay operator-facing before
      durable audit storage exists.
      The current audit blocks add a lot of config surface even though durable
      audit behavior is still a design contract. The question is whether to
      keep these knobs visible now, or demote them into explicit future-design
      docs until the runtime can honor them.
      Acceptance checks: one clear direction is chosen; docs and samples follow
      that direction consistently; and operators can tell whether audit config
      is live runtime behavior or future intent.

- [ ] `P1` Align docs, schema defaults, and sample configs on what is actually
      required versus what is only defaulted implicitly.
      Right now the docs often say "required" while the dataclass schema and
      sample configs rely on defaults. That makes the contract harder to trust
      and increases the cost of future cleanup.
      Acceptance checks: the docs and sample configs match the real schema
      story; "required" means the same thing everywhere; and deployers can tell
      which fields are essential, optional, provisional, or defaulted.

- [ ] `P1` Add startup logging for the Mail Sentry version and a non-sensitive
      config summary.
      Operators need a quick sanity check that the expected build, transport,
      and account layout are actually running, especially once config cleanup
      starts changing the surface area.
      Acceptance checks: startup logs include version plus a safe summary such
      as transport, bind address, account names, and enabled protocol families;
      no secrets or raw env values are emitted; and the log wording remains
      useful for real deployments rather than only for local debugging.

- [ ] `P1` Decide whether personal-account sends should support optional
      bot-signing.
      This is a policy and product question, not just a text-template tweak.
      If the bot drafts or sends through a personal account, the deployment may
      want explicit disclosure in the message body.
      Acceptance checks: the policy decision is recorded; if enabled, the
      config knob and text/HTML injection behavior are defined; and the
      interactive send flow and docs explain when the signature is or is not
      applied.

- [ ] `P2` Decide whether shared policy profiles should remain the long-term
      home for both access gates and audit settings.
      The current implementation now uses shared profiles for access gates,
      caller confirmation, and audit settings. That may still be the right
      abstraction, but it is also plausible that access control, audit, and
      caller confirmation should not all live in the same container.
      Acceptance checks: the design notes compare at least the current shared
      profile approach against one or two clearer alternatives; tradeoffs are
      recorded; and the chosen direction informs the next config cleanup pass.

- [ ] `P2` Reduce duplicated audit-config surface after the future policy model
      is settled.
      The current SMTP and IMAP audit blocks are verbose and repeated across
      profile examples. That may be acceptable if the knobs prove necessary,
      but it should not be cleaned up before the broader audit and profile
      direction is clearer.
      Acceptance checks: the redesign identifies which audit controls truly
      need per-protocol or per-profile variation; shared defaults or a smaller
      schema are considered; and the resulting shape is materially lighter for
      operators.

- [ ] `P2` Improve the OpenClaw skill installer with dry-run file-change
      visibility.
      Operators should be able to see what will change before installation and
      what did change after installation, without having to inspect the target
      directory manually.
      Acceptance checks: the installer can report which files would be added or
      updated; output stays concise by default; and normal installs provide a
      readable change summary rather than dumping full file contents.
