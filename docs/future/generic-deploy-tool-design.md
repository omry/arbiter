# Generic Deploy Tool Design Draft

Status: temporary planning draft.

This document captures the initial shape for a generic deployment lifecycle
tool that can start inside the Arbiter repository as a subproject. It should
stay product-agnostic enough to support more than Arbiter later, but it should
not chase a fully generic platform before Arbiter proves the first target.

## Starting Point

Arbiter's current Docker deployment flow has useful behavior:

- it creates a durable staging deployment directory
- it manages a selected installation bundle for Arbiter and its plugins
- it prepares Arbiter's current Python installation bundle
- it validates config before start/install
- it runs a staged Docker Compose service
- it promotes a tested directory to a system service, currently systemd
- it preserves installed config and env by default
- it has doctor/preinstall checks

The shape has three problems:

- the flow starts with `arbiter-server deploy docker init`, which is circular
  because the server is the thing being installed
- the generated `arbiter-docker` helper has become a large shell program
- deployment and installer logic live inside the Arbiter server package

## Product Thesis

Create a generic, static, single-binary deployment lifecycle tool.

The tool should manage deployments across targets. Docker is the first target,
not the product boundary. Later targets could include bare metal, AWS, or other
cloud/runtime backends.

The durable unit is a deployment directory plus state, not a web UI or long
running controller.

## Working Name

Use `deploy` as the product concept. The initial binary name is `reploy`.
It is a short deploy/redeploy-adjacent name, starts specific enough for this
repository, and still leaves room to promote the tool later if a second use
case appears.

Possible names:

- `reploy` as the initial name; it appeared unclaimed when the PyPI JSON
  endpoint for `reploy` returned 404 on 2026-06-22
- a generic name such as `deploykit`, `stackctl`, or `shipctl` if it becomes
  independent

Keep package/module names generic enough that a future rename is possible.

## Core Model

```text
deploy binary
  -> target backend
  -> deployment directory
  -> plan/apply/check lifecycle
  -> optional app blueprint for product-specific knowledge
```

The deployment directory should contain the generated and operator-owned state
for one deployment:

```text
deploy.yaml
.reploy/
  state.json
  compose.yaml            # Docker target
  docker.env              # Docker target
  bin/reploy              # optional vendored exact management binary
conf/                     # app config, when relevant
data/
backups/
```

The exact layout is target-specific, but the lifecycle concepts should be
shared.

`conf/`, `data/`, and `backups/` are application/deployment state. `bin/` is
tooling state. During the Arbiter-hosted phase, `bin/reploy` is the vendored
management binary name. When present, it should contain the exact deploy binary
version that created or last migrated the deployment directory. This keeps the
deployment operable even if the host PATH changes, a package is uninstalled, or
the global deploy tool advances to an incompatible version. A small
deployment-local wrapper can prefer the vendored binary and fall back to PATH.

## Responsibilities

Generic deploy tool owns:

- target initialization and update
- deployment manifest and state
- plan/apply lifecycle
- deployment phase/profile model
- doctor framework and generic preflight checks
- backup, restore, rollback extension points
- generated file manifests
- host/service-manager abstraction
- target lifecycle commands such as up, down, logs, status
- upgrade orchestration
- config/env preservation policy
- app blueprint metadata interpretation and app capability invocation
- generic bundle and cache lifecycle

Arbiter owns:

- server runtime
- config schema and config validation
- plugin/account bootstrap semantics
- operation and plugin discovery
- Arbiter-specific health checks
- Arbiter-specific config migrations
- Arbiter-specific doctor checks
- Python package, PyPI, source checkout, and wheelhouse semantics

The deploy tool may call Arbiter commands as validation capabilities, but Arbiter
should not own deployment mechanics.

Package ecosystems are app blueprint/provider concerns, not generic deploy-core concerns.
The generic tool should understand bundles, deployable artifacts, locks,
caches, and prepared runtime inputs. It should not bake in PyPI, Python source
builds, or wheelhouses. The Arbiter app blueprint can provide a Python package
provider that maps package roots to a prepared wheelhouse for the Docker target.

## Plan, Apply, And Reporting

The tool should use one result model for mutating commands and their dry-run
counterparts. `init`, `update`, migration, install, and later target changes are
apply operations. A future `plan` mode should compute the same rows without
writing files, creating containers, changing services, or modifying deployment
state.

The default human output should be sparse:

