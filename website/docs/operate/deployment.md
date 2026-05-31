---
title: Deployment
---

Agent Arbiter can write the files for a local Docker Compose deployment from
the installed `arbiter-server` command. The generated service installs the
requested Agent Arbiter package target at container startup, mounts
operator-owned config from the host, and publishes MCP on host loopback by
default:

```text
http://127.0.0.1:8025/mcp
```

## Create the deployment

Write a new deployment directory:

```bash
arbiter-server deploy docker init
```

By default this creates `./arbiter-docker`. For a VM-style deployment,
choose an operator-owned directory explicitly:

```bash
arbiter-server deploy docker docker.dir=/opt/arbiter-server init
```

`init` is a file generator. It does not start Docker, run the server, or create
an Agent Arbiter config. It refuses to overwrite existing deployment files and
writes:

- `compose.yaml`: Docker Compose service definition.
- `docker.env`: Docker Compose/container wrapper settings such as host port,
  image, restart policy, config directory/name, and network values.
- `conf/`: default config directory. `init` creates the directory but not the
  config or env file.
- `requirements.txt`: Python packages or source paths installed inside the
  container.
- `compose.override.yaml`: only when `init` infers a local checkout source
  install; mounts the checkout read-only at `/source/agent-arbiter`.
- `arbiter-docker`: the local helper script for this deployment.
- `.agent-arbiter-deploy.json`: hidden manifest that records hashes for
  generated template files.

Provide config separately. Either bootstrap a config into the default
deployment config directory:

```bash
arbiter-server \
  --config-dir /opt/arbiter-server/conf \
  --config-name arbiter-server \
  bootstrap arbiter
```

Or copy an existing Agent Arbiter config directory to
`/opt/arbiter-server/conf`. If you use a different directory or main config
name, edit `AGENT_ARBITER_CONFIG_DIR` or `AGENT_ARBITER_CONFIG_NAME` in
`docker.env`.

After the config exists, bootstrap or update its env file with the normal env
tooling:

```bash
arbiter-server \
  --config-dir /opt/arbiter-server/conf \
  --config-name arbiter-server \
  env bootstrap
```

Keep `docker.env` separate from `conf/.env`: `docker.env` controls the Compose
wrapper, while `conf/.env` is created by Agent Arbiter env tooling and belongs
to the config package.

## Configure and start

The generated helper lives in the deployment directory. It is not a separate
global console app:

```bash
# Show generated paths and Docker Compose version.
/opt/arbiter-server/arbiter-docker info

# Check deployment files and Docker Compose availability.
/opt/arbiter-server/arbiter-docker doctor

# Also check common permission mistakes for the agent user.
/opt/arbiter-server/arbiter-docker doctor --agent-user codex

# Edit Agent Arbiter runtime values and credentials.
/opt/arbiter-server/arbiter-docker edit-env

# Edit Docker wrapper settings.
/opt/arbiter-server/arbiter-docker edit-docker

# Edit pinned Agent Arbiter core/plugin package requirements.
/opt/arbiter-server/arbiter-docker edit-requirements

# Start or update the Compose service.
/opt/arbiter-server/arbiter-docker up
```

- `info`: print generated paths and Docker Compose version.
- `doctor`: check that generated files exist, env files use `KEY=VALUE`
  syntax, `requirements.txt` uses exact package pins or absolute container
  paths, and Docker Compose is available.
- `doctor --agent-user USER`: also check common permission mistakes for an
  agent identity, such as write access to deployment files, read access to
  `conf/.env`, or Docker socket access.
- `edit-env`: edit Agent Arbiter runtime values and credentials.
- `edit-docker`: edit Docker wrapper settings.
- `edit-requirements`: edit the pinned packages installed inside the container.
- `up`: start or update the Compose service.

After choosing or editing config, resync environment keys. You can use the
normal `arbiter-server env bootstrap` command shown above, or the deployment
helper shorthand:

```bash
/opt/arbiter-server/arbiter-docker sync-env
```

`sync-env` runs `arbiter-server env bootstrap` against the configured config
directory. It creates or updates the config package's env file using the same
logic as the normal env command.

Useful service commands:

```bash
/opt/arbiter-server/arbiter-docker ps
/opt/arbiter-server/arbiter-docker logs
/opt/arbiter-server/arbiter-docker restart
/opt/arbiter-server/arbiter-docker down
```

- `ps`: show Docker Compose service status.
- `logs`: follow service logs.
- `restart`: recreate the container, which also reinstalls the configured
  requirements.
- `down`: stop and remove the Compose service.

## Update the deployment

Use `update` to refresh a deployment directory after Agent Arbiter changes its
generated Docker templates:

```bash
arbiter-server deploy docker docker.dir=/opt/arbiter-server update
```

`update` may rewrite existing files that are still generated-owned. It uses the
hidden manifest to tell the difference between generated files that are
unchanged and files the operator has taken over:

