# Arbiter VM Docker Deployment

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
from the Arbiter runtime/credential env file. The repository-local
`arbiterctl` material below is retained as the original checkout-oriented
deployment helper, but most users should use the installed
`arbiter-server deploy docker` flow documented in
`website/docs/operate/deployment.md`.

This deployment path uses Docker Compose and does not require building a custom image.

It runs the stock `python:3.11-slim` image, installs the requested Arbiter package target at container startup, and keeps deployment-specific config and secrets on the host.

The mounted config is generic and resolves its server and mail account settings from the host-side env file.

## Security model

The recommended host layout is operator-editable but root-owned:

- `compose.yaml` and `config.yaml` are owned by `root:root` and mode `0444`
- `arbiter.env` is owned by `root:root` and mode `0600`
- edits go through `arbiterctl`, which opens a private temp copy with
  `ARBITER_EDITOR` or `vim` and then installs it back with `sudo`
- Docker commands go through `sudo docker compose`

This keeps an ordinary host agent from casually changing the gateway policy,
mail settings, or secrets. It is not a boundary against `root`, passwordless
`sudo`, or membership in the `docker` group. Treat the Docker socket and Docker
group as root-equivalent. If a coding agent should not be able to manipulate the
deployment, run that agent as a user without `sudo` and without Docker socket
access.

This also protects the host files, not the Arbiter HTTP API. Any local process
that can reach `http://127.0.0.1:8075` can use whatever Arbiter operations the
configured policy allows.

## Operator helper

The `arbiterctl` helper installs the deployment files under
`/opt/arbiter`, keeps them root-owned, and provides short edit/run commands.
It refuses symlinked deployment paths so an existing host file cannot be
silently targeted by the privileged setup step.
If `config.yaml` or `arbiter.env` already exist, `install` preserves them
instead of replacing local policy or secrets.

From this repository:

```bash
./deploy/arbiterctl install --install-target arbiter-suite==VERSION
```

For a local source checkout on the host, use an absolute host path:

```bash
./deploy/arbiterctl install --install-target /home/omry/dev/arbiter
```

If you intentionally changed the source deployment config and want to replace
the installed config, use:

```bash
./deploy/arbiterctl install --force --install-target /home/omry/dev/arbiter
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
arbiterctl sync-env
```

If `arbiter.env` already exists, `sync-env` preserves values for matching
variable names and writes a timestamped backup beside it. The generated Docker
deployment env always sets `ARBITER_SERVER_HOST=0.0.0.0` because the service
must listen on the container interface for Docker's host-loopback port publish
to reach it; `compose.yaml` still publishes only on host `127.0.0.1`.

Then edit the operator-owned files with:

```bash
arbiterctl edit-env
arbiterctl edit-config
```

The helper defaults to a plain Vim session without user vimrc/plugins and with
insert-mode cursor-shape terminal sequences disabled. Check the installed edit
backend with:

```bash
arbiterctl doctor
```

To use another editor:

```bash
ARBITER_EDITOR='vim -n' arbiterctl edit-env
```

Apply edits by recreating the container:

```bash
arbiterctl restart
```

Start or inspect the service with:

```bash
arbiterctl up
arbiterctl ps
arbiterctl logs
```

After any manual permission changes, restore the intended ownership and modes:

```bash
arbiterctl protect
```

## Host layout

The helper is the preferred setup path. This section shows the underlying host
layout for manual review or partial manual setup. Env generation still goes
through `arbiterctl sync-env` so the flat env file stays derived from the
hierarchical `config.yaml`.

Create the host directory:

```bash
sudo install -d -o root -g root -m 0755 /opt/arbiter
```

Copy the generic deployment files onto the VM host:

```bash
sudo install -o root -g root -m 0444 /path/to/arbiter/deploy/compose.yaml /opt/arbiter/compose.yaml
sudo install -o root -g root -m 0444 /path/to/arbiter/deploy/config.yaml /opt/arbiter/config.yaml
```

Create the install target file:

