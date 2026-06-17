# Design: IMAP Folder Access and Folder Policy

## Status

This is the active design for tightening IMAP permissions before the initial
release. It should be kept as implementation guidance until the behavior is
stable, then either retained as design history or replaced by operator-facing
documentation.

This document is not a canonical user-facing reference yet, and it is not
superseding an older IMAP folder-policy design document. If accepted and
implemented, it supersedes parts of the current IMAP user documentation and
config schema reference, especially the current statements that folders must be
explicitly configured on the account and that IMAP policy is account-wide
booleans.

## Decision Summary

- `account.folders` is mailbox metadata. It does not create folders or define
  the discoverable folder set. It grants or restricts access only when policy
  explicitly references metadata such as `kind`.
- Folder access lives in IMAP policy and is the first authorization gate for
  every folder operation.
- Folder access rules are an ordered allow/deny list. The first rule must be
  either `allow_glob: "*"` or `deny_glob: "*"`.
- Every matching access rule applies in order. The last matching decision is the
  effective access decision.
- Per-folder operation policy composes from defaults plus matching folder policy
  entries.
- `move` is `bool | MovePolicyConfig`; structured move policy controls both
  whether messages can leave the source and which destinations are allowed.
- `delete_message` is user-facing delete. By default it should move the message
  to an accessible `kind: TRASH` folder. An explicit `permanent: true` operation
  argument performs hard delete.
- `folder_append` gates IMAP APPEND into a folder. The existing SMTP sent-copy
  path is the concrete in-scope append caller.
- Normal discovery hides inaccessible folders. Explicit policy checks can answer
  "can this operation run?" and "why not?" for a named operation payload.

## Problem

The current IMAP model has two useful but incomplete pieces:

- `arbiter.account.imap.<name>.folders` is optional folder metadata with
  descriptions and optional `kind` classification.
- `arbiter.policy.imap.<name>` has account-wide booleans such as `allow_read`,
  `allow_search`, `allow_move`, and `allow_delete`.

That leaves several gaps:

- Folder access is not separately modeled. Any folder the IMAP server exposes
  can be visible to `list_folders` and `search_folders`, and usable by
  folder-taking operations.
- Mutating policy is account-wide, even though operators often want different
  behavior for `INBOX`, archive folders, sent mail, drafts, junk, and trash.
- The internal sent-copy path appends messages to IMAP folders without an
  explicit IMAP append policy. Today sent-copy relies on folder metadata,
  usually `kind: SENT`.
- The current model cannot express "agents may see and search archive folders
  but may only read `INBOX`", "SMTP may append to Sent but agents may not delete
  from Sent", or "agents may move messages from `INBOX` only to archive
  folders".

## Design Direction

Keep account config as the mailbox metadata overlay and move access decisions
into IMAP policy.

- Account config answers: which IMAP server/account is this, and what metadata
  should Arbiter attach to matching IMAP folders?
- Policy config answers: which IMAP folders are accessible, and what operations
  are allowed on those folders?

This keeps operator-facing folder metadata close to account setup while keeping
authorization in the policy surface.

The important distinction is that `account.folders` is not itself an access
rule or the set of allowed folders. It is a metadata overlay. IMAP folders exist
on the server whether or not they have an account metadata entry. Policy can
then expose all, some, or none of those server folders to agents.

Folder classification is account metadata. It is not an access decision by
itself, but policy can reference it through selectors such as `allow_kind` and
`deny_kind`. Operators should be able to assign folder metadata directly to
exact folder names and through pattern rules such as "everything under Archive
is archive" or "derive the archive year from the folder name".

## Proposed Config Shape

The exact field names can still change during implementation, but the model
should be close to:

```yaml
# arbiter/account/imap/personal.yaml
default_folder: INBOX
folders:
  INBOX:
    description: Primary inbox.
  Sent:
    description: Sent mail.
    kind: SENT
  Trash:
    description: Deleted mail.
    kind: TRASH
  "Archives.{range}.{20??:year}":
    description: Archived mail from {year}.
    kind: ARCHIVE
  "Projects.*":
    description: Project mail for {0}.
```

