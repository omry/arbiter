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
- `requirements.txt`: Python packages or source paths installed inside the
  container.
- `compose.override.yaml`: only when `init` infers a local checkout source
  install; mounts the checkout read-only at `/source/arbiter`.
- `arbiter-docker`: the local helper script for this deployment.
- `.arbiter-deploy.json`: hidden manifest that records hashes for
  generated template files.

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
mounts are not production install state. For Linux install, switch
`requirements.txt` to pinned packages or `/wheels/*.whl` entries and remove the
local source mount.

For package pins, wheelhouses, and source checkout testing, see
[Packages and wheels](./packages.md).
