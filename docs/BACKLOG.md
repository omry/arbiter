# Arbiter Backlog

## Agent instructions

When helping with backlog work, treat this file as the active planning
surface for Arbiter. Keep it short, concrete, and easy to scan. Prefer
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

## Now

- [ ] `P1` Fix SMTP idempotency cache readiness in deployed runtimes.
      Keyed sends can fail before delivery when the configured idempotency
      cache directory cannot be created or written, as seen with the relative
      `.arbiter/smtp-idempotency` path in the live MCP mail connector.
      Acceptance checks: deployed SMTP policies use or resolve to a writable
      durable cache path; startup or account checks validate idempotency
      storage when keyed sends are advertised; keyed send failures caused by
      cache setup return an operator-useful configuration error before any SMTP
      submission attempt; and SMTP docs describe cache path and permission
      expectations.

- [ ] `P1` Prepare release packaging and version readiness.
      The service plugin/config reroute is in place, so the package/release
      surface needs one explicit readiness pass before initial release.
      Acceptance checks: the intended version target is chosen; package
      metadata and deployment requirements docs agree; release notes and status notes
      are current; and a build/install smoke path is verified.

- [ ] `P1` Add CI smoke tests for all platform-specific Arbiter skill binaries.
      The `arbiter-skill` selector routes to six native Go client target
      packages, so release CI should prove each target binary starts and can
      talk to an Arbiter-compatible local test server before publishing.
      Acceptance checks: CI builds or downloads all six target wheels; extracts
      or installs each target artifact; runs the packaged `bin/arbiter` or
      `bin/arbiter.exe` for `linux-amd64`, `linux-arm64`, `darwin-amd64`,
      `darwin-arm64`, `windows-amd64`, and `windows-arm64`; each binary passes
      at least `--version` and one smoke request against a local test MCP server;
      and failures identify the target package, OS, architecture, command, and
      server log excerpt.

- [ ] `P1` Run an Arbiter security analysis before initial release.
      Do one focused threat-model and implementation review pass over the
      current architecture before publishing packages. Cover the MCP boundary,
      local and Docker deployment modes, config and env-file handling, plugin
      discovery/loading, package supply chain assumptions, secret handling,
      SMTP/IMAP operation policies, logging, and known audit gaps.
      Acceptance checks: produce a short written security analysis with trust
      boundaries, assets, attacker assumptions, and prioritized findings; turn
      concrete fixes into backlog items or immediate patches; document any
      explicit accepted risks for the initial release; and confirm operator
      docs do not overstate the current security model.

- [ ] `P1` Complete the website documentation readiness pass.
      The Docusaurus site is the user-facing documentation home, but it still
      needs a release-readiness pass across operator and plugin-author
      workflows. Acceptance checks: quickstart, package installation, Docker
      deployment, config, CLI, security, plugin author, testing, and release docs
      use current package names, command names, config paths, version examples,
      and security claims; stale examples are fixed or removed.

## Post-v1

- [ ] `P2` Decide the long-term config and policy shape.
      The MCP discovery surface is moving toward capability-first drill-down,
      and the server config may want the same shape: `smtp.accounts`,
      `smtp.policies`, `imap.accounts`, and `imap.policies` instead of
      top-level account and policy containers. Also decide whether the
      placeholder `etc` config surface should be removed, and whether caller
      confirmation belongs in the same service-scoped policy container as
      runtime access gates.
      Acceptance checks: compare the current Hydra composition shape against a
      service-first shape; decide whether activation remains readable and easy
      to generate; remove or justify `etc`; compare policy-shape alternatives;
      and document the chosen operator model.

- [ ] `P2` Document third-party service plugin naming standards.
      Plugin authors need consistent conventions for distribution package
      names, Python module names, entry point names, capability names, and
      config group names. Compatibility-line versioning and runtime compatibility
      checks already exist; this item is about the naming contract. Acceptance
      checks: recommend PyPI package, Python module, entry point, config group,
      and capability naming patterns; document how those names map to
      `arbiter-server plugins list`, `bootstrap plugin <plugin> ...`, config
      paths, capability ids, and runtime compatibility checks; and update the
      plugin author guide with one complete example.

