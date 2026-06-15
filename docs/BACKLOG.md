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

- [ ] `P1` Prepare release packaging and version readiness.
      The `0.9.1.dev2` full publish validated the package split and PyPI
      trusted-publisher path for `arbiter-server`, `arbiter-imap`,
      `arbiter-smtp`, `arbiter-suite`, `arbiter-skill`, and `arbiter-client`.
      A standalone ASI install from PyPI also validated that `arbiter-skill`
      resolves and copies the `arbiter-client` companion wheel.
      Remaining readiness work is the final-release pass, not the dev publish
      mechanics. Acceptance checks: the intended final version target is
      chosen; package metadata and deployment requirements docs agree; release
      notes and status notes are current; local build/install smoke is verified;
      and PyPI post-publish verification uses release-specific endpoints.

- [ ] `P1` Add CI smoke tests for all platform-specific Arbiter client wheels.
      The `arbiter-skill` package now relies on ASI to copy the native Go client
      from the platform-selected `arbiter-client` companion wheel, so release CI
      should prove each client wheel starts and can talk to an Arbiter-compatible
      local test server before publishing.
      Acceptance checks: CI builds or downloads all six `arbiter-client` wheels;
      extracts or installs each target artifact; runs the packaged `arbiter`
      executable for `linux-amd64`, `linux-arm64`, `darwin-amd64`,
      `darwin-arm64`, `windows-amd64`, and `windows-arm64`; each binary passes
      at least `--version` and one smoke request against a local test MCP server;
      ASI install testing verifies `arbiter-skill` copies the selected companion
      client into `bin/arbiter`; and failures identify the target package, OS,
      architecture, command, and server log excerpt.

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

- [ ] `P1` Promote artifact delivery into a first-class server surface.
      IMAP attachments are the first artifact-producing workflow, but artifact
      delivery is a reusable client-facing facility rather than an IMAP-specific
      detail. Acceptance checks: expose artifact delivery in server/plugin
      discovery with description, guidance, one-time URL semantics, HEAD metadata
      expectations, default TTLs, size/text safety rules, and recommended client
      commands; add config options for artifact behavior and guidance; let
      artifact-producing operations declare that they return `arbiter_artifact`
      delivery; guide agents to avoid consuming large artifacts directly and to
      pipe artifacts to appropriate tools when explicit artifact access is
      needed; document how plugins can produce artifacts without redefining the
      artifact contract from scratch; evaluate whether client commands should
      take an artifact ID resolved through Arbiter instead of a raw artifact URL,
      and remove raw URL handling if the ID-based contract is better for agents
      and sandboxing; coordinate Codex sandbox loopback
      allowlisting so local Arbiter MCP and artifact URLs such as
      `http://127.0.0.1:8025/...` do not require per-command escalation.

- [ ] `P1` Complete the website documentation readiness pass.
      The Docusaurus site is the user-facing documentation home, but it still
      needs a release-readiness pass across operator and plugin-author
      workflows. Acceptance checks: quickstart, package installation, Docker
      deployment, config, CLI, security, plugin author, testing, and release docs
      use current package names, command names, config paths, version examples,
      and security claims; stale examples are fixed or removed.

## Post-v1

- [ ] `P2` Add real OS/process isolation for plugin writable storage.
      The server now gives each plugin a scoped storage capability, but
      same-process Python plugins still run as the same OS user and can bypass
      path-capability conventions. Process isolation is valuable even before it
      becomes a complete security boundary because it can also support live
      plugin add/remove, crash containment, and plugin config reload without
      restarting the main server. Acceptance checks: define a portable plugin
      worker model and an isolation-provider abstraction with capability
      reporting, such as crash containment, live reload, filesystem isolation,
      network isolation, and process-tree isolation; evaluate process,
      container, or OS-user isolation providers for plugins; ensure one plugin
      cannot read another plugin's data directory through ordinary filesystem
      access where the platform supports strong enforcement; document Linux as
      the recommended production platform and most capable provider target for
      stronger isolation; keep Windows and macOS isolation easy and ergonomic
      without overclaiming equivalent security guarantees; and document the
      threat model for trusted versus isolated plugins.

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

