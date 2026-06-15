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
the install target. To also check access for an agent user on the same machine,
pass that user explicitly:

```bash
./arbiter-docker doctor --preinstall --agent-user codex
```

## Install

Promote the prepared directory:

```bash
sudo ./arbiter-docker install
```

By default, install starts or restarts the systemd service.

On the first install, the staging `conf/` directory and env file are copied into
the installed directory. After that, the installed config and env are
authoritative: later installs update the bundle and service wrapper, but keep
the installed config and env by default. This lets operators edit credentials
only in the protected installed directory. When an existing config directory is
preserved, install keeps a protected timestamped copy under `backup/`.

To intentionally replace the installed config from staging:

```bash
sudo ./arbiter-docker install --replace-config
```

Add `--replace-env` when the installed env file should also come from staging.

## Verify

Test the installed Arbiter server with the Arbiter client:

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

- creates the configured system user/group if missing
- checks the config that will be used after install before copying files or
  restarting the service
- copies the prepared deployment directory to the install target
- preserves an existing installed config package unless `--replace-config` is
  passed
- rewrites the copied Compose command to pass
  `arbiter.deployment_scope=installed`
- sets ownership to the configured user/group
- tightens file modes
- writes `/etc/systemd/system/arbiter.service`
- reloads and enables systemd
- restarts the service by default
- checks the running service config and configured accounts after restart

## Privilege Model

The dedicated service user is not added to the Docker group. The systemd unit
is root-managed and runs `docker compose`; the container does not receive the
Docker socket.

This avoids making the dedicated deployment user host-root-equivalent through
Docker socket access. Treat Docker control, Docker socket access, Docker group
membership, and passwordless `sudo` as root-equivalent.

For the broader boundary model, see [Security Model](../security.md).

## Options Reference

Use these when the standard install path needs a small adjustment:

### Install Parameters

The standard install uses these defaults. Override them only when needed:

| Argument | Comment | Default |
| --- | --- | --- |
| `--to DIR` | Install target directory. | `/opt/arbiter` |
| `--user USER` | Service config owner. Created if missing. | `arbiter` |
| `--group GROUP` | Service config group. Created if missing. | Same as `--user` (`arbiter`) |
| `--service NAME` | systemd unit name. | `arbiter` |
| `--no-start` | Install and enable the service without starting it. | Off |
| `--replace-config` | Replace installed config from this staging directory. | Off |
| `--replace-env` | With `--replace-config`, also replace the installed env from staging. | Off |
| `--dry-run` | Print the install plan without changing the host. | Off |
