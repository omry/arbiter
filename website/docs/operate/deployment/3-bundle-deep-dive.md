---
title: Bundle deep-dive
---

This is the package reference behind
[Prepare Docker Deployment](./1-docker-prepare.md). The runbook owns the command
sequence; this page explains what the package bundle is and how to reason about
it.

The bundle has two deployment-owned pieces:

- `requirements.txt`: root Arbiter packages selected for this deployment.
- `wheels/`: the complete wheelhouse used by the container at startup.

The goal is reproducible container creation. Staging resolves package versions
and prepares wheels before install, so Docker can create or recreate the
runtime container without reaching PyPI.

## Package roots

`requirements.txt` records the root packages for the deployment. Normal package
roots are exact pins:

```text title="./requirements.txt"
arbiter-server==0.9.0.dev2
arbiter-imap==0.9.0.dev2
arbiter-smtp==0.9.0.dev2
```

Unpinned names and version ranges are rejected for runtime deployment state.
Use the bundle commands in the prepare runbook to select plugins or upgrade
versions.

By default, `arbiter-server deploy docker init` seeds these roots from the
Arbiter server package and service plugins loaded in the current Python
environment. Check those versions with `arbiter-server version`.

`bundle add` and `bundle remove` update `requirements.txt`. Adding
`arbiter-suite` selects all plugins in the suite meta package. When the suite
must be represented as concrete runtime roots, Arbiter expands it into
`arbiter-server` plus the selected plugin packages.

The package named `arbiter` on PyPI is unrelated to this project. Use
`arbiter-suite` or concrete packages such as `arbiter-server`, `arbiter-smtp`,
and `arbiter-imap`.

## Wheelhouse

`bundle prepare` validates `requirements.txt`, resolves the full runtime
install set with the configured runtime image, and writes all required wheels
to `wheels/`. Each run expands the wheelhouse fully and prunes stale wheels
that are no longer part of the resolved install set.

When the staging directory is in an Arbiter repository checkout, normal
`bundle prepare` first rebuilds local Arbiter wheels from that checkout. Use
`bundle prepare --pypi-only` only when the selected Arbiter package names should
come from the package index instead of the checkout.

The generated Compose file mounts `./wheels` at `/wheels`. During container
startup, the install command uses `/wheels`, so startup does not depend on
package-index access.

Use `bundle check` to validate an already-prepared wheelhouse without
downloading packages or building wheels. Use `bundle list all` after
preparation to show every wheelhouse package, marked as `root` or
`transitive`.

The default Docker env keeps the wheelhouse inside the deployment directory:
`ARBITER_WHEELS_DIR=./wheels`. Keep runtime paths relative to the deployment
directory; Linux install rejects absolute host paths for runtime files.

Plugin writable state is also kept under the deployment directory by default:
`ARBITER_PLUGIN_DATA_DIR=./data/plugins`. Docker mounts that host directory at
`/data/plugins`, and the generated Compose command passes it to Arbiter as
`arbiter.storage.plugin_data_dir=/data/plugins`.

## Upgrades

`bundle upgrade` upgrades root package requirements and rebuilds the
wheelhouse. It reports changed root packages first, followed by changed
transitive packages.

For an installed Linux systemd deployment, prepare and test the staging
directory first, then run `install` again.

Common upgrade targets are: no argument for all selected packages, a release
line such as `0.9`, a package such as `arbiter-smtp`, or an exact pin such as
`arbiter-smtp==0.9.4`.
Prerelease pins such as `0.9.0.dev1` are useful for validating a release line
before the final package release.

Skip upgrade when you want to keep the versions already recorded in
`requirements.txt`.

<details>
<summary>Explicit package roots</summary>

Most deployments should use the bundle menu. To seed exact package roots during
init, pass repeated `docker.requirement=...` values:

```bash
arbiter-server deploy docker \
  docker.requirement=arbiter-server==0.9.0.dev2 \
  docker.requirement=arbiter-smtp==0.9.0.dev2 \
  init
```

The same mechanism can express a curated suite plus an override:

```bash
arbiter-server deploy docker \
  docker.requirement=arbiter-suite==0.9.0 \
  docker.requirement=arbiter-smtp==0.9.1 \
  init
```

Arbiter expands that selection to concrete package pins so pip does not see
conflicting meta-package dependencies:

```text title="./requirements.txt"
arbiter-server==0.9.0
arbiter-smtp==0.9.1
arbiter-imap==0.9.0
```

For explicit local artifact bundles, `requirements.txt` can also name
container wheel paths directly:

```text title="./requirements.txt"
/wheels/arbiter_server-0.9.0.dev2-py3-none-any.whl
/wheels/arbiter_smtp-0.9.0.dev2-py3-none-any.whl
```

</details>

<details>
<summary>Local checkout testing</summary>

Local checkout requirements are testing state. The only non-pinned entries
allowed in `requirements.txt` are absolute container source paths:

```text title="./requirements.txt"
/source/arbiter/server
/source/arbiter/plugins/imap
/source/arbiter/plugins/smtp
```

Mount the checkout explicitly with a local Compose override:

```yaml title="./compose.override.yaml"
services:
  arbiter:
    volumes:
      - /home/example/arbiter:/source/arbiter:ro
```

At container startup, the deployment copies the referenced source paths into a
writable scratch tree, removes stale package metadata such as `*.egg-info`, and
installs that scratch copy in editable mode. This keeps staging tied to the
local checkout without rebuilding package wheels on each start, while allowing
the checkout mount itself to stay read-only.

Do not run wheelhouse commands such as `bundle prepare` or `bundle check` in
local checkout mode. Those commands inspect wheel-backed deployments, while
local checkout requirements are resolved only inside the Compose service that
mounts `/source/arbiter`.

Linux install promotes this testing state automatically: it builds local wheels,
rewrites the installed `requirements.txt` to `/wheels/*.whl` entries, and moves
the `/source/arbiter` Compose override aside before copying the deployment into
place. Direct `doctor --preinstall` still reports local checkout requirements as
not install-ready because that command only checks the current directory; run
`install` to perform the promotion.

</details>

<details>
<summary>Maintainer package-index refresh</summary>

`--pypi-only` is intended for maintainers preparing package-index bundles. It
resolves selected Arbiter package names from the package index instead of the
checked-out repository packages.

For `bundle prepare --pypi-only`, the helper also rejects local path roots such
as `/source/arbiter/...`, ignores the existing deployment wheelhouse, rewrites
`requirements.txt` to the resolved exact versions, and builds a fresh
wheelhouse from package-index results.

For `bundle upgrade --pypi-only`, the helper skips the local repo wheel refresh
step and resolves upgrade targets from the package index.

Use this only when the selected Arbiter packages are expected to be available
from the package index.

</details>
