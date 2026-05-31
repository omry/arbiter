---
title: Deployment
---

The repository includes two Docker deployment paths:

- `deploy/`: the standard VM deployment for the Agent Arbiter MCP server.
- `deploy/readonly-imap/`: a hardened local build for testing one read-only
  IMAP account.

Both keep Agent Arbiter reachable on host loopback by default:

```text
http://127.0.0.1:8025/mcp
```

## Standard Docker deployment

The standard deployment uses Docker Compose with the stock `python:3.11-slim`
image. The container installs the requested Agent Arbiter package target at
startup, mounts operator-owned config from the host, and publishes MCP only on
host `127.0.0.1`.

From the repository checkout, install the host-side deployment files:

```bash
./deploy/agent-arbiterctl install --install-target agent-arbiter==0.1.1
```

`install` creates `/opt/agent-arbiter`, copies `compose.yaml` and `config.yaml`,
writes the install target, creates the env file, installs
`agent-arbiterctl` under `/usr/local/sbin`, and applies root ownership and
restrictive modes. Existing config, env, and install-target files are preserved
unless `--force` is used.

For a local source checkout on the host, use an absolute path instead of a
package spec:

```bash
./deploy/agent-arbiterctl install --install-target /home/omry/dev/agent-arbiter
```

When the install target is a host directory, the helper bind-mounts it
read-only and the container copies the source into `/tmp` before installing it.
That keeps build metadata out of the host checkout.

Generate or update the deployment env file from the installed config:

```bash
agent-arbiterctl sync-env
```

`sync-env` reads `${oc.env:...}` references from `/opt/agent-arbiter/config.yaml`,
adds missing variables to `/opt/agent-arbiter/agent-arbiter.env`, and preserves
existing values. It also writes `AGENT_ARBITER_SERVER_HOST=0.0.0.0` because the
server must listen on the container interface. Compose still publishes the
service only on host loopback.

Check the helper and editor setup:

```bash
agent-arbiterctl doctor
```

Edit operator-owned files through the helper:

```bash
agent-arbiterctl edit-env
agent-arbiterctl edit-config
```

`edit-env` edits credentials and local environment values. `edit-config` edits
the active Agent Arbiter config. Both commands copy the protected file to a
private temp file, open the configured editor, then install the edited file back
with root ownership.

Start or update the service:

```bash
agent-arbiterctl up
```

Useful service commands:

```bash
agent-arbiterctl ps
agent-arbiterctl logs
agent-arbiterctl restart
agent-arbiterctl down
agent-arbiterctl protect
```

- `ps`: show Docker Compose service status.
- `logs`: follow service logs.
- `restart`: recreate the container, which also reinstalls the configured
  package target.
- `down`: stop and remove the Compose service.
- `protect`: reapply the intended ownership and file modes after manual
  changes.

## Updating

To change the deployed package or source target, reinstall with `--force`, then
recreate the container:

```bash
agent-arbiterctl install --force --install-target agent-arbiter==0.1.2
agent-arbiterctl restart
```

`--force` backs up the current config, env file, and install target before
replacing them. The regenerated env file keeps existing values for matching
variable names.

## Docker network overrides

The standard Compose file uses a deterministic Docker bridge so firewall rules
can target stable names:

- Docker network name: `agent-arbiter`
- bridge interface: `agent-arbiter0`
- bridge subnet: `172.31.250.0/24`

If that subnet or interface name conflicts with the host, override them for the
Compose invocation. Keep the bridge interface name short enough for Linux
network interface limits:

```bash
cd /opt/agent-arbiter
AGENT_ARBITER_DOCKER_BRIDGE_NAME=arbiter1 \
AGENT_ARBITER_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=AGENT_ARBITER_DOCKER_BRIDGE_NAME,AGENT_ARBITER_DOCKER_SUBNET \
  docker compose --env-file /opt/agent-arbiter/agent-arbiter.env \
  -f /opt/agent-arbiter/compose.yaml up -d
```

The helper normally wraps `docker compose` for you. Use the manual form only
when you need one-off Docker options that the helper does not expose.

## Security notes

The deployment files are intended to be operator-owned. The helper installs:

- `compose.yaml`, `config.yaml`, and `install-target` as root-owned read-only
  files.
- `agent-arbiter.env` as root-owned mode `0600`.

Do not run coding agents as a user with Docker socket access, membership in the
Docker group, or passwordless `sudo` if the deployment is meant to constrain
them. Docker control is root-equivalent.

The host file permissions protect deployment state and secrets. They do not add
authentication to the MCP API. Any local process that can reach
`http://127.0.0.1:8025/mcp` can use the tools allowed by the configured policy.

## Read-only IMAP deployment

The `deploy/readonly-imap/` variant builds the local `Dockerfile` and runs one
IMAP account with stricter runtime settings:

- no SMTP account
- read/search allowed
- move/delete disabled
- credentials supplied from host-side secret files
- non-root container user
- dropped Linux capabilities
- read-only container filesystem with only `/tmp` writable

Prepare host-side secret files outside the repository:

```bash
sudo install -d -m 700 -o root -g root /opt/agent-arbiter-readonly/secrets
sudo sh -c 'printf "%s" "YOUR_IMAP_USERNAME" > /opt/agent-arbiter-readonly/secrets/imap_username'
sudo sh -c 'printf "%s" "YOUR_IMAP_PASSWORD_OR_APP_PASSWORD" > /opt/agent-arbiter-readonly/secrets/imap_password'
sudo chown root:10001 /opt/agent-arbiter-readonly/secrets/imap_username /opt/agent-arbiter-readonly/secrets/imap_password
sudo chmod 440 /opt/agent-arbiter-readonly/secrets/imap_username /opt/agent-arbiter-readonly/secrets/imap_password
```

Those host permissions are important. Docker Compose may ignore `uid`, `gid`,
and `mode` metadata for local file-backed secrets, so do not rely on Compose
secret metadata as the only permission boundary.

Copy the read-only config outside the repository and replace the placeholder
folder with the real IMAP folder name:

```bash
sudo cp deploy/readonly-imap/config.yaml /opt/agent-arbiter-readonly/config.yaml
sudoedit /opt/agent-arbiter-readonly/config.yaml
sudo chmod 444 /opt/agent-arbiter-readonly/config.yaml
```

Set Compose environment values:

```bash
AGENT_ARBITER_IMAP_HOST=imap.example.com
AGENT_ARBITER_IMAP_PORT=993
AGENT_ARBITER_IMAP_TLS=implicit
AGENT_ARBITER_IMAP_VERIFY_PEER=true
AGENT_ARBITER_HOST_PORT=8025
AGENT_ARBITER_SECRET_DIR=/opt/agent-arbiter-readonly/secrets
AGENT_ARBITER_CONFIG_FILE=/opt/agent-arbiter-readonly/config.yaml
```

Run from `deploy/readonly-imap/`:

```bash
docker compose up --build -d
docker compose logs -f
```

`up --build -d` builds the local Dockerfile if needed, starts the service in the
background, and publishes MCP on host loopback. `logs -f` follows startup and
runtime logs.
