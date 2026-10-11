---
title: Arbiter Client reference
---

`arbiter` is the client-facing command for agents and humans. It connects to an
Arbiter server and exposes discovery and operation execution.

Most users and agents should start with `info server`, then drill into
`plugins` or `op` as needed.

Arbiter exposes a hierarchical discovery surface. Start with server
orientation, then drill into the plugin, account, policy, or operation needed
for the task:

```bash
arbiter info server
arbiter plugins smtp
arbiter op desc smtp:send_email
```

## Global options

```bash
arbiter [--config-dir DIR] [--config-name NAME] <command>
```

- `--config-dir DIR`: client config directory. Defaults to `~/.arbiter`.
- `--config-name NAME`: client config file name without `.yaml`. Defaults to
  `arbiter-client`.
- `--version`: print the installed version.

The client reads the server URL from its config. The current config key is
`arbiter.url`; you can override it per command with a Hydra-style argument:

```bash
arbiter info server arbiter.url=https://127.0.0.1:8075
```

The client accepts Arbiter's local self-signed TLS certificates by default.
Configure `arbiter.tls_ca_file` in the client config when you want certificate
verification against a specific certificate authority file.

URL overrides, including `ARBITER_URL`, replace only the URL. The client still
reads and validates the config file and applies its CA and timeout settings.

When the server reports `deployment_scope=staged`, the client prints a small
heads-up on stderr so you know you are talking to a staged deployment.

Default client config:

```text
~/.arbiter/arbiter-client.yaml
```

Example:

```yaml
arbiter:
  url: https://127.0.0.1:8075
```

## Timeouts

The client separates connection setup, API responses, and artifact download
inactivity:

```yaml
arbiter:
  timeout: 30s
  connect_timeout: 10s
  download_idle_timeout: 30s
  plugins:
    imap:
      timeout:
        default: 30s
        search_messages: 5m
    smtp:
      timeout: 30s
```

- `connect_timeout` bounds connection acquisition, including DNS, TCP, and TLS
  setup. Its default is `10s`.
- `timeout` bounds each API response after connection setup completes, including
  sending the request, waiting for headers, and reading the response body. Its
  default is `30s`. Connection time does not consume this budget.
- `download_idle_timeout` bounds waiting for artifact response headers or for
  more body data during a read. Its default is `30s`. Incoming body data allows
  the next read a fresh inactivity budget. Local processing between reads does
  not consume it. Artifact downloads have no total transfer deadline and do not
  use `timeout`.

Plugin timeouts apply to `op run`. A scalar is the plugin default; a mapping can
contain the reserved `default` key and operation names, without the plugin
prefix. A missing plugin default inherits `arbiter.timeout`. Discovery requests
use the global API budget.

For operation execution, precedence is **command-line API timeout → operation
timeout → plugin default → global default**. These select one response budget;
they do not impose cumulative limits.

Override any global timeout for one command using the same syntax as the URL:

```bash
arbiter op run imap:search_messages --args '{"account":"bot","query":"ALL"}' arbiter.timeout=5m
arbiter info server arbiter.connect_timeout=15s
arbiter artifact save '<artifact-url>' ./artifact.bin arbiter.download_idle_timeout=1m
```

Timeout values must be positive durations with units, such as `250ms`, `30s`,
`5m`, or `1h30m`. Zero, negative, overflowing, and unitless values are rejected.
There are no timeout environment variables. A timeout limits the client's wait;
it does not guarantee that the server stopped an operation. The client does not
automatically retry a timed-out operation.

## bootstrap

Create the client config file.

```bash
arbiter bootstrap client [--force] [override...]
```

- `--force`: overwrite an existing client config.

Example:

```bash
arbiter bootstrap client arbiter.url=https://127.0.0.1:8075
```

## Common flow

```bash
arbiter info server
arbiter plugins
arbiter plugins smtp
arbiter plugins smtp account bot
arbiter op desc smtp:send_email
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```

## info

Show server identity. Bare `arbiter info` prints a short help menu.

```bash
arbiter info
arbiter info server [--yaml]
```

`info server` prints JSON by default so agents and scripts can consume it
directly. For terminal reading, pipe it through `jq`:

```bash
arbiter info server | jq
```

Use `--yaml` when you want readable YAML output instead:

```bash
arbiter info server --yaml
```

- `info`: show help for server info commands.
- `info server`: show the server URL, deployment scope, version, and source
  metadata when available.

## plugins

Discover plugins and plugin-scoped accounts and policies.

```bash
arbiter plugins [--yaml]
arbiter plugins <plugin> [--yaml]
arbiter plugins <plugin> accounts [--yaml]
arbiter plugins <plugin> account <account> [--yaml]
arbiter plugins <plugin> policy <policy> [--yaml]
```

- `plugins`: list installed plugins.
- `plugins <plugin>`: describe one plugin.
- `plugins <plugin> accounts`: list configured accounts for one plugin.
- `plugins <plugin> account <account>`: show one account with its policy.
- `plugins <plugin> policy <policy>`: show one redacted policy.

## op

Discover and run operations.

```bash
arbiter op list [plugin]
arbiter op desc <operation-id>
arbiter op run <operation-id> --args '<json-object>'
```

- `op list`: list operation summaries.
- `op desc`: show one operation and its input schema.
- `op run`: run one operation with JSON arguments.
- `operation` is an alias for `op`.

Example:

```bash
arbiter op run smtp:send_email --args '{"account":"bot","to":["ops@example.com"],"subject":"Hello","text_body":"Hi"}'
```
