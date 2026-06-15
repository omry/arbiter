---
title: Testing
---

Use the repo-local virtualenv when running checks:

```bash
.venv/bin/python -m pytest server/tests plugins/smtp/tests plugins/imap/tests
.venv/bin/python -m nox -s lint
```

Arbiter formally supports Python 3.10 through 3.14. The compatibility
matrix is derived from `project.requires-python`, and Nox is allowed to
download missing interpreters when needed:

```bash
.venv/bin/python -m nox -s compat
```

Use `--download-python never` when you want to require only interpreters that
are already installed locally.

## Useful focused checks

```bash
.venv/bin/python -m pytest server/tests/integration/test_cli_entrypoint.py
.venv/bin/python -m pytest server/tests/unit/test_config.py
```

## Docker Deployment Check

The Docker deployment integration test is skipped by default during normal
pytest runs because it builds and starts a real container. Treat it as the
pre-deploy gate, and run it when changing deployment scaffolding:

```bash
.venv/bin/python -m nox -s deploy-test
```

The test starts a lightweight local IMAP server, generates a Docker deployment
with `arbiter-server deploy docker`, runs generated helper preflight commands
without a privileged install, starts the generated `arbiter-docker` helper,
checks the server URL, and verifies an IMAP operation through the Arbiter
client.

Run the full suite before release or before committing broad interface changes.

See [Release Process](./release-process.md) for package-scoped release notes,
PyPI publish planning, trusted publisher bootstrap, and publish mechanics.