- print one row for each actionable result
- suppress per-file `up_to_date` rows
- print a single `up_to_date` line when there were no actionable results
- send usage errors and operation failures to stderr
- keep machine-readable output as a later explicit format, not the default

The initial result status vocabulary is:

- `updated`: the operation created or changed the target artifact
- `up_to_date`: the desired state already matched
- `skipped`: the operation intentionally preserved a file or resource, usually
  because it appears operator-owned or locally edited

This small vocabulary is enough for the first Docker target. If future plan
output needs to distinguish creation from modification, that should be added as
a plan-format version change rather than inferred from English text.

Every result row should include enough structure for later JSON output:

- target path or resource identifier
- status
- ownership class, such as generated, local/operator-owned, state, artifact, or
  external resource
- optional reason for `skipped`
- optional before/after hashes for generated files and cached artifacts

For apply commands, partial success must be visible. If a command changes one
artifact and then fails, it should report the error with the operation context
and leave enough state for `doctor` or a subsequent `plan` to explain what is
now divergent. Commands should not silently rewrite operator-owned files. When
preservation blocks an update, the result is `skipped`, not success disguised as
`up_to_date`.

Error reporting should use stable layers:

- usage errors: invalid command line, missing required flags, unsupported blueprint
  reference forms; exit 2
- configuration errors: invalid blueprint manifests, invalid deployment state, local
  drift that needs operator action; exit 1
- environment errors: missing Docker, missing files, permission failures, port
  conflicts, network cleanup failures; exit 1
- remote resolution errors: package index, archive fetch, checksum, or cache
  failures; exit 1
- internal errors: unexpected invariants or template bugs; exit 1 with enough
  context to file a bug

Human error messages should name the command and failing operation, for example
`reploy update error: read deployment state: ...`. Lower layers should
wrap errors with the action being attempted, not with product-specific advice.
Advice belongs in `doctor` findings or future structured diagnostics.

## Initial Target: Docker

The first target should preserve the current useful Arbiter behavior:

- initialize a Docker deployment directory
- select and prepare an installation bundle
- run a staged Compose service
- run static and live config checks
- run doctor and preinstall checks
- install/promote to the current systemd-backed service shape
- preserve installed config/env by default

The initial implementation can migrate the existing generated helper behavior
behind a Go command surface. The generated shell script should shrink to a
small wrapper, or the deployment directory should vendor the exact Go binary.

## Future Targets

Bare metal:

- prepare app-provided installation artifacts
- write service-manager integration, with systemd as the first likely example
- manage data/config/env paths
- validate local runtime and ports
- upgrade code and config

AWS:

- deploy to ECS, App Runner, EC2, or another selected AWS shape
- manage generated infrastructure files or direct API calls
- preserve deployment state
- run target-specific doctor checks
- support plan/apply and rollback where feasible

The target interface should be designed after the Docker target is concrete,
not over-generalized before there is evidence.

## App Blueprints

Generic targets need app-specific knowledge without hard-coding Arbiter into
the deploy core.

An app blueprint can define:

- app identity and versions
- package/image roots
- default ports and volumes
- config templates
- env schema
- health checks
- doctor checks
- migration metadata and capabilities
- post-start validation
- package/artifact providers

App blueprints are declarative manifests plus config/templates. They are data, not
executable extensions, and should not be arbitrary code loaded into or run by
the deploy binary.

App blueprints can be:

- local files/directories
- fetched from local or remote version-control repositories

The deploy binary should not embed app blueprints. It should stay generic and load
app-specific behavior from explicit blueprint references. The Arbiter blueprint can
initially live in the same repository, but it should still be an external blueprint
artifact from the deploy core's point of view.

Blueprint references should support pinning. Examples:

```text
blueprint: file:./server/src/arbiter_server/reploy
blueprint: git:https://github.com/omry/arbiter.git//server/src/arbiter_server/reploy?ref=v1.2.3
blueprint: git:ssh://git@github.com/omry/arbiter.git//server/src/arbiter_server/reploy?ref=v1.2.3
blueprint: sl:https://github.com/omry/arbiter.git//server/src/arbiter_server/reploy?rev=v1.2.3
blueprint: arbiter-server
blueprint: arbiter-server==0.1.0
blueprint: pypi:arbiter-server//arbiter_server/reploy
blueprint: pypi:arbiter-server==0.1.0//arbiter_server/reploy
```

