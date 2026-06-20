---
title: Behavior
---

IMAP operations are policy-gated before Arbiter issues upstream IMAP commands.
The caller selects a configured account, folder, and message id; Arbiter
supplies the connection settings, credentials, folder metadata, and access
decisions from deployment config.

## Folder Metadata

Account folder entries are overlays on top of folders that already exist on the
IMAP server. They can add descriptions and `kind` metadata, and they can match
literal folder names or patterns. They do not create upstream folders and do
not grant access by themselves.

`imap:list_folders` and `imap:search_folders` query the upstream server, overlay
matching account folder metadata, and hide folders denied by policy. Folder
results include `name`, `description`, `kind`, `default`, and effective
operation policy. Folder list and search results also include `limit` and
`truncated`.

## Message Ids

Operations that target messages use a folder-scoped `message_id`. This is an
IMAP UID returned by `imap:list_messages` or `imap:search_messages`. A UID is
meaningful in the selected folder; pass the folder with the message id when
reading, moving, flagging, or deleting a message.

## Attachments

`imap:get_message` returns the message body plus an `attachments` inventory.
Each attachment entry includes a MIME-part id, filename, content type, decoded
size, disposition, content id, and whether it is inline. Attachment bodies are
not returned by `imap:get_message`.

Fetch attachment content with `imap:get_attachment` when HTTP artifact delivery
is available. The operation materializes the attachment in the server's IMAP
plugin storage and returns a one-time HTTPS `content_url`. Use an explicit
artifact-aware client command with that returned URL to read the artifact;
request the attachment again if a new one-time URL is needed.

For a small textual artifact only, an agent can explicitly stream the artifact
to stdout:

```bash
arbiter artifact get 'https://127.0.0.1:8075/api/v1/artifacts/.../content?nonce=...' --stdout
```

For binary attachments, run an explicit reader command through the client so
the raw artifact bytes never enter stdout. Path-based tools can use a private
temp file that the client removes when the command exits:

```bash
arbiter artifact with-temp 'https://127.0.0.1:8075/api/v1/artifacts/.../content?nonce=...' -- pandoc '{}' -t plain
```

Stdin-based tools can receive the artifact bytes directly:

```bash
arbiter artifact with-stdin 'https://127.0.0.1:8075/api/v1/artifacts/.../content?nonce=...' -- pandoc -f docx -t plain -
```

When the user explicitly asks to save an attachment to a local file, use the
explicit save command:

```bash
arbiter artifact save 'https://127.0.0.1:8075/api/v1/artifacts/.../content?nonce=...' ./attachment.pdf
```

Do not use persistent saves as the default inspection path; prefer `with-temp`
or `with-stdin` for tool processing.

## Flags

`imap:mark_message_read` mutates the standard `\Seen` flag and requires
`read_write` access to that flag.

`imap:get_message_flags` shows flags that policy allows the caller to see.
`imap:update_message_flags` can add or remove flags only when policy grants
write access to those flags.

`imap:append_message` and SMTP sent-copy appends require `folder_append` on the
destination folder. Any flags supplied with the append, including the default
`\Seen` flag, also require `read_write` access in that folder's effective flag
policy.

`imap:save_draft` appends to the account's configured `DRAFTS` folder unless a
folder is supplied explicitly. It writes both `\Draft` and `\Seen`, so the
destination folder policy must grant `folder_append`, `DRAFT: read_write`, and
`SEEN: read_write`.

Static config checks warn when configured draft support is ambiguous or cannot
support `save_draft`. Live config checks also verify that the selected configured
Drafts folder exists on the upstream IMAP server.

## Delete To Trash

Non-permanent `imap:delete_message` uses an accessible configured TRASH folder
when delete is allowed. Moving a message into a folder marked `kind: TRASH` is
also treated as a delete policy decision for the source folder.

If delete is allowed but no accessible TRASH folder can be resolved, config
checks and live checks report that delete requires an accessible TRASH folder.
