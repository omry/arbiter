# Agent Arbiter VM Docker Deployment

The installed deployment surface is now:

```bash
arbiter-server deploy docker init
./arbiter-docker/arbiter-docker doctor --preinstall
sudo ./arbiter-docker/arbiter-docker install --to /opt/arbiter --user arbiter
```

The first command writes a deployment-local `arbiter-docker` helper into
`./arbiter-docker`. Prepare config and env there as an unprivileged operator,
then use the helper's `install` command to promote the checked directory to
`/opt/arbiter`. It keeps Docker wrapper settings in `docker.env`, separate
from the Agent Arbiter runtime/credential env file. The repository-local
`agent-arbiterctl` material below is retained as the original checkout-oriented
deployment helper, but most users should use the installed
`arbiter-server deploy docker` flow documented in
`website/docs/operate/deployment.md`.

This deployment path uses Docker Compose and does not require building a custom image.

It runs the stock `python:3.11-slim` image, installs the requested Agent Arbiter package target at container startup, and keeps deployment-specific config and secrets on the host.

The mounted config is generic and resolves its server and mail account settings from the host-side env file.

## Security model

The recommended host layout is operator-editable but root-owned:

- `compose.yaml` and `config.yaml` are owned by `root:root` and mode `0444`
- `agent-arbiter.env` is owned by `root:root` and mode `0600`
- edits go through `agent-arbiterctl`, which opens a private temp copy with
  `AGENT_ARBITER_EDITOR` or `vim` and then installs it back with `sudo`
- Docker commands go through `sudo docker compose`

This keeps an ordinary host agent from casually changing the gateway policy,
mail settings, or secrets. It is not a boundary against `root`, passwordless
`sudo`, or membership in the `docker` group. Treat the Docker socket and Docker
group as root-equivalent. If a coding agent should not be able to manipulate the
deployment, run that agent as a user without `sudo` and without Docker socket
access.

This also protects the host files, not the MCP API. Any local process that can
reach `http://127.0.0.1:8025/mcp` can use whatever Agent Arbiter tools the
configured policy allows.

## Operator helper

The `agent-arbiterctl` helper installs the deployment files under
`/opt/agent-arbiter`, keeps them root-owned, and provides short edit/run commands.
It refuses symlinked deployment paths so an existing host file cannot be
silently targeted by the privileged setup step.
If `config.yaml` or `agent-arbiter.env` already exist, `install` preserves them
instead of replacing local policy or secrets.

From this repository:

```bash
./deploy/agent-arbiterctl install --install-target agent-arbiter==0.1.1
```

For a local source checkout on the host, use an absolute host path:

```bash
./deploy/agent-arbiterctl install --install-target /home/omry/dev/agent-arbiter
```

If you intentionally changed the source deployment config and want to replace
the installed config, use:

```bash
./deploy/agent-arbiterctl install --force --install-target /home/omry/dev/agent-arbiter
```

`--force` backs up the installed config, env file, and install target; replaces
the installed config from the source config; updates the install target from the
command line; and regenerates the env file from the installed config's
`${oc.env:...}` references while preserving existing values for matching
variable names.

The env file is generated from the installed `config.yaml`; there is no separate
checked-in env example to keep in sync with the hierarchical account config. To
create a missing env file or add newly required variables after editing the
installed config, run:

```bash
agent-arbiterctl sync-env
```

If `agent-arbiter.env` already exists, `sync-env` preserves values for matching
variable names and writes a timestamped backup beside it. The generated Docker
deployment env always sets `AGENT_ARBITER_SERVER_HOST=0.0.0.0` because the service
must listen on the container interface for Docker's host-loopback port publish
to reach it; `compose.yaml` still publishes only on host `127.0.0.1`.

Then edit the operator-owned files with:

```bash
agent-arbiterctl edit-env
agent-arbiterctl edit-config
```

The helper defaults to a plain Vim session without user vimrc/plugins and with
insert-mode cursor-shape terminal sequences disabled. Check the installed edit
backend with:

```bash
agent-arbiterctl doctor
```

To use another editor:

```bash
AGENT_ARBITER_EDITOR='vim -n' agent-arbiterctl edit-env
```

Apply edits by recreating the container:

```bash
agent-arbiterctl restart
```

Start or inspect the service with:

```bash
agent-arbiterctl up
agent-arbiterctl ps
agent-arbiterctl logs
```

After any manual permission changes, restore the intended ownership and modes:

```bash
agent-arbiterctl protect
```

## Host layout

The helper is the preferred setup path. This section shows the underlying host
layout for manual review or partial manual setup. Env generation still goes
through `agent-arbiterctl sync-env` so the flat env file stays derived from the
hierarchical `config.yaml`.

Create the host directory:

```bash
sudo install -d -o root -g root -m 0755 /opt/agent-arbiter
```