- Manifest-owned templates: `compose.yaml` and `arbiter-docker` are rewritten
  when they are missing, or when the manifest says Agent Arbiter generated them
  and their current content still matches the recorded hash. If one of those
  files exists but is not in the manifest, or if its hash changed, `update`
  skips it.
- Local state files: `docker.env` is regenerated while preserving known and
  extra local values. `conf/.env` is not generated by deploy; create or update
  it with `arbiter-server env bootstrap` or `arbiter-docker sync-env`.
- Requirements: `update` never rewrites an existing `requirements.txt`. If it
  is missing, `update` creates one from `docker.requirement=...` values or the
  default package/source requirements. If that default is a local checkout
  source install, it also creates `compose.override.yaml` when the override is
  missing.

To change the deployed Agent Arbiter version or plugin set, edit the
requirements file, then recreate the container:

```bash
/opt/arbiter-server/arbiter-docker edit-requirements
/opt/arbiter-server/arbiter-docker restart
```

## Requirements

`requirements.txt` is a small pip requirements file installed inside the
container at startup. Package entries must be exact pins such as
`agent-arbiter==0.1.1`; unpinned names and version ranges are rejected by
`docker.requirement=...`, `arbiter-docker doctor`, and service start/restart
commands. By default, `arbiter-server deploy docker init` writes a pinned Agent
Arbiter meta package matching the `arbiter-server` command that generated the
deployment when that command comes from a publishable package version. When run
from a local checkout with a dev version such as `0.1.1.dev1`, `init` instead
writes `/source/agent-arbiter/...` requirements and a local
`compose.override.yaml` that mounts the checkout read-only. Check the command
version with:

```bash
arbiter-server --version
```

The default file looks like:

```text title="/opt/arbiter-server/requirements.txt"
agent-arbiter==0.1.1
```

That meta package installs the core package and the default plugin packages for
the same release. If you want explicit plugin control, seed the file with
repeated `docker.requirement=...` values:

```bash
arbiter-server deploy docker \
  docker.dir=/opt/arbiter-server \
  docker.requirement=agent-arbiter-core==0.1.1 \
  docker.requirement=agent-arbiter-smtp==0.1.1 \
  init
```

The requirements file is operator-owned deployment state. Agent Arbiter accepts
initial pinned values from CLI input, but it does not auto-update core or plugin
versions. Review version changes, edit the file deliberately, then restart the
container.

For networkless installs, mount a wheelhouse at `/wheels` with a local Compose
override. When `/wheels` contains `.whl` files, the generated container command
uses `pip install --no-index --find-links /wheels ...`, so every required wheel
must already be present:

```yaml title="/opt/arbiter-server/compose.override.yaml"
services:
  agent-arbiter:
    volumes:
      - /opt/arbiter-server/wheels:/wheels:ro
```

The requirements file can keep pinned package names that resolve from the
wheelhouse, or it can name wheels directly:

```text title="/opt/arbiter-server/requirements.txt"
/wheels/agent_arbiter_core-0.1.1-py3-none-any.whl
/wheels/agent_arbiter_smtp-0.1.1-py3-none-any.whl
```

For local checkout testing, the only non-pinned entries allowed are absolute
container paths. Point the deployment at a read-only source mount and use those
paths in `requirements.txt`:

```text title="/opt/arbiter-server/requirements.txt"
/source/agent-arbiter/core
/source/agent-arbiter/smtp
```

Mount the checkout explicitly with a local Compose override when using
`/source/agent-arbiter/...` entries:

```yaml title="/opt/arbiter-server/compose.override.yaml"
services:
  agent-arbiter:
    volumes:
      - /home/example/agent-arbiter:/source/agent-arbiter:ro
```

At container startup, the deployment copies the mounted checkout to temporary
storage, builds wheels from the referenced source paths, then installs those
wheels.

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
cd /opt/arbiter-server
AGENT_ARBITER_DOCKER_BRIDGE_NAME=arbiter1 \
AGENT_ARBITER_DOCKER_SUBNET=172.31.251.0/24 \
sudo --preserve-env=AGENT_ARBITER_DOCKER_BRIDGE_NAME,AGENT_ARBITER_DOCKER_SUBNET \
  docker compose --env-file /opt/arbiter-server/docker.env \
  -f /opt/arbiter-server/compose.yaml up -d
```

The helper normally wraps `docker compose` for you. Use the manual form only
when you need one-off Docker options that the helper does not expose.

## Security notes

The deployment files are intended to be operator-owned. For a shared VM, keep
the deployment directory outside any agent-writable workspace and protect it
with normal filesystem permissions:

- `compose.yaml`, the configured config directory, and `requirements.txt`
  should be writable only by the operator.
- `conf/.env` contains credentials and should be readable only by the operator
  running Docker Compose.

Do not run coding agents as a user with Docker socket access, membership in the
Docker group, or passwordless `sudo` if the deployment is meant to constrain
them. Docker control is root-equivalent.

The host file permissions protect deployment state and secrets. They do not add
authentication to the MCP API. Any local process that can reach
`http://127.0.0.1:8025/mcp` can use the tools allowed by the configured policy.
