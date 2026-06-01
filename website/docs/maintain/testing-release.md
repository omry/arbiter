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
`agent-arbiter-core`. For example, `agent-arbiter-smtp==0.8.1` can publish
against `agent-arbiter-core==0.8.0`, but `agent-arbiter-smtp==0.9.0` requires a
core package on the `0.9` line.

GitHub publishing uses the shared `pypi` environment. PyPI must still have a
matching trusted publisher for each project that will be uploaded.

Additional meta packages, such as a future `agent-arbiter-mail`, can follow the
same version-selection flow when they are added.