The `folders` entries above annotate matching IMAP folders. They are not an
exhaustive folder list; the server may expose folders with no matching metadata.

```yaml
# arbiter/policy/imap/personal_policy.yaml
folder_access:
  rules:
    - allow_glob: "*"
    - deny_exact: Junk
    - deny_regex: "^Archives[.][^.]+[.]20[0-1][0-9]$"

operation_defaults:
  read: allow
  search: allow
  move: false
  mark_read: deny
  delete: deny
  folder_append: deny
  system_flags:
    SEEN: read_only
    FLAGGED: read_only
    ANSWERED: read_only
    DELETED: read_only
    DRAFT: read_only
  user_flags: {}

folders:
  INBOX:
    system_flags:
      SEEN: read_write
      FLAGGED: read_write
    user_flags:
      triaged: read_write
    move:
      allowed: true
      to_kind:
        - ARCHIVE
  Sent:
    read: allow
    search: allow
    folder_append: allow
    system_flags:
      SEEN: read_write
  "Archives.*":
    read: allow
    search: allow
    move: false
    delete: deny
    system_flags:
      SEEN: read_only
```

Notes:

- `folder_access` is policy, not account metadata.
- Folder access is an ordered series of allow and deny rules matching IMAP folder
  names or resolved folder metadata. Rules are evaluated in order, and every
  matching rule is applied to the effective access decision for that folder.
- Rule keys should make the match mode explicit: `allow_exact`, `deny_exact`,
  `allow_glob`, `deny_glob`, `allow_regex`, `deny_regex`, `allow_kind`, and
  `deny_kind`.
- Shorthand `allow` and `deny` aliases should not exist. Every rule must choose
  an explicit selector mode.
- `folder_access.rules` must be explicitly defined and must not be empty.
- The first rule must be either `allow_glob: "*"` or `deny_glob: "*"`. This
  forces operators to choose the policy baseline deliberately.
- A rule list that starts with `allow_glob: "*"` is a default-open model that can
  then deny selected folders.
- A rule list that starts with `deny_glob: "*"` is a default-closed model that
  can then allow selected folders.
- Each rule should have exactly one rule key.
- Because the first rule must match every folder, every discovered or referenced
  IMAP folder has a baseline decision before later matching rules can update it.
- Ordered composition is intentional, documented behavior. Operators can start
  broad and end specific, and diagnostics should report the matching rule chain
  and the final effective decision after applying it.
- Do not require rule lists to be monotonic. A default-closed policy may allow a
  broad folder family and then deny one sensitive child folder; a default-open
  policy may deny a broad folder family and then allow one safe child folder.
  Enforcing only-broaden or only-narrow ordering would make those useful
  exceptions impossible.
- Validation should enforce structure, not policy taste. Lint/check output can
  warn about redundant rules, rules that match no discovered IMAP folders, and
  broad rules that overturn earlier specific rules, but runtime semantics should
  still be deterministic ordered composition.
- Account `folders` may contain exact folder entries and folder metadata
  templates keyed by patterns. These entries do not create mailboxes or grant
  access on their own; they describe existing IMAP folders whose names match the
  pattern. Access changes only when policy rules reference the resulting
  metadata.
- `kind` values should be the `IMAPFolderKind` enum, serialized as uppercase
  names such as `INBOX`, `ARCHIVE`, `SENT`, `TRASH`, `DRAFTS`, `JUNK`,
  `FLAGGED`, and `ALL`.
- Folder metadata entries are also evaluated in order. Every matching entry is
  merged into the effective metadata; later entries replace only the fields they
  explicitly set. This applies to exact entries, glob-like pattern entries, and
  capture-pattern entries.

## Selector Syntax

Folder access selectors:

- Exact selectors match the full IMAP folder name exactly.
- Glob selectors are shell-style globs over the full IMAP folder name, not
  IMAP-server discovery globs. This avoids depending on IMAP hierarchy
  delimiter behavior in the first pass.
- Regex selectors are matched against the full IMAP folder name. Use anchors
  when partial matches would be surprising.
