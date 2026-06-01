---
title: Packages And Wheels
---

`requirements.txt` is a small pip requirements file installed inside the
container at startup.

Package entries must be exact pins such as `agent-arbiter==0.9.0.dev1`; unpinned
names and version ranges are rejected by `docker.requirement=...`,
`arbiter-docker doctor`, and service start/restart commands.

## Default package target

By default, `arbiter-server deploy docker init` writes a pinned Agent Arbiter
meta package matching the `arbiter-server` command that generated the
deployment when that command comes from a publishable package version. Check the
command version with:

```bash
arbiter-server --version
```

The default file looks like:

```text title="./arbiter-docker/requirements.txt"
agent-arbiter==0.9.0.dev1
```

That meta package installs the core package and the default plugin packages for
the same release.

## Explicit plugin pins

If you want explicit plugin control, seed the file with repeated
`docker.requirement=...` values:

```bash
arbiter-server deploy docker \
  docker.requirement=agent-arbiter-core==0.9.0.dev1 \
  docker.requirement=agent-arbiter-smtp==0.9.0.dev1 \
  init
```

The requirements file is operator-owned deployment state. Agent Arbiter accepts
initial pinned values from CLI input, but it does not auto-update core or
plugin versions. Review version changes, edit the file deliberately, then
restart or reinstall the service.

## Networkless installs

For networkless installs, mount a wheelhouse at `/wheels` with a local Compose
override. When `/wheels` contains `.whl` files, the generated container command
uses `pip install --no-index --find-links /wheels ...`, so every required wheel
must already be present:

```yaml title="./arbiter-docker/compose.override.yaml"
services:
  agent-arbiter:
    volumes:
      - /opt/arbiter/wheels:/wheels:ro
```

The requirements file can keep pinned package names that resolve from the
wheelhouse, or it can name wheels directly:

```text title="./arbiter-docker/requirements.txt"
/wheels/agent_arbiter_core-0.9.0.dev1-py3-none-any.whl
/wheels/agent_arbiter_smtp-0.9.0.dev1-py3-none-any.whl
```

## Local checkout testing

When run from a local checkout with a dev version such as `0.9.0.dev1`, `init`
writes `/source/agent-arbiter/...` requirements and a local
`compose.override.yaml` that mounts the checkout read-only.

For local checkout testing, the only non-pinned entries allowed are absolute
container paths:

```text title="./arbiter-docker/requirements.txt"
/source/agent-arbiter/core
/source/agent-arbiter/smtp
```

Mount the checkout explicitly with a local Compose override when using
`/source/agent-arbiter/...` entries:

```yaml title="./arbiter-docker/compose.override.yaml"
services:
  agent-arbiter:
    volumes:
      - /home/example/agent-arbiter:/source/agent-arbiter:ro
```

At container startup, the deployment copies the mounted checkout to temporary
storage, builds wheels from the referenced source paths, then installs those
wheels.

Source checkout requirements are for testing. `doctor --preinstall` rejects
them for Linux install because they are not production install state. Switch to
pinned packages or `/wheels/*.whl` entries before running `install`, then
remove the `/source/agent-arbiter` mount from `compose.override.yaml`.
