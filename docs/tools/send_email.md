# Tool: send_email

## Status

- Stage: `v1`
- Owner: `Mail Sentry server`

## Purpose

Send a single email through the SMTP submission path configured for a selected account.

## Intended usage

Use this when the agent has enough information to send a complete message to one or more recipients and sending mail is allowed by local policy.

## Input shape

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["account", "to", "subject"],
  "properties": {
    "account": {
      "type": "string",
      "minLength": 1,
      "description": "Configured account name returned by list_accounts"
    },
    "idempotency_key": {
      "type": "string",
      "minLength": 1,
      "maxLength": 256,
      "description": "Optional caller-supplied key used to suppress duplicate sends on retries"
    },
    "to": {
      "type": "array",
      "minItems": 1,
      "items": { "type": "string", "format": "email" }
    },
    "cc": {
      "type": "array",
      "items": { "type": "string", "format": "email" }
    },
    "bcc": {
      "type": "array",
      "items": { "type": "string", "format": "email" }
    },
    "in_reply_to": {
      "type": "string",
      "description": "Optional Message-ID for reply threading"
    },
    "references": {
      "type": "array",
      "items": { "type": "string" }
    },
    "subject": {
      "type": "string",
      "minLength": 1,
      "maxLength": 998
    },
    "text_body": {
      "type": "string"
    },
    "html_body": {
      "type": "string"
    }
  },
  "anyOf": [
    { "required": ["text_body"] },
    { "required": ["html_body"] }
  ]
}
```

The mail composition contract is intentionally narrow and predictable for MCP clients.

## Output shape

Success:

```json
{
  "ok": true,
  "message_id": "<generated-message-id@example.com>",
  "idempotency_replayed": false
}
```

Failure:

```json
{
  "ok": false,
  "error_code": "SUBMISSION_REJECTED",
  "message": "The SMTP submission failed",
  "retryable": false,
  "idempotency_replayed": false
}
```

Optional submission diagnostics may be included when available, but they should be treated as transport-level acceptance details rather than end-to-end delivery confirmation.

If recipient-level diagnostics are returned, they are best-effort transport details. They are primarily useful when the SMTP server is close to the final destination and may be absent or low-value when sending through an intermediate relay.

## Operation details

`send_email` is a one-shot message submission operation. It does not create drafts, manage conversation state, or confirm final delivery to a recipient inbox.

Expected behavior:

1. Validate the input payload.
2. Resolve the selected configured account.
3. Apply recipient and policy checks.
4. Resolve the deployment-owned sender identity for that account.
5. Build an RFC 5322 message with plain text and optional HTML parts.
6. Add reply-threading headers when provided.
7. Submit the message through the configured SMTP path.
8. Return a normalized success or failure result.

Header and envelope rules:

- `to` and `cc` appear in message headers and SMTP recipient handling
- `bcc` participates in SMTP recipient handling only and must not appear in the final serialized message headers
- the caller may not provide a `Reply-To` override in v1
- the caller must select a configured account explicitly

Idempotency rules:

- the caller may provide `idempotency_key` to suppress duplicate sends during retries
- if the same `idempotency_key` is received again with the same effective payload, the server should return the earlier normalized result instead of sending again and should set `idempotency_replayed` to `true`
- the server may compare effective payloads using a stored cryptographic hash such as SHA-256 instead of retaining the full message content in memory
- if the same `idempotency_key` is reused with a different effective payload, the server should fail with `IDEMPOTENCY_CONFLICT`
- replay applies to prior success and prior failure results alike
- a caller that wants to force a fresh submission attempt after a stored result must use a new `idempotency_key`
- once the configured idempotency record expires, the same `idempotency_key` should be treated as new work
- if no `idempotency_key` is provided, the operation has at-least-once semantics under retry

## Policy checks

- the selected account must exist and have SMTP enabled
- recipient address syntax should be validated
- recipient counts should respect configured safety limits
- configured allowlists and denylists should be enforced
- configured send-rate limits should be enforced before SMTP submission
- the caller may not override SMTP transport settings, `From`, or `Reply-To`

## Audit behavior

- emit debug logs for tool invocation, validation failure, SMTP connection attempt, SMTP submission result, and unexpected exception
- apply durable SMTP audit behavior from `mail.account_access_profiles.<profile>.smtp_audit`
- include the idempotency key in audit records when provided

## Errors

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

## Out of scope

- delivery tracking after SMTP acceptance
- inbox inspection to discover recipients or thread context
- template rendering
- attachments
- bulk fan-out behavior across many recipients

## Test checklist

- valid request succeeds
- request without `text_body` and `html_body` fails validation
- `bcc` is excluded from serialized headers
- configured rate limits are enforced
- idempotent replay returns the stored result with `idempotency_replayed: true`
- same idempotency key with different payload fails with `IDEMPOTENCY_CONFLICT`
- selected account must have SMTP enabled