- [ ] `P2` Design live config reload for service runtimes. A future reload path
      should apply validated configuration changes without interrupting
      in-flight tool calls. Acceptance checks: define whether reload happens by
      rebuilding the full server, swapping only affected service runtimes, or
      refreshing selected subsystem state; new connections or tool calls see
      the new config only after validation succeeds; failed reloads keep the
      previous runtime active; and logs expose which services changed.

- [ ] `P2` Add per-account service smoke tests. Each service plugin should be
      able to register a quick stateless account test that uses the configured
      credentials and returns a structured status. Arbiter should expose one
      aggregate server endpoint that tests all configured service accounts and
      reports per-service/per-account results without mutating remote state.
      Acceptance checks: define the plugin hook contract; implement SMTP and
      IMAP smoke tests that avoid writes or destructive side effects; expose one
      MCP tool for all account tests; return clear success, skipped, and failure
      statuses with operator-useful messages; and document how deployment smoke
      checks can call the aggregate endpoint.

- [ ] `P2` Let Hydra own server logging configuration.
      Arbiter is a server process, so operators need proper logging
      without a parallel Arbiter-specific logging surface. Hydra should remain
      the owner of server logging configuration, including job and Hydra
      logging groups, while the CLI stays simple and prints user-facing
      messages.
      Acceptance checks: document how operators configure server logs through
      Hydra; confirm no library configures logging before server composition;
      decide whether bootstrap should generate any logging config or only
      document it; and keep operational logs separate from future audit
      records.

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

- [ ] `P2` Pick the next transport and identity implementation slice.
      [future/transport-and-identity.md](future/transport-and-identity.md)
      records the design direction. The next backlog item should be one concrete
      implementation slice, not another broad identity brainstorm. Acceptance
      checks: choose one next slice from the design doc, such as remote-access
      warnings, agent identity config, or an mTLS prototype; define the expected
      operator behavior; and keep transport encryption separate from
      bidirectional identity.

- [ ] `P3` Investigate user-owned secret execution for hosted Arbiter.
      A future hosted Arbiter model may need to serve users who do not have
      administrative access to the Arbiter host and do not want the host
      operator to receive their account credentials. Treat this as a careful
      research direction rather than a product promise. Acceptance checks:
      define the hosted threat model and which party is trusted with runtime
      plaintext or usable delegated authority; distinguish storage encryption
      from runtime credential access; evaluate options such as user-owned local
      sidecars, per-user workers, OAuth delegation, PKI-mediated secret release,
      and confidential-computing/enclave designs; and document what each option
      does and does not protect from the Arbiter operator.

- [ ] `P2` Finish the installation security evaluator.
      The Docker helper now has `doctor` and `install` checks for generated
      deployment files, env files, Docker socket access, and agent identity. The
      remaining work is to decide whether this stays Docker-specific or becomes a
      general server/deployment check.
      Acceptance checks: define the non-Docker inspected paths and platform
      limits; decide whether startup should run the evaluator by default, warn,
      or refuse to run on failure; and document intentional overrides for local
      development.

- [ ] `P2` Add a plugin surface for filtering incoming and outgoing data.
      Arbiter should eventually let security plugins inspect or transform data
      crossing trust boundaries, including prompt-injection detection on
      inbound content and data-exfiltration detection on outbound content.
      Acceptance checks: define the filter hook contract and ordering; identify
      which MCP requests, tool arguments, tool results, logs, and service
      payloads are in scope; support allow, block, redact, and warn outcomes;
      specify how findings are reported to callers and operators; and document
      the trust, privacy, latency, and failure-mode expectations for filter
      plugins.

- [ ] `P2` Add Docker deployment uninstall support.
      Operators who use `arbiter-docker install` need a matching cleanup path.
      Acceptance checks: `arbiter-docker uninstall` stops and disables the
      installed systemd service; removes the generated unit; optionally removes
      the installed deployment directory behind an explicit confirmation or flag;
      preserves user-owned config/secrets unless removal is explicitly requested;
      and prints a clear summary of what was removed and what remains.

