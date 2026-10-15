---
title: Configuration Model
---

Arbiter treats configuration as the deployment authority. Operators
declare which services exist, which accounts are available, which policies
apply, and where credentials are referenced. Agents consume the approved
Arbiter operations that result from that configuration.

## Security note

Agents should interact with services through Arbiter, normally by using the
`arbiter` client. They must not control the config directory or Arbiter
deployment, because changing those inputs lets them circumvent the policy they
are supposed to be constrained by.

It is recommended to deny agents direct access to protected services and
credentials, typically by sandboxing the agent. For the full trust model, see
[Security Model](security.md).

## Example deployment directory

Server config is part of the server deployment. In a staged or deployed layout,
YAML config lives under `conf/`, while local environment-backed values live in
`.arbiter.env` beside that `conf/` directory.

An example server deployment directory looks like this:

This can look like a lot at first glance, but operators do not need to create
the structure by hand. `arbiter-server bootstrap`, `arbiter-server config
activate`, and `arbiter-server env bootstrap` create the scaffold, activate
accounts, and maintain the local env file.

```text
arbiter-deployment/
├── .arbiter.env                    # local environment-backed values
└── conf
    ├── arbiter
    │   ├── account
    │   │   ├── imap
    │   │   │   ├── bot.yaml        # IMAP account owned by the bot
    │   │   │   └── personal.yaml   # owner IMAP account
    │   │   └── smtp
    │   │       ├── bot.yaml        # SMTP account owned by the bot
    │   │       └── personal.yaml   # owner SMTP account
    │   ├── policy
    │   │   ├── imap
    │   │   │   ├── bot.yaml        # bot policy with full IMAP access
    │   │   │   └── personal.yaml   # restricted access to owner IMAP
    │   │   └── smtp
    │   │       ├── bot.yaml        # bot policy with full SMTP access
    │   │       └── personal.yaml   # restricted access to owner SMTP
    ├── arbiter-server.yaml         # root server config and base composition
    └── plugins.yaml                # active account and policy selections
```

The config directory is normal files on disk. It should be owned by the
deployment operator, not by the agent using the service.

## Basic model

`arbiter-server.yaml` is the default root server config. It selects the server
schema, imports `plugins.yaml`, and contains the deployment's server settings.

`plugins.yaml` selects the active accounts and policies:

```yaml title="conf/plugins.yaml"
defaults:
  - arbiter/account:
    - smtp/bot
  - arbiter/policy:
    - smtp/bot_policy
  - _self_
```

Operators normally change `plugins.yaml` with `arbiter-server config activate`
and `arbiter-server config deactivate`, not by editing the defaults list by
hand.

Use `--config-name foo` when the root config file is named `foo.yaml`:

```bash
arbiter-server --config-name foo config show
```

Plugin-owned account and policy files live under
`arbiter/account/<plugin>/` and `arbiter/policy/<plugin>/`. Plugins define the
schema for those files, so SMTP, IMAP, and future plugins can each own their
service-specific config shape.

At startup, `arbiter-server serve` composes the active config, validates it
against server and plugin schemas, and only then exposes tools.

## Command-line overrides

Commands that compose config accept Hydra/OmegaConf-style overrides. Use
`config show` without overrides when you want to inspect the configured state:

```bash
arbiter-server config show
```

`config show` and `config check` can also accept optional overrides when you
want to preview or validate a temporary change for this invocation, without
changing the underlying config files.

Use an override with `serve` when you want a one-off runtime change without
editing the config files:

```bash
arbiter-server serve arbiter.server.bind.port=8075
```

That command starts the server on port `8075` for that process only.

Hydra also supports config group overrides. Arbiter uses config groups for
accounts and policies, but normal account activation should go through
`arbiter-server config activate` so `plugins.yaml` remains consistent.

## Core Resource Limits

Core Arbiter owns the limits under `arbiter.limits`. Each plugin owns its
service-specific limit schema and enforcement. These are separate domains;
plugin limits do not inherit from or override core limits.

Operation requests have a default body ceiling of 1 MiB (1,048,576 bytes).
The ceiling counts the complete encoded JSON body, including argument names
and inline message content. Configure it in the server deployment:

```yaml
arbiter:
  limits:
    max_request_body_bytes: 1048576
    max_artifact_bytes: 52428800
    max_artifact_storage_bytes: 1073741824
    max_artifact_count: 2000
    max_blocking_workers: 8
```

The value must be a positive integer; zero does not disable the limit.
Oversized bodies return HTTP `413` with `validation_error` before JSON parsing
or plugin execution. Arbiter checks received bytes even when `Content-Length`
is absent or understates the body size. The response reports the configured
ceiling in `error.details.max_request_body_bytes`.

