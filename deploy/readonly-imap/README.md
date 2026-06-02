# Read-Only IMAP Docker Deployment

This deployment is for testing a real inbox through Arbiter while keeping
the service read-only and restricted to one configured IMAP folder.

The tracked `config.yaml` intentionally uses a placeholder folder:

```text
TARGET_IMAP_FOLDER
```

Before starting the container, copy the deployment config outside the repo and
replace both `default_folder` and the single key under `folders` with the
canonical folder path from your IMAP server.

## Security Model

- The MCP endpoint is published on host loopback only: `127.0.0.1:8025`.
- The Arbiter config contains no credentials.
- IMAP credentials are Docker secrets mounted at `/run/secrets`.
- The container runs as a non-root user with all Linux capabilities dropped.
- The container root filesystem is read-only; only `/tmp` is writable.
- The Arbiter account has no SMTP config.
- The account policy allows IMAP read/search only, denies move/delete, and keeps
  flags read-only.

Root and users in the Docker group can still inspect Docker secrets and
container state. Keep ordinary coding agents out of the Docker group if the goal
is to make credentials inaccessible during normal agent work.

Docker Compose may warn that `uid`, `gid`, and `mode` metadata for local
file-backed secrets are ignored. The host-side ownership and modes below are
therefore the important permission boundary for the secret files.

## Setup

Create host-side secret files outside the repo. They are owned by root and
group-readable only by numeric group `10001`, which is the container user's
primary group:

```bash
sudo install -d -m 700 -o root -g root /opt/arbiter-readonly/secrets
sudo sh -c 'printf "%s" "YOUR_IMAP_USERNAME" > /opt/arbiter-readonly/secrets/imap_username'
sudo sh -c 'printf "%s" "YOUR_IMAP_PASSWORD_OR_APP_PASSWORD" > /opt/arbiter-readonly/secrets/imap_password'
sudo chown root:10001 /opt/arbiter-readonly/secrets/imap_username /opt/arbiter-readonly/secrets/imap_password
sudo chmod 440 /opt/arbiter-readonly/secrets/imap_username /opt/arbiter-readonly/secrets/imap_password
```

Set the IMAP host details in your shell or in a local `.env` file beside this
compose file. An example lives at `.env.example` and intentionally contains no
credentials:

```bash
ARBITER_IMAP_HOST=imap.example.com
ARBITER_IMAP_PORT=993
ARBITER_IMAP_TLS=implicit
ARBITER_IMAP_VERIFY_PEER=true
ARBITER_HOST_PORT=8025
ARBITER_SECRET_DIR=/opt/arbiter-readonly/secrets
```

Create an untracked runtime config from the template:

```bash
sudo cp config.yaml /opt/arbiter-readonly/config.yaml
sudoedit /opt/arbiter-readonly/config.yaml
sudo chmod 444 /opt/arbiter-readonly/config.yaml
```

Then point compose at that runtime config:

```bash
ARBITER_CONFIG_FILE=/opt/arbiter-readonly/config.yaml
```

Build and run from this directory:

```bash
docker compose up --build -d
```

The MCP endpoint is:

```text
http://127.0.0.1:8025/mcp
```

Inspect logs:

```bash
docker compose logs -f
```
