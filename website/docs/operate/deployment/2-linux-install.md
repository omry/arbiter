---
title: Linux Install
---

Install promotes a tested staging directory to a Linux systemd service. Run
these commands from inside the prepared `reploy-staging` directory.

## Preinstall check

Before the privileged install step, check that the directory is ready to
promote:

```bash
./reploy doctor --preinstall
```

This catches common production-install mistakes before anything is copied into
the install target.

Preview the privileged work before applying it:

```bash
./reploy install --to /opt/arbiter --dry-run
```

## Install

Promote the prepared directory:

```bash
sudo ./reploy install --to /opt/arbiter
```

By default, install enables and restarts the systemd service. To install without
starting the service:

```bash
sudo ./reploy install --to /opt/arbiter --no-start
```

Use `--service` when the unit should have a non-default name:

```bash
sudo ./reploy install --to /opt/arbiter --service arbiter-prod
```

Reploy copies the prepared directory as-is. To preserve production credentials
or config across reinstalls, edit the staging directory from the intended
installed state before running install again.

## Verify

Test the installed Arbiter server with the Arbiter client:

```bash
arbiter arbiter.url=https://127.0.0.1:8075 info --yaml plugins
# server_url: https://127.0.0.1:8075
# kind: plugins
# plugins:
# - id: imap
# - id: smtp
```

Installed service operations use systemd:

```bash
sudo systemctl status arbiter.service
sudo journalctl -u arbiter.service -f
```

The generated systemd unit waits for the Docker CLI and API to become usable
before running Compose. This protects boot and WSL startup from racing Docker
Desktop integration or a native Docker daemon that is still starting.

Restart the installed service with:

```bash
sudo systemctl restart arbiter.service
```

## What Install Does

`install` requires root unless `--dry-run` is used. It:

- runs preinstall doctor checks
- copies the prepared deployment directory to the install target
- marks the copied deployment state as `installed`
- writes `/etc/systemd/system/arbiter.service`, including Docker service
  ordering when available and a Docker API readiness check before Compose
  starts
- reloads and enables systemd
- restarts the service by default
- runs `./reploy test` and `./reploy app config check --live` from the installed
  directory after restart

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
| `--to DIR` | Install target directory. Required. | None |
| `--service NAME` | systemd unit name. | `arbiter` |
| `--no-start` | Install and enable the service without starting it. | Off |
| `--start` | Start after install. | On |
| `--dry-run` | Print the install plan without changing the host. | Off |