- Kind selectors match the folder's effective account metadata after exact and
  pattern metadata entries have been merged. They accept only `IMAPFolderKind`
  enum values.

Exact, glob, regex, and kind selectors are all supported because folder access
needs both precise single-folder rules and intentional pattern rules. Pattern
matching is compatible with access policy as long as the selector mode is
explicit in the rule key. Exact, glob, regex, and kind rules share the same
ordered rule list. Operators can quote names with dots, slashes, or spaces using
normal YAML quoting.

Folder metadata patterns:

- `.` is a hard segment delimiter for default metadata matchers.
- Bare `*` matchers capture zero or more non-dot characters positionally. For
  example, `Projects.*` captures the project name as `{0}`, but does not match
  `Projects.A.B`.
- Named capture blocks bind a matcher result to a name. `{name}` is shorthand
  for a one-segment wildcard capture.
- Explicit matcher blocks use `{glob:name}`. `*` matches zero or more non-dot
  characters, `?` matches one non-dot character, and `[0-9]` style character
  classes match one character. Use `**` when a capture intentionally needs to
  span dots.
- Capture names ending in `?` are optional. When an optional capture is followed
  by a literal `.`, the capture and that delimiter are optional together. For
  example, `Archives.{**:prefix?}.{year}` matches both `Archives.2026` and
  `Archives.2020-2029.2026`, with `{year}` bound to the last segment.
- For example, `Archives.{range}.{20??:year}` captures `range` before the next
  literal `.` and captures a four-character year-like suffix beginning with
  `20`.
- Literal text in the pattern matches exactly.
- Pattern matching is over the full folder name.
- Captures can be referenced from metadata strings. In
  `Archives.{range}.{20??:year}`, `{year}` resolves to the named capture. In
  `Projects.*`, `{0}` resolves to the first positional capture.

## Folder Access Semantics

Folder access is the first authorization gate for every IMAP action. The
resolver may compute account metadata first so `allow_kind` and `deny_kind`
rules can match, but metadata resolution does not expose or select a folder by
itself. Folder access controls whether an IMAP folder can be mentioned, listed,
searched, selected, or used by any operation before operation-specific decisions
are evaluated.

Accessible folders:

- appear in `imap:list_folders`
- can be found by `imap:search_folders`
- can be used as `folder` or `destination_folder` arguments
  only if the relevant folder operation also allows that action
- can be selected during account readiness tests

Inaccessible folders:

- do not appear in folder listing/search results
- are rejected as inaccessible before operation-specific policy or IMAP client
  selection
- should not be probed during readiness tests
- should not be selected as an inferred sent-copy destination

Default folder handling:

- `default_folder` must resolve to an accessible IMAP folder for tools that omit
  `folder`.
- If `default_folder` is inaccessible by policy or does not exist on the server,
  that IMAP account should be marked unhealthy or unavailable with a clear
  message, and operations for that account should be unavailable.
- Account readiness is a generic Arbiter concern, not an IMAP-specific failure
  mode. Any plugin account can be misconfigured or unreachable. A single account
  readiness failure must not crash or prevent startup of the whole Arbiter
  server or unrelated plugins/accounts.
- Config composition, schema loading, and other failures that prevent Arbiter
  from constructing the server configuration at all may still be fatal. That is
  a separate boundary from plugin/account readiness.

Sent folder handling:

- Preserve the existing sent-copy destination inference based on folder metadata
  such as `kind: SENT`.
- This design only adds the new IMAP policy gate around that existing behavior:
  the inferred sent-copy destination must be accessible and must allow
  `folder_append`.
- If a sent folder is inaccessible or append-denied, SMTP sent-copy
  should report a normal sent-copy failure/skip through the existing SMTP
  sent-copy result path.

Append handling:

- IMAP APPEND is the protocol mechanism for adding a complete message to a
  folder.
- `folder_append` controls whether Arbiter may append a message to a folder.
- The in-scope caller is the existing SMTP sent-copy path. Draft workflows are
  out of scope for this design; they are higher-level mail workflows that may
  later compose IMAP append, SMTP send, and cleanup policy.

