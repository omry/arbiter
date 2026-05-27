# Mail Sentry VM Docker Deployment

This deployment path uses Docker Compose and does not require building a custom image.

It runs the stock `python:3.11-slim` image, installs the requested Mail Sentry package target at container startup, and keeps deployment-specific config and secrets on the host.

The mounted config is generic and resolves its server and mail account settings from the host-side env file.

## Security model

The recommended host layout is operator-editable but root-owned:

- `compose.yaml` and `config.yaml` are owned by `root:root` and mode `0444`
- `mail-sentry.env` is owned by `root:root` and mode `0600`
- edits go through `mail-sentryctl`, which opens a private temp copy with
  `MAIL_SENTRY_EDITOR` or `vim` and then installs it back with `sudo`
- Docker commands go through `sudo docker compose`

This keeps an ordinary host agent from casually changing the gateway policy,
mail settings, or secrets. It is not a boundary against `root`, passwordless
`sudo`, or membership in the `docker` group. Treat the Docker socket and Docker
group as root-equivalent. If a coding agent should not be able to manipulate the
deployment, run that agent as a user without `sudo` and without Docker socket
access.

This also protects the host files, not the MCP API. Any local process that can
reach `http://127.0.0.1:8025/mcp` can use whatever Mail Sentry tools the
configured policy allows.

## Operator helper

The `mail-sentryctl` helper installs the deployment files under
`/opt/mail-sentry`, keeps them root-owned, and provides short edit/run commands.
It refuses symlinked deployment paths so an existing host file cannot be
silently targeted by the privileged setup step.
If `config.yaml` or `mail-sentry.env` already exist, `install` preserves them
instead of replacing local policy or secrets.

From this repository:

```bash
./deploy/mail-sentryctl install --install-target mail-sentry==0.1.1
```

For a local source checkout on the host, use an absolute host path:

```bash
./deploy/mail-sentryctl install --install-target /home/omry/dev/mail-sentry
```

If you intentionally changed the source deployment config and want to replace
the installed config, use:

```bash
./deploy/mail-sentryctl install --force --install-target /home/omry/dev/mail-sentry
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
mail-sentryctl sync-env
```

If `mail-sentry.env` already exists, `sync-env` preserves values for matching
variable names and writes a timestamped backup beside it. The generated Docker
deployment env always sets `MAIL_SENTRY_SERVER_HOST=0.0.0.0` because the service
must listen on the container interface for Docker's host-loopback port publish
to reach it; `compose.yaml` still publishes only on host `127.0.0.1`.

Then edit the operator-owned files with:

```bash
mail-sentryctl edit-env
mail-sentryctl edit-config
```

The helper defaults to a plain Vim session without user vimrc/plugins and with
insert-mode cursor-shape terminal sequences disabled. Check the installed edit
backend with:

```bash
mail-sentryctl doctor
```

To use another editor:

```bash
MAIL_SENTRY_EDITOR='vim -n' mail-sentryctl edit-env
```

Apply edits by recreating the container:

```bash
mail-sentryctl restart
```

Start or inspect the service with:

```bash
mail-sentryctl up
mail-sentryctl ps
mail-sentryctl logs
```

After any manual permission changes, restore the intended ownership and modes:

```bash
mail-sentryctl protect
```

## Host layout

The helper is the preferred setup path. This section shows the underlying host
layout for manual review or partial manual setup. Env generation still goes
through `mail-sentryctl sync-env` so the flat env file stays derived from the
hierarchical `config.yaml`.

Create the host directory:

```bash
sudo install -d -o root -g root -m 0755 /opt/mail-sentry
```

Copy the generic deployment files onto the VM host:

```bash
sudo install -o root -g root -m 0444 /path/to/mail-sentry/deploy/compose.yaml /opt/mail-sentry/compose.yaml
sudo install -o root -g root -m 0444 /path/to/mail-sentry/deploy/config.yaml /opt/mail-sentry/config.yaml
```

Create the install target file:

```bash
printf '%s\n' 'mail-sentry==0.1.1' | sudo tee /opt/mail-sentry/install-target >/dev/null
sudo chown root:root /opt/mail-sentry/install-target
sudo chmod 0444 /opt/mail-sentry/install-target
```

