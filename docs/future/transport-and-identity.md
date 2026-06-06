# Future: Transport Encryption and Bidirectional Identity

## Status

Future direction. This document records the intended security model direction
for remote and multi-agent Arbiter deployments. It is not an implementation
plan for the initial release.

## Summary

Arbiter should treat transport security and identity as separate concerns:

- **Encrypted transport** protects bytes in transit.
- **Bidirectional identity** proves which Arbiter instance and which agent are
  participating, so policy and audit can attach to stable principals.

Local loopback HTTP can remain a development and single-host mode, but future
networked deployments should use encrypted transport and explicit agent
identity.

## Goals

- Keep the current safe local default understandable: HTTP bound only to
  `127.0.0.1`.
- Define a clean path for remote and multi-agent deployments.
- Identify Arbiter to agents.
- Identify agents to Arbiter.
- Keep identity usable for authorization, policy selection, and audit.
- Avoid mixing certificate lifecycle, agent authorization, and service policy
  into one vague "security" setting.

## Non-goals

- Do not run SSH inside the Arbiter container.
- Do not make public ACME certificate management a server Arbiter server
  responsibility initially.
- Do not treat request signatures as a replacement for transport encryption
  when secrets or sensitive responses cross a network.
- Do not expose plain HTTP on a non-loopback interface as a normal deployment
  mode.

## Concern 1: Encrypted Transport

Transport encryption answers:

- Are request and response bytes protected from network observers?
- Can the client trust it reached the intended endpoint?
- Which deployment component owns certificate issuance and renewal?

### Local Loopback Mode

Local development and same-host agent deployments may use HTTP when the host
bind is loopback-only:

```text
agent -> http://127.0.0.1:8025/mcp -> Arbiter
```

This mode is acceptable only because the traffic stays on the host loopback
interface. It should not be generalized to remote access.

### Public HTTPS Mode

For deployments with a public DNS name, use a host reverse proxy for HTTPS:

```text
agent -> https://arbiter.example.com
      -> host reverse proxy
      -> http://127.0.0.1:8025/mcp
      -> Arbiter container
```

The reverse proxy owns public ACME certificates, renewal, redirects, and
public-port binding. Arbiter remains a loopback HTTP service behind the proxy.

This fits Caddy, nginx with certbot, Traefik, or similar tools. It avoids
embedding public ACME challenge handling, reload behavior, and certificate
storage policy inside Arbiter itself.

### Private HTTPS Mode

For private deployments without public DNS or public reachability, use an
Arbiter-managed private CA or pinned trust model:

- Generate or import a private CA.
- Issue an Arbiter server certificate for the configured endpoint.
- Configure Arbiter clients with the CA certificate or a pinned fingerprint.
- Keep private keys in protected deployment state.

This is the likely path for controlled multi-agent deployments where Arbiter
controls both client and server configuration.

## Concern 2: Bidirectional Identity

Identity answers:

- Which Arbiter instance is the agent talking to?
- Which agent is calling Arbiter?
- Which policy applies to that agent?
- What identity should audit records and operational logs record?

### Arbiter Identity

Arbiter identifies itself through the server side of TLS:

- Public CA certificate in public HTTPS mode.
- Private CA certificate or pinned server identity in private HTTPS mode.
- Optional explicit Arbiter instance id in configuration and audit records.

Agents should fail closed when the Arbiter identity is missing, unknown, or
unexpected in remote modes.

### Agent Identity

Candidate mechanisms:

- mTLS client certificates.
- Signed bearer tokens.
- Detached request signatures over canonical request bytes.
- PGP or another signing system as a request-signature backend.

mTLS is the cleanest symmetric transport identity model:

```text
agent verifies Arbiter certificate
Arbiter verifies agent certificate
agent certificate identity -> Arbiter agent id -> policy and audit
```

Request signatures are still useful when Arbiter needs durable proof that a
specific agent key authorized a specific payload. They are especially relevant
for audit, queues, brokers, or other paths where the request may outlive the
TLS connection. Detached signatures over canonical request bytes are likely
cleaner than PGP clearsigned text for structured MCP payloads.

## Policy and Audit Model

Identity should feed policy and audit without becoming the policy itself:

```text
transport authentication -> agent identity -> policy selection -> operation
```

The policy layer should decide what the identified agent may do. The audit
layer should record:

- Arbiter instance identity.
- Agent identity.
- Transport identity evidence, such as certificate fingerprint.
- Optional request-signature fingerprint or payload hash.
- Operation id, account scope, decision, and result metadata.

## Deployment Modes

| Mode | Transport | Arbiter Identity | Agent Identity | Intended Use |
| --- | --- | --- | --- | --- |
| Local loopback | HTTP on `127.0.0.1` | local process/config trust | local caller trust | development and same-host agents |
| Public reverse proxy | HTTPS via proxy | public CA certificate | mTLS or app-layer auth | public DNS deployments |
| Private managed TLS | HTTPS directly or via private proxy | private CA or pin | mTLS or signed requests | private multi-agent deployments |

## Design Direction

The long-term direction should be:

1. Keep loopback HTTP as the local development mode.
2. Document that remote Arbiter access requires HTTPS.
3. Support public HTTPS through an external reverse proxy.
4. Design private TLS as an Arbiter-managed deployment profile.
5. Add stable agent identity before broad multi-agent support.
6. Prefer mTLS for symmetric transport identity.
7. Consider detached request signatures for durable audit evidence.

## Open Questions

- Should private TLS be terminated by Arbiter itself or by a generated local
  reverse proxy profile?
- Where should private CA keys live, and should the CA remain online after
  issuing certificates?
- What is the canonical agent identity string: certificate subject, SAN,
  configured name, fingerprint, or a separate agent id mapped from credentials?
- Should request signatures be required for high-risk operations, optional for
  audit, or deferred until durable audit storage exists?
- How should agent identity interact with service-scoped policies and future
  audit configuration?
- What is the rotation story for server certificates, agent certificates, and
  request-signing keys?

## First Concrete Follow-ups

- Add operator documentation that remote access should be either loopback HTTP
  through an SSH tunnel or HTTPS through a reverse proxy.
- Add Docker deployment warnings for non-loopback host binds that are not
  explicitly marked as being behind HTTPS or another trusted boundary.
- Define an agent identity config shape independent of SMTP and IMAP service
  policies.
- Prototype mTLS with one agent certificate and one policy mapping.