- [ ] `P2` Create a web interface for Arbiter management.
      Operators should have a first-class browser surface for inspecting and
      managing Arbiter without editing config files or reading raw MCP
      discovery output for common tasks. Acceptance checks: define the initial
      management scope, such as server status, configured services/accounts,
      plugin discovery, policy visibility, and safe configuration checks;
      define authentication and trust assumptions before exposing remote access;
      implement a minimal read-only management view before adding mutation
      paths; and document how the web surface relates to CLI and deployment
      workflows.

- [ ] `P2` Create a side-by-side Arbiter demo surface.
      A demo should show the agent view and the Arbiter management/operator
      view together, so users can send requests and see what the agent is
      seeing through Arbiter. Acceptance checks: provide a runnable demo mode
      with a safe sample configuration; show request input, selected agent
      context/discovery, Arbiter policy and account state, tool call/result
      flow, and operator-visible logs or decisions side by side; avoid exposing
      real credentials or production accounts; and make the demo useful for
      documentation, onboarding, and security-model explanation.

- [ ] `P2` Design live config reload for service runtimes. A future reload path
      should apply validated configuration changes without interrupting
      in-flight tool calls. Acceptance checks: define whether reload happens by
      rebuilding the full server, swapping only affected service runtimes, or
      refreshing selected subsystem state; new connections or tool calls see
      the new config only after validation succeeds; failed reloads keep the
      previous runtime active; and logs expose which services changed.

- [ ] `P2` Add a core synchronous service request/reply bus for plugin-to-plugin
      workflows. SMTP sent-copy saving to IMAP Sent mail is the first use case:
      SMTP should request an IMAP append and receive a success or failure before
      returning to the caller, without taking a direct dependency on the IMAP
      runtime. Acceptance checks: define a core-owned request/reply contract
      with service name, command name, payload, timeout, correlation id, and
      structured success/error replies; let plugins register internal handlers
      that are not exposed as agent operations; route missing service or missing
      handler cases into structured failures; support in-process dispatch first
      while keeping the transport boundary compatible with future process
      isolation; migrate SMTP sent-copy from the temporary server-wired adapter
      to the bus; and document when plugins should use request/reply versus
      client-facing operations.

- [ ] `P2` Add SMTP attachment sending support.
      `smtp:send_email` currently sends text and/or HTML bodies only. Agents
      need a controlled way to include attachments without receiving arbitrary
      filesystem access or embedding unbounded binary payloads in tool calls.
      Acceptance checks: define the attachment input contract, such as
      artifact ids, bounded inline text, or explicit client-provided files;
      enforce size, count, content-type, and filename policy before SMTP
      submission; include attachments in the exact MIME bytes used for SMTP
      delivery and Sent-copy append; make retry keys cover attachment content
      so retries cannot silently change attachments; document safe client
      workflows for attaching files; and add unit/integration coverage for
      attachment MIME construction, policy failures, retry behavior, and
      Sent-copy preservation.

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

- [ ] `P2` Add IMAP message flags APIs.
      Agents need a controlled way to inspect and update message flags such as
      `\Seen`, `\Flagged`, and custom provider flags without dropping to raw
      IMAP behavior. Use the existing IMAP policy model as inspiration for
      which flag reads are always safe, which writes require allowlisting, and
      which writes need confirmation. Acceptance checks: expose operations to
      read flags for one message and update flags by adding, removing, or
      replacing an explicit set; scope operations by account, folder, and UID;
      validate system versus custom flag names; apply policy gates for mutating
      flags; document provider-specific caveats; and test that disallowed or
      confirmation-required flag mutations cannot silently change mailbox state.

- [ ] `P2` Add IMAP folder management APIs.
      Bot accounts need a controlled way to create, rename, and delete mailbox
      folders without requiring operators to pre-create every workflow-specific
      folder manually. Acceptance checks: expose folder create, rename, and
      delete operations scoped by account; require explicit policy allowlisting
      for mutating folder operations, with stricter defaults for non-bot
      accounts; validate folder names against configured account conventions and
      reject unsafe roots such as INBOX unless explicitly allowed; define how
      runtime folder mutations relate to the static configured folder allowlist;
      return clear provider errors for unsupported or protected mailbox
      operations; and document recovery expectations for accidental deletes or
      provider-side rename semantics.

