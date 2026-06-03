---
title: Linux Install
---

Install promotes a prepared deployment directory to a Linux host. Prepare config
and env first, then run the privileged step.

```bash
./arbiter-docker/arbiter-docker sync-env
./arbiter-docker/arbiter-docker doctor --preinstall
sudo ./arbiter-docker/arbiter-docker install --to /opt/arbiter --user arbiter
```

## What install does

`install` requires root unless `--dry-run` is used. It:

- creates the `arbiter` system user/group if missing
- copies the prepared deployment directory to `/opt/arbiter`
- rewrites the copied Compose command to pass
  `arbiter.deployment_scope=installed`
- sets ownership to `arbiter:arbiter`
- tightens file modes
- writes `/etc/systemd/system/arbiter.service`
- reloads and enables systemd
- restarts the service by default

Inspect the host changes without applying them:

```bash
./arbiter-docker/arbiter-docker install --dry-run --to /opt/arbiter --user arbiter
```

Install without starting the service:

```bash
sudo ./arbiter-docker/arbiter-docker install \
  --to /opt/arbiter \
  --user arbiter \
  --no-start
```

Choose a different systemd unit name:

```bash
sudo ./arbiter-docker/arbiter-docker install \
  --to /opt/arbiter \
  --user arbiter \
  --service arbiter-server
```

## Privilege model

The `arbiter` user is not added to the Docker group. The systemd unit is
root-managed and runs `docker compose`; the container does not receive the
Docker socket.

This avoids making the dedicated deployment user host-root-equivalent through
Docker socket access. Treat Docker control, Docker socket access, Docker group
membership, and passwordless `sudo` as root-equivalent.

## Installed service

Installed service commands use systemd:

```bash
sudo systemctl status arbiter.service
sudo systemctl restart arbiter.service
sudo journalctl -u arbiter.service -f
```

For the broader boundary model, see [Security Model](../security.md).
