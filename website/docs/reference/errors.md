---
title: Error contract
---

Structured operation errors use `{"error": {"code": "...", "message": "..."}}`.
The generic `code` is shared across plugins. A plugin chooses the closest
generic classification and its own `domain_code`; Arbiter does not guess from
exception messages. Structured plugin errors also identify `plugin` and may include
deliberately selected `details`.

## Generic classifications

HTTP status is a coarse signal. Several error codes can share a status;
callers should inspect the JSON code for precise handling.

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `validation_error` | 400 or 422 | Invalid request syntax or operation arguments. |
| `not_found` | 404 | Requested operation or resource does not exist. |
| `policy_denied` | 403 | Configured policy prohibits the operation. |
| `rate_limited` | 429 | Arbiter's configured operation rate limit was reached. |
| `conflict` | 409 | Request conflicts with current state, including an idempotency reservation. |
| `upstream_auth_failed` | 500 | Configured service credentials were rejected; this is not caller authentication to Arbiter. |
| `upstream_timeout` | 504 | A required upstream service did not respond in time. |
| `upstream_unavailable` | 503 | An upstream service is temporarily unavailable. |
| `upstream_rejected` | 422 | Upstream service rejected the requested operation. |
| `upstream_protocol_error` | 502 | Upstream response could not be interpreted correctly. |
| `partial_failure` | 500 | Some operation steps completed before another required step failed. |
| `plugin_error` | 500 | Expected plugin-domain failure without a closer generic match. |
| `internal_error` | 500 | Unexpected implementation failure; public diagnostics are withheld. |

Existing `artifact_not_found` (404), `artifact_expired` (410), and
`artifact_consumed` (410) codes retain their meanings.

## Retry guidance and partial outcomes

Structured plugin errors include `retry.transient`: `yes`, `no`, or `unknown`, and
`retry.replay`: `safe`, `unsafe`, `unknown`, or `same_idempotency_key`.
Transience does not imply safe replay. `same_idempotency_key` requires the
same key and payload while the original idempotency record remains valid.
There are no automatic operation retries.

For example, SMTP submission can succeed before a required IMAP Sent-copy
fails:

```json
{
  "error": {
    "code": "partial_failure",
    "plugin": "smtp",
    "domain_code": "sent_copy_failed",
    "message": "SMTP submission succeeded but saving the Sent copy failed.",
    "details": {
      "steps": {
        "smtp_submission": "succeeded",
        "sent_copy": "failed"
      }
    },
    "retry": {"transient": "unknown", "replay": "same_idempotency_key"}
  }
}
```

The existing SMTP `sent_copy.on_failure` policy remains authoritative:
`warn` returns a successful result containing the Sent-copy outcome; `fail`
returns an error with explicit completed and failed steps. Without a usable
idempotency key, resubmitting after confirmed submission is unsafe. An
upstream timeout during a write can leave the outcome unknown.
An uncertain Sent-copy append is reported as `unknown`; replaying a cached
SMTP result does not repeat that append. Inspect the destination before
attempting recovery, since the copy may already exist.

Plugins raise `arbiter_server.errors.OperationError` with public-safe
messages and JSON-compatible details. They own the classification and must
not include credentials, message contents, raw upstream text, or exception
strings.
