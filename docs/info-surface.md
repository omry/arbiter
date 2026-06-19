# Arbiter Info Surface

This document describes the current Arbiter discovery surface and the gaps that
matter before release. Here, "complete" means convenient for a user or agent to
understand what this Arbiter server can do without reading deployment config
files.

Convenient and complete means:

- One server command answers "which server am I connected to, and what is
  available?"
- Predictable drill-down commands answer "show me this plugin, account, policy,
  or operation."
- Account and policy information is available through the client and native HTTP
  API, not only through local deployment config.
- The surface is redacted by default: useful enough to act on, but never a raw
  credentials or deployment-internals dump.

## User Questions

The info surface should make these questions easy:

- What server am I connected to?
- Is this a staged or installed deployment?
- Which plugins are available?
- Which operations are available, and when should I use each one?
- What schema do I need to run one operation?
- Which accounts are available for each plugin?
- What is each account for?
- Which policy applies to each account?
- What does that policy allow or deny?

## Current Native HTTP Surface

The current native HTTP discovery routes are intentionally small:

```text
GET /_health_
GET /api/v1/info
GET /api/v1/plugins
GET /api/v1/plugins/{plugin_id}
GET /api/v1/plugins/{plugin_id}/accounts
GET /api/v1/plugins/{plugin_id}/accounts/{account}
GET /api/v1/plugins/{plugin_id}/policies/{policy}
GET /api/v1/plugins/{plugin_id}/operations
GET /api/v1/operations/{operation_id}
```

They expose server identity, a plugin menu, plugin details, plugin-scoped
account and policy details, per-plugin operation summaries, and one-operation
details.

`GET /_health_` only returns service liveness:

```json
{"status": "ok"}
```

A configuration problem that prevents the server from starting makes the
service unavailable, so this endpoint will not answer. Configuration problems
inside a running deployment belong to installation health checks and
diagnostics, not to this liveness response.

`GET /api/v1/info` returns server identity:

```json
{
  "name": "arbiter",
  "version": "0.9.2.dev1",
  "api_version": "0.9",
  "deployment_scope": "staged"
}
```

The server runtime also exposes local source metadata when available so users
can identify the exact code they are talking to:

```json
{
  "name": "arbiter",
  "version": "0.9.2.dev1",
  "api_version": "0.9",
  "deployment_scope": "staged",
  "source": {
    "commit": "2a1831e",
    "dirty": true,
    "build_time": "2026-06-17T03:45:12Z"
  }
}
```

`source.commit` is the repository commit used for the running server.
`source.dirty` records whether local changes were present when the server
artifact was built or staged. `source.build_time` is the UTC time that artifact
or runtime bundle was produced; it is not the request time. For package installs
where repository or build metadata is not available, source fields can be
`null` or the `source` object can be omitted.

`GET /api/v1/plugins` returns a compact plugin menu:

```json
{
  "plugins": [
    {
      "id": "smtp",
      "summary": "Send email through configured SMTP accounts."
    }
  ]
}
```

`arbiter plugins smtp` should call `GET /api/v1/plugins/smtp` and show the
plugin detail as a canonical resource first:

```json
{
  "id": "smtp",
  "summary": "Send email through configured SMTP accounts."
}
```

Canonical discovery responses should expose stable IDs and summaries, not
assume one navigation style. Client-specific hints can be added based on client
context, such as `User-Agent` or an explicit hint preference. A browser can
receive URL hints, while the native Arbiter client can receive command hints
such as `arbiter plugins smtp accounts` and `arbiter op list smtp`.

Example browser-oriented hints:

```json
{
  "id": "smtp",
  "summary": "Send email through configured SMTP accounts.",
  "hints": {
    "client": "http",
    "accounts": {"href": "/api/v1/plugins/smtp/accounts"},
    "operations": {"href": "/api/v1/plugins/smtp/operations"}
  }
}
```

Example native-client hints:

```json
{
  "id": "smtp",
  "summary": "Send email through configured SMTP accounts.",
  "hints": {
    "client": "cli",
    "accounts": {"command": "arbiter plugins smtp accounts"},
    "operations": {"command": "arbiter op list smtp"}
  }
}
```

Hints are affordances, not resource identity. Agents should prefer the Arbiter
client surface when command hints are available instead of scraping URL-shaped
fields from canonical payloads.

`GET /api/v1/plugins/{plugin_id}/operations` returns operation summaries for
one plugin. It does not include full input schemas.

`GET /api/v1/operations/{operation_id}` returns one operation's details and
input schema.

## Current Client Surface

The `arbiter` client mirrors the current native HTTP surface:

```bash
arbiter info
arbiter info server
arbiter plugins
arbiter plugins <plugin>
arbiter plugins <plugin> accounts
arbiter plugins <plugin> account <account>
arbiter plugins <plugin> policy <policy>
arbiter op list [plugin]
arbiter op desc <operation-id>
```

The client adds `server_url` locally, based on `arbiter.url`, `ARBITER_URL`, or
the default. When the server reports `deployment_scope=staged`, the client
prints a staged-deployment warning on stderr.

