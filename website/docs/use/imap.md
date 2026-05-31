---
title: IMAP
---

The IMAP capability operates on configured accounts and folders. It does not
provide arbitrary mailbox access.

## Operations

- `imap:list_messages`
- `imap:get_message`
- `imap:search_messages`
- `imap:move_message`
- `imap:mark_message_read`
- `imap:delete_message`

## Common inputs

Every IMAP operation takes `account`. Operations that target messages also use a
folder-scoped `message_id`, which is an IMAP UID returned by `imap:list_messages`
or `imap:search_messages`.

Folders must be configured on the selected account.

## Example

```bash
arbiter op run imap:list_messages --args '{
  "account": "bot",
  "folder": "INBOX",
  "limit": 10
}'
```

Then use a returned message id:

```bash
arbiter op run imap:get_message --args '{
  "account": "bot",
  "folder": "INBOX",
  "message_id": "42"
}'
```

## Policy checks

The IMAP policy gates:

- read
- search
- move
- delete
- standard flag visibility and mutation
- configured user flag visibility and mutation

`imap:mark_message_read` mutates the standard `seen` flag and requires
`read_write` access to that flag.
