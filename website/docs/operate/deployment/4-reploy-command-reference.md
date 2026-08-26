---
title: Reploy Command Reference
---

Reploy is the deployment tool used by Arbiter's Docker install path. Install it
before creating a deployment directory:

```bash
curl -fsSL https://reploy.cli.dev/install.sh | sh
```

If you prefer to install through Python packaging, Reploy is also available on
PyPI:

```bash
python3 -m pip install reploy
```

Use the installed `reploy` command for bundle selection, builds, app commands,
lifecycle operations, checks, and installation. Commands accept `--dir` when
you are outside the prepared staging directory:

```bash
reploy COMMAND --dir reploy-staging
cd reploy-staging
reploy COMMAND
```

Reploy also generates the standard deployment-local `appctl` convenience
control script. This reference uses the `reploy` CLI directly when describing
the staging workflow.

For the normal deployment sequence, use
[Prepare Docker Deployment](./1-docker-prepare.md), then
[Linux Install](./2-linux-install.md). This page lists the Arbiter-facing
commands split between those two surfaces.

## Inspect

| Command | Purpose |
| --- | --- |
| `info` | Show deployment state and generated paths. |
| `doctor` | Check state and generated runtime-file drift. |
| `doctor --preinstall --scope system` | Read-only system-install readiness check. |
| `verify` | Fully audit the current build without changing it. |
| `app config check [override...]` | Validate the app config in a one-shot container. |
| `app config check --live [override...]` | Validate app config and account readiness in the deployment runtime. |

## Install

| Command | Purpose |
| --- | --- |
| `install --scope system --to DIR` | Install the staging deployment as a systemd service. |
| `install --scope system --to DIR --no-start` | Install without starting the service. |
| `install --scope system --replace PATH` | Replace one otherwise-preserved managed path. |
| `install --scope system --clean` | Replace all managed content. |

## App

| Command | Purpose |
| --- | --- |
| `app` | Show this deployment's blueprint-declared app subcommands. |
| `app bootstrap server` | Create the app server config through the deployment runtime. |
| `app bootstrap --plugin=NAME --account=ACCOUNT` | Create plugin account and policy config through the deployment runtime. |
| `app bootstrap --plugins=NAME[,NAME...] --account=ACCOUNT` | Create account and policy config for multiple plugins through the deployment runtime. |
| `app config activate --plugin=NAME --account=ACCOUNT` | Activate one account for one plugin. |
| `app config activate --plugins=NAME[,NAME...] --account=ACCOUNT` | Activate one account name for multiple plugins. |
| `app config show` | Show the composed app config through the deployment runtime. |
| `app env bootstrap` | Create or update the app env file from config references. |
| `app env check` | Check that app config env references are satisfied. |

## Run Staging

| Command | Purpose |
| --- | --- |
| `reploy up` | Build if needed, then start or update the staged service. |
| `reploy test` | Probe the staged Arbiter health endpoint. |
| `reploy status` | Show staged service status. |
| `reploy logs --follow` | Follow staged service logs. |
| `reploy restart` | Build if needed, then recreate the service. |
| `reploy down` | Stop and remove the staged service. |
| `reploy app config check [--live]` | Run the blueprint-exposed Arbiter config check command. |

Installed services are operated through systemd instead; see
[Linux Install](./2-linux-install.md#verify).

## Bundle

| Command | Purpose |
| --- | --- |
| `bundle options` | Show component-qualified blueprint options and descriptions. |
| `bundle add COMPONENT/OPTION[,OPTION...]` | Select one or more blueprint options. |
| `bundle remove COMPONENT/OPTION[,OPTION...]` | Deselect blueprint options. |
| `bundle list` | Show the canonical request overlay. |
| `bundle add-package COMPONENT REQUIREMENT...` | Add direct provider-native package requests. |
| `bundle remove-package COMPONENT REQUIREMENT...` | Remove exact direct package requests. |
| `bundle clean` | Remove the deployment-local provider cache without invalidating an existing image. |

## Build

| Command | Purpose |
| --- | --- |
| `build` | Resolve, materialize, and validate the requested environment image. |
| `build --verify` | Fully verify a reusable current build; rebuild if verification fails. |
| `build --no-cache` | Rerun resolvers and image construction. |
| `verify` | Audit the current build without resolving, repairing, or changing state. |

For package selection and build behavior, see
[Bundle deep-dive](./3-bundle-deep-dive.md).