```bash
printf '%s\n' 'arbiter-suite==VERSION' | sudo tee /opt/arbiter/install-target >/dev/null
sudo chown root:root /opt/arbiter/install-target
sudo chmod 0444 /opt/arbiter/install-target
```

Create the env file from the config's `${oc.env:...}` references. The helper
generates placeholders and defaults that match the currently installed config:

```bash
arbiterctl sync-env
```

The resulting layout is:

```text
/opt/arbiter/
  compose.yaml
  config.yaml
  arbiter.env
  install-target
```

Then edit `/opt/arbiter/arbiter.env` and `/opt/arbiter/config.yaml`.
The helper implements this as a copy-to-temp, edit, install-back workflow:

```bash
arbiterctl edit-env
arbiterctl edit-config
```

Set at least:

- the real SMTP and IMAP values
- any non-default server values

## Run the service

This keeps the service on VM loopback at `http://127.0.0.1:8075` with the
generated env defaults.

```bash
arbiterctl up
```

Manual equivalent for package targets such as `arbiter-suite==VERSION`:

```bash
cd /opt/arbiter
sudo env ARBITER_CONTAINER_INSTALL_TARGET="$(sudo sed -n '1p' /opt/arbiter/install-target)" \
  docker compose --env-file /opt/arbiter/arbiter.env -f /opt/arbiter/compose.yaml up -d
```

For local source checkout targets, use `arbiterctl up`; the helper supplies
the extra read-only bind mount that maps the host checkout into the container.

The install target is a pip install target. For a published package, install
with:

```bash
./deploy/arbiterctl install --install-target arbiter-suite==VERSION
```

For a local source checkout on the host, install with:

```bash
./deploy/arbiterctl install --install-target /home/omry/dev/arbiter
```

When the target is an existing host directory, `arbiterctl` bind-mounts it
read-only into the container. The container copies the package files into a
writable temp directory before running `pip`, so the host checkout remains
stationary and build metadata is not written back into the source tree.

The Compose service:

- installs the requested package target with `pip`
- starts the Arbiter server with the mounted `config.yaml`
- binds the container to `0.0.0.0` internally and publishes it only on host loopback via `127.0.0.1:8075:8075`
- uses a deterministic Docker bridge by default so host firewall rules can target a stable interface and subnet

Default Docker network values:

- Docker network name: `arbiter`
- bridge interface: `arbiter0`
- bridge subnet: `172.31.250.0/24`

If a host already uses that interface name or subnet, override them only for the `docker compose` command:

```bash
cd /opt/arbiter
ARBITER_DOCKER_BRIDGE_NAME=arbiter1 \
ARBITER_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=ARBITER_DOCKER_BRIDGE_NAME,ARBITER_DOCKER_SUBNET \
  docker compose --env-file /opt/arbiter/arbiter.env -f /opt/arbiter/compose.yaml up -d
```

For hosts using UFW, allow the deterministic Arbiter bridge before broader private-range denies:

```bash
sudo ufw allow in on arbiter0
sudo ufw allow out on arbiter0
sudo ufw allow in from 172.31.250.0/24
sudo ufw insert 1 allow out to 172.31.250.0/24
sudo ufw route allow in on lo out on arbiter0
sudo ufw route allow in on arbiter0 out on lo
```

## Inspect and test

View logs:

```bash
arbiterctl logs
```

Confirm the container is running:

```bash
arbiterctl ps
```

Codex or another Arbiter client should then use:

```text
http://127.0.0.1:8075
```

If you change `ARBITER_SERVER_PORT` in the env file, keep the Docker port
mapping and client endpoint in sync.

If you change the install target, recreate the container so it installs the new target on startup:

```bash
arbiterctl install --force --install-target /home/omry/dev/arbiter
arbiterctl restart
```

## Update the deployment

To update the deployed Arbiter package target:

```bash
arbiterctl restart
```

The only required host-side change for a package update is replacing
`/opt/arbiter/install-target` with `arbiterctl install --force
--install-target ...` before recreating the container.
