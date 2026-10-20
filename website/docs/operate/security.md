---
title: Security Model
---

Arbiter's current trust model assumes the caller is trusted once connected to
the server. Caller authentication is absent. Supported prerelease use is limited
to trusted local clients; remote, hosted, or shared access is unsupported until
authentication exists. HTTPS protects the connection but does not identify or
authorize its caller.

## Current boundary

The active boundary is deployment-owned configuration plus path control:

- configured services
- configured accounts
- service-specific policies
- deployment-owned credentials and transport settings
- control over alternative paths to the same service

Operators own the deployment inputs. Agents consume the Arbiter operations
produced by those inputs.

Callers may explicitly select any configured account exposed by the server.
Account names and descriptions guide caller behavior, but they are not identity
or authorization tiers.

## Credentials

Agents should never receive service credentials in this model. Credentials live
in operator-owned account configuration and environment values. Service plugins
use those credentials inside the server process after applying policy checks,
then talk to the upstream service using its native protocol.

Account and policy discovery never exports raw deployment configuration.
The server returns public account metadata and only the config fields selected
by explicit plugin projections. Plugins without these projections expose
empty account `config` and policy `rules` objects. Public fields such as account
descriptions and guidance are deliberately visible to callers; do not put
credentials or secret interpolations in those fields. Installed plugins are
trusted code and must keep their discovery projections free of secrets.

The Arbiter operator is the root administrator for the deployment. Arbiter
protects configured services from agents and other callers; it does not protect
account owners from the person or organization that controls the Arbiter host,
process, configuration, plugin packages, logs, and startup environment. Do not
ask users to provide private account credentials to an Arbiter deployment unless
they trust that deployment's operator with those credentials or with equivalent
delegated access.

Do not expose the same credentials to the agent through environment variables,
workspace files, shell startup files, local credential stores, unrestricted API
tokens, or helper tools. If an agent can use the protected service directly,
Arbiter is no longer the enforcement point.

## Artifact URLs and logs

Artifact URLs contain a bearer nonce in their query string. Anyone who obtains
an unconsumed URL can retrieve that artifact. Metadata and HEAD requests do not
consume the nonce, so a URL copied into logs can remain usable.

The standard Arbiter server launcher removes the entire query string from
artifact access-log records before handlers or formatters receive them. This
covers metadata, content, HEAD, and failed requests, while retaining the method,
path and status. Requests still carry their nonce for authentication, and
one-time download behavior is unchanged.

Configure equivalent query redaction before emission in reverse proxies,
external ASGI hosts, tracing and other request logging. Arbiter cannot sanitize
records already written by another process. For example, an Nginx access format
can log `$request_method $uri $server_protocol` and `$status`; `$request` and
`$request_uri` include the query string. Check error/debug logging too, and do
not copy complete artifact URLs into diagnostics or shared logs.

## Configuration output

`config show --resolve` masks `oc.env` and `secret_file` results as
`<REDACTED>`. Scalar selections, including `--package PATH --value`, are masked
before resolution even when `--resolve` is omitted. This applies to all
environment-derived values, including hosts and ports. Normal runtime commands
and readiness checks still resolve the actual values.

Without `--resolve`, YAML output retains unresolved interpolation references.
Secrets written literally in YAML can still be printed: resolver masking is
not a general secret detector. Keep config output operator-only and review it
before sharing.

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

Binding to `127.0.0.1` protects against network access from other hosts, but
any local process that can reach the Arbiter server can use whatever the
configured policy allows.

Startup warns about this unauthenticated boundary, and adds a warning when the
bind host is not a literal loopback IP address. Hostnames also receive that
warning because the server does not assume their resolution is local. These
warnings do not refuse startup or change policy.

Docker's internal `0.0.0.0` bind is separate from the host published address.
Publish the port on host `127.0.0.1`, not `0.0.0.0`; an advertised loopback URL
does not prove the port is private. Check Compose port mappings and any reverse
proxy or forwarding configuration. A container can still be reachable by peers
on its Docker network, so those peers must also be trusted.

The default Go client skips server certificate verification for loopback HTTPS
when no CA file is configured. This does not protect against a local process
impersonating a stopped or restarting Arbiter server. Trust only local clients
and the local host. Server identity, caller authentication, and bidirectional
trust enrollment remain separate unfinished work; no enrollment protocol has
been selected.

Generated IMAP policies default to broad folder read/search access, including
future folders; SMTP policies default to unrestricted recipients. Activation
warnings explain these defaults. Review and narrow the selected policies to
match the account's purpose. These retained defaults are an accepted prerelease
risk, not an isolation boundary against prompt injection.

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
- Limit Arbiter server reachability to trusted local clients.

## Future work

Client identification and authentication are planned design work. Until then,
deploy Arbiter only where the server is reachable by trusted local clients.

Hosted or shared Arbiter deployments need a separate secret-ownership design
before they can safely serve users who do not trust the operator with their
account credentials. PKI-mediated secret release, user-provided decryption keys,
OAuth delegation, local credential brokers, per-user execution workers, and
confidential-computing approaches may all be useful building blocks, but none
should be documented as solving operator trust until the runtime threat model is
explicit.

## Offline authority initialization (development)

The first authentication milestone provides stopped-instance identity setup:

```bash
arbiter-server authority init --state-dir /private/path/authority \
  --recovery-file /operator-only/private/operator-recovery.json
arbiter-server authority status --state-dir /private/path/authority --json
```

Use an operator-owned private directory. The command creates persistent owner
and server UUIDs in a versioned SQLite database. It does not read Hydra
configuration, load service plugins, or contact upstream accounts. A second
initialization fails instead of replacing existing identity records. Status
reports absent or damaged state without creating a replacement database.

Prepare the recovery file's private parent directory separately. Keep the file
outside the authority directory, server/agent storage and synced backup storage.
It holds both backup-decryption and live-recovery authority; theft compromises
both. See [operator recovery custody](credentials.md#operator-recovery-custody)
for reuse and interrupted setup.

Stop the instance before initializing. Concurrent initialization attempts use
one persistent process lock. A failed unpublished initialization can be retried;
a database already published must be inspected rather than overwritten. Keep
the directory private; the command rejects unsafe permissions and linked files.

On Windows, prepare a private parent directory with inheritable access for the
operator, SYSTEM and Administrators only. New authority directories inherit that
ACL. Initialization validates it and refuses unsafe access; it does not repair
existing directory or file permissions.
Ancestors must also be owned by the operator, SYSTEM, Administrators or the
fixed Windows Modules Installer (TrustedInstaller) service and must not let
another user replace their children or change their permissions. The fixed
TrustedInstaller SID exception applies to ancestors only. The authority root and
every state file must be owned by the caller, SYSTEM or Administrators, in
addition to passing the existing private ACL checks. Use a private path under the
operator's profile; other unsupported ownership fails closed.

Initialization also creates separate owner-issuer, server-authority and server
TLS signing credentials in the same private database transaction. Status never
prints private keys. See [credential profiles](credentials.md) for their purposes,
validity and verification rules. This does not enable caller authentication;
the current trusted-local-client restriction above still applies.
