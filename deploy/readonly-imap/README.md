# Read-Only IMAP Docker Deployment

This deployment is for testing a real inbox through Agent Arbiter while keeping
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
- The Agent Arbiter config contains no credentials.
- IMAP credentials are Docker secrets mounted at `/run/secrets`.
- The container runs as a non-root user with all Linux capabilities dropped.
- The container root filesystem is read-only; only `/tmp` is writable.
- The Agent Arbiter account has no SMTP config.
- The account policy allows IMAP read/search only, denies move/delete, and keeps
  flags read-only.

Root and users in the Docker group can still inspect Docker secrets and
container state. Keep ordinary coding agents out of the Docker group if the goal
is to make credentials inaccessible during normal agent work.

## Setup

Create host-side secret files outside the repo. They are owned by root and
group-readable only by numeric group `10001`, which is the container user's
primary group:

```bash
sudo install -d -m 700 -o root -g root /opt/agent-arbiter-readonly/secrets
sudo sh -c 'printf "%s" "YOUR_IMAP_USERNAME" > /opt/agent-arbiter-readonly/secrets/imap_username'
sudo sh -c 'printf "%s" "YOUR_IMAP_PASSWORD_OR_APP_PASSWORD" > /opt/agent-arbiter-readonly/secrets/imap_password'
sudo chown root:10001 /opt/agent-arbiter-readonly/secrets/imap_username /opt/agent-arbiter-readonly/secrets/imap_password
sudo chmod 440 /opt/agent-arbiter-readonly/secrets/imap_username /opt/agent-arbiter-readonly/secrets/imap_password
```

Set the IMAP host details in your shell or in a local `.env` file beside this
compose file. An example lives at `.env.example` and intentionally contains no
credentials:

```bash
AGENT_ARBITER_IMAP_HOST=imap.example.com
AGENT_ARBITER_IMAP_PORT=993
AGENT_ARBITER_IMAP_TLS=implicit
AGENT_ARBITER_IMAP_VERIFY_PEER=true
AGENT_ARBITER_HOST_PORT=8025
AGENT_ARBITER_SECRET_DIR=/opt/agent-arbiter-readonly/secrets
```

Create an untracked runtime config from the template:

```bash
sudo cp config.yaml /opt/agent-arbiter-readonly/config.yaml
sudoedit /opt/agent-arbiter-readonly/config.yaml
sudo chmod 444 /opt/agent-arbiter-readonly/config.yaml
```

Then point compose at that runtime config:

```bash
AGENT_ARBITER_CONFIG_FILE=/opt/agent-arbiter-readonly/config.yaml
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
