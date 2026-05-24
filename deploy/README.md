# Mail Sentry VM Docker Deployment

This deployment path uses Docker Compose and does not require building a custom image.

It runs the stock `python:3.11-slim` image, installs the requested Mail Sentry package version from PyPI at container startup, and keeps deployment-specific config and secrets on the host.

The mounted config is generic and resolves its server and SMTP settings from the host-side env file.

## Host layout

Create the host directory:

```bash
sudo mkdir -p /opt/mail-sentry
```

Copy the generic deployment files onto the VM host:

```bash
sudo cp /path/to/mail-sentry/deploy/compose.yaml /opt/mail-sentry/compose.yaml
sudo cp /path/to/mail-sentry/deploy/config.yaml /opt/mail-sentry/config.yaml
```

Create the env file from the example:

```bash
sudo cp /path/to/mail-sentry/deploy/mail-sentry.env.example /opt/mail-sentry/mail-sentry.env
sudo chmod 600 /opt/mail-sentry/mail-sentry.env
```

The resulting layout is:

```text
/opt/mail-sentry/
  compose.yaml
  config.yaml
  mail-sentry.env
```

Then edit `/opt/mail-sentry/mail-sentry.env` and set:

- `MAIL_SENTRY_PACKAGE_VERSION`
- the real SMTP values
- any non-default server values

## Run the service

This keeps the service on VM loopback at `http://127.0.0.1:8025/mcp` with the defaults shown in the example env file.

```bash
cd /opt/mail-sentry
docker compose up -d
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
docker compose up -d
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
cd /opt/mail-sentry
docker compose logs -f
```

Confirm the container is running:

```bash
cd /opt/mail-sentry
docker compose ps
```

OpenClaw should then use:

```text
http://127.0.0.1:8025/mcp
```

If you change `MAIL_SENTRY_SERVER_PORT` or `MAIL_SENTRY_SERVER_PATH` in the env file, keep the Docker port mapping and OpenClaw endpoint in sync.

If you change `MAIL_SENTRY_PACKAGE_VERSION`, recreate the container so it installs the new version on startup:

```bash
cd /opt/mail-sentry
docker compose up -d --force-recreate
```

## Update the deployment

To update the deployed Mail Sentry version:

```bash
cd /opt/mail-sentry
docker compose up -d --force-recreate
```

The only required host-side change for a package update is editing `MAIL_SENTRY_PACKAGE_VERSION` in the env file before recreating the container.