Create the env file from the config's `${oc.env:...}` references. The helper
generates placeholders and defaults that match the currently installed config:

```bash
mail-sentryctl sync-env
```

The resulting layout is:

```text
/opt/mail-sentry/
  compose.yaml
  config.yaml
  mail-sentry.env
  install-target
```

Then edit `/opt/mail-sentry/mail-sentry.env` and `/opt/mail-sentry/config.yaml`.
The helper implements this as a copy-to-temp, edit, install-back workflow:

```bash
mail-sentryctl edit-env
mail-sentryctl edit-config
```

Set at least:

- the real SMTP and IMAP values
- any non-default server values

## Run the service

This keeps the service on VM loopback at `http://127.0.0.1:8025/mcp` with the generated env defaults.

```bash
mail-sentryctl up
```

Manual equivalent for package targets such as `mail-sentry==0.1.1`:

```bash
cd /opt/mail-sentry
sudo env MAIL_SENTRY_CONTAINER_INSTALL_TARGET="$(sudo sed -n '1p' /opt/mail-sentry/install-target)" \
  docker compose --env-file /opt/mail-sentry/mail-sentry.env -f /opt/mail-sentry/compose.yaml up -d
```

For local source checkout targets, use `mail-sentryctl up`; the helper supplies
the extra read-only bind mount that maps the host checkout into the container.

The install target is a pip install target. For a published package, install
with:

```bash
./deploy/mail-sentryctl install --install-target mail-sentry==0.1.1
```

For a local source checkout on the host, install with:

```bash
./deploy/mail-sentryctl install --install-target /home/omry/dev/mail-sentry
```

When the target is an existing host directory, `mail-sentryctl` bind-mounts it
read-only into the container. The container copies the package files into a
writable temp directory before running `pip`, so the host checkout remains
stationary and build metadata is not written back into the source tree.

The Compose service:

- installs the requested package target with `pip`
- starts the Mail Sentry server with the mounted `config.yaml`
- binds the container to `0.0.0.0` internally and publishes it only on host loopback via `127.0.0.1:8025:8025`
- uses a deterministic Docker bridge by default so host firewall rules can target a stable interface and subnet

Default Docker network values:

- Docker network name: `mail-sentry`
- bridge interface: `mail-sentry0`
- bridge subnet: `172.31.250.0/24`

If a host already uses that interface name or subnet, override them only for the `docker compose` command:

```bash
cd /opt/mail-sentry
MAIL_SENTRY_DOCKER_BRIDGE_NAME=mail-sentry1 \
MAIL_SENTRY_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=MAIL_SENTRY_DOCKER_BRIDGE_NAME,MAIL_SENTRY_DOCKER_SUBNET \
  docker compose --env-file /opt/mail-sentry/mail-sentry.env -f /opt/mail-sentry/compose.yaml up -d
```

For hosts using UFW, allow the deterministic Mail Sentry bridge before broader private-range denies:

```bash
sudo ufw allow in on mail-sentry0
sudo ufw allow out on mail-sentry0
sudo ufw allow in from 172.31.250.0/24
sudo ufw insert 1 allow out to 172.31.250.0/24
sudo ufw route allow in on lo out on mail-sentry0
sudo ufw route allow in on mail-sentry0 out on lo
```

## Inspect and test

View logs:

```bash
mail-sentryctl logs
```

Confirm the container is running:

```bash
mail-sentryctl ps
```

Codex or another MCP client should then use:

```text
http://127.0.0.1:8025/mcp
```

If you change `MAIL_SENTRY_SERVER_PORT` or `MAIL_SENTRY_SERVER_PATH` in the env file, keep the Docker port mapping and MCP client endpoint in sync.

If you change the install target, recreate the container so it installs the new target on startup:

```bash
mail-sentryctl install --force --install-target /home/omry/dev/mail-sentry
mail-sentryctl restart
```

## Update the deployment

To update the deployed Mail Sentry package target:

```bash
mail-sentryctl restart
```

The only required host-side change for a package update is replacing
`/opt/mail-sentry/install-target` with `mail-sentryctl install --force
--install-target ...` before recreating the container.
