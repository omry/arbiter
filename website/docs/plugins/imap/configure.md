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

The generated `default-open` policy lets agents read and search **every current
and future folder**. This is convenient for initial use, but a prompt-injected
caller can read any mail that policy permits. Activation prints a warning about
this bootstrap default; it does not audit your selected policy or ask for a
confirmation. Narrow `folder_access.rules` after trying the account.

For explicit opt-in folder access, generate a separate default-closed policy:

```bash
arbiter-server bootstrap --plugin imap --policy restricted --variant default-closed
```

Set the account's `policy: restricted`, then add allow rules to that policy for
the folders agents should see. Folder metadata in the account file alone does
not narrow access. Both variants retain the same operation defaults; folder
access and permission to perform an operation are separate decisions.

The account file contains deployment-owned connection settings and credentials:

```yaml title="arbiter/account/imap/bot.yaml"
policy: bot_policy
host: ${oc.env:IMAP_BOT_ACCOUNT_HOST}
port: ${oc.env:IMAP_BOT_ACCOUNT_PORT,993}
username: ${oc.env:IMAP_BOT_ACCOUNT_USERNAME}
password: ${oc.env:IMAP_BOT_ACCOUNT_PASSWORD}
tls: implicit
verify_peer: true
default_folder: INBOX
```

Generated config uses environment variables for deployment-specific connection
details. It is still your config: edit the YAML directly when a fixed value is
clearer for your deployment.

## Message and upstream-server limits

Set the maximum whole-message size in the IMAP account file:

```yaml
limits:
  max_message_bytes: 52428800
  max_preview_bytes: 16384
  upstream_server:
    max_response_bytes: 67108864
```

The full-message default is 50 MiB, including encoded attachments.
`max_preview_bytes` defaults to 16 KiB and limits total text fetched per message
for list/search snippets. It excludes attachment content, which list/search do
not fetch. Both values must be positive integers; zero does not disable a cap.
These IMAP-owned limits do not override the core server's artifact size or
storage limits. Oversized messages remain visible in list/search; their full
content can be read only within `max_message_bytes`.

`limits.upstream_server.max_response_bytes` defaults to 64 MiB (67,108,864
bytes). Arbiter enforces this separate safety cap on each complete IMAP
command response, including unsolicited replies, literals, and protocol
framing. Responses still cached by the connection count against the next
command's available budget. The initial greeting is bounded too. This is an
operator-owned limit on data accepted from the server, not a server-advertised
quota. It must be a positive integer.

If the cap is exceeded, Arbiter closes the connection and reports IMAP
`response_too_large` with generic `upstream_protocol_error`. Raise this limit
only when the upstream response volume is expected. A larger message limit
does not automatically raise the upstream-server limit; leave room for framing
and unsolicited data when configuring both. Metadata also retains its existing
internal 50 MiB safety budget.

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
