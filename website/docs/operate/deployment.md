---
title: Deployment
---

The generated service installs the requested Arbiter package target at
container startup, mounts deployment-owned config from the host, and publishes
MCP on host loopback. Prepared directories use staging-specific Docker
identifiers so they can be tested next to an installed deployment. Installation
rewrites the copied directory to the installed identity.

## Deployment Flow

1. Stage and test an Arbiter instance as an unprivileged operator.
   - Initialize a Docker staging directory.
   - Prepare the installation bundle by selecting Arbiter core and service
     plugin packages.
   - Bootstrap configuration for plugin accounts and policies.
   - Bring up the staged Docker instance.
   - Test manually or with an agent against the staged MCP endpoint.
2. Promote the checked staging directory to a system-installed service with
   `sudo`.

For the broader deployment trust model, see
[Security Model](./security.md).