Relative `file:` blueprint paths resolve from the shell working directory. Absolute
`file:` blueprint paths are used as-is.

Package-backed blueprint references should initially use an explicit path to
package-namespaced blueprint data. The deploy tool should fetch and inspect package
artifacts as archives; it should not install or import a Python package just to
discover a blueprint. Installing package data with pip has side effects and should
not be required for blueprint discovery. The deploy tool should not depend on `pip`
or a Python environment for package-backed blueprint lookup; the first
implementation should use Go-native HTTP/archive handling for wheel artifacts.

Blueprint shorthands such as `arbiter-server` should come from a
downloadable Reploy blueprint index, not from hardcoded deploy-binary knowledge.
The Arbiter repository can publish the first index file and Reploy can download
and cache it from the repository until a more formal distribution path exists.
The index should be fetched lazily on first shorthand use, with an explicit
command such as `reploy blueprint-index refresh` available to validate and pre-cache
it for demos, offline preparation, or troubleshooting.
`arbiter-server==VERSION` pins the package version;
plain shorthands request the latest available package. Explicit package-backed
refs may also omit an exact version, for example
`pypi:arbiter-server//arbiter_server/reploy`. These are operator
conveniences for bootstrap. Once resolved, the deployment state must record the
exact package version, artifact filename, and artifact hash so the deployment is
reproducible after initialization.

Each blueprint index entry should declare separate refs for unpinned and pinned
requests so resolver syntax stays inside the target ref:

```json
"arbiter-server": {
  "ref": "pypi:arbiter-server//arbiter_server/reploy",
  "versioned_ref": "pypi:arbiter-server=={version}//arbiter_server/reploy"
}
```

In `versioned_ref`, `{version}` is only the raw version string, such as
`1.2.3`.

Package artifacts should resolve into a Reploy-controlled local cache first,
not directly into a deployment bundle. If a later deployment step needs the same
wheel as part of the installation bundle, it can copy or reference the cached
artifact only after confirming the resolved version and hash match. For a
`latest` request, the cache key must use the resolved exact version and artifact
hash, not the word `latest`.

Deployment state should distinguish the requested blueprint ref from the resolved
artifact. A minimal shape could be:

```json
{
  "blueprint": {
    "requested": "arbiter-server",
    "resolved": {
      "scheme": "pypi",
      "package": "arbiter-server",
      "version": "0.1.0",
      "filename": "arbiter_server-0.1.0-py3-none-any.whl",
      "sha256": "...",
      "subdir": "arbiter_server/reploy",
      "cache_path": "..."
    }
  }
}
```

Initial package lookup convention:

- single-blueprint packages put the blueprint at a shallow package-namespaced path, such
  as `//arbiter_server/reploy`
- packages that intentionally ship multiple blueprints put named blueprint files
  under the same package path

For Python wheels, the blueprint manifest should not live at the archive root. Wheel
root files are installed into the top level of `site-packages`, which would
pollute the Python import path if the package is ever installed normally. Keep
blueprint data package-namespaced, but do not force the common single-blueprint case
through extra path depth. Prefer:

```text
arbiter_server/reploy/
  arbiter.blueprint.yaml
```

Use named blueprint files only when one package really carries multiple
deployment blueprints:

```text
mail_suite/reploy/
  inbound.blueprint.yaml
  outbound.blueprint.yaml
```

Use the app id as the filename, such as `arbiter.blueprint.yaml`. The blueprint
contains the provider identifier and bundle options directly. This keeps
the common case shallow and still leaves room for multiple app blueprints in one
package.

Later, package metadata could help discover a blueprint automatically without the
user specifying the internal package path. That should be a follow-up design
after explicit package paths work.

Fetched blueprints should be cached, checksummed when possible, and recorded in
deployment state so later operations know which app-specific logic was used.
Git and Sapling should be treated as blueprint source adapters, not as assumptions
inside the app blueprint model.

Package installation inputs should be provider-owned, not hardwired to a generic
`requirements.txt` concept. For the simple one-wheel case, the package-backed
blueprint artifact can provide enough wheel metadata to install the app without a
separate requirements file. For apps with selectable runtime capabilities, such
as Arbiter plugins or a mail stack with IMAP, SMTP, Sieve, and spam filtering,
the blueprint may declare bundle options that a provider turns into install inputs.
User-added packages are an extension surface, not the base manifest format.

