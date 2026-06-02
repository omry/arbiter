---
title: Security Model
---

Arbiter's current trust model assumes the caller is trusted once connected
to the MCP server. Caller authentication is not part of the first server
contract yet.

## Current boundary

The active boundary is deployment-owned configuration plus path control:

- configured services
- configured accounts
- service-specific policies
- deployment-owned credentials and transport settings
- control over alternative paths to the same service

Operators own the deployment inputs. Agents consume the MCP tools produced by
those inputs.

Callers may explicitly select any configured account exposed by the server.
Account names and descriptions guide caller behavior, but they are not identity
or authorization tiers.

## Credentials

Agents should never receive service credentials in this model. Credentials live
in operator-owned account configuration and environment values. Service plugins
use those credentials inside the server process after applying policy checks,
then talk to the upstream service using its native protocol.

Do not expose the same credentials to the agent through environment variables,
workspace files, shell startup files, local credential stores, unrestricted API
tokens, or helper tools. If an agent can use the protected service directly,
Arbiter is no longer the enforcement point.

## Bypass paths

Arbiter is not a general sandbox. It gates service access only when the
agent cannot reach that service another way.

Common bypasses include:

- direct service credentials
- local tools such as `sendmail`
- unrestricted API tokens
- writable config, env files, plugin packages, or startup scripts
- administrator access to the Arbiter deployment
- another network path to the same protected service

If an agent can edit the policy source or avoid the server entirely, prompt
injection can turn that capability into a policy bypass.

## Deployment note

Binding to `127.0.0.1` protects against network access from other hosts, but any
local process that can reach the MCP endpoint can use whatever the configured
policy allows.

For production, run Arbiter as a separate least-privileged user. Harden
filesystem permissions so agents and other untrusted users cannot modify config,
environment files, plugin packages, the Arbiter installation, or startup
scripts. Do not run Arbiter as root or an administrator.

For Docker deployments, treat the Docker socket and Docker group as
root-equivalent.

## Practical checklist

- Keep Arbiter config outside agent-writable workspaces.
- Store service credentials only in operator-owned config or env files.
- Ensure agents do not inherit the upstream service credentials.
- Remove local tools or API tokens that reach the protected service directly.
- Run the server as a least-privileged deployment user.
- Limit MCP endpoint reachability to trusted local clients.

## Future work

Client identification and authentication are planned design work. Until then,
deploy Arbiter only where the MCP endpoint is reachable by trusted local
clients.
