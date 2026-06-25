# Reploy Current Deploy Inventory

Status: Phase 1 inventory draft.

This document inventories the current Arbiter Docker deployment behavior before
moving it into `reploy`. It is a characterization aid, not a durable user
manual.

## Current Implementation Map

Primary implementation:

- `server/src/arbiter_server/main.py`
  - parses `arbiter-server deploy docker`
  - infers Python package roots and plugin pins
  - writes generated deployment files
  - owns generated-file manifest hashing and update behavior
- `server/src/arbiter_server/deploy/docker/compose.yaml`
  - Docker Compose template for staged and installed deployments
  - installs runtime Python requirements at container start
  - runs static config check before serving
- `server/src/arbiter_server/deploy/docker/arbiter-docker`
  - generated Bash helper copied into each deployment directory
  - owns bundle/wheelhouse, Docker runtime, doctor, and install behavior

Public docs and media that currently depend on the shape:

- `website/docs/operate/deployment/1-docker-prepare.md`
- `website/docs/operate/deployment/2-linux-install.md`
- `website/docs/operate/deployment/3-bundle-deep-dive.md`
- `website/docs/operate/deployment/4-docker-helper-reference.md`
- `media/recording-scripts/install-and-bootstrap.md`
- `media/conf/config.yaml`
- `media/tools/studio_config.py`

Primary tests:

- `server/tests/unit/test_main.py`
- `server/tests/integration/test_cli_entrypoint.py`
- `plugins/imap/tests/integration/test_deploy_docker_integration.py`
- `media/tests/test_studio_config.py`

## Generated Deployment Directory

Default generated directory: `./reploy-staging`.

Generated or managed files:

- `compose.yaml`
- `docker.env`
- `requirements.txt`
- `reploy`
- `bundle-plugins.tsv`
- `state.json`
- `.reploy-deploy.json`

Generated directories:

- `conf/`
- `bundle/`
- `data/`

Important defaults in `docker.env`:

- image: `python:3.11-slim`
- container: `arbiter-staging`
- container user: current uid/gid when available
- config dir: `./conf`
- config name: `arbiter-server`
- requirements file: `./requirements.txt`
- bundle dir: `./bundle`
- data dir: `./data`
- host bind: `127.0.0.1`
- staged host port: `18075`
- container port: `8075`
- network: `arbiter-staging`
- bridge: `arbiter-stg0`
- subnet: `172.31.251.0/24`

## Server-Side Deploy Command

Current command:

```text
arbiter-server deploy docker init|update [--force] [docker.dir=PATH] [docker.requirement=REQ...]
```

Current behavior:

- `init` refuses to overwrite existing managed files.
- `update` repairs or refreshes manifest-owned generated files.
- `update --force` can overwrite locally modified generated files.
- explicit requirements must be exact pins or absolute container paths.
- `arbiter-suite` can expand into the server package plus known companion
  plugin packages.
- default requirements are inferred from installed Arbiter packages and service
  plugin entry points.
- editable local installs are converted into local wheels when possible.
- generated-file ownership is tracked in `.arbiter-deploy.json`.

Migration owner:

- generic core: deployment state/manifest, generated-file update policy
- Docker target: generated Compose/env/helper layout
- Arbiter pack/provider: Python package inference, plugin catalog,
  `arbiter-suite` expansion, wheelhouse roots
- cleanup: remove this command from `arbiter-server` after `reploy` parity

## Helper Command Surface

Current helper:

```text
./arbiter-docker [--verbose] COMMAND
```

Commands:

- `prepare`
- `bundle`
- `sync-env`
- `edit-config`
- `edit-requirements`
- `edit-env`
- `edit-docker`
- `config check [--live] [--verbose] [override...]`
- `up [--verbose]`
- `restart [--verbose]`
- `test`
- `down`
- `ps`
- `logs`
- `info`
- `doctor`
- `install`

Global and shared behavior:

- deployment directory resolves from the helper location unless
  `ARBITER_DOCKER_DIR` is set
- `ARBITER_PIP_VERBOSE` controls pip output inside startup/check containers
- `ARBITER_CLIENT_COMMAND` is a fallback for server tests
- `ARBITER_EDITOR` overrides editor selection

Migration owner:

- generic core: command/reporting conventions, state loading, plan/apply shape
- Docker target: runtime commands and Compose invocation
- Arbiter pack/provider: env/config helper commands and server URL probing

## Bundle And Wheelhouse Behavior

Current bundle state:

- selected roots live in `requirements.txt`
- supported plugin catalog lives in `bundle-plugins.tsv`
- prepared artifacts live in `wheels/`

Current bundle commands:

- `bundle list-plugins`
- `bundle add NAME`
- `bundle add-package PACKAGE==VERSION`
- `bundle add-wheel PATH`
- `bundle add-source DIR`
- `bundle remove NAME`
- `bundle list`
- `bundle list all`
- `bundle prepare`
- `bundle check`
- `bundle upgrade [TARGET]`
- `prepare` as shortcut for `bundle prepare`

Important behavior:

- validates exact pins and rejects conflicting pins
- expands/removes `arbiter-suite` meta-package roots carefully
- supports external plugin pins
- copies local wheels into the deployment wheelhouse
- builds local source directories into wheels
- can build repo-local package wheels during upgrade/prepare
- can resolve selected roots from PyPI with `--pypi-only`
- treats source path requirements as incompatible with wheelhouse-only
  prepare/check flows