## Folder Operation Semantics

The current account-wide booleans should become policy defaults with explicit
operation policy. Per-folder rules override those defaults by composing matching
entries in order.

Scalar operation decision values for `read`, `search`, `mark_read`, `delete`,
and `folder_append`:

- `deny`: the operation is not allowed.
- `allow`: the scalar operation gate passes. Operation-specific gates, such as
  move destination checks or soft-delete trash resolution, may still reject the
  request.

Suggested operation mapping:

- `list_messages`: requires accessible folder and `read != deny`.
- `get_message`: requires accessible folder and `read != deny`.
- `get_attachment`: requires accessible folder and `read != deny`.
- `search_messages`: requires accessible folder and `search != deny`.
- `move_message`: requires accessible source and destination folders. If the
  destination is `kind: TRASH`, it is handled as soft delete and requires
  `delete != deny` on the source folder. Otherwise it requires
  `move.allowed: true` on the source folder; structured move policies also
  require the destination to match the source folder's effective `move.to_*`
  selectors, while boolean `move: true` allows any accessible non-delete
  destination.
- `get_message_flags`: requires accessible folder and `read != deny`; returns
  only flags whose effective flag mode is not `hidden`.
- `update_message_flags`: requires accessible folder and `read_write` access to
  every flag being added or removed. The operation should accept additive and
  subtractive lists, not a replace-all flag set, so it cannot accidentally remove
  hidden or unrelated flags.
- `mark_message_read`: convenience operation for the standard `seen` flag;
  requires accessible folder, `system_flags.SEEN: read_write`, and
  `mark_read != deny`.
- `delete_message`: requires accessible source folder and `delete != deny`.
  By default, it moves the message to an accessible folder classified as
  `kind: TRASH`. With explicit `permanent: true`, it may permanently delete the
  message instead.
- `append_message`: requires accessible folder, `folder_append != deny`, and
  `read_write` access to every flag supplied with the append request.
  Initial callers include internal SMTP sent-copy append and future explicit
  append operations.

Flag policy should follow the same default plus per-folder override model as
operation policy. `system_flags` and `user_flags` may be configured in
`operation_defaults` and overridden by matching folder policy entries. Per-folder
`system_flags` entries may omit fields; omitted fields inherit from the current
effective policy through the same ordered merge as the rest of folder policy.

Supported system flag modes:

- `hidden`: do not expose the flag.
- `read_only`: expose the flag but do not allow mutation.
- `read_write`: expose the flag and allow mutation.

User flags are opt-in. A configured user flag may be `read_only` or
`read_write`. An unconfigured user flag is hidden by omission: it is not
returned by `list_messages`, `get_message`, or `get_message_flags`, and it
cannot be added or removed by `update_message_flags`.

`mark_read` remains a higher-level operation gate for `imap:mark_message_read`;
the operation also requires `system_flags.SEEN: read_write` on the effective
folder policy.

General flag access and mutation should be exposed at the plugin operation
level. `list_messages` and `get_message` continue to include visible flags, and
`get_message_flags` provides a narrow flag-only read. `update_message_flags`
should support adding and removing standard or configured user flags, subject to
the effective per-folder flag policy. `mark_message_read` can remain as a
convenience operation because marking read/unread is common, but it should be
implemented in terms of the same flag policy decision as `update_message_flags`.

The `move` policy can be either a boolean shorthand or a structured node.

Boolean shorthand:

```yaml
move: false
```

is equivalent to:

```yaml
move:
  allowed: false
```

The structured node has two responsibilities:

- `allowed`: whether messages may leave the source folder.
- `to_*` selectors: where messages from that source folder may be moved.

Suggested `move` shape:

```yaml
move:
  allowed: true
  to_kind: ARCHIVE
  to_glob:
    - Archives.*
    - Projects.*
```

If `move.allowed` is false, `move.to_*` selectors are ignored. If
`move.allowed` is true, the resolved destination folder must satisfy the
effective `move.to_*` selectors. Structured `move` policy with
`allowed: true` must define at least one `to_*` selector; otherwise the operator
should use the boolean shorthand `move: true` for broad move permission.