Artifact downloads are responses and are not restricted by this request-body
ceiling.

`max_blocking_workers` bounds synchronous operations and artifact I/O in one
native server, shared across plugins. It defaults to eight and must be a positive
integer. Plugin invocation, artifact materialization and operation-response
encoding run off the HTTP event loop, keeping health and discovery responsive.
When capacity is full, Arbiter returns HTTP `503` with `resource_exhausted`,
`transient: yes` and `replay: safe`; retry later. Excess work is not placed in an
unbounded waiting queue.

Artifact metadata and HEAD storage work use the same pool. A content GET holds
one slot through lookup, its one-time claim, file reads, network backpressure,
and file closure. Slow downloads therefore share capacity with operations.
Chunks are read off the event loop, one at a time, without buffering the whole
artifact. HEAD and metadata inspection do not consume the artifact.

Cancelling or disconnecting an HTTP request does not stop an already-running
plugin operation or undo its effects. Its capacity stays occupied until the
underlying work finishes. Use an operation's retry guidance and idempotency
support before retrying a request whose outcome is unknown.
Streaming disconnects stop pending transport waits.
A blocking file read/lock wait still owns its slot until it finishes and any
open file is closed. Disconnecting after a content GET claims an artifact does
not restore its one-time capability.
Server shutdown stops admission and cancels work that has not started. Running
operations can continue while the process is alive; Arbiter's daemon workers do
not prevent process exit. Exiting may interrupt an unfinished operation, so its
external outcome can remain unknown.

This is application admission, not a global connection or process-memory cap.
Idle connections, keepalive and transport buffers require separate deployment
limits. The standard launcher does not impose a new global connection ceiling;
such a ceiling would also affect health and discovery traffic.

`max_artifact_bytes` limits creation of each stored artifact to 50 MiB
(52,428,800 bytes) by default, regardless of which plugin produces it. It must
be a positive integer. Oversized artifacts are rejected before hashing or
writing files, with HTTP `403`, `policy_denied`, and
`error.details.max_artifact_bytes`. Files within the limit are stored and served
in full; the limit does not truncate them. Operation discovery publishes the
configured ceiling in `artifact_policy.max_artifact_bytes`.

`max_artifact_storage_bytes` caps total retained artifact file bytes across all
plugins at 1 GiB (1,073,741,824 bytes) by default. It must be a positive integer.
It includes payloads, metadata, and metadata space allocated for later access
updates. Expired artifacts and incomplete creations are reclaimed before new
artifacts are admitted. An artifact whose payload plus reserved metadata exceeds
the total cap even in an empty store returns HTTP `403`, `policy_denied`, and
`retry.transient: no`; waiting for cleanup cannot make that creation fit.
Otherwise, insufficient remaining capacity returns HTTP `503`,
`resource_exhausted`, the message "Artifact storage exhausted; try again later.",
and `retry.transient: yes`. Retry only when replaying the enclosing operation
is safe; the storage error reports `retry.replay: unknown`. It also reports the
configured cap in `error.details.max_artifact_storage_bytes`, and operation
discovery publishes `artifact_policy.max_artifact_storage_bytes`.

Admission and writes are coordinated across store instances and processes
sharing the same local storage root. Existing files count after restart; lowering
the cap does not evict unexpired artifacts. New creation stays blocked until
enough capacity is reclaimed. Cleanup is opportunistic on artifact operations.

`max_artifact_count` caps retained artifacts across all plugins at 2,000 by
default. It must be a positive integer. An artifact's payload, metadata, and
consumption marker together count as one artifact. Downloads consume access
tokens but do not release count capacity; cleanup must remove the retained
artifact first. Expired and incomplete artifacts are reclaimed before admission.
The count is restored from storage after restart, and lowering the limit does
not evict existing artifacts or prevent their downloads.

Creation must fit both the count and byte budgets. A full count budget returns
the same HTTP `503`, `resource_exhausted`, and retry guidance as exhausted byte
capacity, with `error.details.max_artifact_count` alongside the storage byte cap.
Operation discovery publishes `artifact_policy.max_artifact_count`.

This is a retained-file byte budget, not a reservation of free disk space.
Temporary metadata replacement, filesystem allocation and directory overhead,
other plugin state, mail fetching and decoding, and plugin
execution need separate bounds in their own domains. Plugin-specific limits
do not change core limits. These controls
apply to trusted plugins using Arbiter's storage API; they do not isolate
same-process plugin code from the filesystem.

## Server TLS

Arbiter serves HTTPS by default. The server TLS source is configured under
`arbiter.server.tls.source` as an OmegaConf enum, so use uppercase enum names
in YAML and command-line overrides:

