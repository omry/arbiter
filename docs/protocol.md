# Arbiter Native HTTP Protocol

This document sketches the native Arbiter HTTP protocol that will replace the
current MCP-based public surface.

Arbiter is still pre-release, so this design intentionally does not preserve
the old MCP endpoint, command names, or URL contract unless a later release
decision explicitly adds a compatibility bridge.

## Goals

- Expose a simple native HTTP protocol.
- Keep MCP out of user-facing URLs, commands, docs, errors, and generated
  configuration.
- Make `arbiter.url` a stable server base URL, not a protocol endpoint.
- Support progressive discovery so clients and agents do not fetch every full
  operation schema up front.
- Support small inline artifacts, large streamed artifacts, and future large
  attachment uploads.

## Ports

Tentative defaults for the native HTTP protocol:

- Installed server: `8075`
- Staging server: `18075`

Examples:

```text
http://127.0.0.1:8075
http://127.0.0.1:18075
```

Do not append a protocol path such as `/mcp` to user-facing URLs.

## Route Summary

```text
GET  /_health_

GET  /api/v1/info
GET  /api/v1/plugins
GET  /api/v1/plugins/{plugin_id}/operations

GET  /api/v1/operations/{operation_id}
POST /api/v1/operations/{operation_id}

GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/artifacts/{artifact_id}/content

POST /api/v1/artifacts/uploads
PUT  /api/v1/artifacts/{artifact_id}/content
```

Operation IDs are URL path segments. They may contain `:`, so this is valid:

```text
/api/v1/operations/smtp:send_email
```

Operation IDs must not contain `/`, `?`, `#`, spaces, or other characters that
would make path handling ambiguous.

## Health

The health endpoint is intentionally tiny and safe to expose to Docker, systemd,
load balancers, and human operators:

```http
GET /_health_
```

```json
{
  "status": "ok"
}
```

It should not include plugin names, account names, configuration details, or
other discovery data.

## Server Info

`GET /api/v1/info` answers "what Arbiter server am I talking to?"

Example:

```json
{
  "name": "arbiter",
  "version": "0.9.2.dev1",
  "deployment_scope": "staged"
}
```

This endpoint may include small server-level metadata, but detailed capability
discovery belongs in the plugin and operation endpoints.

## Progressive Discovery

Discovery has three levels.

### Level 1: Plugins

`GET /api/v1/plugins` returns a compact capability menu.

Example:

```json
{
  "plugins": [
    {
      "id": "smtp",
      "summary": "Send email through configured SMTP accounts.",
      "operations_url": "/api/v1/plugins/smtp/operations"
    },
    {
      "id": "imap",
      "summary": "Read and manage mailbox messages.",
      "operations_url": "/api/v1/plugins/imap/operations"
    }
  ]
}
```

This level should stay small enough to fetch on normal startup.

### Level 2: Plugin Operations

`GET /api/v1/plugins/{plugin_id}/operations` returns the operation menu for one
plugin. It includes enough information for a human or agent to choose the right
operation, but it does not include full schemas.

Example:

```json
{
  "plugin": "smtp",
  "operations": [
    {
      "id": "smtp:send_email",
      "summary": "Send an email message.",
      "when_to_use": "Use when you need Arbiter to send a plain text or MIME email through a configured SMTP account.",
      "details_url": "/api/v1/operations/smtp:send_email"
    }
  ]
}
```

### Level 3: Operation Details

`GET /api/v1/operations/{operation_id}` returns full details for one operation.

Example:

```json
{
  "id": "smtp:send_email",
  "plugin": "smtp",
  "summary": "Send an email message.",
  "description": "Send a message through a configured SMTP account, subject to account policy.",
  "input_schema": {},
  "output_schema": {},
  "artifact_policy": {
    "inline_max_bytes": 5120,
    "supports_uploads": true
  }
}
```

Clients may cache discovery responses. The protocol can later add `ETag` and
`If-None-Match` support without changing the response shapes.

## Operation Invocation

Use the same operation URL for description and invocation:

