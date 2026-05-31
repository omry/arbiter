# Agent Arbiter Backlog

## Agent instructions

When helping with backlog work, treat this file as the active planning
surface for Agent Arbiter. Keep it short, concrete, and easy to scan. Prefer
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
- After each focused phase, run a focused review of the phase diff and commit
  the ready changes before starting the next phase.
- At every phase boundary or pause, state the current action, why work is
  stopping, and whether the next step needs user review, approval, input, or no
  user action.

## Direction

- Current platform name: `Agent Arbiter`.
- Architecture direction: one service equals one independently installable
  plugin. SMTP and IMAP should move out of the core package into separate
  plugin distributions so future services such as CalDAV, CardDAV, and Sieve
  can be added without forcing operators to carry unused service code or
  configuration.

## Now

- [ ] `P1` Prepare release packaging and version readiness.
      The service plugin/config reroute is in place, so the package/release
      surface needs one explicit readiness pass before initial release.
      Acceptance checks: the intended version target is chosen; package
      metadata and deployment requirements docs agree; release notes and status notes
      are current; and a build/install smoke path is verified.

## Post-v1

- [ ] `P2` Clean up the legacy `docs/` directory now that the website exists.
      Most or all of the old markdown docs may be redundant after the Docusaurus
      website became the operator-facing documentation home. Acceptance checks:
      audit every file under `docs/`; move any still-current content into
      `website/docs/` or link to the website equivalent; delete obsolete or
      duplicated files; keep only intentionally internal planning/reference
      files; and update README or contributor references that still point at
      removed docs.

- [ ] `P2` Build a proper documentation site, likely with Docusaurus.
      The README and markdown docs are enough for early development, but the
      operator and plugin-author surfaces need a real documentation home.
      Acceptance checks: choose the docs framework; create navigation for
      operators, plugin authors, config, CLI, deployment, and safety policy;
      migrate or link the existing markdown docs without duplicating stale
      examples; and document how generated config tooling fits into the docs
      workflow.

- [ ] `P2` Revisit service-first config shape.
      The MCP discovery surface is moving toward capability-first drill-down,
      and the server config may want the same shape: `smtp.accounts`,
      `smtp.policies`, `imap.accounts`, and `imap.policies` instead of
      top-level account and policy containers. Also decide whether the
      placeholder `etc` config surface should be removed until a concrete use
      exists.
      Acceptance checks: compare the current Hydra composition shape against a
      service-first shape; decide whether activation remains readable and easy
      to generate; remove or justify `etc`; and document the chosen operator
      model.

- [ ] `P2` Define third-party service plugin naming standards.
      Plugin authors need consistent conventions for distribution package
      names, Python module names, entry point names, capability names, and
      config group names. Acceptance checks: recommend a PyPI package naming
      pattern, a Python module naming pattern, and an entry point convention;
      document how those names map to `arbiter-server plugins list`,
      `bootstrap plugin <plugin> ...`, config paths, and capability ids; and
      update the plugin author guide with one complete example.

- [ ] `P2` Design live config reload for service runtimes. A future reload path
      should apply validated configuration changes without interrupting
      in-flight tool calls. Acceptance checks: define whether reload happens by
      rebuilding the full server, swapping only affected service runtimes, or
      refreshing selected subsystem state; new connections or tool calls see
      the new config only after validation succeeds; failed reloads keep the
      previous runtime active; and logs expose which services changed.

- [ ] `P2` Let Hydra own server logging configuration.
      Agent Arbiter is a server process, so operators need proper logging
      without a parallel Arbiter-specific logging surface. Hydra should remain
      the owner of server logging configuration, including job and Hydra
      logging groups, while the CLI stays simple and prints user-facing
      messages.
      Acceptance checks: document how operators configure server logs through
      Hydra; confirm no library configures logging before server composition;
      decide whether bootstrap should generate any logging config or only
      document it; and keep operational logs separate from future audit
      records.

- [ ] `P2` Decide whether service-scoped policies should remain the long-term
      home for access gates and caller confirmation.
      The current implementation uses service-scoped policies for access gates
      and caller confirmation. That may still be the right abstraction, but it
      is also plausible that access control and caller confirmation should not
      live in the same container.
      Acceptance checks: the design notes compare the current policy approach
      against one or two clearer alternatives; tradeoffs are recorded; and the
      chosen direction informs the next config cleanup pass.

- [ ] `P2` Design durable audit storage and its policy home.
      Audit is parked for post-v1. The v1 release should not ask operators to
      configure audit behavior that the runtime cannot yet honor. V1 removed
      SMTP and IMAP audit blocks from the operator-facing schema, so future
      audit work should define both durable storage and where audit settings
      belong.
      Acceptance checks: audit storage, retention, event shape, and privacy
      defaults are defined; SMTP and IMAP audit events are emitted through one
      durable path; docs distinguish audit records from operational logs; the
      design decides whether audit belongs in service-scoped policies, a
      separate audit policy block, or another clearer home; and the resulting
      config shape is materially lighter for operators.

- [ ] `P2` Design client identification and caller authentication.
      V1 assumes the caller is trusted once connected. Future hardening should
      decide whether Agent Arbiter needs to identify CLI and MCP clients, only
      authenticate them, or do both. Candidate mechanisms may include a shared
      secret, bearer token, password, client certificate, or mTLS/PKI so
      deployments can prevent unsafe access to the Agent Arbiter MCP boundary.
      Acceptance checks: candidate mechanisms are compared; the design defines
      whether client identity is stable, user-visible, and recorded in logs or
      audit events; the chosen model works for local agent/Codex use, generic
      MCP clients, and Docker deployments; and failure modes are fail-closed
      without leaking credentials.

- [ ] `P2` Add an installation security evaluator.
      Operators need a tool that checks whether an Agent Arbiter installation
      is safe to run: config files, env files, installed package files, plugin
      packages, and startup scripts should not be writable by agent-controlled
      users, and the server should not run as root/admin in normal production
      deployments.
      Acceptance checks: define the inspected paths and platform-specific
      permission checks; add a command that reports actionable findings; decide
      whether startup should run the evaluator by default, warn, or refuse to
      run on failure; and document how operators intentionally override checks
      for local development.

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

- [ ] `P2` Consider programmatic access and language bindings.
      CLI and MCP are the first access surfaces, but some integrations may want
      direct programmatic clients. Future work should decide whether Agent
      Arbiter should provide official bindings for popular languages such as
      Python, TypeScript, Rust, and Go, or instead publish enough protocol and
      schema contracts for community clients.
      Acceptance checks: identify likely embedding use cases; compare official
      bindings against generated clients or protocol-only documentation; define
      versioning and compatibility expectations; and choose the first language
      target, if any.

- [ ] `P2` Evaluate SQL as a future Agent Arbiter service plugin.
      SQL is a valuable agent use case because database access is high-stakes
      and policy control can materially reduce blast radius. It is also
      technically challenging: query input, result output, and large result
      sets may require streaming or pagination instead of simple request/response
      tool calls.
      Acceptance checks: define the initial SQL threat model; decide which
      primitives are safe enough for v1 of a SQL plugin; design policy controls
      for read/write scope, row limits, timeouts, and schema visibility; and
      decide whether MCP/client support needs streaming, pagination, or both.
