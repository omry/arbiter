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

The CLI integration check starts a local Arbiter server. It also builds the
current-platform `arbiter-client` wheel, installs it into a temporary Python
environment, verifies `arbiter --version`, and uses that installed command
against the server. Release and publish workflows run this server integration
suite on each supported OS/architecture runner.

Run the full suite before release or before committing broad interface changes.

See [Release Process](./release-process.md) for package-scoped release notes,
PyPI publish planning, trusted publisher bootstrap, and publish mechanics.