App blueprints should declare their schema and compatibility requirements so the
deploy tool can reject blueprints it does not understand before mutating a
deployment.

Example:

```yaml
blueprint:
  schema: 1
  version: 0.1.0
  requires_reploy: ">=0.1.0"
```

For Arbiter specifically, the app blueprint should describe bundle options as
operator-facing shortcuts for useful package roots. Options may point at meta
packages such as `arbiter-suite` or focused bundles such as a future
`arbiter-mail`, and they may also point at individual plugin packages. This is
app-specific metadata, not a generic deploy-core concept. The Arbiter blueprint can
declare app bundle options that the Arbiter Python provider interprets.
Each option owns its package requirement, so plugin versions can differ from
one another and from the server package. Operators should select catalog
entries explicitly, for example `reploy bundle add --name imap,smtp`,
while positional `bundle add` arguments remain package, wheel, or source roots.
`--force` can make an unknown `--name` value fall back to package-root handling.

Example:

```yaml
app:
  id: arbiter
  provider:
    type: python
    identifier: arbiter-server

bundle:
  options:
    arbiter-suite:
      identifier: arbiter-suite
      group: meta
      description: Install the full Arbiter suite.
    imap:
      identifier: arbiter-imap
      group: plugins
      description: Receive email through IMAP.
    smtp:
      identifier: arbiter-smtp
      group: plugins
      description: Send email through SMTP.
```

External options or package roots might be declared as Python package pins,
local wheels, local source directories, or another artifact shape supported by
the selected
provider. The deploy core should see only provider-neutral artifact roots and
metadata after the Arbiter Python provider interprets them.

Package/artifact providers should hide ecosystem-specific behavior. Examples:

- Python provider: PyPI requirements, local source builds, wheels, wheelhouse
- Container image provider: image references, image digests, registry auth
- Filesystem provider: local archives or static release assets
- OS package provider: dpkg/rpm/apk package files and repositories

The core lifecycle should see the provider output as prepared artifacts and
metadata, not as Python-specific implementation details.

A bundle is the prepared runtime input set for a deployment. It is generic: it
could contain Python wheels, zip archives, dpkg files, container image
references, copied release assets, or a mix of provider outputs. The deploy
core can reason about bundle identity, provenance, cache location, locks, and
freshness without knowing the package ecosystem details inside each artifact.

## App Blueprint Trust And Capabilities

An app blueprint is a manifest/config package for a deployment lifecycle tool. It can
describe app-specific checks, migrations, and lifecycle metadata. These should
be declarative extension points or calls into capabilities exposed by the app or
target environment, not code dynamically loaded into the deploy binary.

For Arbiter, examples include:

- run `arbiter-server config check`
- run `arbiter-server env bootstrap`
- ask the running Arbiter service for plugin/operation health
- run an Arbiter-provided config migration command, when one exists

The deploy tool remains the executor and reporter. The app blueprint declares
metadata such as what capability can be invoked, in which phase, with which
inputs and expected outputs. Some metadata may be purely descriptive and not
executable.

Capability declarations should also state an execution context. The context
answers where the deploy tool invokes the app capability:

- `host`: on the operator host
- `target-once`: in a one-shot target runtime, such as a temporary Docker
  container. This is useful for checks that should run in the same runtime
  shape as the deployed app, but do not require the long-running service to
  already be up.
- `staged-live`: inside or against the running staged deployment
- `installed-live`: inside or against the running installed deployment

For Arbiter, the blueprint can declare a Docker command route such as
`trigger: [config, check]`, the container argv for `arbiter-server config
check`, and explicitly forwardable flags such as `--live`. Reploy should
validate the declared route and flag surface, but it should not hard-code what
`--live` means to Arbiter.

Installing an app already requires trusting that app. However, app blueprints still
need a supply-chain model because they can affect generated service files,
config, install commands, and privileged promotion steps. The deploy tool should
record blueprint source, revision, and checksum where possible. Remote blueprint
references should be pinned for reproducible deployments. Unpinned or changed
blueprint references should be visible in `plan` and `doctor` output.

Executable blueprint steps are out of scope. If a future design ever needs them,
that should be treated as a new trust-boundary decision rather than an
extension of the initial app blueprint model.

OCI should stay on the design radar, but it should not drive the first shape
until we understand it well enough. The Open Container Initiative defines
runtime, image, and distribution specifications. For this tool, the most likely
relevant areas are:

