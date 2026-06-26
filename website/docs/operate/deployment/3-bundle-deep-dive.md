---
title: Bundle deep-dive
---

This is the package reference behind
[Prepare Docker Deployment](./1-docker-prepare.md). The runbook owns the command
sequence; this page explains what the package bundle is and how to reason about
it.

The bundle has two deployment-owned pieces:

- `.reploy/requirements.txt`: root Arbiter packages selected for this
  deployment.
- `.reploy/bundle/`: the complete wheelhouse used by the container at startup.

The goal is reproducible container creation. Staging resolves package versions
and prepares wheels before install, so Docker can create or recreate the
runtime container without reaching PyPI.

## Package roots

`.reploy/requirements.txt` records the root packages for the deployment. Normal
package roots are exact pins:

```text title=".reploy/requirements.txt"
arbiter-server==0.9.0.dev2
arbiter-imap==0.9.0.dev2
arbiter-smtp==0.9.0.dev2
```

Unpinned names and version ranges are rejected for runtime deployment state.
Use the bundle commands in the prepare runbook to select plugins or upgrade
versions.

By default, `reploy init --blueprint arbiter-server` seeds the bundle with the
Arbiter server package root. During bundle build, Reploy resolves the package
root to the exact version selected for the deployment.

`bundle add` and `bundle remove` update `.reploy/requirements.txt`. Adding
`arbiter-suite` selects all plugins in the suite meta package. When the suite
must be represented as concrete runtime roots, Arbiter expands it into
`arbiter-server` plus the selected plugin packages.

Custom service plugins do not need to be known to Reploy's Arbiter blueprint.
They must be Python packages that expose an Arbiter service entry point:

```toml title="pyproject.toml"
[project.entry-points."arbiter.services"]
my_plugin = "my_package:plugin"
```

For a plugin available from a package index, add an exact package pin:

```bash
./reploy bundle add my-arbiter-plugin==1.0.0
```

For a private wheel, copy it into the deployment wheelhouse and add it as a
root:

```bash
./reploy bundle add-wheel ./dist/my_arbiter_plugin-1.0.0-py3-none-any.whl
```

For a local source checkout outside the Arbiter repository, build a wheel into
the deployment wheelhouse and add that wheel as a root:

```bash
./reploy bundle add-source ../my-arbiter-plugin
```

After adding custom package roots, run `bundle build`. Published package pins
may still need package-index access during build; wheel and source workflows
keep the selected plugin artifact local, but any missing transitive dependency
wheels still have to be resolved during build.

The package named `arbiter` on PyPI is unrelated to this project. Use
`arbiter-suite` or concrete packages such as `arbiter-server`, `arbiter-smtp`,
and `arbiter-imap`.

## Wheelhouse

`bundle build` validates `.reploy/requirements.txt`, resolves the full runtime
install set with the configured runtime image, and writes all required wheels
to `.reploy/bundle/`. Each run expands the wheelhouse fully and prunes stale
wheels that are no longer part of the resolved install set.

When the staging directory is in an Arbiter repository checkout, normal
`bundle build` first rebuilds local Arbiter wheels from that checkout. Use
`bundle build --pypi-only` only when the selected Arbiter package names should
come from the package index instead of the checkout.

The generated Compose file mounts `.reploy/bundle` at `/bundle`. During
container startup, the install command uses `/bundle`, so startup does not
depend on package-index access.

Use `bundle check` to validate an already-prepared wheelhouse without
downloading packages or building wheels. Use `bundle list all` after
preparation to show every wheelhouse package, marked as `root` or
`transitive`.

The default Docker env keeps the wheelhouse inside the deployment directory:
`REPLOY_BUNDLE_DIR=./.reploy/bundle`. Keep runtime paths relative to the
deployment directory; Linux install rejects absolute host paths for runtime
files.

Server-owned runtime state is kept under the deployment directory by default:
`ARBITER_SERVER_DATA_DIR=./data/server`. Docker mounts that host directory at
`/data/server`, and the generated Compose command passes it to Arbiter as
`arbiter.storage.server_data_dir=/data/server`. Self-signed TLS material lives
there.

Plugin writable state is separate:
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
`.reploy/requirements.txt`.

<details>
<summary>Explicit package roots</summary>

Most deployments should use the bundle menu. To seed exact package roots during
init, pass repeated `--requirement` values:

```bash
reploy init --blueprint arbiter-suite \
  --requirement arbiter-server==0.9.0.dev2 \
  --requirement arbiter-smtp==0.9.0.dev2
```

The same mechanism can express a curated suite plus an override:

```bash
reploy init --blueprint arbiter-suite \
  --requirement arbiter-suite==0.9.0 \
  --requirement arbiter-smtp==0.9.1
```

Arbiter expands that selection to concrete package pins so pip does not see
conflicting meta-package dependencies:

```text title=".reploy/requirements.txt"
arbiter-server==0.9.0
arbiter-smtp==0.9.1
arbiter-imap==0.9.0
```

For explicit local artifact bundles, `.reploy/requirements.txt` can also name
container wheel paths directly:

```text title=".reploy/requirements.txt"
/bundle/arbiter_server-0.9.0.dev2-py3-none-any.whl
/bundle/arbiter_smtp-0.9.0.dev2-py3-none-any.whl
```

</details>

<details>
<summary>Local checkout testing</summary>

Use `bundle add-source` for local checkout testing. Reploy builds the source
directory inside the configured runtime container, copies the resulting wheel
into `.reploy/bundle/`, and records a `/bundle/*.whl` container root:

```bash
./reploy bundle add-source ../my-arbiter-plugin
```

Persistent `/source/...` roots are not installable. `doctor --preinstall`
rejects them and points you back to `bundle add-source`, so the installed
deployment never depends on a host checkout mount.

</details>

<details>
<summary>Maintainer package-index refresh</summary>

`--pypi-only` is intended for maintainers preparing package-index bundles. It
resolves selected Arbiter package names from the package index instead of the
checked-out repository packages.

For `bundle build --pypi-only`, the helper also rejects local path roots such
as `/source/app/...`, ignores the existing deployment wheelhouse, rewrites
`.reploy/requirements.txt` to the resolved exact versions, and builds a fresh
wheelhouse from package-index results.

For `bundle upgrade --pypi-only`, the helper skips the local repo wheel refresh
step and resolves upgrade targets from the package index.

Use this only when the selected Arbiter packages are expected to be available
from the package index.

</details>
