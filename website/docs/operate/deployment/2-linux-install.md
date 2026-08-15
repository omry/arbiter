---
title: Linux Install
---

Install transfers a checked state-v1 staging deployment into place as a Linux
systemd service. Run these commands from inside the prepared `reploy-staging`
directory.

## Preinstall check

Before the privileged install step, check that the directory is ready to
install:

```bash
reploy doctor --preinstall --scope system
```

This catches common production-install mistakes before anything is copied into
the install target.

`doctor --preinstall` is the read-only readiness check. Current Reploy does not
provide an `install --dry-run` mode.

## Install

Install the prepared directory:

```bash
sudo "$(command -v reploy)" install --scope system --to /opt/arbiter
```

By default, install enables and restarts the systemd service. To install without
starting the service:

```bash
sudo "$(command -v reploy)" install --scope system --to /opt/arbiter --no-start
```

Use `--service` when the unit should have a non-default name:

```bash
sudo "$(command -v reploy)" install --scope system --to /opt/arbiter --service arbiter-prod
```

Reploy preserves blueprint-managed paths and its reserved `.env` file on update
by default. Use `--replace PATH` for one preserved path, `--replace all` for all
preserved content, or `--clean` only when the staged copies should replace all
managed content.

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

The installed deployment also provides its standard control script at
`/opt/arbiter/appctl`. System-scope control operations require root, for example:

```bash
sudo /opt/arbiter/appctl status
sudo /opt/arbiter/appctl health
```

The generated systemd unit waits for the Docker CLI and API to become usable
before running Compose. This protects boot and WSL startup from racing Docker
Desktop integration or a native Docker daemon that is still starting.

Restart the installed service with:

```bash
sudo systemctl restart arbiter.service
```

## What Install Does

System-scope `install` requires root. It:

- ensures the staging deployment has a current validated build
- runs system-scope preinstall checks
- transfers the selected build closure and state-v1 deployment metadata
- creates or preserves the blueprint-managed paths according to their update
  policies
- writes `/etc/systemd/system/arbiter.service`, including Docker service
  ordering when available
- reloads and enables systemd
- restarts the service by default
- runs the blueprint's readiness and live config checks after restart

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
| `--scope system` | Select system service installation. Required. | None |
| `--to DIR` | Install target directory. | Blueprint default (`/opt/arbiter`) |
| `--service NAME` | systemd unit name. | `arbiter` |
| `--no-start` | Install and enable the service without starting it. | Off |
| `--start` | Start after install. | On |
| `--replace PATH` | Replace one preserved managed path or `.env`; repeat as needed. | Preserve |
| `--replace all` | Replace every preserved managed path and `.env`. | Preserve |
| `--clean` | Replace all managed content. | Off |
| `--wait` | Wait for active and earlier queued runs. | Off |
| `--drain` | Let active runs finish, then install. | Off |
| `--force` | Stop active runs, then install. | Off |
