---
title: Reploy Command Reference
---

Reploy is the deployment tool used by Arbiter's Docker install path. Install it
before creating a deployment directory:

```bash
curl -fsSL https://reploy.yadan.net/install.sh | sh
```

If you prefer to install through Python packaging, Reploy is also available on
PyPI:

```bash
python3 -m pip install reploy
```

Use the installed `reploy` command for deployment management commands such as
bundle selection, app bootstrap, doctor, and install. Inside the prepared
staging directory, use the generated `./arbiterctl` app control script for the
staged Arbiter lifecycle and health commands:

```bash
reploy --dir reploy-staging COMMAND
cd reploy-staging
./arbiterctl COMMAND
```

For the normal deployment sequence, use
[Prepare Docker Deployment](./1-docker-prepare.md), then
[Linux Install](./2-linux-install.md). This page lists the Arbiter-facing
commands split between those two surfaces.

## Inspect

| Command | Purpose |
| --- | --- |
| `info` | Show deployment state and generated paths. |
| `doctor` | Check generated files and generated-file drift. |
| `doctor --preinstall` | Check that the prepared directory is ready to install. |
| `app config check [override...]` | Validate the app config in a one-shot container. |
| `app config check --live [override...]` | Validate app config and account readiness in the deployment runtime. |

## Install

| Command | Purpose |
| --- | --- |
| `install --to DIR` | Install the staging directory as a systemd service. |
| `install --to DIR --dry-run` | Print the install plan without changing the host. |
| `install --to DIR --no-start` | Install and enable the service without starting it. |

## App

| Command | Purpose |
| --- | --- |
| `app` | Show this deployment's blueprint-declared app subcommands. |
| `app bootstrap server` | Create the app server config through the deployment runtime. |
| `app bootstrap --plugin NAME --account ACCOUNT` | Create plugin account and policy config through the deployment runtime. |
| `app bootstrap --plugins NAME[,NAME...] --account ACCOUNT` | Create account and policy config for multiple plugins through the deployment runtime. |
| `app config activate --plugin NAME --account ACCOUNT` | Activate one account for one plugin. |
| `app config activate --plugins NAME[,NAME...] --account ACCOUNT` | Activate one account name for multiple plugins. |
| `app config show` | Show the composed app config through the deployment runtime. |
| `app env bootstrap` | Create or update the app env file from config references. |
| `app env check` | Check that app config env references are satisfied. |

## Run Staging

| Command | Purpose |
| --- | --- |
| `./arbiterctl up` | Start or update the staged Compose service. |
| `./arbiterctl health` | Call the staged Arbiter health endpoint. |
| `reploy test` | Probe the staged Arbiter health endpoint with startup retries. |
| `./arbiterctl status` | Show Docker Compose service status. |
| `./arbiterctl logs` | Follow Docker Compose logs with Docker timestamps. |
| `./arbiterctl restart` | Recreate the container. |
| `./arbiterctl down` | Stop and remove the staged Compose service. |
| `./arbiterctl config check [--live]` | Run the blueprint-exposed Arbiter config check command. |

Installed services are operated through systemd instead; see
[Linux Install](./2-linux-install.md#verify).

## Bundle

| Command | Purpose |
| --- | --- |
| `bundle list-options` | Show blueprint-declared bundle options and descriptions. |
| `bundle add --name NAME[,NAME...]` | Add blueprint-declared options such as service plugins or meta packages. |
| `bundle remove NAME[,NAME...]` | Remove selected runtime artifact roots. |
| `bundle list` | Show selected root requirements. |
| `bundle list all` | Show prepared root and transitive wheelhouse packages. |
| `bundle build` | Force immediate preparation and validation of the dependency wheelhouse. Normal staging and app commands prepare it automatically. |
| `bundle check` | Validate the existing wheelhouse without downloading packages or building wheels. |
| `bundle upgrade [TARGET]` | Upgrade selected package roots and mark the installation bundle for refresh. |

For package and wheelhouse behavior, see
[Bundle deep-dive](./3-bundle-deep-dive.md).
