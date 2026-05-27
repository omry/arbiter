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
- After each release-prep phase, run a focused review of the phase diff and
  commit the ready changes before starting the next phase.

## Now

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

- [ ] `P2` Decide whether shared policy profiles should remain the long-term
      home for access gates and caller confirmation.
      The current implementation now uses shared profiles for access gates and
      caller confirmation. That may still be the right abstraction, but it is
      also plausible that access control and caller confirmation should not
      live in the same container.
      Acceptance checks: the design notes compare at least the current shared
      profile approach against one or two clearer alternatives; tradeoffs are
      recorded; and the chosen direction informs the next config cleanup pass.

- [ ] `P2` Improve the OpenClaw skill installer with dry-run file-change
      visibility.
      Operators should be able to see what will change before installation and
      what did change after installation, without having to inspect the target
      directory manually.
      Acceptance checks: the installer can report which files would be added or
      updated; output stays concise by default; and normal installs provide a
      readable change summary rather than dumping full file contents.

## Post-v1

- [ ] `P2` Design and implement durable audit storage.
      Audit is parked for post-v1. The v1 release should not ask operators to
      configure audit behavior that the runtime cannot yet honor.
      Acceptance checks: audit storage, retention, event shape, and privacy
      defaults are defined; SMTP and IMAP audit events are emitted through one
      durable path; and docs distinguish audit records from operational logs.

- [ ] `P2` Decide where audit settings should live in the policy model.
      V1 removed SMTP and IMAP audit blocks from the operator-facing schema.
      Before audit ships, decide whether audit belongs in shared account access
      profiles, a separate audit policy block, or another clearer home.
      Acceptance checks: the redesign identifies which audit controls truly
      need per-protocol or per-profile variation; shared defaults or a smaller
      schema are considered; and the resulting shape is materially lighter for
      operators.

- [ ] `P2` Design bot-to-Sentry caller authentication or authorization.
      V1 assumes the caller is trusted once connected. Future hardening may use
      a shared secret, bearer token, password, client certificate, or mTLS/PKI
      so deployments can prevent unsafe access to the Mail Sentry MCP boundary.
      Acceptance checks: candidate mechanisms are compared; the chosen model
      works for local OpenClaw/Codex use and Docker deployments; and failure
      modes are fail-closed without leaking credentials.
