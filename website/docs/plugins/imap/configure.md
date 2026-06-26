---
title: Configure
---

An IMAP account describes how Arbiter connects to an IMAP server and which
folder metadata overlays it should apply. The matching policy decides which
folders are visible and which message actions are allowed.

Bootstrap an account and starter policy:

```bash
arbiter-server bootstrap --plugin imap --account bot
```

The account file contains deployment-owned connection settings and credentials:

```yaml title="arbiter/account/imap/bot.yaml"
policy: bot_policy
host: imap.example.com
port: 993
username: ${oc.env:IMAP_BOT_ACCOUNT_USERNAME}
password: ${oc.env:IMAP_BOT_ACCOUNT_PASSWORD}
tls: implicit
verify_peer: true
default_folder: INBOX
```

## Folders

Folder entries are metadata overlays. They describe known folders and patterns;
they do not create folders on the server and do not grant access by themselves.

```yaml title="arbiter/account/imap/bot.yaml"
folders:
  INBOX:
    description: Primary inbox.
    kind: INBOX
  Sent:
    description: Sent mail.
    kind: SENT
  Trash:
    description: Deleted mail.
    kind: TRASH
  "Archives.{year}":
    description: Archived mail from {year}.
    kind: ARCHIVE
```

### Types

Folder `kind` values map to IMAP special-use mailbox roles. They are useful for
client display and service behavior such as SMTP Sent copies or IMAP
delete-to-trash. Valid kinds are `INBOX`, `ALL`, `ARCHIVE`, `DRAFTS`,
`FLAGGED`, `JUNK`, `SENT`, and `TRASH`.

### Patterns

Folder keys can be literal names or metadata patterns. Named captures, such as
`{year}`, can be referenced in metadata strings.

See [Folder Pattern Syntax](./reference#folder-pattern-syntax) for the full
pattern reference.

## Policy Shape

The policy file contains the folder access and operation guardrails:

```yaml title="arbiter/policy/imap/bot_policy.yaml"
folder_access:
  rules:
    - allow_glob: "*"

operation_defaults:
  read: allow
  search: allow
  move: false
  mark_read: deny
  delete: deny
  folder_append: deny
```

Per-folder policy overrides can narrow or extend the defaults for matching
folders.

To let agents save drafts with `imap:save_draft`, configure a `DRAFTS` folder on
the account and allow appends plus the draft flags on that folder:

```yaml title="arbiter/policy/imap/bot_policy.yaml"
folders:
  Drafts:
    folder_append: allow
    system_flags:
      SEEN: read_write
      DRAFT: read_write
```

After editing account and policy files, activate the account:

```bash
arbiter-server config activate --plugin imap --account bot
```

If the account uses new environment variables, update the env file:

```bash
arbiter-server env bootstrap
```