When folder policy entries compose, `move` is merged by field. Later matching
entries replace `allowed` when they set it. `move.to_*` selector fields such as
`to_exact`, `to_glob`, `to_regex`, and `to_kind` are also merged by field; a
later entry that sets the same selector field replaces that field's list, while
different selector fields remain in the effective `move` policy.

Each `move.to_*` selector may be written as either a single scalar value or a list
of values. Configuration loading should normalize both forms to lists before
validation and policy evaluation:

```yaml
move:
  allowed: true
  to_kind: ARCHIVE
  to_exact:
    - Projects.ClientA
    - Projects.ClientB
```

With Hydra 1.4 / OmegaConf 2.4, structured config fields can use inline
`list[str] | str | None` annotations for these selectors. Keep the public schema
typed that way, then normalize scalar and list forms to `list[str]` before
policy evaluation.

The public `move` field should be typed as `bool | MovePolicyConfig`. Normalize
`move: false` to `MovePolicyConfig(allowed=False)` and `move: true` to
`MovePolicyConfig(allowed=True)` before policy evaluation. Boolean
`move: true` means broad move permission to any accessible non-delete
destination. Use the structured form when the policy needs destination
constraints.

`move_message` should not also require `delete != deny` on the source folder.
Even though an IMAP move removes the message from the source mailbox, the policy
intent is different: `move.allowed` allows relocation out of a folder, while
`delete` allows delete-style operations. Implementations that perform move as
copy plus source removal can use the granted `move.allowed` decision for the
internal source removal instead of requiring separate delete permission.

Destination constraints are evaluated against the destination folder name and
metadata after folder access. A destination can match by exact name, glob, regex,
or metadata kind. Kind-based matching only works when account metadata assigns a
kind to the destination folder; otherwise exact/glob/regex matching should be
used.

Moving a message to a folder classified as `kind: TRASH` should be treated as a
delete user action, not a normal move. It should follow the same policy path as
`delete_message` with `permanent: false`: require `delete != deny` on the source
folder, require an accessible trash destination, and not require `move.allowed`
or a matching `move.to_*` selector. Normal `move` policy is for relocation such
as archive, project folders, or other non-delete destinations.

Delete handling:

- `delete_message` defaults to soft delete: move the message to an accessible
  folder whose effective metadata is `kind: TRASH`.
- A policy that allows soft delete must leave at least one trash folder
  accessible through `folder_access`. If no folder is designated `kind: TRASH`,
  or if all trash folders are inaccessible, non-permanent `delete_message` should
  fail with a clear policy/configuration error.
- `delete_message` with explicit `permanent: true` performs a hard delete, such
  as IMAP `\Deleted` plus `EXPUNGE`, and does not require a trash destination.
- Neither soft delete nor permanent delete requires `folder_append`; APPEND is
  for adding a new complete message to a folder, not moving or deleting an
  existing message.

## Resolution Order

For every IMAP operation:

1. Resolve the account.
2. Resolve the policy referenced by the account.
3. Resolve the folder name from explicit input or `default_folder`.
4. Resolve account metadata for the folder by applying exact and pattern
   metadata entries. Missing metadata is allowed and means empty description and
   no `kind`.
5. Check the ordered folder access rules. The first configured rule must be
   `allow_glob: "*"` or `deny_glob: "*"`. Start with "inaccessible", then apply
   every matching rule in order. Each matching allow rule sets the effective
   folder access decision to accessible; each matching deny rule sets it to
   inaccessible. The result after the full matching chain is the effective
   access decision.
6. Resolve effective per-folder policy:
   - start with policy defaults
   - apply matching folder policy entries in order
   - merge every matching entry into the effective policy
   - for each operation, later matching entries replace only that operation's
     decision
7. Resolve the operation-specific policy.
8. For scalar operation decisions, reject when the decision is `deny`.
9. For `delete_message`, if `permanent: true`, hard delete the message without
   resolving a trash destination. Otherwise resolve an accessible destination
   folder classified as `kind: TRASH` and move the message there.
