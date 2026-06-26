---
title: Docker Deployment
---

Deploy Arbiter by first preparing a local Docker staging directory, then
promoting that tested directory to a Linux system service.

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
2. [Linux Install](./deployment/2-linux-install.md): promote the checked
   staging directory to a system-installed service with `sudo`.

## References

- [Bundle deep-dive](./deployment/3-bundle-deep-dive.md): package roots,
  wheelhouse behavior, upgrades, and maintainer bundle refreshes.
- [Reploy Command Reference](./deployment/4-reploy-command-reference.md):
  generated helper commands for inspection, staging, and bundle maintenance.
- [Security Model](./security.md): deployment trust boundaries and host access
  assumptions.
