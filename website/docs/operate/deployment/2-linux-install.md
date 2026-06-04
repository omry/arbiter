---
title: Linux Install
---

Install promotes a tested staging directory to a Linux systemd service. Run
these commands from inside the prepared `arbiter-docker` directory.

## Preinstall check

Before the privileged install step, check that the directory is ready to
promote:

```bash
./arbiter-docker doctor --preinstall
```

This catches common production-install mistakes before anything is copied into
`/opt`.

## Optional dry run

Inspect the host changes without applying them:

```bash
./arbiter-docker install --dry-run --to /opt/arbiter --user arbiter
```

## Install

Promote the prepared directory:

```bash
sudo ./arbiter-docker install --to /opt/arbiter --user arbiter
```

By default, install starts or restarts the systemd service.

On the first install, the staging `conf/` directory seeds the installed
configuration package. After that, the installed configuration and env file are
authoritative: later installs update the bundle and service wrapper, but keep
the installed `conf/` package by default. This lets operators edit credentials
only in the protected installed directory. When an existing config directory is
preserved, install keeps a protected timestamped copy under `backup/`.

To intentionally replace the installed config and env from staging:

```bash
sudo ./arbiter-docker install --to /opt/arbiter --user arbiter --replace-config
```

## Verify

Test the installed MCP endpoint with the Arbiter client:

```bash
arbiter arbiter.mcp_url=http://127.0.0.1:8025/mcp info --yaml plugins
# server_url: http://127.0.0.1:8025/mcp
# kind: plugins
# plugins:
# - id: imap
#   version: 0.9.0
# - id: smtp
#   version: 0.9.0
```

Installed service operations use systemd:

```bash
sudo systemctl status arbiter.service
sudo journalctl -u arbiter.service -f
```

Restart the installed service with:

```bash
sudo systemctl restart arbiter.service
```

## What Install Does

`install` requires root unless `--dry-run` is used. It:

- creates the `arbiter` system user/group if missing
- copies the prepared deployment directory to `/opt/arbiter`
- preserves an existing installed config package unless `--replace-config` is
  passed
- rewrites the copied Compose command to pass
  `arbiter.deployment_scope=installed`
- sets ownership to `arbiter:arbiter`
- tightens file modes
- writes `/etc/systemd/system/arbiter.service`
- reloads and enables systemd
- restarts the service by default

## Privilege Model

The `arbiter` user is not added to the Docker group. The systemd unit is
root-managed and runs `docker compose`; the container does not receive the
Docker socket.

This avoids making the dedicated deployment user host-root-equivalent through
Docker socket access. Treat Docker control, Docker socket access, Docker group
membership, and passwordless `sudo` as root-equivalent.

For the broader boundary model, see [Security Model](../security.md).

## Options Reference

Use these when the standard install path needs a small adjustment:

```bash
./arbiter-docker doctor --preinstall --agent-user codex
```

Check common access mistakes for an agent user on the same machine.

```bash
sudo ./arbiter-docker install --to /opt/arbiter --user arbiter --no-start
```

Install without starting the service.

```bash
sudo ./arbiter-docker install --to /opt/arbiter --user arbiter --service arbiter-server
```

Choose a different systemd unit name.
