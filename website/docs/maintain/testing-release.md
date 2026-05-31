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