- container images as installation artifacts
- registries as an artifact distribution mechanism
- non-image OCI artifacts as a possible app blueprint or release artifact transport
- image digests and registry metadata for reproducible deployments
- air-gapped copy/sync flows

Open question: should app blueprints or prepared deployment bundles eventually be
publishable as OCI artifacts, or should OCI stay limited to container image
providers? Treat this as a research item before committing to an interface.

## Deployment Phases

The tool needs an explicit abstraction for staged versus installed deployments.
This is not a standard Docker concept, but it is central to the workflow.

Use "phase" or "profile" to describe the deployment identity being operated:

- `staged`: operator-owned, local, testable before promotion
- `installed`: host/service-owned, durable production service

The generic deploy core should model this explicitly instead of hard-coding
staging ports, names, paths, or ownership into target-specific scripts.

A phase can define:

- deployment identity and naming
- target paths
- service names
- user/group ownership
- file modes
- ports and URLs
- generated config overlays
- state location
- data location
- allowed operations
- promotion rules

Example app/deployment manifest fragment:

```yaml
installed:
  port: 8075

staged:
  port: 18075
```

For Arbiter, this could live in an app blueprint or deployment manifest such as
`arbiter.blueprint.yaml`. The deploy tool should interpret these values through
the phase/profile model rather than scattering staging and installed ports
through target-specific code.

For the current Docker target, staged and installed differ in:

- Compose project/container names
- listen ports and URLs
- deployment scope passed to Arbiter
- operator-owned versus root/service-owned files
- local staging directory versus install target such as `/opt/arbiter`
- direct helper operation versus current systemd-managed operation

Promotion should be a first-class transition:

```text
staged deployment -> preinstall checks -> promote/install -> installed deployment
```

The transition should support:

- dry-run plan
- generated-file rewrite from staged identity to installed identity
- config/env preservation policy
- backups of installed state
- validation before and after promotion
- rollback where feasible

Implementation fix for the current Reploy Docker target:

1. Move staged/installed defaults out of scattered `docker.env`, Compose, and
   systemd code into a phase profile model loaded from the app blueprint plus local
   deployment overrides.
2. Generate installation artifacts from `(target, phase, blueprint, local overrides)`,
   so `init`, `update`, `install`, and future `plan` all calculate the same
   desired files/resources before applying them.
3. Record the active phase and source profile in `state.json`, including the
   generated identity names, ports, service names, install paths, and ownership
   policy used for that deployment.
4. Treat `install` as a transition from the staged profile to the installed
   profile, not as a mostly separate copy operation. The transition should
   reuse the same result model as `init` and `update`, with explicit rows for
   generated files, preserved local files, service resources, and validation
   steps.
5. Make runtime commands resolve the phase before dispatch. `./reploy up` in a
   staging directory should operate on the staged profile; an installed helper
   should operate on the installed profile; explicit `--phase` can be added
   later if multi-phase control from one checkout is needed.

Future targets can map phases differently. AWS may treat phases as separate
environments or accounts. Bare metal may mirror the Docker staged/installed
flow closely. Some targets may not support a local staged phase, but they
should still express what phases they support.

## Doctor Abstraction

Doctor should be a first-class diagnostic framework, not one monolithic command.

The generic deploy core should provide:

- a check registry
- check severity levels
- structured results
- text and machine-readable output
- target/app-blueprint extension points
- quiet/verbose modes
- preflight profiles such as `doctor`, `doctor --preinstall`, and later
  `doctor --upgrade`

Generic checks can cover:

- deployment directory shape
- manifest and state readability
- generated-file ownership/drift
- host OS and architecture support
- required external commands
- path safety and symlink policy
- port availability when target-independent
- cache/artifact presence
- backup/rollback readiness

Target checks can cover:

- Docker/Compose availability for the Docker target
- systemd availability for current systemd-backed installs
- AWS credentials, region, and API reachability for an AWS target
- bare-metal runtime users, paths, ports, and service manager state

App blueprint checks can cover:

- Arbiter config validity
- Arbiter account/plugin readiness
- Python wheelhouse consistency for Arbiter's Python provider
- Arbiter runtime health and operation discovery
- app-specific migration readiness

The doctor command should compose these layers and report where each finding
came from: core, target, artifact provider, or app blueprint.

## Distribution

Primary distribution for the Arbiter-hosted phase should be direct binary
download from Arbiter release artifacts, initially GitHub releases:

