---
title: IMAP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-imap.svg?label=python)](https://pypi.org/project/arbiter-imap/) [![Downloads](https://pepy.tech/badge/arbiter-imap/month)](https://pepy.tech/project/arbiter-imap)

IMAP lets Arbiter read and manage configured mailboxes through policy-scoped
operations. Operators own the IMAP connection settings, credentials, folder
metadata, folder access policy, message-operation policy, flag policy, and
delete behavior. Agents can discover and use the allowed mailbox operations,
but they do not provide IMAP credentials or transport settings.

## Operations

| Operation | Use |
| --- | --- |
| `imap:list_folders` | List policy-accessible folders. |
| `imap:search_folders` | Search policy-accessible folder names and metadata. |
| `imap:list_messages` | List messages in a folder. |
| `imap:search_messages` | Search messages in a folder. |
| `imap:get_message` | Read one message and its attachment inventory. |
| `imap:get_attachment` | Materialize one attachment as an Arbiter artifact when HTTP artifact delivery is available. |
| `imap:move_message` | Move a message to an allowed destination. |
| `imap:delete_message` | Delete a message, normally by moving it to TRASH. |
| `imap:mark_message_read` | Mark one message read or unread. |
| `imap:get_message_flags` | Inspect message flags allowed by policy. |
| `imap:update_message_flags` | Add or remove message flags allowed by policy. |
| `imap:append_message` | Append a message to an allowed folder. |

## Use

Inspect the plugin and an operation:

```bash
arbiter plugins imap
arbiter op desc imap:list_messages
```

Inspect an account before using it:

```bash
arbiter plugins imap account bot
```

List folders, then list messages in one folder:

```bash
arbiter op run imap:list_folders --args '{
  "account": "bot",
  "recursive": false,
  "limit": 10
}'

arbiter op run imap:list_messages --args '{
  "account": "bot",
  "folder": "INBOX",
  "limit": 10
}'
```

Read a returned message id:

```bash
arbiter op run imap:get_message --args '{
  "account": "bot",
  "folder": "INBOX",
  "message_id": "42"
}'
```

## More

- [Configure](./configure): account, folder metadata, and policy setup.
- [Behavior](./behavior): folder overlays, message ids, attachments, flags,
  and delete-to-trash behavior.
- [Reference](./reference): schemas, policy gates, config checks, and live
  checks.
