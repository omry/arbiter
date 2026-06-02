---
title: Packages And Wheels
---

`requirements.txt` is a small pip requirements file installed inside the
container at startup.

Package entries must be exact pins such as `arbiter-suite==0.9.0`; unpinned
names and version ranges are rejected by `docker.requirement=...`,
`arbiter-docker doctor`, and service start/restart commands.

## Default package target

By default, `arbiter-server deploy docker init` writes a pinned `meta:all`
package, `arbiter-suite`, when that all-in-one meta package is installed at a
publishable package version. Check the core runtime version with:

```bash
arbiter-server --version
```

The default file looks like:

```text title="./arbiter-docker/requirements.txt"
arbiter-suite==0.9.0
```

That meta package installs the core package and the default plugin packages for
the same release.

## Explicit package pins

If you want explicit package control, seed the file with repeated
`docker.requirement=...` values:

```bash
arbiter-server deploy docker \
  docker.requirement=arbiter-core==0.9.0.dev2 \
  docker.requirement=arbiter-smtp==0.9.0.dev2 \
  init
```

The requirements file is operator-owned deployment state. Arbiter accepts
initial pinned values from CLI input, but it does not auto-update core or
plugin versions. Review version changes, edit the file deliberately, then
restart or reinstall the service.

The PyPI package `arbiter` is unrelated to Arbiter. Use `arbiter-suite`
for the default bundle, or exact pins for Arbiter packages such as
`arbiter-core`, `arbiter-smtp`, and `arbiter-imap`.

## Meta package with overrides

The all-in-one meta package is an exact curated bundle. If you want that bundle
plus a newer version of one package, pass the meta package and the specific
package override together:

```bash
arbiter-server deploy docker \
  docker.requirement=arbiter-suite==0.9.0 \
  docker.requirement=arbiter-smtp==0.9.1 \
  init
```

Arbiter expands that into real package pins in `requirements.txt` so pip
does not see conflicting meta-package dependencies:

```text title="./arbiter-docker/requirements.txt"
arbiter-core==0.9.0
arbiter-smtp==0.9.1
arbiter-imap==0.9.0
```

The zero-code meta package itself is not installed in this expanded form; it is
used only as the bundle selection shorthand.

## Networkless installs

For networkless installs, mount a wheelhouse at `/wheels` with a local Compose
override. When `/wheels` contains `.whl` files, the generated container command
uses `pip install --no-index --find-links /wheels ...`, so every required wheel
must already be present:

```yaml title="./arbiter-docker/compose.override.yaml"
services:
  arbiter:
    volumes:
      - /opt/arbiter/wheels:/wheels:ro
```

The requirements file can keep pinned package names that resolve from the
wheelhouse, or it can name wheels directly:

```text title="./arbiter-docker/requirements.txt"
/wheels/arbiter_core-0.9.0.dev2-py3-none-any.whl
/wheels/arbiter_smtp-0.9.0.dev2-py3-none-any.whl
```

## Local checkout testing

When run from a local checkout with a dev version such as `0.9.0.dev1`, `init`
writes `/source/arbiter/...` requirements and a local
`compose.override.yaml` that mounts the checkout read-only.

For local checkout testing, the only non-pinned entries allowed are absolute
container paths:

```text title="./arbiter-docker/requirements.txt"
/source/arbiter/core
/source/arbiter/smtp
```

Mount the checkout explicitly with a local Compose override when using
`/source/arbiter/...` entries:

```yaml title="./arbiter-docker/compose.override.yaml"
services:
  arbiter:
    volumes:
      - /home/example/arbiter:/source/arbiter:ro
```

At container startup, the deployment copies the mounted checkout to temporary
storage, builds wheels from the referenced source paths, then installs those
wheels.

Source checkout requirements are for testing. `doctor --preinstall` rejects
them for Linux install because they are not production install state. Switch to
pinned packages or `/wheels/*.whl` entries before running `install`, then
remove the `/source/arbiter` mount from `compose.override.yaml`.
