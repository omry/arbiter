# Error Model

## Purpose

Define the stable error contract shared by the Agent Arbiter server.

## Status

This is the target error contract. The current implementation still surfaces raw Python/MCP errors for many failures rather than normalizing every failure into this response shape.

## Error response shape

```json
{
  "ok": false,
  "error_code": "SUBMISSION_REJECTED",
  "message": "The SMTP submission failed",
  "retryable": false,
  "idempotency_replayed": false
}
```

Each failure response should include:

- `ok: false`
- `error_code`
- human-readable `message`
- `retryable`

## Recommended error codes

- `INVALID_INPUT`
- `POLICY_DENIED`
- `CONFIGURATION_ERROR`
- `AUTHENTICATION_FAILED`
- `TLS_NEGOTIATION_FAILED`
- `CONNECTION_FAILED`
- `RATE_LIMITED`
- `IDEMPOTENCY_CONFLICT`
- `SUBMISSION_REJECTED`
- `SUBMISSION_STATUS_UNKNOWN`
- `INTERNAL_ERROR`

## Error-code definitions

### `INVALID_INPUT`

Returned when the tool payload fails schema validation or other basic input checks.

### `POLICY_DENIED`

Returned when configured policy blocks the attempted action.

### `CONFIGURATION_ERROR`

Returned when the selected account or server configuration is incomplete, invalid, or internally inconsistent.

### `AUTHENTICATION_FAILED`

Returned when SMTP or IMAP authentication fails against the configured account.

### `TLS_NEGOTIATION_FAILED`

Returned when the configured TLS mode cannot be established successfully.

### `CONNECTION_FAILED`

Returned when the server cannot connect to the configured SMTP or IMAP endpoint.

### `RATE_LIMITED`

Returned when the configured send rate limit would be exceeded by the attempted SMTP submission.

### `IDEMPOTENCY_CONFLICT`

Returned when an `idempotency_key` is reused with a different effective payload before its record expires.

### `SUBMISSION_REJECTED`

Returned when SMTP submission is definitively rejected.

### `SUBMISSION_STATUS_UNKNOWN`

Returned when the server cannot determine whether SMTP acceptance occurred.

### `INTERNAL_ERROR`

Returned for unexpected server-side failures not covered by a more specific code.

## Retry semantics

- Retryability should be surfaced explicitly through the `retryable` field.
- Idempotency should be used to prevent accidental duplicate sends caused by retries.
- If the same `idempotency_key` is reused with the same effective payload before expiration, the server should replay the earlier normalized result and set `idempotency_replayed: true`.
- Replay applies to prior success and prior failure results alike.
- A caller that wants to force a fresh submission attempt after a stored result must use a new `idempotency_key`.
- Once the configured idempotency record expires, the same `idempotency_key` is treated as new work.
