# WhatsApp operations

The optional plugin currently exposes `whatsapp:receive_updates`,
`whatsapp:send_message`, `whatsapp:mark_read`, `whatsapp:typing` and
`whatsapp:upload_media`.
See [configuration](configure.md) for enrollment and
[behavior and recovery](behavior.md) for cursor and reply safety.

## Send a text message

Call `whatsapp:send_message` with these arguments:

```json
{
  "account": "creator",
  "participant_id": "user:creator-id-from-updates",
  "message": {
    "type": "text",
    "text": {"body": "Hello", "preview_url": false}
  },
  "reply_to_message_id": "inbound-message-id-from-updates"
}
```

Use the exact inbound sender as `participant_id`. It must have the opaque
`user:<id>` form, contain no controls and fit in 256 UTF-8 bytes. Do not convert
it to a phone number. The provider restricts the recipient to the agent creator.

Text has a nonempty `body` of at most 4096 Unicode code points and an optional
boolean `preview_url`, which defaults to false. Unknown fields and unsupported
message types fail before submission. Media sends are not available yet.

An optional `message.context` object contains only `message_id`, the provider
message to quote. This quote is separate from `reply_to_message_id`, which is
the local duplicate-send protection key. Quote/source IDs must be nonempty,
contain no controls and fit in 512 UTF-8 bytes.

The result contains `delivery`, `provider_message_id`, and
`idempotency_replayed`. Delivery is `accepted` with a provider message ID, or
`unknown` with a null ID. Acceptance reports provider submission, not a delivery
or read receipt. Never resend an unknown outcome through a different source
key or an unkeyed call. `idempotency_replayed: true` means the plugin returned
a retained outcome without another provider submission.

`send_messages: false` denies the operation, including preflight, before any
reservation or provider I/O. This text operation does not require media or read
permissions. All trusted local callers share the same policy and reply state.

## Read receipts and typing

Call `whatsapp:mark_read` with `{"account":"creator","message_id":"wamid.inbound"}`
to mark a provider message read. It requires `read_receipts`, independently of
`send_messages` and `typing`.

Call `whatsapp:typing` with
`{"account":"creator","message_id":"wamid.inbound","active":true}` to start
the typing indicator. The provider also marks that message read, so both
`typing` and `read_receipts` must allow the request. It does not require
`send_messages`. Either operation returns `{"status":"accepted"}` only after
the provider confirms success. The message ID must be nonempty, contain no
control characters and fit in 512 UTF-8 bytes.

`active: false` cannot cancel an indicator. With an authorized, ready account,
it returns `upstream_rejected/typing_reset_unsupported` without submitting a
request. The indicator expires through the provider's reply or timeout behavior.
See [status recovery](behavior.md#read-receipts-and-typing).


## Upload an existing artifact

Call `whatsapp:upload_media` with an artifact ID and its complete URL from the
same Arbiter server. Artifacts from another plugin, such as IMAP, are accepted
when you hold their current one-time capability.

```json
{
  "account": "creator",
  "content": {"id": "artifact-id", "url": "http://localhost:8000/_arbiter/artifacts/artifact-id?nonce=capability"},
  "media_type": "application/pdf"
}
```

Copy both fields from the artifact descriptor. Do not construct a URL or pass a
host file path. This operation does not fetch arbitrary URLs or upload files
from your client. It requires `media_egress`, independently
of `send_messages`. A successful result is `{"media_id":"provider-media-id"}`.
Uploading does not send a message; media sending arrives in the next slice.

The declared `media_type` must match the stored artifact content type exactly.
Supported types and decimal byte ceilings are:

| Types | Maximum bytes |
| --- | ---: |
| `image/jpeg`, `image/png` | 5,000,000 |
| `image/webp` (static or animated sticker) | 500,000 |
| `video/mp4`, `video/3gpp` | 16,000,000 |
| `audio/aac`, `audio/mp4`, `audio/mpeg`, `audio/amr`, `audio/ogg`, `audio/opus` | 16,000,000 |
| `application/pdf`, `text/plain`, legacy Word/Excel/PowerPoint MIME types and their OpenXML MIME types | 16,000,000 |
| `application/octet-stream` | 16,000,000 |

The server's configured artifact byte cap also applies. JPEG/PNG images must
use RGB/RGBA with 8-bit components and at most 25 megapixels. WebP dimensions
cannot exceed 4096 by 4096. Pillow validates bounded headers and structure;
the provider still decides codec suitability. The plugin adds no transcoder.
See [consumption and retry guidance](behavior.md#media-upload-and-artifact-consumption).