- validates the wheelhouse by installing into a temporary target without
  downloading packages
- preserves transactional behavior around failed edits/upgrades

Migration owner:

- generic core: artifact root list, prepared artifact metadata, cache shape
- Arbiter pack/provider: Python requirements semantics, PyPI, wheels,
  wheelhouse validation, `arbiter-suite`, companion plugin catalog

## Docker Runtime Behavior

Current runtime commands:

- `up`
- `restart`
- `down`
- `ps`
- `logs`
- `test`
- `info`
- `app config check`
- `app config check --live`

The staged Arbiter pack declares the `app config check` route and the
forwardable `--live` flag. Reploy should validate and transport declared flags,
not encode their app-specific meaning.

Important behavior:

- `up` and `restart` ensure data directories exist and are writable
- Docker access is checked before Compose operations
- staged subnet conflicts can be auto-repaired before `up`
- Docker pool exhaustion/subnet overlap errors are explained
- helper refuses to operate on a container owned by another deployment
- static config check runs in a one-shot Compose container before `up`
- current Reploy config check uses a temporary Compose project and follows
  the run with `docker compose down --remove-orphans` so normal success/failure
  paths do not leave a project network behind
- live config check sets container action and live-check flag
- `test` probes the HTTPS health endpoint, preferring curl then Python then
  Arbiter client fallback
- `logs` includes Docker timestamps
- `down` removes orphans only for managed Compose shapes

Migration owner:

- generic core: lifecycle verbs, diagnostics, user-facing reports
- Docker target: Compose command construction, subnet handling, ownership checks
- Arbiter pack/provider: config-check and health-test capabilities

## Doctor Behavior

Current doctor modes:

- `doctor`
- `doctor --preinstall`
- `doctor --agent-user USER`
- `doctor --agent-uid UID`
- `doctor --quiet`

Important checks:

- required generated files exist
- env file syntax is readable
- requirements file is valid
- Compose file matches the manifest
- generated files have not drifted from manifest ownership
- Docker access and Docker socket permissions
- container name ownership
- Docker network subnet overlap
- plugin/server data directory wiring and permissions
- config/env file permissions
- container user is not root for preinstall
- runtime paths stay below the deployment directory
- wheel path roots exist during preinstall
- selected agent identity cannot read, write, or replace protected state
- `--preinstall` skips Docker daemon checks and focuses on promotion readiness

Migration owner:

- generic core: doctor framework, severities, structured output
- Docker target: Docker/Compose/network checks
- Arbiter pack/provider: config, env, requirements, wheelhouse, plugin data checks

## Install And Promotion Behavior

Current command:

```text
./arbiter-docker install [options]
```

Options:

- `--to DIR`
- `--user USER`
- `--group GROUP`
- `--service NAME`
- `--no-start`
- `--start`
- `--replace-config`
- `--replace-env`
- `--skip-static-config-check`
- `--dry-run`
- `--verbose`

Important behavior:

- requires absolute install target without whitespace
- validates user/group/service names
- prepares local checkout wheels before install when needed
- runs preinstall doctor
- requires root unless dry-run
- performs static config check unless skipped or Docker is unavailable
- copies deployment into install target with symlink rejection
- preserves installed config and env by default
- supports explicit config/env replacement
- backs up preserved config paths under `backup/`
- rewrites staged identity to installed identity
- records install metadata
- applies ownership and file modes
- writes systemd unit
- handles Docker unavailable cases, including unit-only updates when an active
  service exists
- restarts service, tests server URL, and runs live config check when starting
- reports successful installed URL and follow-up commands

Migration owner:

- generic core: promotion plan/apply, preservation policy, backup/rollback
  hooks, install summaries
- Docker target: installed Compose rewrite and Docker-backed checks
- service-manager target: systemd unit creation and service restart
- Arbiter pack/provider: config/live checks, local source wheel handling

## Test Coverage Map

Unit coverage in `server/tests/unit/test_main.py` currently characterizes:

- init/update generated files and manifest behavior
- requirement parsing and meta-package expansion
- default requirement inference from installed packages
- local wheel/source handling
- bundle add/remove/list/prepare/check/upgrade
- Docker access and subnet failure reporting
- config check one-shot container behavior
- runtime `up`, `down`, `logs`, `test`, `info`
- doctor and preinstall checks
- install dry-run and promotion behavior
- config/env preservation and replacement
- install failure safety around symlinks, bad wheelhouses, and path roots

Integration coverage:

- `server/tests/integration/test_cli_entrypoint.py` verifies CLI help exposes
  `arbiter-server deploy docker`.
- `plugins/imap/tests/integration/test_deploy_docker_integration.py` verifies a
  real Docker deployment can serve IMAP operations from local source and from a
  prepared wheelhouse.
- `media/tests/test_studio_config.py` verifies the install-and-bootstrap media
  flow still uses expected deploy/helper commands.

## Migration Notes

Do first:

- scaffold `reploy` without changing current deployment behavior
- make `reploy docker init` capable of writing the same first-milestone
  deployment directory shape
- keep existing helper behavior delegated until each command family has a
  replacement with equivalent characterization tests

Do not preserve long term:

- `arbiter-server deploy docker`
- deployment generation logic inside `arbiter_server.main`
- the monolithic generated Bash implementation

Do not introduce:

- executable app packs
- news fragments for this unreleased migration
- a compatibility/deprecation layer unless a later release constraint appears