Copy the generic deployment files onto the VM host:

```bash
sudo install -o root -g root -m 0444 /path/to/agent-arbiter/deploy/compose.yaml /opt/agent-arbiter/compose.yaml
sudo install -o root -g root -m 0444 /path/to/agent-arbiter/deploy/config.yaml /opt/agent-arbiter/config.yaml
```

Create the install target file:

```bash
printf '%s\n' 'agent-arbiter==0.1.1' | sudo tee /opt/agent-arbiter/install-target >/dev/null
sudo chown root:root /opt/agent-arbiter/install-target
sudo chmod 0444 /opt/agent-arbiter/install-target
```

Create the env file from the config's `${oc.env:...}` references. The helper
generates placeholders and defaults that match the currently installed config:

```bash
agent-arbiterctl sync-env
```

The resulting layout is:

```text
/opt/agent-arbiter/
  compose.yaml
  config.yaml
  agent-arbiter.env
  install-target
```

Then edit `/opt/agent-arbiter/agent-arbiter.env` and `/opt/agent-arbiter/config.yaml`.
The helper implements this as a copy-to-temp, edit, install-back workflow:

```bash
agent-arbiterctl edit-env
agent-arbiterctl edit-config
```

Set at least:

- the real SMTP and IMAP values
- any non-default server values

## Run the service

This keeps the service on VM loopback at `http://127.0.0.1:8025/mcp` with the generated env defaults.

```bash
agent-arbiterctl up
```

Manual equivalent for package targets such as `agent-arbiter==0.1.1`:

```bash
cd /opt/agent-arbiter
sudo env AGENT_ARBITER_CONTAINER_INSTALL_TARGET="$(sudo sed -n '1p' /opt/agent-arbiter/install-target)" \
  docker compose --env-file /opt/agent-arbiter/agent-arbiter.env -f /opt/agent-arbiter/compose.yaml up -d
```

For local source checkout targets, use `agent-arbiterctl up`; the helper supplies
the extra read-only bind mount that maps the host checkout into the container.

The install target is a pip install target. For a published package, install
with:

```bash
./deploy/agent-arbiterctl install --install-target agent-arbiter==0.1.1
```

For a local source checkout on the host, install with:

```bash
./deploy/agent-arbiterctl install --install-target /home/omry/dev/agent-arbiter
```

When the target is an existing host directory, `agent-arbiterctl` bind-mounts it
read-only into the container. The container copies the package files into a
writable temp directory before running `pip`, so the host checkout remains
stationary and build metadata is not written back into the source tree.

The Compose service:

- installs the requested package target with `pip`
- starts the Agent Arbiter server with the mounted `config.yaml`
- binds the container to `0.0.0.0` internally and publishes it only on host loopback via `127.0.0.1:8025:8025`
- uses a deterministic Docker bridge by default so host firewall rules can target a stable interface and subnet

Default Docker network values:

- Docker network name: `agent-arbiter`
- bridge interface: `agent-arbiter0`
- bridge subnet: `172.31.250.0/24`

If a host already uses that interface name or subnet, override them only for the `docker compose` command:

```bash
cd /opt/agent-arbiter
AGENT_ARBITER_DOCKER_BRIDGE_NAME=agent-arbiter1 \
AGENT_ARBITER_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=AGENT_ARBITER_DOCKER_BRIDGE_NAME,AGENT_ARBITER_DOCKER_SUBNET \
  docker compose --env-file /opt/agent-arbiter/agent-arbiter.env -f /opt/agent-arbiter/compose.yaml up -d
```

For hosts using UFW, allow the deterministic Agent Arbiter bridge before broader private-range denies:

```bash
sudo ufw allow in on agent-arbiter0
sudo ufw allow out on agent-arbiter0
sudo ufw allow in from 172.31.250.0/24
sudo ufw insert 1 allow out to 172.31.250.0/24
sudo ufw route allow in on lo out on agent-arbiter0
sudo ufw route allow in on agent-arbiter0 out on lo
```

## Inspect and test

View logs:

```bash
agent-arbiterctl logs
```

Confirm the container is running:

```bash
agent-arbiterctl ps
```

Codex or another MCP client should then use:

```text
http://127.0.0.1:8025/mcp
```

If you change `AGENT_ARBITER_SERVER_PORT` or `AGENT_ARBITER_SERVER_PATH` in the env file, keep the Docker port mapping and MCP client endpoint in sync.

If you change the install target, recreate the container so it installs the new target on startup:

```bash
agent-arbiterctl install --force --install-target /home/omry/dev/agent-arbiter
agent-arbiterctl restart
```

## Update the deployment

To update the deployed Agent Arbiter package target:

```bash
agent-arbiterctl restart
```

The only required host-side change for a package update is replacing
`/opt/agent-arbiter/install-target` with `agent-arbiterctl install --force
--install-target ...` before recreating the container.
