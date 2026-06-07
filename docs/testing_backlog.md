# Testing Backlog

## Purpose

Keep the remaining test work small, explicit, and prioritized.

This file tracks the highest-value gaps between:

- the current Arbiter implementation
- the documented SMTP and IMAP contracts
- the tests already in place

## Status legend

- `done`: implemented and covered by tests
- `todo`: agreed gap, not implemented yet
- `blocked`: needs an implementation seam or design decision first

## P0

- TLS handshake failure is surfaced during SMTP submission
  - Why: fail-closed transport security
  - Level: integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/integration/test_smtp_integration.py`

- SMTP authentication failure is surfaced cleanly
  - Why: server submission behavior
  - Level: unit + integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/unit/test_smtp.py`, `plugins/smtp/tests/integration/test_smtp_integration.py`

- Server unavailable / connection failure is surfaced
  - Why: common operational failure mode
  - Level: unit + integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/unit/test_smtp.py`, `plugins/smtp/tests/integration/test_smtp_integration.py`

- SMTP rejection after `RCPT TO` or `DATA`
  - Why: needed to distinguish submission rejection from connection failure
  - Level: integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/integration/test_smtp_integration.py`

- Submission status unknown after partial SMTP progress
  - Why: needed for retry/idempotency semantics
  - Level: integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/integration/test_smtp_integration.py`

## P1

- Improve CI performance with dependency caching
  - Why: platform CI is now broad enough that repeated setup dominates runtime, especially Windows arm64
  - Level: CI
  - Status: `todo`
  - Acceptance: Windows arm64 caches vcpkg/OpenSSL setup or uses vcpkg binary caching; nox/pip editable install overhead is reduced where practical; cache keys include dependency inputs so stale toolchains are not reused silently; and CI job logs make cache hits/misses obvious.

- `verify_peer=true` succeeds against a trusted local CA
  - Why: complete the TLS success-path contract
  - Level: integration
  - Status: `blocked`
  - Note: current client has no custom CA injection seam

- Invalid SMTP config combinations are rejected
  - Why: avoid ambiguous runtime behavior
  - Level: unit
  - Status: `done`
  - Coverage: `server/tests/unit/test_config.py`, `plugins/smtp/tests/unit/test_smtp.py`
  - Examples: unknown TLS mode, auth enabled without both username and password, credentials set while auth is disabled

- HTML-only message serialization
  - Why: MIME behavior is user-visible
  - Level: unit + integration
  - Status: `done`
  - Coverage: `server/tests/unit/test_app.py`, `plugins/smtp/tests/integration/test_smtp_integration.py`

- Non-ASCII subject and display-name handling
  - Why: common real-world interoperability case
  - Level: unit + integration
  - Status: `done`
  - Coverage: `server/tests/unit/test_app.py`, `plugins/smtp/tests/integration/test_smtp_integration.py`

## P2

- Recipient refusal policy at SMTP layer
  - Why: improves transport diagnostics
  - Level: unit + integration
  - Status: `done`
  - Coverage: `plugins/smtp/tests/unit/test_smtp.py`, `plugins/smtp/tests/integration/test_smtp_integration.py`

- Logging coverage for connection attempt, submission result, and failure paths
  - Why: operational debugging and auditability
  - Level: unit
  - Status: `blocked`
  - Note: logging contract is documented but not implemented yet

- Normalized error-code mapping tests
  - Why: locks down external API semantics
  - Level: unit + integration
  - Status: `blocked`
  - Note: docs define the error model, but the implementation still surfaces raw exceptions

- Rate limiting enforcement
  - Why: policy correctness
  - Level: unit + integration
  - Status: `done`
  - Coverage: `server/tests/unit/test_app.py`
  - Note: unit coverage is in place for the current per-process sliding-window implementation; add integration coverage if a transport-level seam becomes useful
