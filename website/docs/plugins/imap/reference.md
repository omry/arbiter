---
title: Reference
---

## Config Schemas

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

IMAP policy config is registered as `arbiter/policy/imap/schema`:

```python
class IMAPFlagMode(str, Enum):
    hidden = "hidden"
    read_only = "read_only"
    read_write = "read_write"


@dataclass
class IMAPAccessPolicyConfig(Policy):
    folder_access: IMAPFolderAccessConfig = field(default_factory=IMAPFolderAccessConfig)
    operation_defaults: IMAPFolderPolicyDefaultsConfig = field(
        default_factory=IMAPFolderPolicyDefaultsConfig
    )
    folders: dict[str, IMAPFolderOperationPolicyConfig] = field(default_factory=dict)
```

System flag fields use OmegaConf missing values so folder policy overrides can
set only the flags they change. `operation_defaults` supplies the complete
baseline policy.

## Folder Pattern Syntax

Folder keys in account metadata can be literal names or patterns. Pattern
matching covers the full folder name. `.` is a hard segment delimiter for the
default matchers:

- `*` matches zero or more non-dot characters.
- `?` matches one non-dot character.
- `[0-9]` style character classes match one character.
- `{name}` is shorthand for a named one-segment wildcard capture.
- `**` inside an explicit capture intentionally spans dots.

Named captures can be referenced in metadata strings. For example,
`Archives.{year}` matches `Archives.2026`, with `{year}` bound to `2026`.

Capture names ending in `?` are optional. When an optional capture is followed
by a literal `.`, the capture and delimiter are optional together. For example,
`Archives.{**:prefix?}.{[0-9][0-9]*:year}` matches both `Archives.2026` and
`Archives.2020-2029.2026`, with `{year}` bound to the final numeric segment.

## Enforcement

The IMAP policy gates:

- folder access
- read
- search
- move
- delete
- mark read/unread
- append
- save draft
- standard flag visibility and mutation
- configured user flag visibility and mutation

When exposed, `imap:get_attachment` is read-only and is governed by the
effective `read` decision for the selected folder.

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

## Config Checks

| Command | What it checks | Contacts IMAP? |
| --- | --- | --- |
| `arbiter-server config check` | Static IMAP account, folder metadata, and policy validity. | No |
| `arbiter-server config check --live` | Static checks plus configured account readiness against the server. | Yes |

Static IMAP checks:

| Check | Failure or warning |
| --- | --- |
| policy has valid folder access rules and folder policy patterns | config check issue |
| `default_folder` resolves through account metadata and policy | config check issue |
| configured folder keys resolve through metadata and policy | config check issue |
| all configured folders are denied | warning |
| delete is allowed but no accessible configured TRASH folder exists | config check issue |
| literal `Drafts` folder exists but is not marked `kind: DRAFTS` | warning |
| multiple configured `DRAFTS` folders exist | warning |
| selected configured `DRAFTS` folder is denied, cannot append, or cannot set `DRAFT`/`SEEN` flags | warning |

Live IMAP checks:

| Check | Notes |
| --- | --- |
| `connect` | Opens the configured IMAP connection. |
| `noop` | Verifies the authenticated session can issue a read-only command. |
| `examine` | Verifies accessible test folders can be selected read-only. |
| `trash_destination` | Runs when delete is allowed for an accessible live folder; verifies an accessible live TRASH folder exists. |
| `save_draft_destination` | Runs when an account has a configured `DRAFTS` folder; verifies the selected configured Drafts folder exists. |

The live check is read-only for message contents and mailbox state. It lists
folders, probes selected folders with read-only examine behavior, and does not
move, delete, append, or flag messages.