Bare `arbiter info` behaves like a command group entry point and prints a short
help menu. `arbiter info server` calls `GET /api/v1/info`.

`arbiter plugins <plugin>` calls `GET /api/v1/plugins/{plugin_id}` and returns
the plugin detail without fetching the full plugin list first. Account and
policy drill-downs use plugin-scoped routes.

## Operator-Only Config Inspection

The client info surface now exposes account-to-policy mapping. Operators can
still use config checks to validate the local deployment configuration before
the server starts:

```bash
./arbiter-docker/arbiter-docker config check
```

Example output:

```text
server                           | pass
Plugins                          | pass
├── smtp                         | pass
│   └── bot/bot_policy           | pass | account/policy pair valid
└── imap                         | pass
    └── personal/personal_policy | pass | account/policy pair valid
```

Tree rows use fixed-width `name | status | message` columns so warnings and
errors stay easy to scan.

Full composed account and policy details are also available locally with:

```bash
arbiter-server --config-dir arbiter-docker/conf --config-name arbiter-server config show
```

Relevant paths:

```text
arbiter.account.<plugin>.<account>.policy
arbiter.policy.<plugin>.<policy>
```

This is useful for operators, but agents and first-time users should prefer the
client discovery surface because it is redacted and does not require local
config access.

## Internal Catalog Surface

The server catalog already has internal info kinds for:

```text
overview
plugins
plugin
accounts
account
tests
test
ops
op
```

The native HTTP routes and Go client expose the server, plugin, account,
policy, and operation parts of that surface.

Account tests and diagnostic results belong to installation health checks, not
to the basic info surface.

Explaining why the client chose a server URL also belongs to diagnostics. The
info surface can show the effective server URL. `arbiter config` should print a
short config help menu, and `arbiter config url` should print both the resolved
URL and its source: command-line override, `ARBITER_URL`, the exact client
config file path, or the built-in default.

The diagnostics surface should be able to check plugin readiness at two levels:

- Static plugin configuration: is the plugin enabled, are its accounts and
  policies present, and do accounts reference valid policies?
- Live account tests: can a specific configured account authenticate and perform
  the minimal plugin-owned operation needed to prove it is usable?

Those checks should be runnable per plugin and per account. They should produce
redacted, operator-actionable failures, but they should not be part of the basic
discovery response.

## Release Completion Gaps

The remaining release work is to harden and polish the surface:

- Make the diagnostics surface report URL source, static plugin readiness, and
  live account checks.
- Verify source build metadata is populated by release and Docker tooling, not
  only by local runtime metadata.
- Keep credentials and deployment internals out of client-facing discovery.

## Target Surface

The CLI surface should make the common path obvious:

```bash
arbiter info
arbiter info server
arbiter plugins
arbiter plugins <plugin>
arbiter plugins <plugin> accounts
arbiter plugins <plugin> account <account>
arbiter plugins <plugin> policy <policy>
```

The HTTP surface should support that CLI without forcing broad catalog
downloads:

```text
GET /api/v1/info
GET /api/v1/plugins
GET /api/v1/plugins/{plugin_id}
GET /api/v1/plugins/{plugin_id}/accounts
GET /api/v1/plugins/{plugin_id}/accounts/{account}
GET /api/v1/plugins/{plugin_id}/policies/{policy}
```

`arbiter plugins <plugin>` should call `GET /api/v1/plugins/{plugin_id}`. That
response should describe one plugin. Account drill-downs should use
`arbiter plugins <plugin> accounts`; operation drill-downs should use the
operation surface, such as `arbiter op list <plugin>`. The client should not
fetch the full global plugin list first.

There is no separate policy list route in the read-only info surface. Policies
are discovered through accounts because a policy is only useful to a user or
agent when it is attached to an account.

Policy responses should be explicit, read-only policy resources, not compact
summaries. A policy answers "what can this account do?", so hiding or
summarizing the rules does not buy much. The boundary is redaction: do not
expose credentials, local file paths, or unnecessary deployment structure.

## Suggested Shapes

An account summary should be small enough to include in account list views:

```json
{
  "plugin": "imap",
  "account": "bot",
  "description": "Bot mailbox for Arbiter requests.",
  "guidance": "Use this account for automated request intake.",
  "policy": "bot_policy"
}
```

An account detail can include redacted connection metadata and the explicit
policy that applies to the account, but not secrets:

```json
{
  "kind": "account",
  "plugin": "imap",
  "account": "bot",
  "description": "Bot mailbox for Arbiter requests.",
  "guidance": "Use this account for automated request intake.",
  "config": {
    "host": "127.0.0.1",
    "port": 1143,
    "username": "bot",
    "password": "<redacted>"
  },
  "policy": {
    "kind": "policy",
    "plugin": "imap",
    "policy": "bot_policy",
    "rules": {
      "folders": ["INBOX"],
      "read": true,
      "delete": false
    }
  }
}
```
