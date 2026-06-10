---
title: IMAP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-imap.svg?label=python)](https://pypi.org/project/arbiter-imap/) [![Downloads](https://pepy.tech/badge/arbiter-imap/month)](https://pepy.tech/project/arbiter-imap)

The IMAP capability operates on configured accounts and folders. It does not
provide arbitrary mailbox access.

## Operations

- `imap:list_messages`
- `imap:get_message`
- `imap:get_attachment` when HTTP artifact delivery is available
- `imap:search_messages`
- `imap:move_message`
- `imap:mark_message_read`
- `imap:delete_message`

## Common inputs

Every IMAP operation takes `account`. Operations that target messages also use a
folder-scoped `message_id`, which is an IMAP UID returned by `imap:list_messages`
or `imap:search_messages`.

Folders must be configured on the selected account.

## Config schema

<details>
<summary>Dataclasses</summary>

Hydra validates IMAP account and policy config against these schemas during
config composition, including values supplied through command-line overrides.

IMAP account config is registered as `arbiter/account/imap/schema`:

```python
class MailTlsMode(str, Enum):
    none = "none"
    starttls = "starttls"
    implicit = "implicit"


@dataclass
class IMAPFolderConfig:
    description: str = ""


@dataclass
class IMAPConfig(Policy):
    policy: str = "bot"
    description: str = ""
    host: str = "localhost"
    port: int = 993
    username: str = ""
    password: str = ""
    tls: MailTlsMode = MailTlsMode.implicit
    verify_peer: bool = True
    timeout_seconds: float = 30.0
    default_folder: str | None = None
    folders: dict[str, IMAPFolderConfig] = field(default_factory=dict)
```

IMAP policy config is registered as `arbiter/policy/imap/schema`:

```python
class IMAPFlagMode(str, Enum):
    hidden = "hidden"
    read_only = "read_only"
    read_write = "read_write"


class IMAPConfirmationAction(str, Enum):
    read = "read"
    search = "search"
    move = "move"
    mark_read = "mark_read"
    delete = "delete"


@dataclass
class IMAPSystemFlagsPolicyConfig:
    seen: IMAPFlagMode = IMAPFlagMode.read_only
    flagged: IMAPFlagMode = IMAPFlagMode.read_only
    answered: IMAPFlagMode = IMAPFlagMode.read_only
    deleted: IMAPFlagMode = IMAPFlagMode.read_only
    draft: IMAPFlagMode = IMAPFlagMode.read_only


@dataclass
class IMAPAccessPolicyConfig(Policy):
    allow_read: bool = True
    allow_search: bool = True
    allow_move: bool = True
    allow_delete: bool = True
    confirmation_required: list[IMAPConfirmationAction] = field(default_factory=list)
    system_flags: IMAPSystemFlagsPolicyConfig = field(
        default_factory=IMAPSystemFlagsPolicyConfig
    )
    user_flags: dict[str, IMAPFlagMode] = field(default_factory=dict)
```

</details>

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

`imap:get_message` returns the message body plus an `attachments` inventory.
Each attachment entry includes a MIME-part id, filename, content type, decoded
size, disposition, content id, and whether it is inline. Attachment bodies are
not returned by `imap:get_message`.

Fetch attachment content with `imap:get_attachment`. The operation does not
return attachment bytes in the tool result. Instead, it materializes the
attachment in the server's IMAP plugin storage and returns a one-time artifact
URL. Use an explicit artifact-aware client command to read the artifact; request
the attachment again if a new one-time URL is needed. This operation is exposed
only when Arbiter is running with HTTP artifact delivery; stdio transports do
not have a URL channel for artifact access.

```bash
arbiter op run imap:get_attachment --args '{
  "account": "bot",
  "folder": "INBOX",
  "message_id": "42",
  "attachment_id": "part-3"
}'
```

The Go client will not fetch artifact bytes automatically. For a small textual
artifact only, an agent can explicitly stream the artifact to stdout:

```bash
arbiter artifact get 'http://127.0.0.1:8000/_arbiter/artifacts/...' --stdout
```

The client checks artifact metadata first and refuses stdout for non-text,
unknown-size, or over-limit artifacts.

For binary attachments, run an explicit reader command through the client so the
raw artifact bytes never enter stdout. Path-based tools can use a private temp
file that the client removes when the command exits:

```bash
arbiter artifact with-temp 'http://127.0.0.1:8000/_arbiter/artifacts/...' -- pandoc '{}' -t plain
```

Stdin-based tools can receive the artifact bytes directly:

```bash
arbiter artifact with-stdin 'http://127.0.0.1:8000/_arbiter/artifacts/...' -- pandoc -f docx -t plain -
```

The client executes the command argv directly, without a shell. Only bounded,
textual child stdout is written back.

When the user explicitly asks to save an attachment to a local file, use the
explicit save command:

```bash
arbiter artifact save 'http://127.0.0.1:8000/_arbiter/artifacts/...' ./attachment.pdf
```

Do not use persistent saves as the default agent inspection path; prefer
`with-temp` or `with-stdin` for tool processing.

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

`imap:get_attachment` is read-only and is governed by `allow_read`.
