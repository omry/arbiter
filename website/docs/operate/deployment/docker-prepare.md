---
title: Prepare Docker Deployment
---

Prepare and test the deployment directory as an unprivileged operator. This
phase writes files, config, and env locally; it can start the staged container
for smoke testing, but it does not install anything under `/opt`.

## Create the directory

```bash
arbiter-server deploy docker init
```

By default this creates `./arbiter-docker`, including an `arbiter-docker`
helper script for preparing and installing the Arbiter Docker container. `init`
refuses to overwrite existing managed files.

<details>
<summary>Files created by init</summary>

Most operators should use the `arbiter-docker` helper and Arbiter config
commands instead of editing generated deployment files directly.

- `arbiter-docker`: local helper script for this deployment.
- `conf/`: deployment config directory.
- `requirements.txt`: exact package pins or explicit wheel paths installed
  inside the container.
- `wheels/`: prepared dependency wheelhouse.
- `docker.env`: Docker Compose/container wrapper settings.
- `compose.yaml`: generated Docker Compose service definition.
- `compose.override.yaml`: optional generated local Compose overrides.
- `.arbiter-deploy.json`: generated file manifest.

</details>

Prepared Docker directories are staged deployments. They use staging-specific
Docker names and ports so they can run next to an installed Arbiter. During
install, the copied directory is rewritten to the installed identity. For the
networking details, see [Networking](./networking.md).

## Prepare packages

Prepare the dependency wheelhouse before configuring accounts:

```bash
./arbiter-docker/arbiter-docker bundle prepare
```

`bundle prepare` validates `requirements.txt` and builds a complete wheelhouse
from the configured package pins or wheel paths. Running it before config work
catches package resolution, Docker image, and wheel compatibility problems
early. Each run prunes stale wheels that are no longer part of the resolved
runtime install set.

Use `--pypi-only` when `requirements.txt` contains package pins and you want
preparation to resolve the selected package names from the package index
instead of considering existing wheels from the deployment wheelhouse:

```bash
./arbiter-docker/arbiter-docker bundle prepare --pypi-only
```

This rewrites `requirements.txt` to the exact versions selected from the
package index, including pre/dev releases, then builds and validates the
wheelhouse.

If the current Python environment uses editable local Arbiter packages, refresh
the generated package pins and local wheels before preparing the wheelhouse:

```bash
arbiter-server deploy docker update --force
./arbiter-docker/arbiter-docker bundle prepare
```

For Linux install, use pinned packages or generated `/wheels/*.whl` entries and
remove any local source mount. A prepared wheelhouse lets the installed service
start without downloading packages or building wheels at runtime.

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

## Start and test

After adding config and env, start the staged service and smoke test the MCP
endpoint:

```bash
./arbiter-docker/arbiter-docker up
./arbiter-docker/arbiter-docker test
```

`up` prints the MCP URL for this staged directory. `test` calls `version_info`
through that URL and waits through transient startup connection failures.

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

For package pins, wheelhouses, and source checkout testing, see
[Packages and wheels](./packages.md).
