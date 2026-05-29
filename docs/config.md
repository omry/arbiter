# Configuration

## Purpose

Define the deployment-owned configuration contract for the Agent Arbiter server.

## Configuration system

The implementation language is Python so the server can use OmegaConf directly
for hierarchical configuration and environment-variable interpolation.

Examples below use OmegaConf interpolation. Secrets may be sourced from
environment variables via `oc.env` or from files via `secret_file`.

Agent Arbiter does not ship a runnable service config. Operators create a
Hydra config and pass its directory with `--config-dir`. The repository ignores
`config.local/` as scratchspace for local configs and secrets.

## Current model

Accounts are grouped under the `arbiter` config node by service:

- `arbiter.account.smtp.<account>`
- `arbiter.account.imap.<account>`

Policies are also grouped under `arbiter` by service:

- `arbiter.policy.smtp.<policy>`
- `arbiter.policy.imap.<policy>`

Each service account selects a reusable policy in the same service namespace
with `policy: <name>`. For example, multiple SMTP accounts can share
`arbiter.policy.smtp.bot`, while IMAP accounts reuse
`arbiter.policy.imap.readonly`.

Config bootstrap commands are documented in
[config_bootstrap.md](config_bootstrap.md).

Two surrounding areas are still only partially implemented:

- SMTP idempotency config is reserved for future runtime work. The current
  server fails closed at startup if those unsupported fields are configured.
- Durable audit storage and audit policy configuration are parked for post-v1.
  V1 examples avoid audit knobs because the runtime does not honor them yet.

## Illustrative config shape

```yaml
arbiter:
  account:
    smtp:
      primary:
        policy: bot
        description: Bot-owned account for automated email tasks.
        host: ${arbiter.etc.mailserver.smtp_host}
        port: 587
        authenticate: true
        username: bot@example.com
        password: ${oc.env:SMTP_PASSWORD}
        tls: starttls
        verify_peer: true
        from_email: bot@example.com
        from_name: Bot
      personal:
        policy: personal
        description: Personal account with stricter send policy.
        host: ${arbiter.etc.mailserver.smtp_host}
        port: 587
        authenticate: true
        username: personal@example.com
        password: ${oc.env:PERSONAL_SMTP_PASSWORD}
        tls: starttls
        verify_peer: true
        from_email: personal@example.com
        from_name: Personal

    imap:
      primary:
        policy: bot
        description: Bot inbox.
        host: ${arbiter.etc.mailserver.imap_host}
        port: 993
        username: bot@example.com
        password: ${oc.env:IMAP_PASSWORD}
        tls: implicit
        verify_peer: true
        default_folder: INBOX
        folders:
          INBOX:
            description: Primary inbox folder.
          Alerts:
            description: Operational notifications.

  policy:
    smtp:
      bot:
        require_confirmation: false
        limits:
          max_messages_per_minute: 30
          max_recipients_per_message: 20
        recipient_policy:
          allowed_recipients:
            - ops@example.com
          blocked_recipients: []
          allowed_domain_patterns:
            - example.com
            - "*.example.org"
          blocked_domain_patterns: []
      personal:
        require_confirmation: true

    imap:
      bot:
        allow_read: true
        allow_search: true
        allow_move: true
        allow_delete: true
        confirmation_required: []
        system_flags:
          seen: read_write
          flagged: read_write
          answered: read_write
          deleted: read_write
          draft: read_write
        user_flags: {}

  etc:
    mailserver:
      smtp_host: smtp.example.com
      imap_host: imap.example.com
```

The `arbiter.etc` node is weakly structured operator-owned space for
interpolation and composition. The server does not assign product semantics to
keys under `arbiter.etc`.

## Policy model

- `arbiter.account.<service>.<account>.policy` attaches a reusable service
  policy to an account.
- Policies are scoped by service. An SMTP account can reference only
  `arbiter.policy.smtp`, and an IMAP account can reference only
  `arbiter.policy.imap`.
- A service is active when it has at least one configured account.
- A configured account must reference an existing policy for that service.
- Unsupported SMTP idempotency config currently fails closed during startup
  validation instead of being silently ignored.

### SMTP service policy

`arbiter.policy.smtp.<policy>` answers "under what constraints may this account
send mail?"

- `require_confirmation`: whether callers should require explicit confirmation
  before sending from accounts that use this policy
- `limits.max_messages_per_minute`: enforced as a per-account, per-process
  rolling 60-second submission cap
- `limits.max_recipients_per_message`: enforced per submission
- `idempotency.expiration_days`: reserved for future idempotency retention;
  startup rejects configs that customize it today
- `recipient_policy`: outbound recipient guardrails

### IMAP service policy

`arbiter.policy.imap.<policy>` answers "which IMAP operations and flag
mutations are allowed?"

- `allow_read`
- `allow_search`
- `allow_move`
- `allow_delete`
- `confirmation_required`: action list scoped to IMAP only
- `system_flags`
- `user_flags`

Current IMAP confirmation action vocabulary:

- `read`
- `search`
- `move`
- `mark_read`
- `delete`

Flag modes:

- `hidden`: do not expose the flag in tool-visible responses and do not allow
  mutation
- `read_only`: expose the flag in tool-visible responses but do not allow
  mutation
- `read_write`: expose the flag and allow mutation

## Secrets

Store credentials outside source control. Deployment config may use
environment-variable interpolation, secret files, or an external secret manager.
Agent Arbiter does not own, generate, or load `.env` files; `${oc.env:...}` is
resolved by OmegaConf from the Arbiter process environment.

A local env file can still be useful as an operator-owned shell convenience:

```bash
# config.local/local.env
SMTP_USERNAME_PRIMARY_ACCOUNT=agent@example.com
SMTP_PASSWORD_PRIMARY_ACCOUNT=change-me
```

Load it before running Arbiter:

```bash
set -a
. config.local/local.env
set +a
agent-arbiter --config-dir "$PWD/config.local" --config-name config config check
```