10. For `move_message`, resolve the destination folder and check that it is
   accessible. If the destination folder is classified as `kind: TRASH`, treat
   the request as `delete_message` with `permanent: false`: require
   `delete != deny` on the source folder and bypass normal move policy.
   Otherwise, reject if `move.allowed` is false. If the source folder's effective
   move policy has `to_*` selectors, check the destination against them. If the
   policy came from boolean `move: true`, allow any accessible non-delete
   destination.
11. If the operation policy allows the request, call the IMAP client.

Determinism matters. Preserve mapping order and make ordered composition part of
the public contract for folder access rules, folder metadata patterns, and
per-folder policy overrides. Treat overlapping, redundant, contradictory, and
malformed user configuration as expected input to validation and tests, not as
edge cases left to incidental behavior.

Implementations may cache the folder access decision per IMAP folder after the
first evaluation. This cache is only for the access gate, not for per-operation
policy. The cache key should include the resolved account folder metadata and
the folder access policy, because kind-based access rules depend on metadata. If
the policy surface ever becomes mutable at runtime, any folder access policy or
metadata mutation that can affect access must invalidate the cached decisions
before subsequent operations run.

## Discovery Output

Account discovery should expose enough policy context for agents to avoid
probing inaccessible folders.

For `arbiter plugins imap account <account>`, include:

- accessible folder count
- default folder
- brief operation summary

For `list_folders` and `search_folders`, include each folder's effective
operation decisions:

```json
{
  "name": "Sent",
  "description": "Sent mail.",
  "kind": "SENT",
  "default": false,
  "operations": {
    "read": "allow",
    "search": "allow",
    "move": {
      "allowed": false
    },
    "delete": "deny",
    "folder_append": "allow",
    "system_flags": {
      "SEEN": "read_only",
      "FLAGGED": "read_only"
    },
    "user_flags": {}
  }
}
```

Listing and search should discover folders from the IMAP server, apply
`folder_access`, and then overlay any matching account metadata. A server folder
with no metadata entry can still be listed if policy allows it.

Avoid exposing inaccessible folder names in denial messages or discovery output
when the folder came from user input. Use generic wording such as "folder is not
accessible for account".

## Policy Check

Filtered discovery and debuggability need different surfaces.
`list_folders` and `search_folders` should not enumerate inaccessible folders,
but a user needs a way to ask "why can't I see FolderX?" without editing config
blindly.

A policy check surface should be generic across Arbiter plugins. It can use the
same protocol shape as the operation being checked: the caller names an
operation and supplies the same arguments it would have supplied to execute that
operation, but asks Arbiter to evaluate whether the operation is allowed instead
of running it.

For IMAP, checking `imap:get_message` would use the `get_message` argument
schema, checking `imap:move_message` would use the `move_message` argument
schema, and checking `imap:append_message` would use the append argument schema.
This path should work even when the referenced folder is not accessible. It
should use the same resolver as runtime operations.

The generic check path is not normal resource discovery or execution:

- It evaluates only the resources named in the supplied operation arguments.
- It does not list sibling resources or suggest other inaccessible resources.
- It does not call the external service or mutate state.
- It can report whether each supplied resource is accessible or denied by
  policy.
- It can report the matching rule chain and final access decision.
- It can report effective resource metadata that applies to that resource, even
  when access is denied, subject to the plugin's redaction rules.
- It can report whether the named operation would be allowed after resource
  access and why.
- For IMAP `move_message`, it should report both source access/policy and
  destination access/policy, including whether the destination matched the
  source folder's `move.to_*` selectors.

Suggested client CLI shape, using operation-shaped arguments:

```bash
arbiter op check imap:get_message --args '{"account":"personal","folder":"FolderX","message_id":"123"}'
arbiter op check imap:move_message --args '{"account":"personal","folder":"INBOX","message_id":"123","destination_folder":"Archives.2020-2029.2026"}'
arbiter op check imap:append_message --args '{"account":"personal","folder":"Sent"}'
```

