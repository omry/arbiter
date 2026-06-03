---
title: Prepare Docker Deployment
---

Prepare the deployment directory as an unprivileged operator. This phase writes
files, config, and env locally; it does not start Docker or install anything
under `/opt`.

## Create the directory

```bash
arbiter-server deploy docker init
```

By default this creates `./arbiter-docker`. `init` refuses to overwrite existing
managed files and writes:

- `compose.yaml`: Docker Compose service definition.
- `docker.env`: Docker Compose/container wrapper settings such as host port,
  image, restart policy, config directory/name, and network values.
- `conf/`: default config directory. `init` creates the directory but not the
  config or env file.
- `requirements.txt`: exact package pins or explicit wheel paths installed
  inside the container.
- `wheels/`: generated wheelhouse when the current Python environment contains
  editable local Arbiter packages.
- `compose.override.yaml`: optional local Compose overrides, such as an
  explicit source checkout mount for testing.
- `arbiter-docker`: the local helper script for this deployment.
- `.arbiter-deploy.json`: hidden manifest that records hashes for
  generated template files.

Prepared Docker directories are staged deployments. The generated Compose
command passes `arbiter.deployment_scope=staged` to the server, so startup
logs, `version_info`, and clients can distinguish a staged server from an
installed one. Staged directories also use staging-specific Docker wrapper
defaults so they can run next to an installed Arbiter without changing files by
hand:

- MCP URL: `http://127.0.0.1:18025/mcp`
- container name: `arbiter-staging`
- Docker network: `arbiter-staging`
- bridge interface: `arbiter-stg0`
- bridge subnet: `172.31.251.0/24`

When `up` sees that the staged subnet overlaps an existing Docker network, the
helper rewrites `ARBITER_DOCKER_SUBNET` in `docker.env` to an unused staging
candidate before starting Compose.

## Add config

Either bootstrap a config into the default deployment config directory:

```bash
arbiter-server \
  --config-dir ./arbiter-docker/conf \
  --config-name arbiter-server \
  bootstrap arbiter
```

Or copy an existing Arbiter config directory to `./arbiter-docker/conf`.
If you use a different directory or main config name, edit
`ARBITER_CONFIG_DIR` or `ARBITER_CONFIG_NAME` in `docker.env`.

## Sync env

After the config exists, bootstrap or update its env file:

```bash
./arbiter-docker/arbiter-docker sync-env
```

`sync-env` runs `arbiter-server env bootstrap` against the configured config
directory. It creates or updates the config package's env file using the same
logic as the normal env command.

Keep `docker.env` separate from `conf/.env`: `docker.env` controls the Compose
wrapper, while `conf/.env` is created by Arbiter env tooling and belongs
to the config package.

## Preinstall check

Before promoting the directory to a host install:

```bash
./arbiter-docker/arbiter-docker doctor --preinstall
```

`doctor --preinstall` checks that the prepared directory is self-contained and
ready to promote. It skips Docker daemon checks and fails when source checkout
requirements or `/source/arbiter` mounts are present, because local source
mounts are not production install state. It also rejects absolute host paths in
`docker.env` for runtime files; use paths relative to the deployment directory
so the installed service only reads from the installation path.

If the current Python environment uses editable local Arbiter packages, refresh
the generated package pins and local wheels before preparing the wheelhouse:

```bash
arbiter-server deploy docker update --force
```

Then prepare the wheelhouse with:

```bash
./arbiter-docker/arbiter-docker bundle prepare
```

For Linux install, use pinned packages or generated `/wheels/*.whl` entries and
remove any local source mount. `bundle prepare` prepares a complete wheelhouse
so the installed service can start without downloading packages or building
wheels at runtime.

For package pins, wheelhouses, and source checkout testing, see
[Packages and wheels](./packages.md).
