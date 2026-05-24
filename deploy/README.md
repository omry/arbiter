# Mail Sentry VM Docker Deployment

This deployment path uses Docker Compose and does not require building a custom image.

It runs the stock `python:3.11-slim` image, installs the requested Mail Sentry package version from PyPI at container startup, and keeps deployment-specific config and secrets on the host.

The mounted config is generic and resolves its server and SMTP settings from the host-side env file.

## Security model

The recommended host layout is operator-editable but root-owned:

- `compose.yaml` and `config.yaml` are owned by `root:root` and mode `0444`
- `mail-sentry.env` is owned by `root:root` and mode `0600`
- edits go through `sudoedit`
- Docker commands go through `sudo docker compose`

This keeps an ordinary host agent from casually changing the gateway policy,
SMTP settings, or secrets. It is not a boundary against `root`, passwordless
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
./deploy/mail-sentryctl install
```

Then edit the operator-owned files with:

```bash
mail-sentryctl edit-env
mail-sentryctl edit-config
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

The helper is the preferred setup path. This section is the equivalent manual
layout if you do not want to use `mail-sentryctl`.

Create the host directory:

```bash
sudo install -d -o root -g root -m 0755 /opt/mail-sentry
```

Copy the generic deployment files onto the VM host:

```bash
sudo install -o root -g root -m 0444 /path/to/mail-sentry/deploy/compose.yaml /opt/mail-sentry/compose.yaml
sudo install -o root -g root -m 0444 /path/to/mail-sentry/deploy/config.yaml /opt/mail-sentry/config.yaml
```

Create the env file from the example:

```bash
sudo install -o root -g root -m 0600 /path/to/mail-sentry/deploy/mail-sentry.env.example /opt/mail-sentry/mail-sentry.env
```

The resulting layout is:

```text
/opt/mail-sentry/
  compose.yaml
  config.yaml
  mail-sentry.env
```

Then edit `/opt/mail-sentry/mail-sentry.env` and `/opt/mail-sentry/config.yaml`
with `sudoedit`:

```bash
sudoedit /opt/mail-sentry/mail-sentry.env
sudoedit /opt/mail-sentry/config.yaml
```

Set at least:

- `MAIL_SENTRY_PACKAGE_VERSION`
- the real SMTP values
- any non-default server values

## Run the service

This keeps the service on VM loopback at `http://127.0.0.1:8025/mcp` with the defaults shown in the example env file.

```bash
mail-sentryctl up
```

Manual equivalent:

```bash
cd /opt/mail-sentry
sudo docker compose --env-file /opt/mail-sentry/mail-sentry.env -f /opt/mail-sentry/compose.yaml up -d
```

The Compose service:

- installs the requested package version from PyPI
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

OpenClaw should then use:

```text
http://127.0.0.1:8025/mcp
```

If you change `MAIL_SENTRY_SERVER_PORT` or `MAIL_SENTRY_SERVER_PATH` in the env file, keep the Docker port mapping and OpenClaw endpoint in sync.

If you change `MAIL_SENTRY_PACKAGE_VERSION`, recreate the container so it installs the new version on startup:

```bash
mail-sentryctl restart
```

## Update the deployment

To update the deployed Mail Sentry version:

```bash
mail-sentryctl restart
```

The only required host-side change for a package update is editing `MAIL_SENTRY_PACKAGE_VERSION` in the env file before recreating the container.