The same idea can be exposed through the regular client protocol as a `check`
request around a normal operation payload:

```json
{
  "check": {
    "operation": "imap:move_message",
    "arguments": {
      "account": "personal",
      "source_folder": "INBOX",
      "message_id": "123",
      "destination_folder": "Archives.2020-2029.2026"
    }
  }
}
```

An IMAP-specific alias can exist for folder-only debugging if it is useful, but
it should be only a thin wrapper around the generic operation-shaped check path,
not a separate bulk preview surface:

```bash
arbiter imap folders check personal FolderX
```

Default output should stay compact and center on:

1. Is this operation allowed?
2. If not, what policy gate denied it?

For folder access failures, compact output should also include the matching
access rule chain because that is the shortest useful answer to "why not?".

Possible JSON output for a denied read:

```json
{
  "operation": "imap:get_message",
  "allowed": false,
  "why_not": "folder FolderX is denied by folder_access",
  "access_rules": [
    {
      "index": 1,
      "rule": {"deny_glob": "*"},
      "decision": "deny"
    },
    {
      "index": 4,
      "rule": {"allow_exact": "FolderX"},
      "decision": "allow"
    },
    {
      "index": 7,
      "rule": {"deny_regex": "^FolderX$"},
      "decision": "deny"
    }
  ]
}
```

For an access failure, the default check response should include all matching
access rules in order. Non-matching rules should be omitted by default. The last
matching decision in the list is the effective access decision.

Possible JSON output for a denied move:

```json
{
  "operation": "imap:move_message",
  "allowed": false,
  "why_not": "destination folder Projects.ClientA is not allowed by INBOX move policy"
}
```

Verbose output can include the detailed rule chain, effective metadata, and
per-gate evidence for operators or debugging:

```json
{
  "operation": "imap:move_message",
  "allowed": false,
  "why_not": "destination folder Projects.ClientA is not allowed by INBOX move policy",
  "evidence": {
    "source_folder": {
      "name": "INBOX",
      "access": "allowed",
      "move": {
        "allowed": true,
        "to_kind": "ARCHIVE"
      }
    },
    "destination_folder": {
      "name": "Projects.ClientA",
      "access": "allowed",
      "kind": null
    },
    "failed_gate": "destination_selector"
  }
}
```

If this check is exposed to agents, it should be a deliberate diagnostic tool
rather than part of ordinary discovery. A limited client-facing form can be safe
if it requires an exact operation payload supplied by the user or by a failed
operation, and if it does not enumerate denied resources. Bulk folder-access
preview is out of scope for v1; operation-shaped checks provide vertical slices
of the same policy behavior without adding an account-wide denied-folder audit
surface.

## Compatibility Plan

Initial compatibility defaults:

- Existing policies without folder access fail configuration validation until
  the operator adds an explicit access policy.
- Existing server folders continue to list and search only when the explicit
  folder access policy allows them. Existing account folder entries continue to
  provide metadata for matching folders.
- Existing sent-copy destination inference continues to work, with the added
  requirement that the inferred destination is accessible and
  `folder_append != deny`.

Bootstrap policy should not silently create a deny-all IMAP account. Instead,
plugin bootstrap should support named template variants so the operator chooses
one of the explicit access baselines.

Suggested CLI shape:

```bash
arbiter-server bootstrap plugin imap policy --list-variants
arbiter-server bootstrap plugin imap policy personal_policy --variant default-open
arbiter-server bootstrap plugin imap policy personal_policy --variant default-closed
```

Variant listing should include a short description, for example:

```text
default-open    allow all folders first, then add deny rules
default-closed  deny all folders first, then add allow rules
```

The bootstrap contract should be generic across plugins. A plugin can expose
zero or more variants per bootstrap object kind. When variants exist, the server
should be able to list them with descriptions and pass the selected variant back
to the plugin when rendering the template.

Default-open template:

```yaml
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
  system_flags:
    SEEN: read_only
    FLAGGED: read_only
    ANSWERED: read_only
    DELETED: read_only
    DRAFT: read_only
  user_flags: {}
folders: {}
```