```text
reploy-linux-amd64
reploy-linux-arm64
reploy-darwin-amd64
reploy-darwin-arm64
reploy-windows-amd64.exe
checksums.txt
```

A standalone generic tool would likely use normal system package distribution
too, such as Homebrew, PyPI, apt, yum/dnf, or similar package managers. Direct
release binaries are a cheap and useful starting point, not the whole long-term
distribution model.

## AWD Master Plan

Use runtime-backed AWD for implementation. This work has many dependent stages,
decision gates, and verification points.

```text
design draft
-> scope first milestone
-> choose subproject shape ?>
-> scaffold Go subproject
-> define deployment manifest and state
-> define target/app-blueprint interfaces
-> migrate Docker init/update generation
-> shrink generated shell helper
-> migrate Docker bundle commands
-> migrate Docker run/status/log/check commands
-> migrate doctor/preinstall checks
-> migrate install/service-manager promotion
-> add Arbiter app blueprint
-> update docs and media scripts
-> test matrix !>
-> review stack !>
-> cleanup !>
-> merge-ready summary
```

## Phase Plan

### Phase 0: Design and Boundaries

Goal: agree on the target shape before code moves.

Tasks:

- capture design draft
- list current `arbiter-docker` capabilities
- decide initial binary and module names
- decide repo placement
- decide whether the first release is Arbiter-branded or generic-branded
- decide the first-milestone Arbiter blueprint reference or wrapper command

Gate:

```text
design accepted ?>
```

### Phase 1: Inventory Current Behavior

Goal: avoid losing useful deployment behavior during migration.

Tasks:

- inventory generated files
- inventory helper commands
- inventory doctor checks
- inventory install/service-manager behavior
- inventory tests covering Docker deployment
- classify behavior as generic core, Docker target, or Arbiter app blueprint

Gate:

```text
behavior inventory complete !>
```

### Phase 2: Subproject Scaffold

Goal: create the Go project without changing deployment behavior yet.

Tasks:

- add subproject directory
- add build/test/lint wiring
- add release artifact shape
- add minimal CLI with version/help
- add smoke tests

Gate:

```text
go scaffold checks pass !>
```

### Phase 3: Model and Interfaces

Goal: define enough structure to migrate behavior without baking in Arbiter.

Tasks:

- define deployment manifest
- define deployment state
- define generated file manifest
- define target backend interface
- define app blueprint interface
- parse app blueprint metadata with a real structured parser and keep provider-owned
  install identifiers inside the blueprint instead of hard-coding Arbiter
  packaging paths
- decide how target-owned templates, app-owned target inputs, and
  provider-owned metadata are separated so generic Docker code does not
  accidentally depend on Arbiter blueprint or plugin semantics
- define explicit package-backed blueprint lookup without requiring package
  installation
- define plan/apply result format
- define error/reporting conventions

Gate:

```text
model reviewed ?>
```

### Phase 4: Docker Target MVP

Goal: replace the current initialization path while preserving existing output.

Tasks:

- implement Docker target init/update
- generate the current deployment directory layout
- write a minimal deployment-local wrapper
- preserve generated manifest behavior
- do not promise migration compatibility for unreleased `arbiter-docker`
  deployment directories; first release behavior should start from `reploy init`
- keep current tests passing or add equivalent Go-level tests

Gate:

```text
docker init parity !>
```

### Phase 5: Docker Runtime Commands

Goal: move staging lifecycle into the Go tool.

Tasks:

- implement up/down/restart
- implement logs/ps/status
- implement config check and live config check
- keep the config-check temporary Compose project cleanup path, extend the same
  pattern to any additional one-shot Docker checks, and add interruption-path
  validation so repeated checks cannot exhaust Docker address pools
- implement test/smoke command
- preserve clear user-facing output

Gate:

```text
docker runtime parity !>
```

### Phase 6: Bundle And Runtime Preparation

Goal: move bundle preparation out of shell without baking Python concepts into
the generic core.

Tasks:

- implement generic artifact-provider plumbing
- implement list/add/remove of app/provider artifact roots
- implement build/check/upgrade through providers
- define provider-neutral bundle metadata and cache layout
- implement bundle options handling as Arbiter app/provider behavior,
  not generic deploy-core behavior; if the catalog remains a file, treat it as
  a declared blueprint artifact rather than a Docker target hardcoded path
