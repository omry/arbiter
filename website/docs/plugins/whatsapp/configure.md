---
sidebar_position: 2
---

# Configure a WhatsApp account

The optional `arbiter-whatsapp` package is under development. This page documents
the enrollment slice in this source tree. Agent message and media operations are
added by later slices. The package is not ready for production rollout. Its
release requires a server version containing all of the plugin's adapters;
older servers, including previously published development builds, do not qualify.

Use a provider account eligible for the WhatsApp Agent Platform. Follow the
provider's registration instructions and obtain its API token. Keep the token
private. Each deployment and private state root must have one owner for each
provider agent. Do not run another root or external poller for the same agent.

Create the normal configuration files:

```sh
arbiter-server --config-dir CONFIG bootstrap --server
arbiter-server --config-dir CONFIG bootstrap --plugin whatsapp --account creator
arbiter-server --config-dir CONFIG env bootstrap
```

Edit the generated account and policy files. The account contains an opaque
reference such as `credential: whatsapp/creator` and `policy: creator_policy`.
The reference is not a token or a file path. Names contain only ASCII letters,
digits, underscores and dashes. Never place the token in YAML, shell arguments,
environment variables or diagnostic output. Existing configuration edits survive
ordinary activation and an interrupted enrollment attempt.

All eight policy fields default to `true`: `updates`, `send_messages`,
`read_receipts`, `typing`, `media_ingress`, `media_download`, `media_egress` and
`media_delete`. Values must be actual booleans. Quoted strings and integers are
invalid. Policies apply per WhatsApp account and are shared by all callers that
can reach the trusted local Arbiter server. Account names do not isolate callers.
Deleting an explicit policy field restores its allow default on the next normal
activation/restart. Only the local operator can edit these settings.

Enroll through an operator terminal:

```sh
arbiter-server --config-dir CONFIG config activate --plugin whatsapp --account creator --enroll
```

Enter the token at the hidden prompt. To redirect a token from a protected local
file, add `--token-stdin` and redirect that file to standard input. Input is
limited to 8 KiB plus one optional line ending. A controlling operator terminal
is still required to display the enrollment challenge. The command does not
print the token or challenge to redirected stdout/stderr.

Enrollment first scans retained updates from zero, then displays
`Arbiter enrollment <code>` in that terminal. Send that exact text from the
provider creator chat. Old challenges and text with added whitespace do not
prove this attempt. The challenge expires after 180 seconds. Administrative
requests are paced at least 4.1 seconds apart and back off on provider 429s.

The scan permits at most 300 request starts and 1,500 seconds. With no retries,
this covers at most 29,900 entries and the final empty response. Larger backlogs
or provider failures can exhaust the budget. A scan can take up to 25 minutes.
OS DNS resolution can overrun its socket timeout; the request budget is checked
before submission after resolution. No hard resolver deadline is promised.

If setup is cancelled or fails before publication, configuration remains and the
account stays unbound. Reopen the same enrollment command for a fresh scan and
challenge. A valid committed binding makes repeated enrollment complete without
another provider request or credential prompt. Never interpret a generic failure
as proof that publication did not happen; inspect readiness first.

To replace a token for an enrolled account, use:

```sh
arbiter-server --config-dir CONFIG config activate --plugin whatsapp --account creator --replace-credential
arbiter-server --config-dir CONFIG config check --live
```

Replacement verifies the candidate token against the existing provider identity.
An empty/inconclusive scan cannot prove identity. Failed proof preserves the
committed local bytes. Provider token regeneration may already have invalidated
the old token. Verification never changes normal cursors or observed sources.
`--enroll` and `--replace-credential` are mutually exclusive and require one
explicit plugin/account selection. Ordinary activation, previews and live
configuration checks never enroll or replace credentials.

Use the same absolute configured plugin storage root for the server and CLI.
Bindings, SQLite state and persistent locks belong together on a local filesystem
with working SQLite and process-lock semantics. On POSIX the private directories
and files require owner-only permissions; Windows requires the documented
operator/SYSTEM/Administrators ACL boundary. Unsafe existing objects are rejected
and are not silently repaired. A missing/mismatched database with existing
bindings is a failure, not a fresh installation.

After an uncertain publication, retain state and temporary evidence for inspection.
Do not delete a database, copy just a token, or reset cursors/reply history while
retaining credentials. Recovery and migration must preserve the coherent binding,
store UUID and all durable safety rows. Stop every old owner before an offline
move; validate the complete protected destination before starting its owner.
