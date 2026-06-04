---
title: IMAP
---

[![PyPI](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/) [![Python](https://img.shields.io/pypi/pyversions/arbiter-imap.svg?label=python)](https://pypi.org/project/arbiter-imap/) [![Downloads](https://pepy.tech/badge/arbiter-imap/month)](https://pepy.tech/project/arbiter-imap)

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
