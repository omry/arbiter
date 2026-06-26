---
title: Reploy Command Reference
---

The installed `reploy` command and the generated deployment-local helper run the
same deployment commands. Use the installed command from the parent directory,
or run the generated helper from inside the prepared staging directory:

```bash
reploy --dir reploy-staging COMMAND
cd reploy-staging
./reploy COMMAND
```

For the normal deployment sequence, use
[Prepare Docker Deployment](./1-docker-prepare.md), then
[Linux Install](./2-linux-install.md). This page is only a command reference for
the helper.

## Inspect

| Command | Purpose |
| --- | --- |
| `info` | Show deployment state and generated paths. |
| `doctor` | Check generated files and generated-file drift. |
| `doctor --preinstall` | Check that the prepared directory is ready to promote. |
| `app config check [override...]` | Validate the app config in a one-shot container. |
| `app config check --live [override...]` | Validate app config and account readiness in the deployment runtime. |

## Install

| Command | Purpose |
| --- | --- |
| `install --to DIR` | Promote the staging directory to an installed systemd service. |
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
| `up` | Start or update the staged Compose service, then print the server URL. |
| `test` | Call the Arbiter client through the computed server URL, retrying through transient startup failures. |
| `ps` | Show Docker Compose service status. |
| `logs` | Follow Docker Compose logs with Docker timestamps. |
| `restart` | Recreate the container, reinstalling the configured requirements, then print the server URL. |
| `down` | Stop and remove the staged Compose service. |

Installed services are operated through systemd instead; see
[Linux Install](./2-linux-install.md#verify).

## Bundle

| Command | Purpose |
| --- | --- |
| `bundle list-options` | Show blueprint-declared bundle options and descriptions. |
| `bundle add --name NAME[,NAME...]` | Add blueprint-declared options such as service plugins or meta packages. |
| `bundle add-wheel PATH` | Copy a local wheel into the wheelhouse and add it as a root. |
| `bundle add-source DIR` | Build a local package source directory into the wheelhouse and add it as a root. |
| `bundle remove NAME[,NAME...]` | Remove selected runtime artifact roots. |
| `bundle list` | Show selected root requirements. |
| `bundle list all` | Show prepared root and transitive wheelhouse packages. |
| `bundle build` | Build and validate the dependency wheelhouse. |
| `bundle check` | Validate the existing wheelhouse without downloading packages or building wheels. |
| `bundle upgrade [TARGET]` | Upgrade selected package roots and rebuild the wheelhouse. |

For package and wheelhouse behavior, see
[Bundle deep-dive](./3-bundle-deep-dive.md).