```yaml
arbiter:
  server:
    tls:
      source: SELF_SIGNED
```

`SELF_SIGNED` is the default. Arbiter generates and reuses a local certificate
and private key under the server storage directory. This prevents cleartext
traffic on the Arbiter port, but it does not make the certificate publicly
trusted.

Docker deployments store this under `data/server/tls` by default. Override
`arbiter.storage.server_data_dir` when server-owned runtime state should live
somewhere else.

Use `CERT_FILES` when you already have certificate files:

```yaml
arbiter:
  server:
    tls:
      source: CERT_FILES
      cert_file: /etc/arbiter/tls/arbiter.crt
      key_file: /etc/arbiter/tls/arbiter.key
```

The private key file must not be group- or world-readable on Unix-like
systems.

## Accounts

An account describes how a plugin reaches an upstream service. Account config
usually includes connection settings, credential references, display metadata,
and the policy name used for that account.

Accounts are deployment-owned. Agents may select exposed accounts through
Arbiter operations, but they should not receive the service credentials behind
those accounts.

## Policies

A policy describes what the plugin should allow for an account. Policies are
plugin-specific because each service has different meaningful controls. An SMTP
policy might limit recipients or send rate; an IMAP policy might limit readable
folders or message operations.

Policies are cross-cutting config: they are separate files, but accounts refer
to them by name. Bootstrap creates a matching policy for each new account, but
multiple accounts can share one policy when that is useful.

## Scaffolding flow

Server config commands accept `--config-dir <dir>` before the subcommand. Use
the deployment's `conf/` directory:

```bash
arbiter-server --config-dir ./arbiter-deployment/conf bootstrap --server
```

For a normal deployment, start by bootstrapping the root server scaffold once:

```bash
arbiter-server --config-dir ./arbiter-deployment/conf bootstrap --server
```

To protect user config, all bootstrap commands refuse to rewrite an existing
file unless `--force` is added.

After the root scaffold exists, bootstrap plugin-owned objects as needed:

```bash
arbiter-server --config-dir ./arbiter-deployment/conf bootstrap --plugin smtp --account bot
arbiter-server --config-dir ./arbiter-deployment/conf bootstrap --plugin imap --account personal
```

Creating a config file does not make it active. Activate accounts after editing
the generated templates. Activation updates `plugins.yaml`:

```bash
arbiter-server --config-dir ./arbiter-deployment/conf config activate --plugin smtp --account bot
arbiter-server --config-dir ./arbiter-deployment/conf config activate --plugin imap --account personal
```

Use `config show` to inspect the composed result and `config check` before
serving:

```bash
arbiter-server --config-dir ./arbiter-deployment/conf config show
arbiter-server --config-dir ./arbiter-deployment/conf config check
```

## Env file management

Generated account configs should reference credentials through
`${oc.env:...}` rather than storing secrets directly in YAML. Arbiter can
help maintain the local env file named by server config:

```yaml title="conf/arbiter-server.yaml"
arbiter:
  env_file: ../.arbiter.env
```

Relative env file paths are resolved from the config directory. With the staged
deployment layout, `../.arbiter.env` resolves from `conf/` to `.arbiter.env` at
the deployment root.

```bash
arbiter-server --config-dir ./arbiter-deployment/conf env bootstrap
arbiter-server --config-dir ./arbiter-deployment/conf env check
```

When `arbiter.env_file` is set, `arbiter-server` loads that dotenv file into
its own process before composing config. This does not require Docker or an
external wrapper. Existing process environment variables take precedence over
values from the env file.

Unlike config bootstrap, `env bootstrap` is meant to be re-run. It reads the
existing env file, keeps existing values, keeps unrelated variables, and adds
any missing variables discovered in the composed config. If no env file is
configured yet, it adds `arbiter.env_file: ../.arbiter.env` to the root config
and creates that file. `env check` verifies that all referenced variables are
available before the server starts.

The generated env file depends on the config that is active when the command
runs. For example, a composed config with SMTP and IMAP bot accounts might
produce entries like:

```dotenv title=".arbiter.env"
# arbiter-imap
IMAP_BOT_ACCOUNT_HOST=
# IMAP_BOT_ACCOUNT_PORT=993
IMAP_BOT_ACCOUNT_USERNAME=
IMAP_BOT_ACCOUNT_PASSWORD=

# arbiter-smtp
SMTP_BOT_ACCOUNT_HOST=
# SMTP_BOT_ACCOUNT_PORT=587
SMTP_BOT_ACCOUNT_USERNAME=
SMTP_BOT_ACCOUNT_PASSWORD=

# miscellaneous
EXTRA_LOCAL_VALUE=keep-me
```

Different active accounts, policies, plugins, or command-line overrides can
require different environment variables.
