---
title: Docker Deployment
---

Deploy Arbiter by first preparing a local Docker staging directory, then
installing that checked staging directory as a Linux system service.

## Reploy

Reploy is the deployment tool used by Arbiter's Docker install path. It creates
the staging directory, manages generated Docker files, runs deployment-scoped
app commands, and installs a checked staging directory as a Linux service.

Install Reploy on the operator machine before starting:

```bash
curl -fsSL https://reploy.yadan.net/install.sh | sh
```

If you prefer to install through Python packaging, Reploy is also available on
PyPI:

```bash
python3 -m pip install reploy
```

The staging directory is ordinary user-owned state: it contains the selected
package bundle, generated Docker files, config, env placeholders, and a local
helper script. You can start it locally, test the Arbiter server, and iterate
before anything is installed under `/opt`.

When staging works, install copies that directory into place and rewrites it
from a staging identity to the installed service identity.

## Deployment Flow

1. [Prepare Docker Deployment](./deployment/1-docker-prepare.md): stage and
   test an Arbiter instance as an unprivileged operator.
   - Initialize a Docker staging directory.
   - Prepare the installation bundle by selecting Arbiter server and service
     plugin packages.
   - Bootstrap configuration for plugin accounts and policies.
   - Bring up the staged Docker instance.
   - Test manually or with an agent against the staged Arbiter server.
2. [Linux Install](./deployment/2-linux-install.md): install the checked
   staging directory as a system service with `sudo`.

## References

- [Bundle deep-dive](./deployment/3-bundle-deep-dive.md): package roots,
  wheelhouse behavior, upgrades, and maintainer bundle refreshes.
- [Reploy Command Reference](./deployment/4-reploy-command-reference.md):
  generated helper commands for inspection, staging, and bundle maintenance.
- [Security Model](./security.md): deployment trust boundaries and host access
  assumptions.