- preserve Arbiter's Python wheel, source, and wheelhouse workflows through the
  Arbiter Python provider
- preserve offline prepared artifact behavior

Gate:

```text
bundle preparation parity !>
```

### Phase 7: Doctor and Install

Goal: move safety-critical host checks and promotion logic into testable code.

Tasks:

- implement doctor
- implement doctor --preinstall
- implement agent permission checks
- implement install/promote to the target service manager
- implement config/env preservation
- implement backups and install summaries

Gate:

```text
install parity !>
```

### Phase 8: Arbiter Integration

Goal: make Arbiter use the new tool without regressing existing workflows.

Tasks:

- add Arbiter app blueprint
- update media scripts
- update docs and media scripts to use `reploy`
- do not add news fragments for this unreleased migration unless a later
  release-note decision changes

Gate:

```text
arbiter workflow accepted ?>
```

### Phase 9: Verification and Stack Review

Goal: ship a coherent stack.

Tasks:

- run unit tests
- run Docker deployment integration tests
- validate repeated one-shot Docker config/install checks do not leak temporary
  networks or containers, including failure paths
- validate app blueprint metadata drives the generated requirements and bundle option
  catalog paths, with tests that fail if Docker target code hardcodes
  Arbiter-specific blueprint file names
- validate Reploy-owned deployment directories update without silently
  overwriting operator-owned files
- run docs build
- run media checks where relevant
- review generated artifact diffs
- split/organize commits
- prepare PR notes

Gate:

```text
release-quality checks pass !>
```

### Phase 10: Cleanup

Goal: remove migration leftovers and make the repository easy to understand
after the new deploy tool is accepted.

Tasks:

- remove obsolete generated-shell logic that no longer owns behavior
- remove old `arbiter-server deploy` deployment logic once replacement behavior
  has parity
- delete superseded docs and examples
- remove stale tests that only cover retired paths
- prune temporary migration helpers
- verify no duplicate command surfaces remain without a documented reason
- verify generated artifacts are reproducible from the new tool
- verify the design draft has either been promoted to durable docs or archived

Gate:

```text
cleanup complete !>
```

## Open Decisions

1. Repository placement:
   - `deploy-tool/`
   - `tools/deploy/`
   - `deploy/go/`
   - another location

2. Standalone rename criteria:
   - start with `reploy`
   - choose a generic name only if the tool is promoted to a standalone project
   - identify what second use case or adoption signal justifies the rename

3. Compatibility:
   - Arbiter is unreleased, so there is no need to preserve
     `arbiter-server deploy docker`
   - remove deployment logic from the server package in the cleanup phase once
     the replacement path has parity

4. State model:
   - local JSON state in deployment directory
   - target-specific state only
   - support external state later

5. Blueprint packaging:
   - local file/directory references
   - local or remote Git references
   - local or remote Sapling references
   - package references, such as a PyPI package containing the blueprint, using an
     explicit package-internal path first
   - local or remote archive references later, if needed

6. Distribution:
   - direct release binaries first for the Arbiter-hosted phase, probably via
     GitHub releases
   - package-manager distribution later for a standalone generic tool
   - decide whether a PyPI wrapper is useful for Arbiter or only for a later
     standalone release

## First Milestone Proposal

Milestone 1 should be intentionally narrow:

- add Go subproject
- provide a local checkout path:
  `reploy init --blueprint file:server/src/arbiter_server/reploy`
  run from the repository root, because relative `file:` blueprint paths resolve
  from the shell working directory
- provide a standalone init path using a pinned remote blueprint reference, for
  example:
  `reploy init --blueprint git:https://github.com/omry/arbiter.git//server/src/arbiter_server/reploy?ref=v0.1.0`
- provide a standalone init path from a package that contains the blueprint, for
  example through the blueprint index:
  `reploy init --blueprint arbiter-server`
- generate the same Docker deployment directory as today
- vendor or generate a tiny deployment-local wrapper
- keep the app blueprint external to the binary; a repo-local wrapper may supply the
  blueprint path for convenience
- make milestone code either honor blueprint-declared target file paths or clearly
  label hardcoded Arbiter blueprint paths as temporary migration scaffolding with a
  follow-up removal task
- keep existing Arbiter deployment docs mostly unchanged except for the first
  command
- leave the current helper's bundle, runtime, and install behavior delegated
  until later phases

This gives us a non-circular entrypoint early without moving all logic at once.
