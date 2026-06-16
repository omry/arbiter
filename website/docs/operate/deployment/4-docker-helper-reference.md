---
title: Docker Helper Reference
---

The generated `arbiter-docker` helper lives in each deployment directory. It is
not a global console app. Run these commands from inside the prepared staging
directory:

```bash
cd arbiter-docker
./arbiter-docker COMMAND
```

For the normal deployment sequence, use
[Prepare Docker Deployment](./1-docker-prepare.md), then
[Linux Install](./2-linux-install.md). This page is only a command reference for
the helper.

## Inspect

| Command | Purpose |
| --- | --- |
| `info` | Show generated paths, including the plugin data directory, and Docker Compose version. |
| `doctor` | Check generated files, env syntax, package requirements, and Docker Compose availability. |
| `doctor --agent-user USER` | Also check common permission mistakes for an agent identity. |
| `doctor --preinstall` | Check that the prepared directory is ready to promote. |
| `config check [override...]` | Validate the deployment config in a one-shot container. |
| `config check --live [override...]` | Validate config and account readiness in the running service container. |

## Install

| Command | Purpose |
| --- | --- |
| `install` | Promote the staging directory to an installed systemd service. First install seeds config from staging; later installs preserve installed config and env. |
| `install --replace-config` | Promote and explicitly replace the installed config package from staging. |

## Edit

| Command | Purpose |
| --- | --- |
| `edit-env` | Edit Arbiter runtime values and credentials in `conf/.env`. |
| `edit-docker` | Edit Docker wrapper settings in `docker.env`. |
| `edit-requirements` | Edit root package requirements or explicit wheel paths. Prefer the bundle commands for normal plugin selection. |

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
| `bundle list-plugins` | Show addable service plugins and descriptions. |
| `bundle add NAME` | Add a service plugin or meta package to `requirements.txt`. |
| `bundle add-package PACKAGE==VERSION` | Add an exact package pin for an external plugin. |
| `bundle add-wheel PATH` | Copy a local wheel into the wheelhouse and add it as a root. |
| `bundle add-source DIR` | Build a local package source directory into the wheelhouse and add it as a root. |
| `bundle remove NAME` | Remove a service plugin or meta package from `requirements.txt`. |
| `bundle list` | Show selected root requirements. |
| `bundle list all` | Show prepared root and transitive wheelhouse packages. |
| `bundle prepare` | Build and validate the dependency wheelhouse. |
| `bundle check` | Validate the existing wheelhouse without downloading packages or building wheels. |
| `bundle upgrade [TARGET]` | Upgrade selected package roots and rebuild the wheelhouse. |

For package and wheelhouse behavior, see
[Bundle deep-dive](./3-bundle-deep-dive.md).
