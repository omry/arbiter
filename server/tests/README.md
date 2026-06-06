# Test Layout

Use this directory for tests that mirror the split design documents.

Suggested subdirectories:

- `unit/`
- `integration/`
- `spec/`

Recommended coverage:

- config validation
- policy enforcement
- normalized errors
- `list_accounts` behavior
- `send_email` behavior
- logging and audit side effects

Run tests from the repo-local virtualenv when working directly in this checkout:

- `python3 -m venv .venv`
- `.venv/bin/python -m pip install -r requirements-dev.txt`
- `.venv/bin/python -m pytest`
