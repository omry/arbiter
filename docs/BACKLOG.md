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
- At every phase boundary or pause, state the current action, why work is
  stopping, and whether the next step needs user review, approval, input, or no
  user action.

## Now

- [ ] `P1` Prepare release packaging and version readiness.
      The v1 contract is now clearer, but the package/release surface still
      needs one explicit readiness pass before initial release.
      Acceptance checks: the intended version target is chosen; package
      metadata and install-target docs agree; release notes and status notes
      are current; and a build/install smoke path is verified.

## Post-v1

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

- [ ] `P2` Design durable audit storage and its policy home.
      Audit is parked for post-v1. The v1 release should not ask operators to
      configure audit behavior that the runtime cannot yet honor. V1 removed
      SMTP and IMAP audit blocks from the operator-facing schema, so future
      audit work should define both durable storage and where audit settings
      belong.
      Acceptance checks: audit storage, retention, event shape, and privacy
      defaults are defined; SMTP and IMAP audit events are emitted through one
      durable path; docs distinguish audit records from operational logs; the
      design decides whether audit belongs in shared account access profiles, a
      separate audit policy block, or another clearer home; and the resulting
      config shape is materially lighter for operators.

- [ ] `P2` Design bot-to-Sentry caller authentication or authorization.
      V1 assumes the caller is trusted once connected. Future hardening may use
      a shared secret, bearer token, password, client certificate, or mTLS/PKI
      so deployments can prevent unsafe access to the Mail Sentry MCP boundary.
      Acceptance checks: candidate mechanisms are compared; the chosen model
      works for local OpenClaw/Codex use and Docker deployments; and failure
      modes are fail-closed without leaking credentials.

- [ ] `P2` Generate baseline CLI parameters from MCP tool schemas.
      The MCP surface already defines rich input shape metadata, and that
      contract should become the default source for a generic CLI layer rather
      than being re-declared by hand for each tool. Service-specific wrappers
      can still add better UX on top.
      Acceptance checks: a design or implementation path exists for deriving
      CLI flags from MCP `inputSchema`; required, optional, list, enum, and
      bounded scalar fields map predictably; generated invocations round-trip
      into valid tool arguments; and the design clearly separates generic
      schema-driven CLI generation from optional task-specific wrapper
      behavior.