```http
GET  /api/v1/operations/smtp:send_email
POST /api/v1/operations/smtp:send_email
```

`GET` describes the operation. `POST` invokes it.

Example request:

```json
{
  "args": {
    "account": "bot",
    "to": ["ops@example.com"],
    "subject": "Hello",
    "text_body": "Hi"
  }
}
```

Example response:

```json
{
  "result": {},
  "artifacts": [],
  "warnings": []
}
```

If asynchronous execution is needed later, `POST` can return `202 Accepted`
with a run handle and status URL.

## Success And Error Shapes

Successful responses should be shaped like the resource or result they return.
Do not wrap every successful response in a universal `{"ok": true, "data": ...}`
envelope. HTTP status codes already distinguish success from failure, and
resource-shaped responses are easier for users, clients, and docs to read.

Errors should use a consistent envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Missing required argument: account",
    "details": {
      "field": "account"
    }
  }
}
```

Use standard HTTP status codes where possible:

- `400`: invalid request shape
- `401`: unauthenticated
- `403`: forbidden by authentication, authorization, or policy
- `404`: unknown plugin, operation, artifact, or route
- `409`: conflict
- `413`: payload too large
- `415`: unsupported media type
- `422`: valid request rejected by operation validation or policy
- `429`: rate limited
- `500`: server bug
- `503`: server not ready or dependency unavailable

Arbiter-specific semantics should normally live in `error.code`, not in custom
HTTP status codes. Private HTTP codes remain an option if a later design needs
them, but they should be introduced deliberately because standard tooling
handles standard codes better.

## Artifacts

Operation responses may include artifacts.

Every artifact should include integrity metadata:

```json
{
  "id": "art_456",
  "name": "archive.zip",
  "mime_type": "application/zip",
  "size": 981234,
  "sha256": "4e07408562bedb8b60ce05c1decfe3ad16b7223092...",
  "content_url": "/api/v1/artifacts/art_456/content"
}
```

### Inline Artifacts

Small artifacts may be returned inline up to the server's configured inline
limit. The initial target limit is 5 KiB.

Text example:

```json
{
  "id": "art_123",
  "name": "messages.json",
  "mime_type": "application/json",
  "size": 3210,
  "sha256": "4e07408562bedb8b60ce05c1decfe3ad16b7223092...",
  "inline": {
    "encoding": "utf-8",
    "data": "[...]"
  }
}
```

Binary example:

```json
{
  "id": "art_124",
  "name": "image.png",
  "mime_type": "image/png",
  "size": 4096,
  "sha256": "4e07408562bedb8b60ce05c1decfe3ad16b7223092...",
  "inline": {
    "encoding": "base64",
    "data": "..."
  }
}
```

### Streamed Artifact Content

Large artifacts are fetched through HTTP streaming:

```http
GET /api/v1/artifacts/{artifact_id}/content
```

The response should use normal HTTP content headers when available:

```http
Content-Type: application/json
Content-Length: 123456
Digest: sha-256=...
```

The JSON artifact metadata remains the durable integrity contract. The digest
header is useful transport metadata when available.

## Uploads And Large Attachments

Large uploads should use temporary artifacts that operations can reference.
Implementation and tests can come later, but the high-level flow is:

1. Create an upload slot.

   ```http
   POST /api/v1/artifacts/uploads
   ```

   ```json
   {
     "artifact": {
       "id": "tmp_art_123",
       "kind": "upload",
       "expires_at": "2026-06-17T12:00:00Z"
     },
     "upload_url": "/api/v1/artifacts/tmp_art_123/content"
   }
   ```

2. Stream bytes to the upload slot.

   ```http
   PUT /api/v1/artifacts/tmp_art_123/content
   ```

3. Reference the uploaded artifact from an operation.

   ```json
   {
     "args": {
       "account": "bot",
       "to": ["ops@example.com"],
       "subject": "Report",
       "attachments": [
         {
           "artifact_id": "tmp_art_123",
           "filename": "report.pdf"
         }
       ]
     }
   }
   ```

Temporary upload artifacts should expire if they are not consumed.
