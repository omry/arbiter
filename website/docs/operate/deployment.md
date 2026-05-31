---
title: Deployment
---

Agent Arbiter's Docker deployment is a two-phase flow:

1. Prepare a deployment directory as an unprivileged operator.
2. Promote the checked directory to a Linux host install with `sudo`.

The generated service installs the requested Agent Arbiter package target at
container startup, mounts deployment-owned config from the host, and publishes
MCP on host loopback by default:

```text
http://127.0.0.1:8025/mcp
```

## Standard flow

```bash
arbiter-server deploy docker init
./arbiter-docker/arbiter-docker sync-env
./arbiter-docker/arbiter-docker doctor --preinstall
sudo ./arbiter-docker/arbiter-docker install --to /opt/arbiter --user arbiter
```

`init` is only a file generator. It does not create Agent Arbiter config, start
Docker, or run the server. Prepare config and env in `./arbiter-docker`, then
run `doctor --preinstall` before promoting that directory to `/opt/arbiter`.

## Deployment pages

- [Prepare Docker deployment](./deployment/docker-prepare.md): create the local
  deployment directory, add config, sync env, and run preinstall checks.
- [Linux install](./deployment/linux-install.md): promote a prepared directory
  to `/opt/arbiter`, install the systemd unit, and understand the host
  privilege model.
- [Operate the service](./deployment/operations.md): inspect, start, restart,
  log, update, and recover a deployment.
- [Packages and wheels](./deployment/packages.md): manage `requirements.txt`,
  exact pins, plugin packages, wheelhouses, and local source testing.
- [Networking](./deployment/networking.md): configure host ports, Docker bridge
  names, and subnet overrides.

For the broader deployment trust model, see
[Security Model](./security.md).