- [ ] `P2` Support IMAP folder allow-list and deny-list access models.
      Operators should be able to configure either a deny-all-by-default
      allow-list model or an allow-all-by-default deny-list model for folder
      visibility and operations. Acceptance checks: define the config shape and
      defaults for both modes; support exact names and wildcard patterns using
      the account's mailbox naming conventions; apply the same access decision
      consistently to folder listing, search, message reads, message moves,
      flag updates, sent-copy append targets, and future folder management
      operations; return clear policy errors when a folder is hidden or denied;
      expose enough policy summary in discovery for agents to understand the
      available surface without leaking denied folders; and document safe
      defaults for personal versus bot accounts.

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

- [ ] `P2` Centralize movable scratch directories.
      Several tools create local-only build, publish, cache, and staging
      directories at different repo locations, which makes cleanup and watcher
      ignores harder to reason about. Move scratch outputs that do not need to
      live beside their source into one clearly named local scratch root.
      Acceptance checks: inventory current and potential scratch directories,
      including `dist-publish`, `dist`, `.ci`, `outputs`, `temp`,
      `client/go-cli/dist`, `skill/bin`, website build output, and package
      build caches; decide which ones must stay in place and which can move;
      update tool defaults, docs, `.gitignore`, `.dockerignore`, and
      `.watchmanconfig`; preserve release and CI behavior; and document the
      cleanup command or policy for the central scratch root.

- [ ] `P2` Convert the Docker helper to a Python-backed deployment tool.
      The generated `arbiter-docker` Bash helper has grown into a large
      deployment application with duplicated rules for env parsing,
      requirements, bundle metadata, wheelhouse state, generated-file manifests,
      Docker Compose, systemd install, doctor checks, and host permissions.
      Convert it in phases so the operator command surface remains stable while
      deployment semantics move into testable Python owned by `arbiter-server`.
      Keep the generated helper usable on conservative hosts: target a
      primitive Python subset compatible with Python 3.6, avoid optional modern
      runtime dependencies, and keep the bootstrap path clear when Python or
      Arbiter packages are not yet installed.
      Plan: first extract the current Docker deploy generation logic from
      `arbiter_server.main` into a dedicated deployment module without changing
      behavior; define the host contract for the generated helper, including
      whether it may require `/usr/bin/env python3`, whether it may run Python
      inside the Arbiter container, and which commands must remain pure
      bootstrap; add characterization tests for the current Bash helper's public
      commands and important failure messages; replace duplicated data rules
      with generated metadata files or shared Python functions, starting with
      compose defaults, plugin bundle metadata, meta-package expansion,
      requirement validation, env-file parsing, path resolution, and deploy
      manifest hashes; introduce a Python implementation for low-risk local
      commands such as `info`, `bundle list`, requirement editing/validation,
      and manifest inspection while the Bash wrapper delegates to it; migrate
      wheelhouse, bundle upgrade, doctor, install, and systemd behavior only
      after the Python implementation can run in dry-run mode and produce the
      same plans as Bash; keep `arbiter-docker COMMAND` as the user-facing
      entrypoint throughout; and remove the old Bash bodies only after command
      parity is covered by tests and docs.
      Acceptance checks: the generated deployment still contains an executable
      `arbiter-docker` entrypoint; supported hosts with Python 3.6 can run the
      helper without installing extra Python packages; deployments that only
      have Docker access can still prepare, check, install, and operate without
      requiring a globally installed Arbiter package; server-side generation and
      helper-side execution share one source of truth for requirement syntax,
      meta-package expansion, bundle plugin metadata, compose defaults, env-file
      parsing, path resolution, and manifest ownership; existing Docker helper
      tests pass with updated expectations; new tests cover dry-run install
      plans, doctor findings, wheelhouse validation commands, local checkout
      source handling, generated-file drift, and missing-Python error guidance;
      docs explain the helper runtime requirements and upgrade path; and
      follow-up Docker items such as uninstall, upgrade, and bundle locking are
      either implemented on the Python foundation or explicitly deferred until
      after the conversion.

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
