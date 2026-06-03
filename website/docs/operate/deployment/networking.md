---
title: Networking
---

Prepared staging directories publish MCP on a staging-specific host port by
default. This keeps staging local while avoiding the installed service's default
host port.

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
- `ARBITER_HOST_PORT`: host port, staging default `18025`; installed default
  `8025`.
- `ARBITER_CONTAINER_PORT`: container port, default `8025`.
- `ARBITER_DOCKER_NETWORK_NAME`: Docker network name, default
  `arbiter-staging` before install and `arbiter` after install.
- `ARBITER_DOCKER_BRIDGE_NAME`: bridge interface name, default
  `arbiter-stg0` before install and `arbiter0` after install.
- `ARBITER_DOCKER_SUBNET`: bridge subnet, staging default
  `172.31.251.0/24`; installed default `172.31.250.0/24`.

## Staging and install identities

Staging and installed deployments use different Docker identifiers so a
prepared directory can be tested on the same machine as the installed service.
The generated staging directory starts with staging names and ports. During
`install`, the copied directory is rewritten to the installed identity.

The host port is rewritten because it is the address clients use: staging needs
a stable local port that does not take over the installed service's default.
The Docker subnet is separate because Docker bridge networks cannot overlap.
If the default staged subnet already overlaps another Docker network, staged
`up` updates `ARBITER_DOCKER_SUBNET` in `docker.env` to an unused staging
candidate before Compose creates the network.

## Bridge overrides

The generated Compose file uses a deterministic Docker bridge so firewall rules
can target stable names. Prepared staging directories default to:

- Docker network name: `arbiter-staging`
- bridge interface: `arbiter-stg0`
- bridge subnet: `172.31.251.0/24`

Installed deployments are rewritten to:

- Docker network name: `arbiter`
- bridge interface: `arbiter0`
- bridge subnet: `172.31.250.0/24`

If a subnet or interface name conflicts with the host, edit `docker.env` with
the deployment helper and then run `up` or `restart` again:

```bash
./arbiter-docker/arbiter-docker edit-docker
./arbiter-docker/arbiter-docker up
```

Keep the bridge interface name short enough for Linux network interface limits.
For installed deployments, run the installed helper from `/opt/arbiter` or use
the systemd service after editing `/opt/arbiter/docker.env`.

## Exposure

Keep the host bind on `127.0.0.1` unless the deployment is intentionally exposed
through a controlled local proxy or firewall rule. The host file permissions
protect deployment state and secrets; they do not add authentication to the MCP
API. Any local process that can reach the MCP endpoint can use the tools allowed
by the configured policy.
