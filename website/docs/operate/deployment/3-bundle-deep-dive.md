---
title: Bundle deep-dive
---

This is the package-selection reference behind
[Prepare Docker Deployment](./1-docker-prepare.md). The runbook owns the command
sequence; this page explains how current Reploy turns requested Arbiter
components into a validated environment image.

## Request overlay

The blueprint declares the base `arbiter-server` requirement and optional
`arbiter-suite`, `imap`, and `smtp` package contributions. A staging
deployment's request overlay records which options and direct package additions
the operator selected; it does not store a hand-maintained requirements file or
wheelhouse.

List the available component-qualified options and current overlay:

```bash
reploy bundle options
reploy bundle list
```

Select or deselect options with their application prefix:

```bash
reploy bundle add application/imap,smtp
reploy bundle remove application/smtp
reploy bundle add application/arbiter-suite
```

Selections are normalized, validated together, and applied atomically. Changing
the overlay makes the current build stale but does not build automatically.

## Direct package additions

Custom Arbiter service plugins must be Python packages that expose an Arbiter
service entry point:

```toml title="pyproject.toml"
[project.entry-points."arbiter.services"]
my_plugin = "my_package:plugin"
```

Add an exact direct Python request to the blueprint's application contribution:

```bash
reploy bundle add-package \
  application/application/python \
  my-arbiter-plugin==1.0.0
```

Remove it by supplying the same component and normalized request:

```bash
reploy bundle remove-package \
  application/application/python \
  my-arbiter-plugin==1.0.0
```

The package named `arbiter` on PyPI is unrelated to this project. Use
`arbiter-suite` or concrete packages such as `arbiter-server`, `arbiter-smtp`,
and `arbiter-imap`.

## Build and provider store

Run `reploy build` after changing the blueprint, request overlay, or development
overrides. Reploy resolves each provider request, records immutable artifacts in
the deployment-local provider store, materializes the cumulative environment
layers, validates the final application image, and commits a build lock that
binds the blueprint, platform, overlay, inputs, and outputs.

```bash
reploy build
```

`reploy up` and `reploy restart` ensure the build automatically. App commands,
shells, tests, and observations consume the committed image and reject a
missing or stale build instead of resolving packages implicitly.

Use `reploy verify` to audit the current build without changing state. It hashes
the reachable provider-store closure, re-inspects the image and layers, reruns
provider checks in temporary network-disabled containers, and verifies that the
declared runtime commands still resolve.

`reploy bundle clean` removes the deployment-local provider cache. It does not
invalidate an already committed image, but a later build may need to reacquire
artifacts.

## Updating versions

Current Reploy intentionally has no `bundle upgrade` command. Change the
blueprint constraint, an exact direct package request, or a development
override, then run `reploy build`. This keeps version intent in the owning
input instead of rewriting a generated requirements projection.

For an installed system deployment, make and test the change in staging first,
then run `reploy install --scope system` again. Install ensures the staged build
is current and transfers only the selected build closure and managed deployment
state.

## Local checkout testing

A local blueprint reference selects the blueprint from the checkout:

```bash
reploy stage file:server/src/arbiter_server/reploy/arbiter.blueprint.yaml
```

Local package sources are explicit development overrides, not blueprint-owned
path mappings. Run `reploy overrides` in the staging deployment and select the
local `arbiter-server`, client, plugin, or suite projects that should replace
their published package sources. Reploy stores those choices in the staging
deployment's `overrides.yaml`; they participate in build identity but are not
installed as persistent host source mounts.