- [ ] `P2` Add Docker deployment upgrade support.
      Operators need an explicit path for updating an installed deployment
      without guessing which source directory, wheelhouse, or systemd state is
      authoritative. Acceptance checks: define whether upgrade is a mode of
      `install` or a separate command; refresh requirements and the installed
      wheelhouse before restarting; preserve config and secrets by default;
      report the old and new package set when possible; and document rollback
      expectations.

- [ ] `P2` Add Docker bundle lock and package-management commands.
      The Docker helper has `bundle prepare`, `bundle check`, `bundle list`,
      wheelhouse-based `bundle list all`, and package-root `bundle upgrade`,
      but operators also need a managed way to add/remove roots and persist
      resolver state.
      Acceptance checks: support `bundle add` and `bundle remove` for one or
      more root packages; persist enough lock metadata to report resolved
      package changes after prepare/upgrade; and detect when the wheelhouse or
      lock is stale relative to root requirements.

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

- [ ] `P2` Bring the Go CLI client to parity with the Python CLI.
      The Go CLI is expected to become the canonical distributable Arbiter
      client, while the Python CLI is transitional and repo-local. Before the
      Python CLI can be removed or fully demoted, the Go CLI should cover the
      working user-facing behavior that currently exists there.
      Acceptance checks: compare command, flag, config, override, output, error,
      and version-warning behavior between `client/python-cli` and
      `client/go-cli`; close or explicitly defer each gap; ensure the native
      `arbiter-client` wheel exercises the canonical CLI in release smoke
      tests; update docs to prefer the Go CLI; and either remove the Python CLI
      or document the remaining repo-local reason it exists.

- [ ] `P2` Add plugin-authored workflow discovery.
      Service plugins should be able to describe domain-specific manual
      workflows that help agents choose and sequence existing atomic
      operations without adding orchestration to Arbiter itself. These
      workflows are plugin-owned guidance, not user-authored workflows.
      Acceptance checks: define a small workflow metadata contract with stable
      ids, purpose, prerequisites, referenced operation ids, suggested steps,
      policy notes, and preferred output views; expose workflows through
      discovery for MCP and CLI surfaces from the same metadata; add at least
      one IMAP example such as message lookup using `imap:search_messages` then
      `imap:get_message`; and keep operation schemas/descriptions owned by the
      plugin's atomic operations.

- [ ] `P2` Design user-authored cross-plugin workflow surface.
      User-authored workflows are a separate product surface from
      plugin-authored workflow discovery. They should be able to describe
      manual workflows that cross plugin boundaries, such as reading an
      original message with IMAP and sending an approved response with SMTP,
      while preserving the underlying per-account policies and operation
      boundaries.
      Acceptance checks: define where user-authored workflows are stored and
      discovered; define how workflows reference canonical operation ids across
      plugins; distinguish user-authored workflow guidance from plugin-owned
      atomic operation descriptions; model policy and approval gates without
      hiding the underlying operations; and document at least one mail
      cross-plugin example without making Arbiter execute the workflow.

- [ ] `P2` Add skill-local discovery caching.
      The skill client should be able to cache Arbiter discovery responses
      under a skill subdirectory and reuse them to speed up time to first
      request. Cache validity should be keyed by a server-returned config hash
      available on all requests, so the client can detect config changes and
      re-fetch the discovery surface when needed. Acceptance checks: the server
      exposes a stable config/discovery hash on all relevant responses; the
      skill client stores discovery cache files under the installed skill tree;
      cache entries are invalidated when the hash changes; cache layout mirrors
      the server's request/response discovery layers on disk; and the first
      request can shortcut discovery when a valid cache is present without
      hiding config-change detection failures.

## Done Recently

- [x] Split SMTP and IMAP into independently installable service-plugin
      packages. Future service packages should follow this plugin distribution
      model.

- [x] Remove duplicated legacy markdown docs from `docs/`. The public
      documentation surface is `website/docs/`; `docs/` is now internal planning
      and future design notes only.
