---
title: IMAP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-imap.svg?label=python)](https://pypi.org/project/arbiter-imap/) [![Downloads](https://pepy.tech/badge/arbiter-imap/month)](https://pepy.tech/project/arbiter-imap)

The IMAP capability operates on configured accounts and policy-accessible
folders. Account folder entries are metadata overlays; folder access lives in
IMAP policy.

## Operations

- `imap:list_messages`
- `imap:get_message`
- `imap:get_attachment` when HTTP artifact delivery is available
- `imap:list_folders`
- `imap:search_messages`
- `imap:search_folders`
- `imap:move_message`
- `imap:mark_message_read`
- `imap:get_message_flags`
- `imap:update_message_flags`
- `imap:append_message`
- `imap:delete_message`

## Common inputs

Every IMAP operation takes `account`. Operations that target messages also use a
folder-scoped `message_id`, which is an IMAP UID returned by `imap:list_messages`
or `imap:search_messages`.

`imap:list_folders` and `imap:search_folders` query the upstream IMAP server,
overlay matching account folder metadata, and hide folders denied by
`folder_access`. Folder results include `name`, `description`, `kind`,
`default`, and effective operation policy. Folder list and search results also
include `limit` and `truncated`.

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


class IMAPFolderKind(str, Enum):
    INBOX = "INBOX"
    ALL = "ALL"
    ARCHIVE = "ARCHIVE"
    DRAFTS = "DRAFTS"
    FLAGGED = "FLAGGED"
    JUNK = "JUNK"
    SENT = "SENT"
    TRASH = "TRASH"


@dataclass
class IMAPFolderConfig:
    description: str = ""
    kind: IMAPFolderKind | None = None


@dataclass
class IMAPConfig(Policy):
    policy: str = "bot"
    description: str = ""
    guidance: str = ""
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

Folder `kind` values map to IMAP special-use mailbox roles. They are optional
metadata for clients; they do not grant permissions or discover upstream
folders.

```yaml
folders:
  INBOX:
    description: Primary inbox.
  Sent:
    description: Sent mail.
    kind: SENT
  Archives/2020-2029/2024:
    description: Archived mail from 2024.
    kind: ARCHIVE
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
    folder_append = "folder_append"


@dataclass
class IMAPSystemFlagsPolicyConfig:
    SEEN: IMAPFlagMode = MISSING
    FLAGGED: IMAPFlagMode = MISSING
    ANSWERED: IMAPFlagMode = MISSING
    DELETED: IMAPFlagMode = MISSING
    DRAFT: IMAPFlagMode = MISSING


def default_imap_system_flags_policy() -> IMAPSystemFlagsPolicyConfig:
    return IMAPSystemFlagsPolicyConfig(
        SEEN=IMAPFlagMode.read_only,
        FLAGGED=IMAPFlagMode.read_only,
        ANSWERED=IMAPFlagMode.read_only,
        DELETED=IMAPFlagMode.read_only,
        DRAFT=IMAPFlagMode.read_only,
    )


@dataclass
class IMAPAccessPolicyConfig(Policy):
    folder_access: IMAPFolderAccessConfig = field(default_factory=IMAPFolderAccessConfig)
    operation_defaults: IMAPFolderPolicyDefaultsConfig = field(
        default_factory=IMAPFolderPolicyDefaultsConfig
    )
    folders: dict[str, IMAPFolderOperationPolicyConfig] = field(default_factory=dict)
    confirmation_required: list[IMAPConfirmationAction] = field(default_factory=list)
```

System flag fields use OmegaConf missing values so folder policy overrides can
set only the flags they change. `operation_defaults` supplies the complete
baseline policy.

</details>

## Example

List configured top-level folders:

```bash
arbiter op run imap:list_folders --args '{
  "account": "bot",
  "recursive": false,
  "limit": 10
}'
```

Search configured folder names, descriptions, and kinds:

```bash
arbiter op run imap:search_folders --args '{
  "account": "bot",
  "query": "archive",
  "recursive": true,
  "limit": 10
}'
```

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

`imap:append_message` and SMTP sent-copy appends require `folder_append` on the
destination folder. Any flags supplied with the append, including the default
`\Seen` flag, also require `read_write` access in that folder's effective flag
policy.

`imap:get_attachment` is read-only and is governed by the effective `read`
decision for the selected folder.

Use `op check` to ask why a specific operation payload is allowed or denied
without calling the upstream IMAP server:

```bash
arbiter op check imap:move_message --args '{
  "account": "bot",
  "folder": "INBOX",
  "message_id": "42",
  "destination_folder": "Archive"
}'
```
