---
title: Packages And Wheels
---

`requirements.txt` is a small pip requirements file installed inside the
container at startup.

Package entries must be exact pins such as `arbiter-core==0.9.0`; unpinned
names and version ranges are rejected by `docker.requirement=...`,
`arbiter-docker doctor`, and service start/restart commands.

## Default package target

By default, `arbiter-server deploy docker init` follows the Arbiter core
package and service plugins installed in the current Python environment. Check
the loaded versions with:

```bash
arbiter-server version
```

For packages installed from a package index, the default file uses exact pins:

```text title="./arbiter-docker/requirements.txt"
arbiter-core==0.9.0
arbiter-imap==0.9.0
arbiter-smtp==0.9.0
```

The generated pins follow the package versions already loaded by
`arbiter-server`. This keeps Docker installs aligned with the Python
environment you used to prepare the deployment.

For editable local packages, such as an Arbiter checkout installed with
`pip install -e`, Arbiter builds wheels into `./arbiter-docker/wheels` and
keeps package pins in `requirements.txt`:

```text title="./arbiter-docker/requirements.txt"
arbiter-core==0.9.0.dev2
arbiter-imap==0.9.0.dev2
arbiter-smtp==0.9.0.dev2
```

The generated Compose file mounts `./wheels` at `/wheels`, so the deployment
can satisfy those pins from the prepared wheelhouse when promoted to a Linux
host.

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

## Service plugin selection

Use the bundle plugin menu for the usual case of selecting Arbiter service
plugins:

```bash
./arbiter-docker/arbiter-docker bundle list-plugins
./arbiter-docker/arbiter-docker bundle add imap
./arbiter-docker/arbiter-docker bundle add smtp
./arbiter-docker/arbiter-docker bundle add arbiter-suite
./arbiter-docker/arbiter-docker bundle remove smtp
./arbiter-docker/arbiter-docker bundle remove arbiter-suite
./arbiter-docker/arbiter-docker bundle list
```

`bundle add` and `bundle remove` update `requirements.txt`. Adding a plugin
uses the current `arbiter-core` package version as the plugin pin; use
`bundle upgrade` afterwards when you want the resolver to choose newer package
versions. Adding or removing `arbiter-suite` is interpreted as adding or
removing all plugins in that meta package. Removing a plugin or meta package
from an `arbiter-suite` requirement expands the suite into `arbiter-core` plus
the remaining selected plugin pins.

After editing the root requirements, prepare the wheelhouse:

```bash
./arbiter-docker/arbiter-docker bundle prepare
./arbiter-docker/arbiter-docker bundle prepare --pypi-only
```

`bundle prepare` builds a complete wheelhouse with the configured runtime image
and validates that the wheelhouse can satisfy the networkless runtime install
command. It also removes stale wheels that are not part of that resolved
install set. With package pins, use `--pypi-only` to resolve the selected
package names from the package index, including pre/dev releases, rewrite
`requirements.txt` to those exact versions, and ignore the existing deployment
wheelhouse while building.

Use `bundle check` to validate an already-prepared wheelhouse without
downloading packages or building wheels.

Use `bundle list` to show the root requirements. Use `bundle list all` after
preparation to show every package in the wheelhouse, marked as `root` or
`transitive`.

Use `bundle upgrade` to upgrade package root requirements and rebuild the
wheelhouse:

```bash
./arbiter-docker/arbiter-docker bundle upgrade
./arbiter-docker/arbiter-docker bundle upgrade --pypi-only
./arbiter-docker/arbiter-docker bundle upgrade 0.9
./arbiter-docker/arbiter-docker bundle upgrade arbiter-smtp
./arbiter-docker/arbiter-docker bundle upgrade arbiter-smtp==0.9.4
```

`bundle upgrade` upgrades the prepared bundle. To upgrade a Linux systemd
deployment, run `install` again after the bundle is prepared. Wheel roots such
as `/wheels/*.whl` are local artifacts, so a no-argument `bundle upgrade`
refreshes the wheelhouse without changing those root wheel paths. On success,
the command reports changed root packages first, followed by changed
transitive packages.

When run from an Arbiter source checkout, `bundle upgrade` first builds local
Arbiter wheels for matching root packages and lets the resolver consider those
wheels. Use `--pypi-only` to resolve upgrades from the package index only.

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

For networkless installs, put wheels in `./arbiter-docker/wheels`, or set
`ARBITER_WHEELS_DIR` in `docker.env` to another relative wheelhouse path. The
generated Compose file mounts that directory at `/wheels`. Linux install
rejects absolute host paths for runtime files so the service does not read from
outside the installation path.

```text title="./arbiter-docker/docker.env"
ARBITER_WHEELS_DIR=./wheels
```

During preparation, `arbiter-docker bundle prepare` builds a complete wheelhouse
with the configured runtime image, including wheels built from source
distributions when an index does not provide a prebuilt wheel. The generated
container command then uses
`pip install --no-index --find-links /wheels ...`, so service startup does not
depend on network access. Install validates the promoted wheelhouse before
writing or restarting the systemd service. The requirements file can keep
pinned package names that resolve from the wheelhouse. For explicit local
artifact bundles, it can also name container wheel paths directly:

```text title="./arbiter-docker/requirements.txt"
/wheels/arbiter_core-0.9.0.dev2-py3-none-any.whl
/wheels/arbiter_smtp-0.9.0.dev2-py3-none-any.whl
```

## Local checkout testing

Local checkout installs are explicit testing state. The only non-pinned
entries allowed are absolute container paths:

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
