---
title: Terminal Recordings
---

Arbiter docs should include short terminal sessions for CLI-heavy flows.
The recordings are meant to make the workflow legible without replacing
copyable commands.

## Planned recordings

- Bootstrap a server config.
- Bootstrap, edit, and activate an SMTP account.
- Bootstrap and check an env file.
- Start the server and configure the client.
- Discover capabilities and inspect operations.
- Run an operation against a local test setup.
- Use the deployment helper to install, sync env, inspect, and start.

## Recording format

The preferred source format is `asciinema` cast files:

```bash
asciinema rec website/static/casts/bootstrap-server.cast
```

Static renderings can be generated with `agg`:

```bash
agg website/static/casts/bootstrap-server.cast website/static/img/casts/bootstrap-server.svg
```

Each recording should also have copyable commands nearby so agents and humans
can follow the flow without watching media.

## Reproducibility rule

Recordings should use disposable config directories:

```bash
tmpdir="$(mktemp -d)"
arbiter-server --config-dir "$tmpdir" bootstrap arbiter
arbiter-server --config-dir "$tmpdir" bootstrap plugin smtp account bot
arbiter-server --config-dir "$tmpdir" config activate account smtp bot
arbiter-server --config-dir "$tmpdir" config show
```

Avoid recording against `config.local/` or machine-specific secrets.
