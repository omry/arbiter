---
title: Testing And Release Readiness
---

Use the repo-local virtualenv when running checks:

```bash
.venv/bin/python -m pytest core/tests smtp/tests imap/tests
.venv/bin/python -m nox -s lint
```

Agent Arbiter formally supports Python 3.10 through 3.14. The compatibility
matrix is derived from `project.requires-python`, and Nox is allowed to
download missing interpreters when needed:

```bash
.venv/bin/python -m nox -s compat
```

Use `--download-python never` when you want to require only interpreters that
are already installed locally.

## Current release-readiness focus

- package metadata and deployment requirements agree
- CLI help and config bootstrap flows work from installed console scripts
- docs describe the current command and config shape
- deployment examples use current console entry points
- security limitations are explicit

## Useful focused checks

```bash
.venv/bin/python -m pytest core/tests/integration/test_cli_entrypoint.py
.venv/bin/python -m pytest core/tests/unit/test_config.py
```

## User-initiated Docker deployment check

The Docker deployment integration test is skipped by default because it builds
and starts a real container. Run it explicitly when changing deployment
scaffolding:

```bash
.venv/bin/python -m nox -s deploy-test
```

The test starts a lightweight local IMAP server, generates a Docker deployment
with `arbiter-server deploy docker`, starts the generated
`arbiter-docker` helper, and verifies an IMAP operation through the MCP
endpoint.

Run the full suite before release or before committing broad interface changes.

## PyPI publish selection

The publish workflow builds all bundled distributions, then runs:

```bash
tools/plan_pypi_publish --prepare-output-dir
```

The planner compares local package versions with PyPI and copies only packages
whose local version is newer, or whose PyPI project does not exist yet, into
`dist-publish/` for upload. It covers the core package, bundled plugin packages,
and the default `agent-arbiter` meta package. It rejects local package versions
that are older than PyPI.

Plugin packages must stay on the same `MAJOR.MINOR` line as
`agent-arbiter-core`. A plugin patch release can publish against an existing
core package on the same line, but a plugin on a new minor line requires a core
package on that new line.

GitHub publishing uses the shared `pypi` environment. PyPI must still have a
matching trusted publisher for each project that will be uploaded.

For the initial PyPI bootstrap, PyPI currently allows only one pending trusted
publisher per GitHub repo/workflow/environment. Use manual workflow dispatch
with one selected package at a time, creating the matching pending publisher
before each run:

1. `core` (`agent-arbiter-core`)
2. `imap` (`agent-arbiter-imap`)
3. `smtp` (`agent-arbiter-smtp`)
4. `meta:all` (`agent-arbiter`)

The same subset selection is available locally:

```bash
tools/plan_pypi_publish --packages core --prepare-output-dir
tools/plan_pypi_publish --packages core,imap --prepare-output-dir
```

After the projects exist and have ordinary trusted publishers, release events
can use the default all-package selection.

Additional meta packages, such as a future `agent-arbiter-mail`, can follow the
same version-selection flow when they are added.
