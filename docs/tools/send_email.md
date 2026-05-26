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
  "recipient_count": 1
}
```

The current implementation lets FastMCP/Python surface failures. The normalized error response below is the target contract, not the current runtime shape:

```json
{
  "ok": false,
  "error_code": "SUBMISSION_REJECTED",
  "message": "The SMTP submission failed",
  "retryable": false
}
```

Optional submission diagnostics may be included when available, but they should be treated as transport-level acceptance details rather than end-to-end delivery confirmation.

If recipient-level diagnostics are returned, they are best-effort transport details. They are primarily useful when the SMTP server is close to the final destination and may be absent or low-value when sending through an intermediate relay.

## Operation details

`send_email` is a one-shot message submission operation. It does not create drafts, manage conversation state, or confirm final delivery to a recipient inbox.

Expected behavior:

1. Validate the input payload.
2. Resolve the selected configured account.
3. Apply basic recipient and account send-policy checks.
4. Resolve the deployment-owned sender identity for that account.
5. Build an RFC 5322 message with plain text and optional HTML parts.
6. Submit the message through the configured SMTP path.
7. Return a success result with the generated Message-ID and recipient count.

Header and envelope rules:

- `to` and `cc` appear in message headers and SMTP recipient handling
- `bcc` participates in SMTP recipient handling only and must not appear in the final serialized message headers
- the caller may not provide a `Reply-To` override in v1
- the caller must select a configured account explicitly

Idempotency is reserved in config as
`mail.account_access_profiles.<profile>.services.smtp.idempotency.expiration_days`.
The current server rejects non-default values for that field at startup until
replay/conflict behavior is implemented.

## Policy checks

- the selected account must exist and have SMTP enabled
- recipient address syntax is validated with a basic `@` check
- configured `max_messages_per_minute` is enforced as a per-account,
  per-process rolling 60-second limit
- configured `max_recipients_per_message` is enforced
- configured exact-recipient and domain-pattern allow/block rules are enforced
- startup rejects non-default SMTP idempotency config
- the caller may not override SMTP transport settings, `From`, or `Reply-To`

## Audit behavior

Structured debug logs and durable SMTP audit records are target behavior, not current implementation behavior.

The target audit model is:

- emit debug logs for tool invocation, validation failure, SMTP connection attempt, SMTP submission result, and unexpected exception
- apply durable SMTP audit behavior from `mail.account_access_profiles.<profile>.services.smtp.audit`

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
- selected account must have SMTP enabled
- configured `max_messages_per_minute` is enforced
- configured `max_recipients_per_message` is enforced
- exact-recipient and domain-pattern recipient policy is enforced
- startup rejects unsupported idempotency SMTP config
