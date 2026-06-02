---
title: Networking
---

The generated Compose service publishes MCP on host loopback by default:

```text
http://127.0.0.1:8025/mcp
```

The service listens on all container interfaces so Docker's host-loopback port
publish can reach it. The host publish remains loopback-only unless you change
`ARBITER_HOST_BIND`.

## Docker env values

Edit Docker wrapper settings with:

```bash
./arbiter-docker/arbiter-docker edit-docker
```

Common values in `docker.env`:

- `ARBITER_HOST_BIND`: host bind address, default `127.0.0.1`.
- `ARBITER_HOST_PORT`: host port, default `8025`.
- `ARBITER_CONTAINER_PORT`: container port, default `8025`.
- `ARBITER_DOCKER_NETWORK_NAME`: Docker network name, default
  `arbiter`.
- `ARBITER_DOCKER_BRIDGE_NAME`: bridge interface name, default
  `arbiter0`.
- `ARBITER_DOCKER_SUBNET`: bridge subnet, default `172.31.250.0/24`.

## Bridge overrides

The standard Compose file uses a deterministic Docker bridge so firewall rules
can target stable names:

- Docker network name: `arbiter`
- bridge interface: `arbiter0`
- bridge subnet: `172.31.250.0/24`

If that subnet or interface name conflicts with the host, override them for the
Compose invocation. Keep the bridge interface name short enough for Linux
network interface limits:

```bash
cd /opt/arbiter
ARBITER_DOCKER_BRIDGE_NAME=arbiter1 \
ARBITER_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=ARBITER_DOCKER_BRIDGE_NAME,ARBITER_DOCKER_SUBNET \
  docker compose --env-file /opt/arbiter/docker.env \
  -f /opt/arbiter/compose.yaml up -d
```

The helper normally wraps `docker compose` for you. Use the manual form only
when you need one-off Docker options that the helper does not expose.

## Exposure

Keep the host bind on `127.0.0.1` unless the deployment is intentionally exposed
through a controlled local proxy or firewall rule. The host file permissions
protect deployment state and secrets; they do not add authentication to the MCP
API. Any local process that can reach the MCP endpoint can use the tools allowed
by the configured policy.
