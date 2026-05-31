---
title: Testing And Release Readiness
---

Use the repo-local virtualenv when running checks:

```bash
.venv/bin/python -m pytest core/tests smtp/tests imap/tests
.venv/bin/python -m nox -s lint
```

## Current release-readiness focus

- package metadata and install targets agree
- CLI help and config bootstrap flows work from installed console scripts
- docs describe the current command and config shape
- deployment examples use current console entry points
- security limitations are explicit

## Useful focused checks

```bash
.venv/bin/python -m pytest core/tests/integration/test_cli_entrypoint.py
.venv/bin/python -m pytest core/tests/unit/test_config.py
```

Run the full suite before release or before committing broad interface changes.