Default-closed template:

```yaml
folder_access:
  rules:
    - deny_glob: "*"
operation_defaults:
  read: allow
  search: allow
  move: false
  mark_read: deny
  delete: deny
  folder_append: deny
  system_flags:
    SEEN: read_only
    FLAGGED: read_only
    ANSWERED: read_only
    DELETED: read_only
    DRAFT: read_only
  user_flags: {}
folders: {}
```

The implementation does not keep top-level `allow_read` style aliases. This is pre-release behavior, and `operation_defaults` plus per-folder policy are the required policy shape.

## Tests

Add focused unit coverage for these behavior groups:

- `folder_access.rules` validation: required, non-empty, first rule must be
  `allow_glob: "*"` or `deny_glob: "*"`, exactly one rule key per entry,
  string exact/glob/regex patterns, valid regex patterns, and valid kind enum
  values.
- Access composition: default-open and default-closed policies, overlapping
  exact/glob/regex/kind rules, redundant/contradictory rules, and final
  decision from all matching rules in order.
- Account metadata overlay: exact entries, wildcard entries, named captures,
  positional captures, explicit matcher blocks such as `{20??:year}`, merge
  order, folders with no metadata, and proof that metadata neither creates
  folders nor grants access unless policy explicitly references it.
- Operation policy composition: defaults plus matching folder overrides,
  scalar `deny|allow` decisions, and per-folder `read`, `search`, `delete`,
  `mark_read`, `folder_append`, `system_flags`, and `user_flags`.
- Flag APIs: `list_messages`, `get_message`, and `get_message_flags` expose only
  visible flags; unconfigured user flags are hidden by omission;
  `update_message_flags` requires `read_write` for every changed flag and never
  performs replace-all mutation.
- Move policy: boolean and structured forms, structured `allowed: true`
  requiring at least one `to_*` selector, selector normalization from scalar or
  list, source `move.allowed`, destination access, destination selector
  matching, broad boolean moves, and the special case that moving to
  `kind: TRASH` is governed by soft-delete policy.
- Delete policy: default delete resolves an accessible `kind: TRASH` destination
  and moves the message there; `permanent: true` hard-deletes without a trash
  destination; neither path requires `folder_append`.
- Runtime gates: inaccessible folders are rejected, `list_folders` and
  `search_folders` hide inaccessible folders, readiness probes only accessible
  folders, and an inaccessible default folder makes only that IMAP account
  unavailable.
- Sent-copy: existing destination inference is preserved, and the inferred
  append destination must be accessible and allow `folder_append`.
- Diagnostics: client policy checks return `allowed: true|false`, include
  matching access rules for access failures, avoid denied-folder enumeration,
  and match runtime denial behavior.
- Bootstrap variants: plugin bootstrap can list variants with descriptions and
  render the selected IMAP policy variant.

Add integration coverage for at least one accessible folder and one denied
folder, sent-copy append allowed and denied cases, policy check output matching
runtime denial behavior.

## Out Of Scope

- Draft lifecycle workflows. IMAP provides primitives such as APPEND and flags,
  but "save draft, review, send later, then clean up" is a higher-level mail
  workflow, not an IMAP folder-policy primitive.
- Redesigning SMTP sent-copy destination inference. This design only adds
  access and append policy gates around the existing behavior.
- Bulk operator folder-access preview. The client `op check` surface is the v1
  diagnostic mechanism. Once Arbiter has authentication and an operator
  authorization boundary, a broader operator-only preview can be added without
  exposing denied folder enumeration to ordinary clients.

## Implementation Scope

The implementation should deliver:

- the new account metadata and IMAP policy schema
- a shared resolver for folder metadata, folder access, and per-folder operation
  policy
- runtime enforcement for listing/search, message operations, readiness checks,
  flag access/mutation, and SMTP sent-copy append
- client policy checks using the same resolver
- bootstrap variant support, updated templates, user documentation, and focused
  test coverage

Detailed dataclass layout, resolver decomposition, and test sequencing belong
to the implementation plan rather than this design.
